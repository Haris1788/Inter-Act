"""ArmAgent — a robotic-arm actor in the decentralized negotiation runtime.

An arm fills `arm`-kind slots (pickup / place) for a station zone it can
reach. Stage 3: `_execute_leg` performs real arm animation (angle sweep)
and the load/unload handshake with the AGV.

Phase 4 Stage 3: `_execute_leg` resolves task_id + round from
`self._current_participant` (set by ActorAgent._check_consensus_for just before
the call). Handshake signals are scoped per-task via task_id.

For backward compatibility: when `self._current_participant` is None (e.g. old
monkeypatched test code calls _execute_leg with slot_id only), we fall back to
reading task_id from the first participant in self._tasks (single-task path),
or to empty string if none available.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from app.agents.actor_agent import ActorAgent
from app.agents.actuation import PICK, PLACE, REST, sweep_arm
from app.agents.negotiation import accepted_actors

logger = logging.getLogger(__name__)

SIGNAL_POLL_INTERVAL: float = 0.25
UNLOAD_CLEAR_TIMEOUT: float = 30.0  # wait for AGV to clear the unload signal


class ArmAgent(ActorAgent):
    """Robotic-arm actor. Bids on reachable arm slots; bid score is reach
    cost (0.0 when it serves the slot's zone)."""

    def _bid_score(self, slot: dict) -> Optional[float]:
        if slot.get("kind") != "arm":
            return None
        zone = slot.get("zone")
        if zone is None or zone not in self.profile.reachable_zones:
            return None
        return 0.0

    # ── Stage 3: real leg execution ────────────────────────────────────────

    def _execute_leg(self, slot_id: str) -> bool:
        """Arm pickup or place sequence.

        Reads task_id + round from self._current_participant (set by
        ActorAgent._check_consensus_for before this call). Handshake signals are
        scoped per-task so concurrent tasks cannot cross-pollinate load/unload state.

        pickup slot:
            1. sweep to PICK, sweep to REST
            2. signal_load(agv_id, object_id, task_id=task_id)

        place slot:
            1. signal_unload(agv_id, task_id=task_id)
            2. wait for AGV to clear the signal
            3. sweep to PLACE, sweep to REST

        Barrier-checked between every sweep. On halt: stop, log it.

        Returns True when the leg completed fully, False when halted early.
        Unknown slots and missing runtime state are no-op successes (return True).
        """
        if slot_id not in ("pickup", "place"):
            logger.debug("ArmAgent %s: ignoring unknown slot %s", self.actor_id, slot_id)
            return True

        arm_id = self._numeric_arm_id()
        if arm_id is None:
            logger.error(
                "ArmAgent %s: cannot parse numeric arm id — skipping leg", self.actor_id
            )
            return True

        # Resolve task_id and round from the current participant.
        participant = self._current_participant
        if participant is not None:
            task_id = participant.task_id
            neg_round = participant.round
        else:
            # Fallback: single-task path (backward compat).
            task_id = next((p.task_id for p in self._tasks.values()), "")
            neg_round = next((p.round for p in self._tasks.values()), -1)

        history = self.bus.history()
        accepts = accepted_actors(history, task_id, neg_round)
        agv_id = accepts.get("transport")
        if agv_id is None:
            logger.warning(
                "ArmAgent %s: no transport slot holder found — cannot execute %s",
                self.actor_id, slot_id,
            )
            return True

        # Derive a simple object_id from the task_id.
        object_id = f"pkg-{task_id}"

        halted = False

        def _valid() -> bool:
            p = self._current_participant
            if p is not None:
                return p.execution_valid(slot_id)
            # Fallback: check only agent-level fault flag.
            return not self._fault_requested

        def _halt_check() -> bool:
            return self._running and _valid()

        def _halt(reason: str) -> None:
            nonlocal halted
            halted = True
            logger.info("ArmAgent %s: halt — %s", self.actor_id, reason)
            _emit_log(self.actor_id, f"Arm {self.actor_id} halting execution: {reason}")

        if slot_id == "pickup":
            self._execute_pickup(arm_id, agv_id, object_id, task_id, _valid, _halt_check, _halt)
        else:
            self._execute_place(arm_id, agv_id, task_id, _valid, _halt_check, _halt)

        return not halted

    def _execute_pickup(
        self, arm_id: int, agv_id: str, object_id: str, task_id: str,
        _valid, _halt_check, _halt,
    ) -> None:
        """Pickup sequence: sweep PICK → REST, then signal AGV to proceed."""
        from app.routes.agv_routes import signal_load

        if not _valid():
            return

        logger.info("ArmAgent %s: pickup — sweep to PICK", self.actor_id)
        ok = sweep_arm(arm_id, PICK, halt_check=_halt_check)
        if not ok:
            _halt("sweep to PICK halted")
            return

        if not _valid():
            _halt("validity false after PICK sweep")
            return

        logger.info("ArmAgent %s: pickup — sweep to REST", self.actor_id)
        ok = sweep_arm(arm_id, REST, halt_check=_halt_check)
        if not ok:
            _halt("sweep to REST halted")
            return

        if not _valid():
            _halt("validity false after REST sweep")
            return

        # Signal AGV with task_id scope: package is on the AGV, it may proceed.
        logger.info("ArmAgent %s: pickup — signalling load to %s (task=%s)", self.actor_id, agv_id, task_id)
        signal_load(agv_id, object_id, task_id=task_id)

    def _execute_place(
        self, arm_id: int, agv_id: str, task_id: str,
        _valid, _halt_check, _halt,
    ) -> None:
        """Place sequence: signal unload (AGV waiting), wait for AGV to clear,
        sweep PLACE → REST."""
        from app.routes.agv_routes import signal_unload, poll_unload

        if not _valid():
            return

        # Signal the AGV that we are ready to receive (task-scoped).
        logger.info("ArmAgent %s: place — signalling unload to %s (task=%s)", self.actor_id, agv_id, task_id)
        signal_unload(agv_id, task_id=task_id)

        # Wait until the AGV has consumed the unload signal (cleared by AGV step 9).
        logger.info("ArmAgent %s: place — waiting for AGV to clear unload signal", self.actor_id)
        deadline = time.time() + UNLOAD_CLEAR_TIMEOUT
        while poll_unload(agv_id, task_id=task_id):
            if not self._running:
                _halt("agent stopped while waiting for AGV to clear unload")
                return
            if not _valid():
                _halt("validity false while waiting for AGV to clear unload")
                return
            if time.time() >= deadline:
                _halt(f"timeout waiting for AGV {agv_id} to clear unload signal")
                return
            time.sleep(SIGNAL_POLL_INTERVAL)

        if not _valid():
            _halt("validity false after unload signal cleared")
            return

        logger.info("ArmAgent %s: place — sweep to PLACE", self.actor_id)
        ok = sweep_arm(arm_id, PLACE, halt_check=_halt_check)
        if not ok:
            _halt("sweep to PLACE halted")
            return

        if not _valid():
            _halt("validity false after PLACE sweep")
            return

        logger.info("ArmAgent %s: place — sweep to REST", self.actor_id)
        ok = sweep_arm(arm_id, REST, halt_check=_halt_check)
        if not ok:
            _halt("sweep to REST (final) halted")
            return

        logger.info("ArmAgent %s: place leg complete", self.actor_id)

    def _numeric_arm_id(self) -> Optional[int]:
        """Extract the integer arm index from actor_id (e.g. 'arm-1' -> 1)."""
        parts = self.actor_id.split("-")
        if len(parts) >= 2:
            try:
                return int(parts[-1])
            except ValueError:
                pass
        return None


def _emit_log(source: str, message: str) -> None:
    """Best-effort SocketIO log_message emit."""
    try:
        from app import socketio
        from datetime import datetime
        socketio.emit("log_message", {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "source": source,
            "message": message,
        })
    except Exception:
        pass
