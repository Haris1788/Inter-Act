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
    """Condition-based handshake between two adjacent arms.

    All reads/writes inside the Condition's lock — no race gap.
    """

    def __init__(self, name):
        self.name = name
        self._object_id = None
        self._cond = threading.Condition()

    def place(self, object_id):
        with self._cond:
            self._object_id = object_id
            self._cond.notify_all()

    def wait_and_pick(self, timeout=60.0, cancel_check=None):
        """Wait for an object. If cancel_check returns False, abort immediately.

        Polls cancel_check every 2 seconds so downstream arms react
        quickly when upstream is stopped — not after a 60-second timeout.
        """
        import time as _time
        deadline = _time.time() + timeout
        with self._cond:
            while self._object_id is None:
                # Check if this arm has been cancelled (upstream stopped)
                if cancel_check and not cancel_check():
                    return None

                remaining = deadline - _time.time()
                if remaining <= 0:
                    return None

                # Wait in short intervals so cancel_check runs frequently
                self._cond.wait(timeout=min(remaining, 2.0))

            obj_id = self._object_id
            self._object_id = None
            self._cond.notify_all()
            return obj_id

    def wait_until_empty(self, timeout=30.0, cancel_check=None):
        """Wait until cleared. Polls cancel_check every 2 seconds."""
        import time as _time
        deadline = _time.time() + timeout
        with self._cond:
            while self._object_id is not None:
                if cancel_check and not cancel_check():
                    return False
                remaining = deadline - _time.time()
                if remaining <= 0:
                    return False
                self._cond.wait(timeout=min(remaining, 2.0))
            return True

    def is_empty(self):
        with self._cond:
            return self._object_id is None


# ── Arm Controller (runs in its own thread) ────────────────


class ArmController:
    """Controls one arm in the pipeline. Checks dependencies before each cycle."""

    def __init__(self, arm_id, host, speed_factor, input_tp, output_tp,
                 num_products, log_fn, handoff_place=None,
                 set_angle_fn=None, object_event_fn=None,
                 failure_policy=None,
                 is_allowed_fn=None, on_started_fn=None, on_failed_fn=None):
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
        self._is_allowed = is_allowed_fn    # () -> bool: can this arm execute?
        self._on_started = on_started_fn    # () -> None: mark arm as alive
        self._on_failed = on_failed_fn      # (error) -> None: mark arm as dead, propagate

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
        """Process a single product through this arm's cycle."""
        # Wait for object from upstream (or pick from intake)
        if self.input_tp is not None:
            self.log(self.arm_id, f"Waiting for {obj_id}...")
            picked_id = self.input_tp.wait_and_pick(
                timeout=60.0,
                cancel_check=self._is_allowed,  # checks every 2s if upstream is still alive
            )
            if not picked_id:
                # Distinguish between timeout and cancellation
                if self._is_allowed and not self._is_allowed():
                    raise RuntimeError("upstream arm stopped")
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
            self.output_tp.wait_until_empty(timeout=30.0, cancel_check=self._is_allowed)
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
            # Check dependency health before each product
            if self._is_allowed and not self._is_allowed():
                self.error = "stopped by upstream failure"
                self.log(self.arm_id, "STOPPED: upstream dependency failed")
                if self._on_failed:
                    self._on_failed("upstream dependency failed")
                return

            obj_id = f"Obj{product_num}"
            try:
                self._process_one_product(obj_id)
                self.processed += 1
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


# ── Dynamic Pipeline Builder ──────────────────────────────


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
        on_health_fn(arm_id, status): report health change
        on_failed_fn(arm_id, error): report failure, propagates to dependents
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
        if is_allowed_fn:
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

    # Build arm controllers based on mode
    controllers = []

    if mode == "sequential":
        tps = [TransferPoint(f"TP{i+1} (Arm{arm_ids[i]}->Arm{arm_ids[i+1]})")
               for i in range(n - 1)]

        for i, arm_cfg in enumerate(arms_cfg):
            input_tp = tps[i - 1] if i > 0 else None
            output_tp = tps[i] if i < n - 1 else None
            handoff = f"handoff_{i + 1}" if i < n - 1 else None

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
            )
            controllers.append(ctrl)

    elif mode == "dependency":
        # Build transfer points from the depends_on graph.
        # For a linear chain (1→2→3), this is identical to sequential.
        # For branching graphs, each dependency edge gets its own transfer point.
        arm_map = {a["id"]: a for a in arms_cfg}
        tps = {}  # (from_id, to_id) -> TransferPoint

        for arm_cfg in arms_cfg:
            for dep_id in arm_cfg.get("depends_on", []):
                key = (dep_id, arm_cfg["id"])
                tps[key] = TransferPoint(f"TP (Arm{dep_id}->Arm{arm_cfg['id']})")

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

            # Input: transfer point from first dependency (linear chains have one)
            input_tp = tps.get((deps[0], arm_id)) if deps else None

            # Output: transfer points to all arms that depend on this one
            output_keys = [(arm_id, child) for child in
                           [a["id"] for a in arms_cfg if arm_id in a.get("depends_on", [])]]
            output_tp = tps[output_keys[0]] if len(output_keys) == 1 else None

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
            )
            controllers.append(ctrl)

    # Launch all arms
    threads = []
    for ctrl in controllers:
        t = threading.Thread(target=ctrl.run, name=f"Arm-{ctrl.arm_id}", daemon=True)
        threads.append(t)

    start_time = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
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

    def http_on_health(arm_id, status):
        send_health(host, arm_id, status)

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
