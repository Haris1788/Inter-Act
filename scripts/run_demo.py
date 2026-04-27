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


class ArmController:
    """Controls one arm in the pipeline. Checks dependencies before each cycle."""

    def __init__(self, arm_id, host, speed_factor, input_tp, output_tp,
                 num_products, log_fn, handoff_place=None,
                 set_angle_fn=None, object_event_fn=None,
                 failure_policy=None,
                 is_allowed_fn=None, on_started_fn=None, on_failed_fn=None,
                 upstream_alive_fn=None, downstream_alive_fn=None,
                 on_health_change_fn=None):
        self.arm_id = arm_id
        self.host = host
        self.speed = speed_factor
        self.input_tp = input_tp
        self.output_tp = output_tp
        self.num_products = num_products
        self.log = log_fn
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
        """Report a pipeline state change (draining, stopped) without cascade."""
        if self._on_health_change:
            self._on_health_change(state, reason)

    def _report_drain_stop(self, reason):
        """Report this arm as stopped after drain completion (no cascade)."""
        self.error = reason
        self._report_state_change("stopped", reason)

    def run(self):
        # Check if this arm is allowed to start (upstream dependencies alive)
        if self._is_allowed and not self._is_allowed():
            self.error = "blocked by upstream dependency"
            self.log(self.arm_id, "BLOCKED: upstream dependency stopped or failed")
            return

        if self._on_started:
            self._on_started()

        try:
            self._run_cycle()
        except Exception as e:
            self.error = str(e)
            self.log(self.arm_id, f"FATAL ERROR: {e}")
            if self._on_failed:
                self._on_failed(str(e))

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


def _find_asset_chain(src_arm_id, dst_arm_id, all_assets):
    """Find a non-arm chain (vehicle/conveyor) connecting two arms.

    Returns ordered list of asset dicts, or None if no chain exists.
    Uses upstream/downstream string ids (`arm-1`, `veh-a`, `conv-1`).
    """
    src_id = f"arm-{src_arm_id}"
    dst_id = f"arm-{dst_arm_id}"
    by_id = {a["id"]: a for a in all_assets}

    first = None
    for a in all_assets:
        if a.get("type") == "arm":
            continue
        if src_id in a.get("upstream", []):
            first = a
            break
    if first is None:
        return None

    chain = [first]
    cur = first
    while cur.get("downstream"):
        nxt = by_id.get(cur["downstream"][0])
        if nxt is None:
            break
        if nxt["id"] == dst_id:
            return chain
        if nxt.get("type") == "arm":
            return None
        chain.append(nxt)
        cur = nxt
    return None


def _build_asset_controller(asset, input_tp, output_tp, num_products,
                            speed_factor, src_arm_id, dst_arm_id,
                            object_event_fn, is_allowed_fn, log_fn):
    """Construct a VehicleController or ConveyorController for an asset dict."""
    if not _ASSET_RUNTIME_AVAILABLE:
        return None

    def emit(obj_id, state, **extra):
        # Adapt to the demo runner's object_event callback signature.
        carrier_arm = extra.get("carrier_arm")
        try:
            object_event_fn(obj_id, state, carrier_arm=carrier_arm,
                            carrier_asset=extra.get("carrier_asset"),
                            slot=extra.get("slot"))
        except TypeError:
            # Old 3-arg signature — fall back to direct HTTP call.
            send_object_event(DEFAULT_HOST, obj_id, state,
                              carrier_arm=carrier_arm,
                              carrier_asset=extra.get("carrier_asset"),
                              slot=extra.get("slot"))

    upstream_alive = (lambda: is_allowed_fn(src_arm_id)) if is_allowed_fn else (lambda: True)
    downstream_alive = (lambda: is_allowed_fn(dst_arm_id)) if is_allowed_fn else (lambda: True)

    common = dict(
        asset_id=asset["id"],
        label=asset.get("label", asset["id"]),
        input_tp=input_tp,
        output_tp=output_tp,
        speed_factor=speed_factor,
        log_fn=log_fn,
        object_event_fn=emit,
        upstream_alive_fn=upstream_alive,
        downstream_alive_fn=downstream_alive,
        num_products=num_products,
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

    # Build arm controllers based on mode
    controllers = []

    if mode == "sequential":
        tps = [TransferPoint(f"TP{i+1} (Arm{arm_ids[i]}->Arm{arm_ids[i+1]})")
               for i in range(n - 1)]

        for i, arm_cfg in enumerate(arms_cfg):
            input_tp = tps[i - 1] if i > 0 else None
            output_tp = tps[i] if i < n - 1 else None
            handoff = f"handoff_{i + 1}" if i < n - 1 else None
            upstream_id = arm_ids[i - 1] if i > 0 else None
            downstream_id = arm_ids[i + 1] if i < n - 1 else None

            ctrl = ArmController(
                arm_id=arm_cfg["id"],
                host=None,
                speed_factor=speed_factor,
                input_tp=input_tp,
                output_tp=output_tp,
                num_products=num_products,
                log_fn=thread_safe_log,
                handoff_place=handoff,
                set_angle_fn=set_angle_fn,
                object_event_fn=object_event_fn,
                failure_policy=_get_arm_failure_policy(arm_cfg, failure_cfg),
                is_allowed_fn=make_is_allowed(arm_cfg["id"]),
                on_started_fn=make_on_started(arm_cfg["id"]),
                on_failed_fn=make_on_failed(arm_cfg["id"]),
                upstream_alive_fn=make_is_allowed(upstream_id),
                downstream_alive_fn=make_is_allowed(downstream_id),
                on_health_change_fn=make_on_health_change(arm_cfg["id"]),
            )
            controllers.append(ctrl)

    elif mode == "dependency":
        # Build transfer points from the depends_on graph.
        # For a linear chain (1→2→3), this is identical to sequential.
        # For branching graphs, each dependency edge gets its own transfer point.
        arm_map = {a["id"]: a for a in arms_cfg}
        tps = {}  # (from_id, to_id) -> TransferPoint
        # Phase 2: when a non-arm chain (vehicle/conveyor) connects two arms,
        # the dst arm's input_tp is overridden to the chain exit TP. Source
        # arm continues to write into the original tps[(src,dst)] which feeds
        # the first AssetController in the chain.
        chain_input_overrides = {}     # (src_id, dst_id) -> TP for dst arm
        asset_controllers = []
        if _ASSET_RUNTIME_AVAILABLE:
            asset_state.reset()

        for arm_cfg in arms_cfg:
            for dep_id in arm_cfg.get("depends_on", []):
                key = (dep_id, arm_cfg["id"])
                tps[key] = TransferPoint(f"TP (Arm{dep_id}->Arm{arm_cfg['id']})")

        # Splice asset controllers into edges that have a non-arm chain.
        if _ASSET_RUNTIME_AVAILABLE and _pc is not None:
            try:
                all_assets = _pc.get_all_assets()
            except Exception:
                all_assets = []
            for arm_cfg in arms_cfg:
                for dep_id in arm_cfg.get("depends_on", []):
                    chain = _find_asset_chain(dep_id, arm_cfg["id"], all_assets)
                    if not chain:
                        continue
                    edge_key = (dep_id, arm_cfg["id"])
                    src_tp = tps[edge_key]
                    internal_tps = [
                        TransferPoint(f"TP-{a['id']}->{arm_cfg['id']}-{i}")
                        for i, a in enumerate(chain)
                    ]
                    for i, asset in enumerate(chain):
                        in_tp = src_tp if i == 0 else internal_tps[i - 1]
                        out_tp = internal_tps[i]
                        ctrl = _build_asset_controller(
                            asset=asset,
                            input_tp=in_tp,
                            output_tp=out_tp,
                            num_products=num_products,
                            speed_factor=speed_factor,
                            src_arm_id=dep_id,
                            dst_arm_id=arm_cfg["id"],
                            object_event_fn=object_event_fn,
                            is_allowed_fn=is_allowed_fn,
                            log_fn=lambda src, msg: thread_safe_log_asset(src, msg),
                        )
                        if ctrl is not None:
                            asset_controllers.append(ctrl)
                    # Dst arm picks from chain's exit TP, not the original.
                    chain_input_overrides[edge_key] = internal_tps[-1]

        # Use topological order from config to determine execution order
        topo_order = load_pipeline_config().get("_topo_order", None)
        if topo_order is None:
            # Compute it inline (Kahn's algorithm)
            in_deg = {a["id"]: len(a.get("depends_on", [])) for a in arms_cfg}
            queue = sorted([aid for aid, d in in_deg.items() if d == 0])
            topo_order = []
            dependents_map = {}
            for a in arms_cfg:
                for dep in a.get("depends_on", []):
                    dependents_map.setdefault(dep, []).append(a["id"])
            while queue:
                cur = queue.pop(0)
                topo_order.append(cur)
                for child in sorted(dependents_map.get(cur, [])):
                    in_deg[child] -= 1
                    if in_deg[child] == 0:
                        queue.append(child)

        for arm_id in topo_order:
            arm_cfg = arm_map[arm_id]
            deps = arm_cfg.get("depends_on", [])

            # Input: transfer point from first dependency (linear chains have one).
            # Phase 2: if a non-arm chain feeds this arm, use the chain's exit TP.
            if deps:
                edge_key = (deps[0], arm_id)
                input_tp = chain_input_overrides.get(edge_key, tps.get(edge_key))
            else:
                input_tp = None
            upstream_id = deps[0] if deps else None

            # Output: transfer points to all arms that depend on this one
            output_keys = [(arm_id, child) for child in
                           [a["id"] for a in arms_cfg if arm_id in a.get("depends_on", [])]]
            output_tp = tps[output_keys[0]] if len(output_keys) == 1 else None
            downstream_id = output_keys[0][1] if output_keys else None

            # Handoff name: based on the output transfer point index
            handoff = None
            if output_tp:
                # Find the handoff index by position in topo order
                tp_index = list(tps.keys()).index(output_keys[0])
                handoff = f"handoff_{tp_index + 1}"

            ctrl = ArmController(
                arm_id=arm_cfg["id"],
                host=None,
                speed_factor=speed_factor,
                input_tp=input_tp,
                output_tp=output_tp,
                num_products=num_products,
                log_fn=thread_safe_log,
                handoff_place=handoff,
                set_angle_fn=set_angle_fn,
                object_event_fn=object_event_fn,
                failure_policy=_get_arm_failure_policy(arm_cfg, failure_cfg),
                is_allowed_fn=make_is_allowed(arm_cfg["id"]),
                on_started_fn=make_on_started(arm_cfg["id"]),
                on_failed_fn=make_on_failed(arm_cfg["id"]),
                upstream_alive_fn=make_is_allowed(upstream_id),
                downstream_alive_fn=make_is_allowed(downstream_id),
                on_health_change_fn=make_on_health_change(arm_cfg["id"]),
            )
            controllers.append(ctrl)

    elif mode == "parallel":
        for arm_cfg in arms_cfg:
            ctrl = ArmController(
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
            )
            controllers.append(ctrl)

    # Launch all arms
    threads = []
    for ctrl in controllers:
        t = threading.Thread(target=ctrl.run, name=f"Arm-{ctrl.arm_id}", daemon=True)
        threads.append(t)

    # Phase 2: launch asset controller threads (vehicle/conveyor) — they
    # are themselves Thread subclasses, so just start them.
    asset_ctrl_list = locals().get("asset_controllers", []) or []
    for ac in asset_ctrl_list:
        ac.start()

    start_time = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    for ac in asset_ctrl_list:
        ac.join(timeout=10)
    elapsed = time.time() - start_time

    # Summary
    print()
    errors = [(c.arm_id, c.error) for c in controllers if c.error]
    if errors:
        print("=== Pipeline FAILED ===")
        for arm_id, err in errors:
            print(f"  Arm {arm_id}: {err}")
    else:
        print("=== Pipeline Complete ===")

    parts = [f"Arm{c.arm_id}={c.processed}" for c in controllers]
    print(f"Products processed: {', '.join(parts)}")
    print(f"Total time: {elapsed:.1f}s")

    # Reset all to rest
    for arm_id in arm_ids:
        set_angle_fn(arm_id, REST_ANGLE)
    print("All arms returned to rest.")


def run_pipeline(host, speed_factor, num_products):
    """HTTP mode — used by CLI and demo-runner container."""
    def http_set_angle(arm_id, angle):
        send_command(host, arm_id, angle)

    def http_object_event(obj_id, state, carrier_arm=None):
        send_object_event(host, obj_id, state, carrier_arm)

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
