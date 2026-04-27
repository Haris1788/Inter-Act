"""
INTER.act Pipeline Demo — config-driven multi-arm handoff simulation.

Reads arm count and pipeline settings from pipeline.yaml.
Supports N arms in sequential (chain) or parallel (independent) mode.
Failure policies: stop, skip, retry — configurable per arm or globally.

Usage:
    python scripts/run_demo.py
    python scripts/run_demo.py --products 10
    python scripts/run_demo.py --speed fast
    python scripts/run_demo.py --dry-run
"""

import argparse
import os
import threading
import time
import sys
import yaml

from utils import (send_command, send_object_event, send_log, reset_objects,
                    send_health, send_stop_arm, is_arm_allowed_http, DEFAULT_HOST)

# Phase 2: optional asset chain (vehicle/conveyor) controllers — imported
# lazily so the demo can still run without the Flask app context (older
# call sites that bypass `run_pipeline_with_callbacks`).
try:
    from app.services.asset_controllers import VehicleController, ConveyorController
    from app.models import asset_state, pipeline_config as _pc
    _ASSET_RUNTIME_AVAILABLE = True
except Exception:
    VehicleController = None
    ConveyorController = None
    asset_state = None
    _pc = None
    _ASSET_RUNTIME_AVAILABLE = False

# ── Configuration ──────────────────────────────────────────

PICK_ANGLE = 180.0
PLACE_ANGLE = 0.0
REST_ANGLE = 90.0
DEFAULT_PRODUCTS = 5

SPEED_FACTORS = {
    "fast": 0.8,
    "normal": 1.5,
    "slow": 3.0,
}

# Must be longer than browser ANIM_DURATION (600ms) + Docker network latency
MOVE_DURATION = 1.5
ACTION_DURATION = 0.4

# Path to pipeline config
PIPELINE_CONFIG_PATH = os.environ.get(
    'PIPELINE_CONFIG',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pipeline.yaml')
)


def load_pipeline_config():
    """Load pipeline.yaml and return the parsed dict."""
    path = os.path.abspath(PIPELINE_CONFIG_PATH)
    with open(path) as f:
        return yaml.safe_load(f)


# ── Transfer Point (thread-safe handshake) ─────────────────


class TransferPoint:
    """Passive buffer between two adjacent arms (single-slot).

    Models a physical handoff zone that can hold exactly one product.
    Upstream places product → product sits until downstream picks it.
    The buffer is passive: placement does not require downstream to be alive.

    ATOMICITY: All reads/writes execute under the Condition's internal lock.
    In wait_and_pick, the emptiness check (while _object_id is None) and
    the cancel_check both run under the same lock acquisition. Since place()
    also acquires this lock, no product can arrive between the emptiness
    check and the cancel decision — this is a single synchronized state
    transition, not two separate unsynchronized checks.
    """

    def __init__(self, name):
        self.name = name
        self._object_id = None
        self._cond = threading.Condition()

    def place(self, object_id):
        """Place a product into the buffer. Blocks if lock is held by wait_and_pick."""
        with self._cond:
            self._object_id = object_id
            self._cond.notify_all()

    def wait_and_pick(self, timeout=60.0, cancel_check=None):
        """Wait for a product, pick it atomically.

        Returns object_id on success, None on cancel or timeout.
        The cancel_check only fires when the buffer IS empty — if a product
        exists, it is returned before cancel_check is ever consulted. This
        guarantees drain-before-stop: any buffered product is always picked
        before a drain-stop decision is made.

        Polls cancel_check every 2 seconds for responsive failure detection.
        """
        deadline = time.time() + timeout
        with self._cond:
            while self._object_id is None:
                if cancel_check and not cancel_check():
                    return None

                remaining = deadline - time.time()
                if remaining <= 0:
                    return None

                self._cond.wait(timeout=min(remaining, 2.0))

            obj_id = self._object_id
            self._object_id = None
            self._cond.notify_all()
            return obj_id

    def wait_until_empty(self, timeout=30.0, cancel_check=None):
        """Wait until buffer is empty (downstream picked the product).

        Returns True if empty, False on cancel or timeout.
        If the buffer is already empty, returns True immediately — the
        cancel_check never fires. This enables passive buffering: an upstream
        arm can always place into an empty buffer regardless of downstream health.
        """
        deadline = time.time() + timeout
        with self._cond:
            while self._object_id is not None:
                if cancel_check and not cancel_check():
                    return False
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._cond.wait(timeout=min(remaining, 2.0))
            return True

    def is_empty(self):
        """Check if the buffer is empty (no product waiting)."""
        with self._cond:
            return self._object_id is None


# ── Drain Stop Exception ─────────────────────────────────

class _DrainComplete(Exception):
    """Raised when an arm stops because upstream/downstream is dead and no products remain.

    This is NOT a failure — it's a graceful stop after draining the handoff zone.
    Caught separately from real errors to avoid triggering failure cascades.
    """
    pass


# ── Arm Controller (runs in its own thread) ────────────────


try:
    from app.core.module import Module as _ArmModuleBase
except Exception:
    _ArmModuleBase = object


class ArmController(_ArmModuleBase):
    """Controls one arm in the pipeline. Checks dependencies before each cycle.

    Phase 4: inherits from `app.core.module.Module` so the pipeline builder
    can treat arms identically to vehicles and conveyors. Internal behaviour
    (drain-and-halt, retry policy, dependency awareness) is unchanged.
    """

    module_type = "arm"

    def __init__(self, arm_id, host, speed_factor, input_tp, output_tp,
                 num_products, log_fn, handoff_place=None,
                 set_angle_fn=None, object_event_fn=None,
                 failure_policy=None,
                 is_allowed_fn=None, on_started_fn=None, on_failed_fn=None,
                 upstream_alive_fn=None, downstream_alive_fn=None,
                 on_health_change_fn=None,
                 label=None, keep_alive=False):
        self.keep_alive = keep_alive
        if _ArmModuleBase is not object:
            super().__init__(module_id=f"arm-{arm_id}", label=label or f"Arm {arm_id}")
        self.arm_id = arm_id
        self.host = host
        self.speed = speed_factor
        self.input_tp = input_tp
        self.output_tp = output_tp
        self.num_products = num_products
        self.log = log_fn
        self.log_fn = log_fn
        self.processed = 0
        self.handoff_place = handoff_place
        self.error = None
        self._set_angle = set_angle_fn
        self._object_event = object_event_fn
        self.failure_policy = failure_policy or {"action": "stop"}
        # Dependency-aware callbacks
        self._is_allowed = is_allowed_fn          # () -> bool: can this arm execute?
        self._on_started = on_started_fn          # () -> None: mark arm as alive
        self._on_failed = on_failed_fn            # (error) -> None: mark arm as dead, propagate
        # Drain-aware callbacks and state
        self._upstream_alive = upstream_alive_fn   # () -> bool: is upstream arm alive?
        self._downstream_alive = downstream_alive_fn  # () -> bool: is downstream arm alive?
        self._on_health_change = on_health_change_fn  # (status, error) -> None: report health
        self._draining = False                     # True once upstream/downstream failure detected

    def move_to(self, angle):
        if self._set_angle:
            self._set_angle(self.arm_id, angle)
        else:
            send_command(self.host, self.arm_id, angle)
        time.sleep(MOVE_DURATION * self.speed)

    def emit_object(self, obj_id, state, carrier_arm=None):
        if self._object_event:
            self._object_event(obj_id, state, carrier_arm)
        else:
            send_object_event(self.host, obj_id, state, carrier_arm)

    def _report_state_change(self, state, reason=None):
        """Report a pipeline state change (draining, stopped) without cascade.

        Phase 5 lifecycle: also mirror the change into Module._state so
        non-arm neighbours observe arm failures through the unified Module
        interface (their cancel_check calls `is_module_alive()`).
        """
        if self._on_health_change:
            self._on_health_change(state, reason)
        # Map arm health vocabulary to Module state machine.
        mapped = {
            "alive": "idle",
            "idle": "idle",
            "busy": "busy",
            "draining": "draining",
            "unhealthy": "draining",
            "dead": "failed",
            "stopped": "stopped",
            "failed": "failed",
            "recovering": "recovering",
        }.get(state, "idle")
        try:
            self.set_state(mapped, reason)
        except (ValueError, AttributeError):
            pass

    def _report_drain_stop(self, reason):
        """Report this arm as stopped after drain completion (no cascade)."""
        self.error = reason
        self._report_state_change("stopped", reason)

    def run(self):
        # Check if this arm is allowed to start (upstream dependencies alive)
        if self._is_allowed and not self._is_allowed():
            self.error = "blocked by upstream dependency"
            self.log(self.arm_id, "BLOCKED: upstream dependency stopped or failed")
            try:
                self.set_state("stopped", self.error)
            except (ValueError, AttributeError):
                pass
            return

        if self._on_started:
            self._on_started()
        try:
            self.set_state("busy")
        except (ValueError, AttributeError):
            pass

        try:
            self._run_cycle()
        except Exception as e:
            self.error = str(e)
            self.log(self.arm_id, f"FATAL ERROR: {e}")
            if self._on_failed:
                self._on_failed(str(e))
            try:
                self.set_state("failed", str(e))
            except (ValueError, AttributeError):
                pass

        # Module state reflects natural completion if not already failed/stopped.
        try:
            if self.state not in ("failed", "stopped"):
                self.set_state("stopped")
        except (ValueError, AttributeError):
            pass

        # Phase 5 lifecycle: stay alive after the configured product run
        # finishes. Idle-wait until stop_module() fires (or fail/stopped).
        if getattr(self, "keep_alive", False):
            stop_event = getattr(self, "_stop_event", None)
            if stop_event is not None:
                self.log(self.arm_id, "idle — pipeline staying alive")
                while not stop_event.is_set():
                    time.sleep(0.5)

    def _process_one_product(self, obj_id):
        """Process a single product through this arm's cycle.

        Raises _DrainComplete if upstream is dead and handoff is empty,
        or if downstream is dead and product can't be placed.
        """
        # Wait for object from upstream (or pick from intake)
        if self.input_tp is not None:
            self.log(self.arm_id, f"Waiting for {obj_id}...")

            # Cancel if this arm is stopped OR upstream is dead (drain complete)
            def input_cancel():
                if self._is_allowed and not self._is_allowed():
                    return False
                if self._upstream_alive and not self._upstream_alive():
                    return False
                return True

            picked_id = self.input_tp.wait_and_pick(
                timeout=60.0,
                cancel_check=input_cancel,
            )
            if not picked_id:
                # Upstream dead + handoff empty → drain complete (graceful stop)
                if self._upstream_alive and not self._upstream_alive():
                    raise _DrainComplete("upstream arm failed, no more products")
                if self._is_allowed and not self._is_allowed():
                    raise RuntimeError("this arm was stopped")
                raise TimeoutError(f"TIMEOUT waiting for {obj_id}")
            obj_id = picked_id
            self.log(self.arm_id, f"Picking {obj_id} from transfer point")
            time.sleep(ACTION_DURATION * self.speed)
        else:
            self.log(self.arm_id, f"Picking {obj_id} from intake")
            time.sleep(ACTION_DURATION * self.speed)

        # Pick up: move to 180, attach, hold
        self.move_to(PICK_ANGLE)
        self.emit_object(obj_id, "carried", self.arm_id)
        time.sleep(ACTION_DURATION * self.speed)

        # Carry to 0
        self.log(self.arm_id, f"Carrying {obj_id}...")
        self.move_to(PLACE_ANGLE)

        # Place: signal downstream, retract in parallel
        if self.output_tp is not None:
            # Cancel if this arm stopped OR downstream is dead
            def output_cancel():
                if self._is_allowed and not self._is_allowed():
                    return False
                if self._downstream_alive and not self._downstream_alive():
                    return False
                return True

            can_place = self.output_tp.wait_until_empty(
                timeout=30.0, cancel_check=output_cancel,
            )
            if not can_place:
                # Can't deliver — downstream dead or handoff full. Retract safely.
                self.log(self.arm_id, f"Cannot place {obj_id}, retracting safely...")
                self.move_to(REST_ANGLE)
                raise _DrainComplete("downstream arm unavailable, cannot deliver product")

            self.log(self.arm_id, f"Placed {obj_id}, signaling...")
            self.emit_object(obj_id, self.handoff_place)
            self.output_tp.place(obj_id)
            self.log(self.arm_id, f"Retracting...")
            self.move_to(REST_ANGLE)
        else:
            self.log(self.arm_id, f"Delivered {obj_id} to output!")
            self.emit_object(obj_id, "output")
            self.move_to(REST_ANGLE)

        time.sleep(ACTION_DURATION * self.speed)
        return obj_id

    def _run_cycle(self):
        for product_num in range(1, self.num_products + 1):
            # Check this arm's own health
            if self._is_allowed and not self._is_allowed():
                self.error = "this arm was stopped"
                self.log(self.arm_id, "STOPPED: this arm is not allowed to run")
                return

            # ── Upstream failure detection (middle/last arms) ──
            # Enter DRAINING if upstream arm is dead. The actual drain
            # happens in _process_one_product via wait_and_pick: if the
            # TP has product it's picked first; if empty, _DrainComplete.
            if self.input_tp is not None:
                if not self._draining and self._upstream_alive and not self._upstream_alive():
                    self._draining = True
                    self._report_state_change("draining")
                    self.log(self.arm_id, "Upstream failed — draining remaining products")

            # ── Downstream failure detection (first arm, passive buffer) ──
            # First arm can still place into an EMPTY buffer even if downstream
            # is dead — the product sits there until recovery. But once the
            # buffer is occupied (or already drained once), stop.
            if self.input_tp is None and self._downstream_alive and not self._downstream_alive():
                if self._draining:
                    # Already filled buffer on previous iteration → stop
                    self._report_drain_stop("buffer filled, awaiting downstream recovery")
                    return
                if self.output_tp and not self.output_tp.is_empty():
                    # Buffer occupied, downstream dead → can't place, stop
                    self._report_state_change("draining")
                    self._report_drain_stop("downstream failed, buffer occupied")
                    return
                # Buffer empty → allow ONE more product to fill it (passive buffer)
                self._draining = True
                self._report_state_change("draining")
                self.log(self.arm_id, "Downstream failed — filling buffer with final product")
                # Fall through: pick from intake, carry, place into empty buffer

            obj_id = f"Obj{product_num}"
            try:
                self._process_one_product(obj_id)
                self.processed += 1
            except _DrainComplete as e:
                # Graceful drain stop — report as stopped, no cascade
                self.log(self.arm_id, f"Drain complete: {e}")
                self._report_drain_stop(str(e))
                return
            except Exception as e:
                action = self.failure_policy.get("action", "stop")

                if action == "retry":
                    max_retries = self.failure_policy.get("retry_max", 3)
                    delay = self.failure_policy.get("retry_delay", 2.0)
                    succeeded = False
                    for attempt in range(1, max_retries + 1):
                        self.log(self.arm_id, f"Retry {attempt}/{max_retries} for {obj_id}")
                        time.sleep(delay)
                        try:
                            self._process_one_product(obj_id)
                            self.processed += 1
                            succeeded = True
                            break
                        except Exception:
                            continue
                    if not succeeded:
                        self.error = str(e)
                        self.log(self.arm_id, f"FAILED after {max_retries} retries: {e}")
                        if self._on_failed:
                            self._on_failed(str(e))
                        return

                elif action == "skip":
                    self.log(self.arm_id, f"SKIPPING {obj_id}: {e}")
                    continue

                elif action == "isolate":
                    # Isolate: stop this arm but don't propagate
                    self.error = str(e)
                    self.log(self.arm_id, f"ISOLATED: {e}")
                    return

                else:  # stop — propagates to downstream
                    self.error = str(e)
                    self.log(self.arm_id, f"STOPPING: {e}")
                    if self._on_failed:
                        self._on_failed(str(e))
                    return


# ── Logging ────────────────────────────────────────────────

_log_lock = threading.Lock()
_browser_log_fn = None

# Phase 5 lifecycle: shared registry of running modules + manual stop signal
# so external HTTP endpoints (demo-runner /stop) can halt the pipeline.
_active_modules = []
_stop_pipeline_event = threading.Event()


def stop_pipeline():
    """Stop every running module and signal the main demo loop to exit.

    Safe to call from any thread (e.g. the demo-runner /stop route handler).
    Idempotent — calling when no pipeline is active is a no-op.
    """
    _stop_pipeline_event.set()
    for m in list(_active_modules):
        try:
            m.stop_module()
        except Exception:
            pass


def get_active_modules():
    """Return a snapshot list of currently running modules (debug helper)."""
    return list(_active_modules)


def thread_safe_log(arm_id, message):
    with _log_lock:
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] Arm {arm_id}: {message}")
        if _browser_log_fn:
            _browser_log_fn(f"arm_{arm_id}", message)


def thread_safe_log_asset(asset_id, message):
    """Log helper for non-arm assets (vehicle, conveyor)."""
    with _log_lock:
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] {asset_id}: {message}")
        if _browser_log_fn:
            _browser_log_fn(asset_id, message)


# ── Dynamic Pipeline Builder ──────────────────────────────


def _build_arm_module(arm_cfg, num_products, speed_factor,
                      set_angle_fn, object_event_fn, failure_cfg,
                      make_is_allowed, make_on_started, make_on_failed,
                      make_on_health_change, keep_alive=False):
    """Construct an ArmController as a Module.

    Phase 4: arms expose the same interface as vehicles/conveyors. Internal
    behaviour (drain logic, retry, dependency awareness) is preserved.
    Neighbour-alive callbacks are wired by the caller after TP linking.
    """
    return ArmController(
        arm_id=arm_cfg["id"],
        host=None,
        speed_factor=speed_factor,
        input_tp=None,
        output_tp=None,
        num_products=num_products,
        log_fn=thread_safe_log,
        set_angle_fn=set_angle_fn,
        object_event_fn=object_event_fn,
        failure_policy=_get_arm_failure_policy(arm_cfg, failure_cfg),
        is_allowed_fn=make_is_allowed(arm_cfg["id"]),
        on_started_fn=make_on_started(arm_cfg["id"]),
        on_failed_fn=make_on_failed(arm_cfg["id"]),
        on_health_change_fn=make_on_health_change(arm_cfg["id"]),
        label=arm_cfg.get("label"),
        keep_alive=keep_alive,
    )


def _build_asset_module(asset, num_products, speed_factor, object_event_fn,
                        keep_alive=False):
    """Construct a VehicleController or ConveyorController as a Module.

    Caller wires `input_tp`, `output_tp`, and neighbour-alive callbacks
    after the linear TP chain is built.
    """
    if not _ASSET_RUNTIME_AVAILABLE:
        return None

    def emit(obj_id, state, **extra):
        kw = dict(
            carrier_asset=extra.get("carrier_asset"),
            slot=extra.get("slot"),
            start_time=extra.get("start_time"),
            duration_ms=extra.get("duration_ms"),
            event_time=extra.get("event_time"),
        )
        try:
            object_event_fn(obj_id, state,
                            carrier_arm=extra.get("carrier_arm"), **kw)
        except TypeError:
            send_object_event(DEFAULT_HOST, obj_id, state,
                              carrier_arm=extra.get("carrier_arm"), **kw)

    common = dict(
        asset_id=asset["id"],
        label=asset.get("label", asset["id"]),
        input_tp=None,
        output_tp=None,
        speed_factor=speed_factor,
        log_fn=thread_safe_log_asset,
        object_event_fn=emit,
        num_products=num_products,
        keep_alive=keep_alive,
    )
    if asset["type"] == "vehicle":
        return VehicleController(**common, travel_time=2.0)
    if asset["type"] == "conveyor":
        geom = asset.get("geometry", {}) or {}
        return ConveyorController(
            **common,
            slot_count=int(geom.get("slot_count", 3)),
            shift_interval=float(geom.get("shift_interval", 1.0)),
        )
    return None


def _get_arm_failure_policy(arm_cfg, global_policy):
    """Resolve failure policy for an arm: per-arm override or global default."""
    per_arm = arm_cfg.get("failure_policy")
    if per_arm:
        return {"action": per_arm, **global_policy}
    return {"action": global_policy.get("default", "stop"), **global_policy}


def _run_pipeline_core(speed_factor, num_products,
                       set_angle_fn, object_event_fn, reset_objects_fn,
                       log_fn=None, config=None,
                       is_allowed_fn=None, on_health_fn=None, on_failed_fn=None):
    """Build and run an N-arm pipeline from config.

    Health callbacks:
        is_allowed_fn(arm_id) -> bool: check if arm can execute
        on_health_fn(arm_id, status, error=None): report health change
        on_failed_fn(arm_id, error): report failure, propagates to dependents

    Drain behavior: when an arm dies, downstream arms continue processing
    any products already in handoff zones, then stop gracefully.
    """
    global _browser_log_fn
    _browser_log_fn = log_fn

    # Load config
    if config is None:
        config = load_pipeline_config()

    arms_cfg = config["arms"]
    n = len(arms_cfg)
    mode = config.get("pipeline", {}).get("mode", "sequential")
    failure_cfg = config.get("failure_policy", {"default": "stop"})
    arm_ids = [arm["id"] for arm in arms_cfg]

    print(f"=== INTER.act Pipeline Demo ===")
    print(f"Arms: {n} | Mode: {mode} | Products: {num_products} | Speed: {speed_factor}x")
    print()

    # Reset all arms
    print(f"Resetting {n} arms to rest position...")
    for arm_id in arm_ids:
        set_angle_fn(arm_id, REST_ANGLE)
    reset_objects_fn()

    # Feed objects into intake one at a time
    for i in range(1, num_products + 1):
        object_event_fn(f"Obj{i}", "intake")
        time.sleep(0.5 * speed_factor)
    time.sleep(0.3 * speed_factor)
    print()

    # Build health callback factories (closures capture arm_id)
    def make_is_allowed(aid):
        if is_allowed_fn and aid is not None:
            return lambda: is_allowed_fn(aid)
        return None

    def make_on_started(aid):
        if on_health_fn:
            return lambda: on_health_fn(aid, "alive")
        return None

    def make_on_failed(aid):
        if on_failed_fn:
            return lambda err: on_failed_fn(aid, err)
        return None

    def make_on_health_change(aid):
        if on_health_fn:
            return lambda status, error=None: on_health_fn(aid, status, error)
        return None

    # ── Phase 4: unified module-based pipeline builder. ─────────────
    # Every component (arm, vehicle, conveyor, future types) implements the
    # `Module` interface. The builder reads the flow order from config and
    # connects modules linearly via TransferPoints. There is no type-specific
    # branching here — adding a new module type requires zero changes in
    # this function, only a class implementing `Module`.
    if _ASSET_RUNTIME_AVAILABLE:
        asset_state.reset()

    if mode == "parallel":
        # Each arm is its own source+sink — no TP chain, no inter-module flow.
        modules = [
            _build_arm_module(arm_cfg, num_products, speed_factor,
                              set_angle_fn, object_event_fn, failure_cfg,
                              make_is_allowed, make_on_started, make_on_failed,
                              make_on_health_change)
            for arm_cfg in arms_cfg
        ]
    else:
        # sequential / dependency: linear pipeline through ordered modules.
        flow_ids = (_pc.get_module_flow_order()
                    if _pc is not None
                    else [f"arm-{a['id']}" for a in arms_cfg])
        arm_map = {a["id"]: a for a in arms_cfg}
        extras_map = {}
        if _pc is not None:
            try:
                extras_map = {ea["id"]: ea for ea in _pc.get_extra_assets()}
            except Exception:
                extras_map = {}

        modules = []
        for sid in flow_ids:
            if sid.startswith("arm-"):
                arm_id_int = int(sid.split("-", 1)[1])
                arm_cfg = arm_map.get(arm_id_int)
                if arm_cfg is None:
                    continue
                modules.append(_build_arm_module(
                    arm_cfg, num_products, speed_factor,
                    set_angle_fn, object_event_fn, failure_cfg,
                    make_is_allowed, make_on_started, make_on_failed,
                    make_on_health_change))
            else:
                ea = extras_map.get(sid)
                if not ea:
                    continue
                m = _build_asset_module(ea, num_products, speed_factor,
                                        object_event_fn)
                if m is not None:
                    modules.append(m)

        # Connect TPs linearly. Arm modules also get a `handoff_place` name
        # so existing arm-zone object_event tracking keeps emitting valid
        # `handoff_N` states for the legacy frontend rendering layer.
        arm_to_arm_index = 0
        for i in range(len(modules) - 1):
            tp = TransferPoint(f"TP {modules[i].id}->{modules[i+1].id}")
            modules[i].output_tp = tp
            modules[i + 1].input_tp = tp
            if hasattr(modules[i], "arm_id") and hasattr(modules[i + 1], "arm_id"):
                arm_to_arm_index += 1
                modules[i].handoff_place = f"handoff_{arm_to_arm_index}"

        # Wire neighbor-alive callbacks uniformly. Every module's drain
        # decisions reference its immediate neighbors via two checks:
        #   1. `is_module_alive()` — state-based (failed/stopped → dead).
        #   2. `Thread.is_alive()` — real thread liveness; covers the case
        #      where a module's run() returned without ever updating _state
        #      (e.g. early exit on _is_allowed False, or natural completion).
        # A neighbour is "alive" only when both predicates hold.
        def _make_alive(m):
            return (m is None
                    or (m.is_module_alive() and m.is_alive()))
        for i, m in enumerate(modules):
            prev_m = modules[i - 1] if i > 0 else None
            next_m = modules[i + 1] if i + 1 < len(modules) else None
            m._upstream_alive = (lambda pm=prev_m: _make_alive(pm))
            m._downstream_alive = (lambda nm=next_m: _make_alive(nm))

    controllers = modules  # legacy alias for the summary block below

    # Phase 5: register modules for external stop control + open the demo
    # status window. The blocking main loop below keeps this thread alive so
    # the demo-runner reports "running" until manual stop or all modules die.
    global _active_modules
    _active_modules = list(modules)
    _stop_pipeline_event.clear()

    print("[SYSTEM] Demo started, modules running...")
    for m in modules:
        print(f"[SYSTEM]   module: {m.id} ({type(m).__name__})")

    start_time = time.time()
    for m in modules:
        m.start()

    try:
        last_log = 0.0
        while not _stop_pipeline_event.is_set():
            # Use Thread.is_alive() (real thread liveness) so the main loop
            # exits ONLY on manual stop or when every worker thread has truly
            # terminated. A module reporting state=='failed' does NOT halt
            # the pipeline — keep_alive idle wait keeps its thread alive
            # until stop_pipeline() fires.
            threads_alive = [m for m in modules if m.is_alive()]
            if not threads_alive:
                break
            now = time.time()
            if now - last_log >= 5.0:
                module_alive = [m.id for m in modules if m.is_module_alive()]
                print("[SYSTEM] Alive modules:", module_alive,
                      "| threads:", len(threads_alive))
                last_log = now
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("[SYSTEM] KeyboardInterrupt — stopping modules")
        for m in modules:
            m.stop_module()

    # Final join with bounded timeout so we don't hang forever on stuck threads.
    for m in modules:
        m.join(timeout=5.0)

    _active_modules = []
    elapsed = time.time() - start_time
    print(f"[SYSTEM] Demo loop exited after {elapsed:.1f}s")

    # Summary
    print()
    arm_modules = [c for c in controllers if hasattr(c, "arm_id")]
    asset_modules = [c for c in controllers if not hasattr(c, "arm_id")]
    errors = [(c.arm_id, c.error) for c in arm_modules if c.error]
    asset_errors = [(c.id, c.error) for c in asset_modules if getattr(c, "error", None)]
    if errors or asset_errors:
        print("=== Pipeline FAILED ===")
        for arm_id, err in errors:
            print(f"  Arm {arm_id}: {err}")
        for aid, err in asset_errors:
            print(f"  {aid}: {err}")
    else:
        print("=== Pipeline Complete ===")

    arm_parts = [f"Arm{c.arm_id}={c.processed}" for c in arm_modules]
    asset_parts = [f"{c.id}={getattr(c, 'processed', 0)}" for c in asset_modules]
    print(f"Products processed: {', '.join(arm_parts + asset_parts)}")
    print(f"Total time: {elapsed:.1f}s")

    # Reset all to rest
    for arm_id in arm_ids:
        set_angle_fn(arm_id, REST_ANGLE)
    print("All arms returned to rest.")


def run_pipeline(host, speed_factor, num_products):
    """HTTP mode — used by CLI and demo-runner container."""
    def http_set_angle(arm_id, angle):
        send_command(host, arm_id, angle)

    def http_object_event(obj_id, state, carrier_arm=None, **kw):
        send_object_event(host, obj_id, state, carrier_arm=carrier_arm, **kw)

    def http_reset():
        reset_objects(host)

    def http_log(source, message):
        send_log(host, source, message)

    def http_is_allowed(arm_id):
        return is_arm_allowed_http(host, arm_id)

    def http_on_health(arm_id, status, error=None):
        send_health(host, arm_id, status, error)

    def http_on_failed(arm_id, error):
        send_stop_arm(host, arm_id, error)

    _run_pipeline_core(speed_factor, num_products,
                       http_set_angle, http_object_event, http_reset,
                       log_fn=http_log,
                       is_allowed_fn=http_is_allowed,
                       on_health_fn=http_on_health,
                       on_failed_fn=http_on_failed)


def run_pipeline_with_callbacks(set_angle_fn, object_event_fn,
                                reset_objects_fn, speed_factor, num_products,
                                log_fn=None):
    """Direct callback mode — bypasses HTTP."""
    _run_pipeline_core(speed_factor, num_products,
                       set_angle_fn, object_event_fn, reset_objects_fn,
                       log_fn=log_fn)


def dry_run(speed_factor, num_products):
    config = load_pipeline_config()
    arms_cfg = config["arms"]
    n = len(arms_cfg)
    mode = config.get("pipeline", {}).get("mode", "sequential")

    est_per_product = (MOVE_DURATION * 3 + ACTION_DURATION * 2) * speed_factor
    est_total = est_per_product * n + est_per_product * (num_products - 1)

    print(f"=== Dry Run ===")
    print(f"Arms: {n} | Mode: {mode} | Products: {num_products} | Speed: {speed_factor}x")
    print()
    print(f"Cycle: rest(90) -> pick(180) -> carry -> place(0) -> retract(90)")
    print(f"  Move: {MOVE_DURATION * speed_factor:.1f}s | Action: {ACTION_DURATION * speed_factor:.1f}s")
    print(f"  Cycle time: ~{est_per_product:.1f}s per arm per product")
    print()

    if mode == "sequential":
        flow = " -> ".join(
            [f"Intake"] +
            [f"[Arm {a['id']}]" for a in arms_cfg] +
            [f"Output"]
        )
        print(f"Flow: {flow}")
    else:
        for a in arms_cfg:
            print(f"  Arm {a['id']}: independent (no handoffs)")

    print(f"Estimated total: ~{est_total:.0f}s")


def main():
    parser = argparse.ArgumentParser(description="INTER.act Pipeline Demo")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--speed", choices=["fast", "normal", "slow"], default="normal")
    parser.add_argument("--products", type=int, default=DEFAULT_PRODUCTS)
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    speed_factor = SPEED_FACTORS[args.speed]

    if args.dry_run:
        dry_run(speed_factor, args.products)
        return

    try:
        run_pipeline(args.host, speed_factor, args.products)
    except KeyboardInterrupt:
        config = load_pipeline_config()
        arm_ids = [a["id"] for a in config["arms"]]
        print("\nPipeline interrupted. Resetting arms...")
        for arm_id in arm_ids:
            send_command(args.host, arm_id, REST_ANGLE)
        print("Done.")


if __name__ == "__main__":
    main()
