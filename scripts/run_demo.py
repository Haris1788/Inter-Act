"""
INTER.act Pipeline Demo — config-driven multi-arm handover simulation.

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

import requests as _http

from utils import (send_command, send_object_event, send_log, reset_objects,
                    send_health, send_stop_arm, is_arm_allowed_http, DEFAULT_HOST)

# Phase 8: asset controllers (vehicle/conveyor) live ONLY inside their
# respective Docker containers. The demo-runner no longer instantiates
# VehicleController / ConveyorController in-process. We still import the
# asset_state registry + pipeline_config helper so the runner can detect
# asset chains and route through HTTP bridges.
try:
    from app.models import asset_state, pipeline_config as _pc
    _ASSET_RUNTIME_AVAILABLE = True
except Exception:
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


def load_pipeline_config(force_reload=False):
    """Load pipeline config from the shared runtime source.

    Prefer `app.models.pipeline_config` when available so demo-runner and
    backend consume the same in-memory view after reload.
    """
    if _pc is not None:
        if force_reload:
            _pc.reload()
        cfg = _pc.get_config()
        if cfg is not None:
            return cfg

    path = os.path.abspath(PIPELINE_CONFIG_PATH)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Transfer Point (thread-safe handshake) ─────────────────


class TransferPoint:
    """Passive buffer between two adjacent arms (single-slot).

    Models a physical handover zone that can hold exactly one product.
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
        """Place a product into the buffer, waiting if occupied."""
        with self._cond:
            while self._object_id is not None:
                self._cond.wait(timeout=0.5)
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

    This is NOT a failure — it's a graceful stop after draining the handover zone.
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
                 num_products, log_fn, handover_place=None,
                 set_angle_fn=None, object_event_fn=None,
                 failure_policy=None,
                 is_allowed_fn=None, on_started_fn=None, on_failed_fn=None,
                 upstream_alive_fn=None, downstream_alive_fn=None,
                 on_health_change_fn=None,
                 label=None, keep_alive=False, is_terminal_fn=None):
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
        self.handover_place = handover_place
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
        self._is_terminal = is_terminal_fn or (lambda: False)

    def is_ready_for_input(self):
        """True when an upstream handover may feed this arm."""
        if self.state == "draining":
            return False
        if not self.is_module_alive():
            return False
        if getattr(self, "_current_object", None) is not None:
            return False
        if not self.downstream_ready():
            return False
        return True

    def downstream_ready(self):
        if self.state in ("failed", "recovering", "draining", "stopped"):
            return False
        if not self.is_module_alive():
            return False
        # Arms use TP-capacity semantics. The TP is a passive 1-slot buffer —
        # placement is allowed whenever the slot is free, regardless of whether
        # the downstream module itself is currently healthy.
        return self.tp_has_capacity()

    def _hard_stopped(self):
        return self.state in ("failed", "recovering", "stopped")

    def tp_has_capacity(self) -> bool:
        """True when the output TP slot is free (capacity = 1 buffer)."""
        if self.output_tp is None:
            return True
        is_empty = getattr(self.output_tp, "is_empty", None)
        return not callable(is_empty) or bool(is_empty())

    def move_to(self, angle):
        if self._set_angle:
            self._set_angle(self.arm_id, angle)
        else:
            send_command(self.host, self.arm_id, angle)
        time.sleep(MOVE_DURATION * self.speed)

    def emit_object(self, obj_id, state, carrier_arm=None, owner_id=None, owner_type=None):
        if self._object_event:
            try:
                self._object_event(
                    obj_id, state, carrier_arm,
                    owner_id=owner_id, owner_type=owner_type,
                    event_time=int(time.time() * 1000),
                )
            except TypeError:
                self._object_event(obj_id, state, carrier_arm)
        else:
            send_object_event(
                self.host, obj_id, state, carrier_arm,
                owner_id=owner_id, owner_type=owner_type,
                event_time=int(time.time() * 1000),
            )

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

    def _wait_until_allowed(self, context="work"):
        """Pause (without exiting thread) while this arm is disallowed.

        This keeps the worker alive across temporary arm failures/recovery
        during a running demo so it can resume once health flips back.
        """
        if not self._is_allowed:
            return True
        wait_logged = False
        while not self._is_allowed():
            stop_event = getattr(self, "_stop_event", None)
            if stop_event is not None and stop_event.is_set():
                return False
            if not wait_logged:
                self.log(self.arm_id, f"[FLOW] arm-{self.arm_id} paused — waiting for recovery ({context})")
                wait_logged = True
            time.sleep(0.25)
        if wait_logged:
            self.log(self.arm_id, f"[FLOW] arm-{self.arm_id} resumed")
        return True

    def _wait_for_upstream_recovery(self, timeout_s=30.0):
        """Wait for upstream to come back before declaring drain complete.

        This avoids stopping downstream arms permanently on temporary
        upstream container restarts (kill -> recover during a running demo).
        Returns True if upstream recovered in time, else False.
        """
        if not self._upstream_alive:
            return True
        deadline = time.time() + timeout_s
        wait_logged = False
        while time.time() < deadline:
            stop_event = getattr(self, "_stop_event", None)
            if stop_event is not None and stop_event.is_set():
                return False
            if self._upstream_alive():
                if wait_logged:
                    self.log(self.arm_id, f"[FLOW] arm-{self.arm_id} upstream recovered")
                return True
            if not wait_logged:
                self.log(self.arm_id, f"[FLOW] arm-{self.arm_id} waiting for upstream recovery")
                wait_logged = True
            time.sleep(0.25)
        return False

    def run(self):
        # Check if this arm is allowed to start (upstream dependencies alive)
        if not self._wait_until_allowed("startup"):
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

        Raises _DrainComplete if upstream is dead and handover is empty,
        or if downstream is dead and product can't be placed.
        """
        wait_logged = False
        if self._hard_stopped():
            return obj_id
        while self.output_tp is not None and not self.tp_has_capacity():
            if not self._wait_until_allowed("pre-pick"):
                return obj_id
            if self._hard_stopped():
                return obj_id
            if not wait_logged:
                self.log(self.arm_id, f"[FLOW] arm-{self.arm_id} blocked — TP full")
                wait_logged = True
            time.sleep(0.25)

        # Wait for object from upstream (or pick from intake)
        if self.input_tp is not None:
            self.log(self.arm_id, f"Waiting for {obj_id}...")

            # Cancel if this arm is stopped OR upstream is dead (drain complete)
            def input_cancel():
                if self._upstream_alive and not self._upstream_alive():
                    return False
                return True

            picked_id = None
            while picked_id is None:
                if not self._wait_until_allowed("input-wait"):
                    return obj_id
                picked_id = self.input_tp.wait_and_pick(
                    timeout=2.0,
                    cancel_check=input_cancel,
                )
                if picked_id is not None:
                    break
                # Upstream dead + handover empty → drain complete (graceful stop)
                if self._upstream_alive and not self._upstream_alive():
                    if self._wait_for_upstream_recovery(timeout_s=30.0):
                        continue
                    raise _DrainComplete("upstream unavailable too long, no more products")
            obj_id = picked_id
            self.log(self.arm_id, f"Picking {obj_id} from transfer point")
            self._current_object = obj_id
            time.sleep(ACTION_DURATION * self.speed)
        else:
            self.log(self.arm_id, f"Picking {obj_id} from intake")
            self._current_object = obj_id
            time.sleep(ACTION_DURATION * self.speed)

        if self._hard_stopped():
            return obj_id
        # Pick up: move to 180, attach, hold
        self.move_to(PICK_ANGLE)
        if self._hard_stopped():
            return obj_id
        self.emit_object(
            obj_id, "carried", self.arm_id,
            owner_id=f"arm-{self.arm_id}", owner_type="arm",
        )
        time.sleep(ACTION_DURATION * self.speed)

        if self._hard_stopped():
            return obj_id
        # Carry to 0
        self.log(self.arm_id, f"Carrying {obj_id}...")
        self.move_to(PLACE_ANGLE)
        if self._hard_stopped():
            return obj_id

        # Place: wait for TP capacity only — downstream module liveness is NOT checked.
        # The TP is a passive 1-slot buffer; objects sit there until downstream recovers.
        if self.output_tp is not None:
            hold_logged = False
            while True:
                if self._stop_event.is_set() or self._hard_stopped():
                    return obj_id
                ok = self.output_tp.wait_until_empty(
                    timeout=2.0,
                    cancel_check=lambda: not self._stop_event.is_set(),
                )
                if ok:
                    break
                if not hold_logged:
                    self.log(self.arm_id,
                             f"[FLOW] arm-{self.arm_id} holding {obj_id} — TP occupied")
                    hold_logged = True
                time.sleep(0.1)

            if self._hard_stopped():
                return obj_id
            self.output_tp.place(obj_id)
            self.log(self.arm_id, f"Placed {obj_id}, signaling...")
            self._current_object = None
            tp_owner_id = getattr(self.output_tp, "name", None)
            if not tp_owner_id and hasattr(self.output_tp, "asset_id"):
                tp_owner_id = f"TP arm-{self.arm_id}->{self.output_tp.asset_id}"
            self.emit_object(
                obj_id, self.handover_place or "transfer_point",
                owner_id=tp_owner_id or self.handover_place,
                owner_type="transfer_point",
            )
            self.log(self.arm_id, f"Retracting...")
            self.move_to(REST_ANGLE)
        else:
            if not self._is_terminal():
                raise RuntimeError(
                    f"non-terminal arm arm-{self.arm_id} cannot emit to output"
                )
            if self._hard_stopped():
                return obj_id
            self.log(self.arm_id, f"Delivered {obj_id} to output!")
            self._current_object = None
            self.emit_object(
                obj_id, "output",
                owner_id="output-zone", owner_type="zone",
            )
            self.move_to(REST_ANGLE)

        time.sleep(ACTION_DURATION * self.speed)
        return obj_id

    def _run_cycle(self):
        for product_num in range(1, self.num_products + 1):
            # Check this arm's own health
            if not self._wait_until_allowed("cycle"):
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

            # Downstream backpressure is handled inside _process_one_product.
            # Do not pre-fill a passive buffer when the next module is blocked.

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


# ── Phase 8: HTTP bridges replace in-process TransferPoint at every
# arm↔asset boundary. They expose the same minimal API as TransferPoint
# (`place`, `wait_and_pick`, `wait_until_empty`, `is_empty`) so the
# ArmController body stays unchanged.

def _docker_mode():
    """True when running inside a Docker container.

    `/.dockerenv` is created by Docker on every container at boot — it is
    the canonical, container-agnostic marker. Falling back to env-var
    detection broke after Phase 8 because the demo-runner container had no
    such variable set, sending HTTP bridges to localhost:8010 (which
    refers to the demo-runner itself, not the asset container).
    """
    if os.environ.get("ASSET_DNS_MODE") == "service":
        return True
    if os.environ.get("ASSET_DNS_MODE") == "localhost":
        return False
    return os.path.exists("/.dockerenv") or os.environ.get(
        "SERVER_HOST", "").startswith("http://backend")


def _asset_url(asset_id, port=8000):
    """Build the URL for an asset container's API.

    Inside Docker: use the compose service name (`http://veh-a:8000`).
    On the host machine: use the published port (`http://localhost:8010`).
    """
    if _docker_mode():
        return f"http://{asset_id}:{port}"
    fallback = {}
    if _pc is not None:
        try:
            fallback = _pc.get_asset_host_ports()
        except Exception:
            fallback = {}
    return f"http://localhost:{fallback.get(asset_id, port)}"


def _asset_alive_remote(asset_id):
    if asset_state is None:
        local_alive = True
    else:
        rec = asset_state.get(asset_id)
        if rec is not None and rec.get("status") in ("failed", "stopped"):
            return False
        local_alive = True
    try:
        resp = _http.get(f"{_asset_url(asset_id)}/health", timeout=1.0)
        if resp.status_code != 200:
            return False
        data = resp.json() or {}
        return data.get("state") not in ("failed", "recovering", "stopped")
    except Exception:
        return False


def _asset_ready_remote(asset_id):
    try:
        resp = _http.get(f"{_asset_url(asset_id)}/ready", timeout=1.0)
        if resp.status_code == 200:
            data = resp.json() or {}
            return bool(data.get("downstream_ready"))
    except Exception:
        pass
    return _asset_alive_remote(asset_id)


class HttpOutputBridge:
    """arm.output → asset container `/object` POST."""

    def __init__(self, asset_id, label=None, log_fn=None):
        self.asset_id = asset_id
        self.url = _asset_url(asset_id)
        self.label = label or asset_id
        self._log = log_fn or (lambda src, msg: None)

    def place(self, obj_id):
        while True:
            if not _asset_alive_remote(self.asset_id):
                raise RuntimeError(f"{self.asset_id} container down")
            try:
                resp = _http.post(
                    f"{self.url}/object",
                    json={"object_id": obj_id},
                    timeout=5,
                )
                if resp.status_code in (200, 201):
                    return
                self._log(self.asset_id,
                          f"POST /object → {resp.status_code}, retrying")
            except _http.exceptions.RequestException as exc:
                self._log(self.asset_id, f"POST /object failed: {exc}")
            time.sleep(0.5)

    def wait_until_empty(self, timeout=30.0, cancel_check=None):
        """Returns True when the downstream asset is idle (slot free, ready to accept).

        Uses /health state == 'idle' rather than downstream_ready — so an arm
        can place into a vehicle/conveyor buffer even while that asset's own
        downstream is blocked.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cancel_check is not None and not cancel_check():
                return False
            try:
                resp = _http.get(f"{self.url}/health", timeout=1.5)
                if resp.status_code == 200:
                    asset_state_val = resp.json().get("state", "unknown")
                    if asset_state_val == "idle":
                        return True
                    if asset_state_val in ("failed", "stopped"):
                        return False
                    # busy / draining / recovering — wait and retry
            except Exception:
                pass
            remaining = deadline - time.time()
            if remaining > 0.05:
                time.sleep(min(0.25, remaining))
        return False

    def is_empty(self):
        """True when the downstream asset is idle (no object in flight)."""
        try:
            resp = _http.get(f"{self.url}/health", timeout=1.5)
            if resp.status_code == 200:
                return resp.json().get("state") == "idle"
        except Exception:
            pass
        return False


class HttpInputBridge:
    """asset container `/object` GET → arm.input (polling)."""

    def __init__(self, asset_id, label=None, log_fn=None, poll_interval=0.3):
        self.asset_id = asset_id
        self.url = _asset_url(asset_id)
        self.label = label or asset_id
        self._log = log_fn or (lambda src, msg: None)
        self.poll_interval = poll_interval

    def place(self, obj_id):
        try:
            _http.post(f"{self.url}/object",
                       json={"object_id": obj_id}, timeout=3)
        except Exception:
            pass

    def wait_and_pick(self, timeout=120.0, cancel_check=None):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cancel_check is not None and not cancel_check():
                return None
            try:
                resp = _http.get(f"{self.url}/object", timeout=3)
                if resp.status_code == 200:
                    obj = (resp.json() or {}).get("object_id")
                    if obj:
                        return obj
            except _http.exceptions.RequestException as exc:
                self._log(self.asset_id, f"GET /object failed: {exc}")
            time.sleep(self.poll_interval)
        return None

    def wait_until_empty(self, timeout=30.0, cancel_check=None):
        return True

    def is_empty(self):
        try:
            resp = _http.get(f"{self.url}/object", timeout=2)
            return resp.status_code != 200
        except Exception:
            return True


def _build_arm_module(arm_cfg, num_products, speed_factor,
                      set_angle_fn, object_event_fn, failure_cfg,
                      make_is_allowed, make_on_started, make_on_failed,
                      make_on_health_change, keep_alive=False,
                      is_terminal_fn=None):
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
        is_terminal_fn=is_terminal_fn,
    )


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
    any products already in handover zones, then stop gracefully.
    """
    global _browser_log_fn
    _browser_log_fn = log_fn

    # Load config from a single source of truth at demo start.
    if config is None:
        config = load_pipeline_config(force_reload=True)
    elif _pc is not None:
        # Keep pipeline_config helpers (flow order, assets) consistent with
        # explicit config objects passed by tests or callers.
        try:
            _pc._config = config
        except Exception:
            pass

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

    # Phase 8 fix: tell every non-arm asset container to drain stale state
    # from a previous (possibly crashed) demo run before this one starts.
    if _pc is not None:
        try:
            for ea in _pc.get_extra_assets():
                aid = ea["id"]
                try:
                    _http.post(f"{_asset_url(aid)}/reset", timeout=3)
                    print(f"[SYSTEM] reset asset {aid}", flush=True)
                except Exception as exc:
                    print(f"[SYSTEM] reset {aid} failed: {exc}", flush=True)
        except Exception:
            pass

    if mode == "parallel":
        # Each arm is its own source+sink — no TP chain, no inter-module flow.
        terminal_arm_id = arm_ids[-1] if arm_ids else None
        modules = [
            _build_arm_module(arm_cfg, num_products, speed_factor,
                              set_angle_fn, object_event_fn, failure_cfg,
                              make_is_allowed, make_on_started, make_on_failed,
                              make_on_health_change,
                              is_terminal_fn=(lambda aid=arm_cfg["id"], t=terminal_arm_id: aid == t))
            for arm_cfg in arms_cfg
        ]
    else:
        # ── Phase 8: arm-only module list. Asset chains live in containers. ──
        # Build only ArmController instances. Detect whether each arm-arm
        # boundary has a non-arm chain in between (vehicle/conveyor) and
        # connect via HTTP bridges in that case; otherwise use a real
        # in-process TransferPoint (arm↔arm flow stays unchanged).
        flow_ids = (
            _pc.get_module_flow_order()
            if _pc is not None
            else [f"arm-{a['id']}" for a in arms_cfg]
        )
        arm_map = {a["id"]: a for a in arms_cfg}

        # Walk flow_ids; build ArmControllers in order, remember each arm's
        # position in the flow so we can find non-arm assets between them.
        modules = []
        arm_flow_indices = []  # parallel to modules: index into flow_ids
        for idx, sid in enumerate(flow_ids):
            if not sid.startswith("arm-"):
                continue
            arm_id_int = int(sid.split("-", 1)[1])
            arm_cfg = arm_map.get(arm_id_int)
            if arm_cfg is None:
                raise RuntimeError(
                    f"flow references arm-{arm_id_int} but config arms are "
                    f"{sorted(arm_map.keys())}"
                )
            modules.append(_build_arm_module(
                arm_cfg, num_products, speed_factor,
                set_angle_fn, object_event_fn, failure_cfg,
                make_is_allowed, make_on_started, make_on_failed,
                make_on_health_change,
                is_terminal_fn=(lambda sid=sid, flow_ids=flow_ids: sid == flow_ids[-1])))
            arm_flow_indices.append(idx)

        if len(modules) != len(arms_cfg):
            built = [m.arm_id for m in modules]
            expected = [a["id"] for a in arms_cfg]
            raise RuntimeError(
                f"topology mismatch: built arm modules {built}, expected {expected}"
            )

        # Link consecutive arm modules. handover_place is the physical transfer
        # index between arm N and arm N+1. Do not compress out asset-bridged
        # gaps, or arm-3→arm-4 renders at arm-2→arm-3's zone.
        for k in range(len(modules) - 1):
            src_arm = modules[k]
            dst_arm = modules[k + 1]
            between = flow_ids[arm_flow_indices[k] + 1: arm_flow_indices[k + 1]]
            if src_arm.output_tp is not None or dst_arm.input_tp is not None:
                raise RuntimeError(
                    f"boundary already wired: {src_arm.id} -> {dst_arm.id}"
                )
            if not between:
                # Direct arm→arm: keep in-process TransferPoint (unchanged).
                tp = TransferPoint(f"TP {src_arm.id}->{dst_arm.id}")
                src_arm.output_tp = tp
                dst_arm.input_tp = tp
                src_arm.handover_place = f"handover_{k + 1}"
                print(f"[LINK] {src_arm.id} -> {dst_arm.id}: transfer-point")
            else:
                if any(aid.startswith("arm-") for aid in between):
                    raise RuntimeError(
                        f"invalid mixed boundary {src_arm.id}->{dst_arm.id}: {between}"
                    )
                # Asset chain in between (lives in containers): bridge via HTTP.
                first_asset = between[0]
                last_asset = between[-1]
                src_arm.output_tp = HttpOutputBridge(
                    first_asset, log_fn=thread_safe_log_asset)
                dst_arm.input_tp = HttpInputBridge(
                    last_asset, log_fn=thread_safe_log_asset)
                # No handover_N — placement event for an asset boundary is
                # owned by the asset container; suppress arm-side emission.
                src_arm.handover_place = None
                print(
                    f"[LINK] {src_arm.id} -> {dst_arm.id}: http assets={between}"
                )

        # Neighbor-alive callbacks. For asset-bridged boundaries, the
        # neighbour is a remote container — proxy through asset_state.
        def _module_alive(m):
            return m is None or (m.is_module_alive() and m.is_alive())

        def _asset_alive_for(asset_id):
            return _asset_alive_remote(asset_id)

        def _asset_ready_for(asset_id):
            return _asset_ready_remote(asset_id)

        for k, m in enumerate(modules):
            # Upstream
            if k == 0:
                m._upstream_alive = lambda: True
            else:
                between_up = flow_ids[arm_flow_indices[k - 1] + 1: arm_flow_indices[k]]
                if between_up:
                    last_up = between_up[-1]
                    m._upstream_alive = (lambda aid=last_up:
                                         _asset_alive_for(aid))
                else:
                    prev_m = modules[k - 1]
                    m._upstream_alive = (lambda pm=prev_m: _module_alive(pm))

            # Downstream
            if k == len(modules) - 1:
                m._downstream_alive = lambda: True
            else:
                between_down = flow_ids[arm_flow_indices[k] + 1: arm_flow_indices[k + 1]]
                if between_down:
                    first_down = between_down[0]
                    m._downstream_alive = (lambda aid=first_down:
                                           _asset_ready_for(aid))
                else:
                    next_m = modules[k + 1]
                    m._downstream_alive = (lambda nm=next_m:
                                           nm.is_ready_for_input()
                                           if hasattr(nm, "is_ready_for_input")
                                           else _module_alive(nm))

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
            print(f"  Arm {a['id']}: independent (no handovers)")

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
