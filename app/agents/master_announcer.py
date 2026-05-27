"""master_announcer — backend-side announcer for the contract-net protocol.

Phase 2:
  The master AI (qwq:32b planner) emits packages, but no longer hard-binds
  each to a specific AGV. Instead, the backend publishes a `task_announce`
  per package to the message bus. AGVs hear it, decide independently via
  their qwen14b tactical brain whether to bid, and the master collects bids
  over a window then publishes a `claim` naming the winner.

  This module owns that loop. It runs in a single background thread that:
    1. Holds the announced-task state (task_id -> bids dict + deadline).
    2. Subscribes to `bid` events from the bus.
    3. Every tick, resolves any task whose deadline has expired by picking
       the highest-score bid and publishing the `claim`.

  Once a claim is out, the actual physical handoff to an AGV container goes
  through the same `_assign_agv` + `_auto_arm_signals` pipeline that the
  legacy dispatch_plan used, because that's the proven choreography. The
  difference is *who chose the AGV*: peers + master via bid scores, not
  the planner hard-coding it.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from app.routes.bus_routes import publish_local
from app.routes.dispatch_routes import (
    _AGV_CONTAINER_URLS,
    _assign_agv,
    _auto_arm_signals,
    _log,
)

# Initial bid window after task_announce. qwen14b on the shared TUC GPU
# takes 8-40s per call; we start with a generous window and extend on bid
# arrival until the AGVs go quiet.
BID_WINDOW_S = 30.0

# When a fresh bid arrives, push the deadline at least this far into the
# future — keeps the window open while peers are still answering.
BID_QUIET_S = 6.0

# Hard ceiling — no matter how many bids keep trickling in, give up after this.
BID_HARD_S = 90.0

# How often the resolver thread looks for expired deadlines.
RESOLVE_TICK_S = 1.0

# Cap on how many times the same task may be re-announced after a busy-bounce
# from the winning AGV's /assign. Without this cap, a permanently faulted
# AGV would loop forever.
MAX_REANNOUNCE = 2

# Label → display color (mirrors plan_executor)
_LABEL_COLORS = {
    "red": "#e74c3c", "orange": "#e67e22", "yellow": "#f1c40f",
    "green": "#27ae60", "blue": "#2980b9", "indigo": "#3f51b5",
    "violet": "#8e44ad", "purple": "#8e44ad",
}
_FALLBACK_COLORS = ["#e74c3c", "#e67e22", "#f1c40f", "#27ae60", "#2980b9", "#8e44ad"]


def _color_for(label: str, idx: int) -> str:
    return _LABEL_COLORS.get((label or "").strip().lower(),
                             _FALLBACK_COLORS[idx % len(_FALLBACK_COLORS)])


class MasterAnnouncer:
    """Single backend-side announcer for contract-net allocation.

    Lifecycle: instantiated once in create_app and reused. The same instance
    handles multiple plans concurrently; tasks are keyed by package_id.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # task_id -> {"deadline","bids","claimed","payload","label","color","idx"}
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._stop = threading.Event()
        self._resolver_started = False
        # AGVs that already won a package in the current plan. The resolver
        # prefers un-won AGVs so N packages spread across N AGVs instead of
        # collapsing onto one tie-break winner when bid scores are flat.
        self._assigned_agvs: set[str] = set()

    # ── public API ─────────────────────────────────────────────────────────
    def _initial_deadline(self) -> tuple[float, float]:
        now = time.time()
        return now + BID_WINDOW_S, now + BID_HARD_S

    def announce_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Announce every assignment in `plan` to the bus.

        The planner's `agv_id` field is treated as a HINT (and stored in
        payload.original_agv) — the actual winner is chosen by bidding.
        Each task gets its own bid window; they run concurrently.
        """
        assignments: List[dict] = list(plan.get("assignments") or [])
        if not assignments:
            return {"status": "failed", "error": "plan has no assignments"}

        # Make sure resolver is alive
        self._ensure_resolver()

        # Make sure we hear bid events on the in-process bus
        self._ensure_subscriptions()

        # Fresh plan — clear the per-plan winner set so spreading restarts.
        with self._lock:
            self._assigned_agvs.clear()

        assignments.sort(key=lambda a: a.get("sequence", 0))
        announced: List[dict] = []
        skipped: List[dict] = []

        for idx, a in enumerate(assignments):
            package_id = a.get("package_id")
            label = a.get("label", "")
            if not package_id:
                skipped.append({**a, "_reason": "missing package_id"})
                continue
            color = _color_for(label, idx)
            payload = {
                "package_id": package_id,
                "label": label,
                "route": a.get("route") or ["bay", "input", "output", "bay"],
                "sequence": a.get("sequence", idx + 1),
                "agv_hint": a.get("agv_id"),
            }
            with self._lock:
                # If this package was already announced (and not yet claimed),
                # don't double-announce.
                if package_id in self._tasks and not self._tasks[package_id]["claimed"]:
                    skipped.append({**a, "_reason": "already announced"})
                    continue
                deadline, hard = self._initial_deadline()
                self._tasks[package_id] = {
                    "deadline": deadline,
                    "hard_deadline": hard,
                    "bids": {},
                    "claimed": False,
                    "payload": payload,
                    "label": label,
                    "color": color,
                    "idx": idx,
                    "retry": 0,
                }
            publish_local("task_announce", from_="master",
                          task_id=package_id, payload=payload)
            _log("planner",
                 f"announce {package_id} ({label or 'no-label'}) — waiting "
                 f"{BID_WINDOW_S}s for bids")
            announced.append({"package_id": package_id, "label": label,
                              "sequence": payload["sequence"]})
        return {"status": "announced", "announced": announced,
                "skipped": skipped}

    # ── bus subscriber: collect bids ───────────────────────────────────────
    def on_bid(self, ev: dict) -> None:
        """Called by the in-process MessageBus when a `bid` event arrives."""
        task_id = ev.get("task_id")
        bidder = ev.get("from")
        if not task_id or not bidder:
            return
        try:
            score = float((ev.get("payload") or {}).get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        with self._lock:
            record = self._tasks.get(task_id)
            if not record or record["claimed"]:
                return
            record["bids"][bidder] = {
                "score": score,
                "rationale": (ev.get("payload") or {}).get("rationale", ""),
                "eta_s": (ev.get("payload") or {}).get("eta_s", 0),
            }
            # Extend deadline while bids keep coming, capped at hard ceiling.
            new_deadline = time.time() + BID_QUIET_S
            if new_deadline > record["deadline"]:
                record["deadline"] = min(new_deadline, record["hard_deadline"])
        _log("planner",
             f"bid for {task_id}: {bidder} score={score:.1f}")

    # ── resolver: expire deadlines, publish claims, kick off execution ─────
    def _resolver_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                _log("planner", f"announcer error: {exc}")
            time.sleep(RESOLVE_TICK_S)

    def _tick(self) -> None:
        now = time.time()
        # Snapshot expired tasks under lock; execute outside the lock
        to_claim: List[tuple[str, str, dict]] = []
        with self._lock:
            for task_id, rec in list(self._tasks.items()):
                if rec["claimed"]:
                    continue
                if now < rec["deadline"]:
                    continue
                bids = rec["bids"]
                if not bids:
                    # No bidders even at deadline (extension already exhausted
                    # via BID_QUIET_S on first bid). If hard ceiling reached,
                    # fall back to the agv_hint to keep the run alive.
                    if now < rec.get("hard_deadline", 0):
                        # Still room — just wait one more tick, the deadline
                        # extension on first bid will keep us going.
                        continue
                    fallback = (rec["payload"].get("agv_hint")
                                if (rec["payload"].get("agv_hint") in _AGV_CONTAINER_URLS)
                                else None)
                    if fallback is None:
                        rec["claimed"] = True
                        _log("planner",
                             f"{task_id} unclaimed — no bidders + no usable hint")
                        continue
                    winner = fallback
                    winning = {"score": 0.0, "rationale": "fallback hint",
                               "eta_s": 0.0}
                else:
                    # Rank bids high-score-first, tie-break lowest agent id.
                    ranked = sorted(
                        bids.items(),
                        key=lambda kv: (kv[1]["score"],
                                        -sum(ord(c) for c in kv[0])),
                        reverse=True,
                    )
                    # Prefer a bidder that has not already won a package in
                    # this plan — spreads N packages over N AGVs instead of
                    # collapsing onto one when scores are flat. Fall back to
                    # the top bid if every bidder is already loaded (more
                    # packages than AGVs).
                    fresh = [kv for kv in ranked
                             if kv[0] not in self._assigned_agvs]
                    winner, winning = fresh[0] if fresh else ranked[0]
                self._assigned_agvs.add(winner)
                rec["claimed"] = True
                rec["winner"] = winner
                to_claim.append((task_id, winner, winning))

        for task_id, winner, winning in to_claim:
            with self._lock:
                rec = self._tasks.get(task_id) or {}
            payload = rec.get("payload") or {}
            label = rec.get("label", "")
            color = rec.get("color") or _color_for(label, rec.get("idx", 0))
            assignment = {
                "agv_id": winner,
                "package_id": task_id,
                "label": label,
                "route": payload.get("route") or
                ["bay", "input", "output", "bay"],
                "sequence": payload.get("sequence"),
                "task_id": task_id,
            }
            _log("planner",
                 f"claim-publish: {task_id} winner={winner} "
                 f"score={winning['score']:.1f}")
            publish_local("claim", from_="master", task_id=task_id, payload={
                "winner": winner,
                "winning_score": winning["score"],
                "rationale": winning.get("rationale", ""),
                "eta_s": winning.get("eta_s", 0),
                "assignment": assignment,
            })

            # Actually push to the AGV + start arm choreography. The AGV's
            # BusListener will also self-trigger via _on_claim (because we
            # mirror to SocketIO + in-process subscribers), but a direct
            # /assign is the proven path and is idempotent — agv_server's
            # _take_task checks "already running" and bails.
            try:
                ok = self._kickoff_execution(winner, task_id, label, color)
            except Exception as exc:
                _log("planner",
                     f"{task_id} kickoff failed for {winner}: {exc}")
                ok = False
            if not ok:
                # Bug-1 fix (US-32 refinement, found during 22 May live test):
                # the winner could not actually take the task (typically /assign
                # returned 409 because that AGV is still running an earlier
                # claim it also won). Re-announce so peers can re-bid.
                self._reannounce(task_id, winner)

    def _kickoff_execution(self, agv_id: str, package_id: str,
                            label: str, color: str) -> bool:
        if agv_id not in _AGV_CONTAINER_URLS:
            _log("planner",
                 f"unknown winner container {agv_id} — claim unreachable")
            return False
        ok = _assign_agv(agv_id, package_id, label)
        if not ok:
            _log("planner",
                 f"winner {agv_id} busy/unreachable for {package_id}")
            return False
        _log("planner",
             f"{agv_id} claimed {package_id}; starting arm choreography")
        threading.Thread(
            target=_auto_arm_signals,
            args=(agv_id, package_id, color),
            daemon=True,
        ).start()
        return True

    def _reannounce(self, task_id: str, failed_winner: str) -> None:
        """A claim was published but the winner couldn't actually take it.
        Reset the task record, drop the failed winner from the spread set,
        and re-publish task_announce so peers re-bid. Capped at MAX_REANNOUNCE
        to prevent infinite loops if every AGV is wedged."""
        with self._lock:
            rec = self._tasks.get(task_id)
            if not rec:
                return
            retry = rec.get("retry", 0) + 1
            if retry > MAX_REANNOUNCE:
                _log("planner",
                     f"{task_id} re-announce ceiling reached "
                     f"({MAX_REANNOUNCE}); abandoning")
                return
            # Reset for another round.
            self._assigned_agvs.discard(failed_winner)
            deadline, hard = self._initial_deadline()
            rec["deadline"] = deadline
            rec["hard_deadline"] = hard
            rec["bids"] = {}
            rec["claimed"] = False
            rec["retry"] = retry
            rec.pop("winner", None)
            payload = dict(rec.get("payload") or {})
            payload["retry"] = retry
            payload["previous_winner"] = failed_winner
            rec["payload"] = payload
        _log("planner",
             f"re-announce {task_id} (retry {retry}/{MAX_REANNOUNCE}) — "
             f"{failed_winner} could not take it")
        publish_local("task_announce", from_="master",
                      task_id=task_id, payload=payload)

    # ── one-shot wiring ────────────────────────────────────────────────────
    def _ensure_resolver(self) -> None:
        if self._resolver_started:
            return
        threading.Thread(target=self._resolver_loop,
                         name="master-announcer", daemon=True).start()
        self._resolver_started = True

    def _ensure_subscriptions(self) -> None:
        try:
            from app.agents.message_bus import get_bus
            bus = get_bus()
            # subscribe is idempotent-by-callback? No — but we only call
            # _ensure_subscriptions once per resolver lifetime alongside
            # _ensure_resolver, so it stays single.
            if not getattr(self, "_sub_bound", False):
                bus.subscribe("bid", self.on_bid)
                self._sub_bound = True
        except Exception as exc:
            _log("planner", f"could not subscribe to bid events: {exc}")


# ── module-level singleton, instantiated in create_app ────────────────────
_announcer: Optional[MasterAnnouncer] = None


def get_announcer() -> MasterAnnouncer:
    global _announcer
    if _announcer is None:
        _announcer = MasterAnnouncer()
    return _announcer
