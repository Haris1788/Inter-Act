"""Asset controllers for non-arm pipeline modules (vehicle, conveyor).

Phase 4 refactor: both controllers inherit from `app.core.module.Module`.
They expose a uniform interface (accept/process/emit) so the pipeline
builder can treat every component identically. Internal behaviour
(travel-time delay, slot shifting, fail/recover semantics, asset_state
registry mirror) is unchanged.
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional

from app.core.module import Module
from app.models import asset_state


class AssetController(Module):
    """Shared base for non-arm modules.

    Subclasses (`VehicleController`, `ConveyorController`) override
    `process()` only; accept/emit follow the Module contract.
    """

    module_type = "asset"
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
        keep_alive: bool = False,
    ) -> None:
        super().__init__(asset_id, label)
        self.input_tp = input_tp
        self.output_tp = output_tp
        self.speed = speed_factor
        self.log_fn = log_fn
        self._object_event = object_event_fn
        self._upstream_alive = upstream_alive_fn or (lambda: True)
        self._downstream_alive = downstream_alive_fn or (lambda: True)
        self.num_products = num_products
        self.keep_alive = keep_alive
        self.processed = 0
        self.error: Optional[str] = None
        asset_state.register(asset_id, self.asset_type, label)

    # ── Module hooks ──────────────────────────────────────────────────

    def _on_state_change(self, new_state, reason):
        # Mirror module state into asset_state runtime registry.
        try:
            asset_state.set_status(self.id, new_state, reason)
        except (KeyError, ValueError):
            # Status not in asset_state's vocabulary — ignore gracefully.
            pass

    def _emit_event(self, obj_id: str, state: str, **extra) -> None:
        if not self._object_event:
            return
        try:
            self._object_event(obj_id, state, **extra)
        except TypeError:
            self._object_event(obj_id, state)

    # ── thread entry: run num_products iterations ────────────────────

    def run(self) -> None:
        try:
            for _ in range(self.num_products):
                if not self._is_running():
                    break
                self._cycle_one_product()
            # Phase 5 lifecycle: stay alive after finishing the configured
            # product count when keep_alive is set. Continue accepting any
            # objects upstream sends; idle-loop until stop_module() fires.
            if self.keep_alive:
                self.set_state("idle")
                self._log("idle — awaiting more work")
                while not self._stop_event.is_set():
                    if self._fail_event.is_set():
                        # cleared by recover(); resume idle wait
                        if self.state == "failed":
                            time.sleep(0.2)
                            continue
                    if self.input_tp is not None:
                        obj = self.input_tp.wait_and_pick(
                            timeout=1.0,
                            cancel_check=lambda: not self._stop_event.is_set(),
                        )
                        if obj is None:
                            continue
                        if not self.can_accept(obj):
                            continue
                        self.accept(obj)
                        try:
                            self.process()
                        except Exception as exc:
                            self.error = str(exc)
                            self.set_state("failed", str(exc))
                            continue
                        if self._stop_event.is_set():
                            break
                        self.emit()
                    else:
                        time.sleep(0.2)
        except Exception as exc:  # pragma: no cover — defensive
            self.error = str(exc)
            self.set_state("failed", str(exc))
            self._log(f"FATAL: {exc}")
        finally:
            if self.state not in ("failed", "stopped"):
                self.set_state("stopped")

    # ── per-product accept/process/emit cycle ────────────────────────

    def _cycle_one_product(self) -> None:
        # accept
        def pick_cancel():
            if not self._is_running():
                return False
            return self._upstream_alive()

        obj_id = self.input_tp.wait_and_pick(timeout=120.0, cancel_check=pick_cancel)
        if obj_id is None:
            return
        if not self.can_accept(obj_id):
            return
        self.accept(obj_id)

        # process — subclasses do real work and may abort early on fail/stop.
        try:
            self.process()
        except Exception as exc:
            self.error = str(exc)
            self.set_state("failed", str(exc))
            self._log(f"process error: {exc}")
            return

        if not self._is_running():
            asset_state.set_carried(self.id, None)
            self._current_object = None
            return

        # emit
        self.emit()

    def accept(self, obj_id: str) -> None:
        self._current_object = obj_id
        self.set_state("busy")
        asset_state.set_carried(self.id, obj_id)
        self._log(f"accepted {obj_id}")

    def emit(self) -> None:
        obj_id = self._current_object
        if obj_id is None:
            return

        def drop_cancel():
            if not self._is_running():
                return False
            return self._downstream_alive()

        ok = self.output_tp.wait_until_empty(timeout=60.0, cancel_check=drop_cancel)
        if not ok:
            self._log(f"cannot emit {obj_id} — downstream blocked")
            asset_state.set_carried(self.id, None)
            self._current_object = None
            return

        self.output_tp.place(obj_id)
        # Phase 5 fix: do NOT emit `at_<id>_exit` here. Removing this event
        # avoids a frontend detach race where the object would briefly orphan
        # itself off the carrier (visually jumping to slot 0 for a conveyor
        # because no slot field was attached). The downstream module's first
        # event (`carried` / `on_<id>#0`) seamlessly takes over rendering
        # at the correct position.
        asset_state.set_carried(self.id, None)
        self.processed += 1
        self._log(f"emitted {obj_id}")
        self._current_object = None
        self.set_state("idle")


# ── Vehicle ───────────────────────────────────────────────────────────

class VehicleController(AssetController):
    """Linear shuttle: accept, simulate travel time, emit."""

    asset_type = "vehicle"

    def __init__(self, *args, travel_time: float = 1.5, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.travel_time = travel_time

    def accept(self, obj_id: str) -> None:
        super().accept(obj_id)
        travel_total_s = self.travel_time * self.speed
        start_ms = int(time.time() * 1000)
        self._emit_event(
            obj_id, "carried",
            carrier_asset=self.id,
            start_time=start_ms,
            duration_ms=int(travel_total_s * 1000),
        )

    def process(self) -> None:
        deadline = time.time() + self.travel_time * self.speed
        while time.time() < deadline:
            if not self._is_running():
                return
            time.sleep(min(0.2, deadline - time.time()))


# ── Conveyor ──────────────────────────────────────────────────────────

class ConveyorController(AssetController):
    """Belt with N slots. Object enters slot 0, shifts to last, then emits."""

    asset_type = "conveyor"

    def __init__(self, *args, slot_count: int = 3, shift_interval: float = 1.0,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.slot_count = max(1, slot_count)
        self.shift_interval = shift_interval
        self.slots: List[Optional[str]] = [None] * self.slot_count

    def accept(self, obj_id: str) -> None:
        super().accept(obj_id)
        self.slots[0] = obj_id
        self._emit_event(
            obj_id, f"on_{self.id}",
            carrier_asset=self.id, slot=0,
            event_time=int(time.time() * 1000),
        )
        self._log(f"loaded {obj_id} at slot 0")

    def process(self) -> None:
        obj_id = self._current_object
        if obj_id is None:
            return
        for next_slot in range(1, self.slot_count):
            interval = self.shift_interval * self.speed
            deadline = time.time() + interval
            while time.time() < deadline:
                if not self._is_running():
                    return
                time.sleep(min(0.2, deadline - time.time()))
            self.slots[next_slot] = obj_id
            self.slots[next_slot - 1] = None
            self._emit_event(
                obj_id, f"on_{self.id}",
                carrier_asset=self.id, slot=next_slot,
                event_time=int(time.time() * 1000),
            )
            self._log(f"shifted {obj_id} to slot {next_slot}")

    def emit(self) -> None:
        # Clear last slot once the object successfully transferred downstream.
        super().emit()
        if self._current_object is None:
            self.slots[self.slot_count - 1] = None
