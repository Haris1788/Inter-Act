"""Unified Module interface for pipeline assets (Phase 4).

Every pipeline component — arm, vehicle, conveyor, future types — is a
`Module`. Modules communicate ONLY via TransferPoint. The pipeline builder
treats them uniformly: read flow order from config, connect TPs linearly,
spawn threads. There are no type-specific branches outside each module's
own class.

Spec contract (from Phase 4 brief):
    accept(obj)     -> store the picked object as in-flight
    process()       -> run the module's transformation/movement
    emit()          -> push the in-flight object to output_tp
    can_accept(obj) -> gating predicate (defaults to True)

`Module.run` provides a generic accept/process/emit loop. Subclasses with
existing complex behaviour (ArmController) override `run` directly while
still inheriting the Module identity, state machine, and TransferPoint
plumbing — so the pipeline builder can stay unaware of asset type.

Note on naming: `threading.Thread.is_alive()` is reserved by stdlib for
"thread is currently running". Module liveness for the pipeline (i.e. "this
module can still accept/emit objects") is exposed as `is_module_alive()`.
"""

from __future__ import annotations

import threading
import time
from typing import Optional


VALID_MODULE_STATES = (
    "idle",         # registered, nothing in flight
    "busy",         # currently processing an object
    "draining",     # finishing in-flight work, then stopping
    "failed",       # explicit failure; downstream sees this as upstream-dead
    "recovering",   # post-failure, waiting for next cycle
    "stopped",      # terminal — no further work
)


class Module(threading.Thread):
    """Base interface every pipeline asset implements.

    Subclasses are expected to override at least one of:
      * `accept`/`process`/`emit` (recommended; the default `run` will
        invoke them in sequence per object), or
      * `run` (when existing logic is too coupled to split — wrap as-is).
    """

    module_type = "module"

    def __init__(self, module_id: str, label: Optional[str] = None) -> None:
        super().__init__(name=f"Module-{module_id}", daemon=True)
        self.id = module_id
        self.label = label or module_id
        self.input_tp = None        # TransferPoint or None for source modules
        self.output_tp = None       # TransferPoint or None for sink modules
        self._state = "idle"
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._fail_event = threading.Event()
        self._fail_reason: Optional[str] = None
        self._current_object: Optional[str] = None
        # Optional logger hook; pipeline builder injects this.
        self.log_fn = None

    # ── identity / state ──────────────────────────────────────────────

    @property
    def state(self) -> str:
        with self._state_lock:
            return self._state

    def set_state(self, new_state: str, reason: Optional[str] = None) -> None:
        if new_state not in VALID_MODULE_STATES:
            raise ValueError(f"invalid module state {new_state!r}")
        with self._state_lock:
            self._state = new_state
        self._on_state_change(new_state, reason)

    def is_module_alive(self) -> bool:
        return self.state not in ("failed", "stopped")

    # ── lifecycle controls ────────────────────────────────────────────

    def stop_module(self) -> None:
        """Signal this module to terminate after the current iteration."""
        self._stop_event.set()
        # Wake any blocking TP wait by also tripping fail flag.
        self._fail_event.set()

    def fail(self, reason: str) -> None:
        """Mark module as failed. Downstream sees upstream-dead."""
        self._fail_reason = reason
        self._fail_event.set()
        self.set_state("failed", reason)
        self._log(f"FAILED: {reason}")

    def recover(self) -> None:
        """Clear fail flag; next cycle resumes."""
        self._fail_reason = None
        self._fail_event.clear()
        self.set_state("recovering")
        self._log("Recovering")

    def _is_running(self) -> bool:
        return not self._stop_event.is_set() and not self._fail_event.is_set()

    # ── default accept/process/emit hooks ─────────────────────────────

    def can_accept(self, obj: str) -> bool:
        return True

    def accept(self, obj: str) -> None:
        self._current_object = obj
        self.set_state("busy")
        self._log(f"accepted {obj}")

    def process(self) -> None:
        # Subclasses override with real work.
        pass

    def emit(self) -> None:
        if self.output_tp is not None and self._current_object is not None:
            self.output_tp.place(self._current_object)
        if self._current_object is not None:
            self._log(f"emitted {self._current_object}")
        self._current_object = None
        if self.is_module_alive():
            self.set_state("idle")

    # ── default thread entry point ────────────────────────────────────

    def run(self) -> None:
        """Generic accept/process/emit loop.

        Phase 5 lifecycle: loop continues until `stop_module()` is called.
        A short blocking pick on the input TP keeps the loop responsive but
        idle when nothing arrives — the module never exits just because the
        pipeline is momentarily empty.

        Subclasses with complex flow (e.g. ArmController, AssetController)
        override `run` directly while still honoring `_is_running()`.
        """
        try:
            while not self._stop_event.is_set():
                if self.input_tp is None:
                    # Source/sink module with no upstream — idle until stopped.
                    time.sleep(0.1)
                    continue
                obj = self.input_tp.wait_and_pick(
                    timeout=1.0,
                    cancel_check=lambda: not self._stop_event.is_set(),
                )
                if obj is None:
                    # Idle — loop instead of exiting so the pipeline stays alive.
                    continue
                if not self.can_accept(obj):
                    continue
                self.accept(obj)
                self.process()
                if self._stop_event.is_set():
                    return
                self.emit()
        except Exception as exc:  # pragma: no cover — defensive
            self.set_state("failed", str(exc))
            self._log(f"FATAL: {exc}")
        finally:
            if self.state not in ("failed", "stopped"):
                self.set_state("stopped")

    # ── logging ───────────────────────────────────────────────────────

    def _log(self, message: str) -> None:
        if self.log_fn:
            try:
                self.log_fn(self.id, message)
            except Exception:
                pass

    def _on_state_change(self, new_state: str, reason: Optional[str]) -> None:
        """Hook for subclasses to mirror state into runtime registries."""
        pass
