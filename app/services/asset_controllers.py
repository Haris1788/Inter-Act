"""Lightweight controllers for non-arm pipeline assets.

Each controller runs in its own daemon thread and ferries one product at a time
between an `input_tp` (TransferPoint) and `output_tp`. Arms remain the primary
controllers of pick/place; vehicles and conveyors slot transparently between
arm-arm edges so the rest of the pipeline doesn't need to know they exist.

State machine (mirrors `app.models.asset_state.VALID_STATUSES`):
    idle    -> busy (on accept) -> idle (on deliver)
    any     -> failed (on .fail)
    failed  -> recovering -> idle (on .recover() then next cycle)
    any     -> stopped (terminal, after drain)

Controllers expose start(), stop(), fail(reason), recover(). They are
event-driven via TransferPoint Conditions; no polling loops.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

from app.models import asset_state


# Sentinel returned by TransferPoint.wait_and_pick on cancel/timeout.
_PICK_NONE = None


class AssetController(threading.Thread):
    """Base class for vehicle and conveyor controllers."""

    asset_type = "asset"

    def __init__(
        self,
        asset_id: str,
        label: str,
        input_tp,
        output_tp,
        speed_factor: float = 1.0,
        log_fn: Optional[Callable[[str, str], None]] = None,
        object_event_fn: Optional[Callable[..., None]] = None,
        upstream_alive_fn: Optional[Callable[[], bool]] = None,
        downstream_alive_fn: Optional[Callable[[], bool]] = None,
        num_products: int = 1,
    ) -> None:
        super().__init__(name=f"Asset-{asset_id}", daemon=True)
        self.asset_id = asset_id
        self.label = label
        self.input_tp = input_tp
        self.output_tp = output_tp
        self.speed = speed_factor
        self._log = log_fn or (lambda src, msg: None)
        self._object_event = object_event_fn
        self._upstream_alive = upstream_alive_fn or (lambda: True)
        self._downstream_alive = downstream_alive_fn or (lambda: True)
        self.num_products = num_products
        self.processed = 0
        self.error: Optional[str] = None

        self._stop_event = threading.Event()
        self._fail_event = threading.Event()
        self._fail_reason: Optional[str] = None

        asset_state.register(asset_id, self.asset_type, label)

    # ── public lifecycle ─────────────────────────────────────────────

    def stop(self) -> None:
        """Signal the controller to terminate after the current iteration."""
        self._stop_event.set()
        # Wake any blocking TP wait by setting fail flag too.
        self._fail_event.set()

    def fail(self, reason: str) -> None:
        """Mark controller as failed. Downstream sees this via upstream_alive_fn."""
        self._fail_reason = reason
        self._fail_event.set()
        asset_state.fail(self.asset_id, reason)
        self._log(self.asset_id, f"FAILED: {reason}")

    def recover(self) -> None:
        """Clear failure flag. Controller resumes on next cycle."""
        self._fail_reason = None
        self._fail_event.clear()
        asset_state.recover(self.asset_id)
        self._log(self.asset_id, "Recovering")

    # ── helpers ──────────────────────────────────────────────────────

    def _is_running(self) -> bool:
        return not self._stop_event.is_set() and not self._fail_event.is_set()

    def _emit(self, obj_id: str, state: str, **extra) -> None:
        """Forward an object state change via the configured event hook."""
        if self._object_event:
            try:
                self._object_event(obj_id, state, **extra)
            except TypeError:
                # Older signature without keyword args — fall back gracefully.
                self._object_event(obj_id, state)

    # ── thread entry ─────────────────────────────────────────────────

    def run(self) -> None:
        try:
            for _ in range(self.num_products):
                if self._stop_event.is_set():
                    break
                self._cycle_one_product()
        except Exception as exc:  # pragma: no cover — defensive
            self.error = str(exc)
            asset_state.fail(self.asset_id, str(exc))
            self._log(self.asset_id, f"FATAL: {exc}")
        finally:
            asset_state.set_status(self.asset_id, "stopped", self.error)

    # ── per-product cycle (override in subclasses) ───────────────────

    def _cycle_one_product(self) -> None:
        raise NotImplementedError


# ── Vehicle controller ────────────────────────────────────────────────

class VehicleController(AssetController):
    """Shuttle that moves a single product from input_tp to output_tp.

    Movement is simulated as a time delay scaled by speed_factor.
    """

    asset_type = "vehicle"

    def __init__(self, *args, travel_time: float = 1.5, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.travel_time = travel_time

    def _cycle_one_product(self) -> None:
        # Pick from upstream
        def pick_cancel():
            if not self._is_running():
                return False
            return self._upstream_alive()

        obj_id = self.input_tp.wait_and_pick(timeout=120.0, cancel_check=pick_cancel)
        if obj_id is None:
            return

        # Mark busy, attach
        asset_state.set_status(self.asset_id, "busy")
        asset_state.set_carried(self.asset_id, obj_id)
        self._emit(obj_id, "carried", carrier_asset=self.asset_id)
        self._log(self.asset_id, f"Picked {obj_id} from upstream")

        # Simulate travel — wake early on stop/fail
        deadline = time.time() + self.travel_time * self.speed
        while time.time() < deadline:
            if not self._is_running():
                self._log(self.asset_id, f"Travel interrupted carrying {obj_id}")
                asset_state.set_carried(self.asset_id, None)
                return
            time.sleep(min(0.2, deadline - time.time()))

        # Wait for downstream slot
        def drop_cancel():
            if not self._is_running():
                return False
            return self._downstream_alive()

        ok = self.output_tp.wait_until_empty(timeout=60.0, cancel_check=drop_cancel)
        if not ok:
            self._log(self.asset_id, f"Cannot drop {obj_id} — downstream blocked")
            asset_state.set_carried(self.asset_id, None)
            return

        self.output_tp.place(obj_id)
        self._emit(obj_id, f"at_{self.asset_id}_exit", carrier_asset=self.asset_id)
        asset_state.set_carried(self.asset_id, None)
        asset_state.set_status(self.asset_id, "idle")
        self.processed += 1
        self._log(self.asset_id, f"Delivered {obj_id} to downstream")


# ── Conveyor controller ───────────────────────────────────────────────

class ConveyorController(AssetController):
    """Belt with N slots. Accepts at slot 0; shifts each `shift_interval`
    until last slot, then places to output_tp.

    For Phase 2 we model only the timing — no per-slot simultaneity. One product
    is in flight at a time per controller. Multiple objects could be supported
    later by overlapping cycles; current scope keeps it sequential.
    """

    asset_type = "conveyor"

    def __init__(self, *args, slot_count: int = 3, shift_interval: float = 1.0,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.slot_count = max(1, slot_count)
        self.shift_interval = shift_interval
        self.slots: List[Optional[str]] = [None] * self.slot_count

    def _cycle_one_product(self) -> None:
        def pick_cancel():
            if not self._is_running():
                return False
            return self._upstream_alive()

        obj_id = self.input_tp.wait_and_pick(timeout=120.0, cancel_check=pick_cancel)
        if obj_id is None:
            return

        asset_state.set_status(self.asset_id, "busy")
        asset_state.set_carried(self.asset_id, obj_id)
        self.slots[0] = obj_id
        self._emit(obj_id, f"on_{self.asset_id}", carrier_asset=self.asset_id, slot=0)
        self._log(self.asset_id, f"Loaded {obj_id} at slot 0")

        # Shift across slots
        for next_slot in range(1, self.slot_count):
            interval = self.shift_interval * self.speed
            deadline = time.time() + interval
            while time.time() < deadline:
                if not self._is_running():
                    self._log(self.asset_id, f"Belt halted carrying {obj_id} at slot {next_slot - 1}")
                    asset_state.set_carried(self.asset_id, None)
                    return
                time.sleep(min(0.2, deadline - time.time()))
            self.slots[next_slot] = obj_id
            self.slots[next_slot - 1] = None
            self._emit(obj_id, f"on_{self.asset_id}", carrier_asset=self.asset_id, slot=next_slot)
            self._log(self.asset_id, f"Shifted {obj_id} to slot {next_slot}")

        # Wait for downstream slot
        def drop_cancel():
            if not self._is_running():
                return False
            return self._downstream_alive()

        ok = self.output_tp.wait_until_empty(timeout=60.0, cancel_check=drop_cancel)
        if not ok:
            self._log(self.asset_id, f"Cannot drop {obj_id} — downstream blocked")
            asset_state.set_carried(self.asset_id, None)
            return

        self.output_tp.place(obj_id)
        self.slots[self.slot_count - 1] = None
        self._emit(obj_id, f"at_{self.asset_id}_exit", carrier_asset=self.asset_id)
        asset_state.set_carried(self.asset_id, None)
        asset_state.set_status(self.asset_id, "idle")
        self.processed += 1
        self._log(self.asset_id, f"Delivered {obj_id} to downstream")
