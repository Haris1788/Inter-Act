"""Asset controllers for non-arm pipeline modules (vehicle).

Phase 4 refactor: controllers inherit from `app.core.module.Module`.
They expose a uniform interface (accept/process/emit) so the pipeline
builder can treat every component identically. Internal behaviour
(travel-time delay, fail/recover semantics, asset_state registry
mirror) is unchanged.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from app.core.module import Module
from app.models import asset_state


class AssetController(Module):
    """Shared base for non-arm modules.

    Subclasses (`VehicleController`) override `process()` only;
    accept/emit follow the Module contract.
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
            # Status not in asset_state's vocabulary - ignore gracefully.
            pass

    def _emit_event(self, obj_id: str, state: str, **extra) -> None:
        if not self._object_event:
            return
        if "event_time" not in extra:
            extra["event_time"] = int(time.time() * 1000)
        if "owner_id" not in extra:
            if "carrier_asset" in extra and extra["carrier_asset"]:
                extra["owner_id"] = extra["carrier_asset"]
                extra["owner_type"] = "asset"
            elif isinstance(state, str) and (
                state.startswith("on_") or state.startswith("at_")
            ):
                extra["owner_id"] = self.id
                extra["owner_type"] = "asset"
        try:
            self._object_event(obj_id, state, **extra)
        except TypeError:
            self._object_event(obj_id, state)

    def _log_flow(self, message: str) -> None:
        self._log(f"[FLOW] {message}")

    def _hard_stopped(self) -> bool:
        return self.state in ("failed", "recovering", "stopped")

    def _transfer_point_owner_id(self) -> Optional[str]:
        tp_name = getattr(self.output_tp, "name", None)
        if isinstance(tp_name, str) and tp_name.startswith("TP "):
            return tp_name
        downstream = getattr(self, "_downstream_target", None)
        if downstream:
            return f"TP {self.id}->{downstream}"
        return None

    def _downstream_path_ready(self) -> bool:
        if self._hard_stopped():
            return False
        if self._downstream_alive and not self._downstream_alive():
            return False
        if self.output_tp is None:
            return True
        is_empty = getattr(self.output_tp, "is_empty", None)
        if callable(is_empty):
            return bool(is_empty())
        return True

    def downstream_ready(self) -> bool:
        if self.state in ("failed", "recovering", "draining", "stopped"):
            return False
        return self._downstream_path_ready()

    # ── thread entry: run with failure/recovery awareness ────────────

    def run(self) -> None:
        """Thread loop. Stays alive across fail/recover cycles.

        Phase 9: distinguishes `stop_event` (terminate) from `fail_event`
        (halt processing, await recovery). The thread does NOT exit on
        failure - it idle-waits so `recover()` can resume work in-place.
        Only `stop_module()` (or hitting non-keep_alive completion) ends
        the thread.
        """
        try:
            while not self._stop_event.is_set():
                if self._hard_stopped():
                    time.sleep(0.1)
                    continue

                if self.state == "draining":
                    if (
                        self._current_object is not None
                        and self._downstream_path_ready()
                    ):
                        self.set_state("busy")
                        self.emit()
                    elif self._current_object is None:
                        # Empty assets must remain accept-capable even when
                        # downstream is blocked. Draining is for "holding an
                        # object that cannot be emitted", not for "empty but
                        # downstream unavailable".
                        self.set_state("idle")
                    time.sleep(0.1)
                    continue

                # Hard halt while failed or recovering. Accept queues stay
                # populated; new POSTs are refused at the HTTP layer.
                # Drain remaining products as long as we stay healthy.
                while (
                    self.processed < self.num_products
                    and not self._stop_event.is_set()
                    and self.state
                    not in ("failed", "recovering", "draining", "stopped")
                ):
                    self._cycle_one_product()

                if self.processed >= self.num_products and not self.keep_alive:
                    break

                # keep_alive idle wait - do nothing destructive to state.
                if self.state == "busy":
                    self.set_state("idle")
                time.sleep(0.1)
        except Exception as exc:  # pragma: no cover
            self.error = str(exc)
            self.set_state("failed", str(exc))
            self._log(f"FATAL: {exc}")
        finally:
            if self.state not in ("failed", "stopped"):
                self.set_state("stopped")

    # ── per-product accept/process/emit cycle ────────────────────────

    def _cycle_one_product(self) -> None:
        if self._hard_stopped():
            return
        if self.state == "draining":
            return

        # accept
        def pick_cancel():
            if not self._is_running():
                return False
            if self.state in ("recovering", "failed", "draining", "stopped"):
                return False
            return self._upstream_alive()

        obj_id = self.input_tp.wait_and_pick(timeout=120.0, cancel_check=pick_cancel)
        if obj_id is None:
            return
        # Phase 9 race-fix: state may have flipped to failed/recovering AFTER
        # an object landed in the queue but BEFORE we checked. Put it back so
        # nothing is processed during halt - caller can replay later.
        if self._hard_stopped() or not self._is_running():
            try:
                self.input_tp.place(obj_id)
            except Exception:
                pass
            return
        if not self.can_accept(obj_id):
            try:
                self.input_tp.place(obj_id)
            except Exception:
                pass
            return
        self.accept(obj_id)

        # process - subclasses do real work and may abort early on fail/stop.
        try:
            self.process()
        except Exception as exc:
            self.error = str(exc)
            self.set_state("failed", str(exc))
            self._log(f"process error: {exc}")
            return

        if not self._is_running() or self._hard_stopped():
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
        """Retry-until-delivered emit. Phase 8++.

        The asset HOLDS its current object until the downstream accepts. This
        gives natural backpressure: if downstream is dead the object stays
        with this asset, blocking the next cycle from picking new input.
        Only stops retrying if the asset itself is killed (stop_module/fail).
        """
        obj_id = self._current_object
        if obj_id is None:
            return

        def drop_cancel():
            return not self._stop_event.is_set()

        attempts = 0
        while True:
            if self._stop_event.is_set() or self._hard_stopped():
                return
            if not self._downstream_path_ready():
                if self.state != "draining":
                    self.set_state("draining")
                attempts += 1
                if attempts == 1 or attempts % 15 == 0:
                    self._log_flow(
                        f"{self.id} draining ({obj_id} held, downstream blocked)"
                    )
                return
            ok = self.output_tp.wait_until_empty(timeout=2.0, cancel_check=drop_cancel)
            if ok:
                break
            attempts += 1
            if attempts == 1 or attempts % 15 == 0:
                self._log(f"holding {obj_id} - downstream blocked (attempt {attempts})")
            # Loop and retry. Any caller can call stop_module() to break.

        if self._hard_stopped():
            return
        tp_owner_id = self._transfer_point_owner_id()
        if tp_owner_id:
            self._emit_event(
                obj_id,
                "transfer_point",
                owner_id=tp_owner_id,
                owner_type="transfer_point",
            )
        self.output_tp.place(obj_id)
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

    def can_accept(self, obj: str) -> bool:
        if self.state == "draining":
            return False
        if self._hard_stopped():
            return False
        return super().can_accept(obj)

    def accept(self, obj_id: str) -> None:
        if self.state == "draining" or self._hard_stopped():
            return
        super().accept(obj_id)
        travel_total_s = self.travel_time * self.speed
        start_ms = int(time.time() * 1000)
        self._emit_event(
            obj_id,
            "carried",
            carrier_asset=self.id,
            start_time=start_ms,
            duration_ms=int(travel_total_s * 1000),
        )

    def process(self) -> None:
        if self._hard_stopped():
            return
        deadline = time.time() + self.travel_time * self.speed
        while time.time() < deadline:
            if not self._is_running() or self._hard_stopped():
                return
            time.sleep(min(0.2, deadline - time.time()))
