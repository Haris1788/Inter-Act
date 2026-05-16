"""Asset Microservice — Flask wrapper around VehicleController.

Mirrors the arm container pattern: each non-arm asset (vehicle) runs in
its own container, exposes the same lifecycle endpoints, and can be
killed via `docker stop` to trigger the same downstream failure propagation.

Phase 6 scope: container shells + health monitoring only. Object transfer
between containers via /object endpoints is wired but not orchestrated by
the demo-runner yet (TransferClient comes in a later phase). Existing
in-process pipeline in run_demo.py continues unchanged.

Environment variables
---------------------
    ASSET_TYPE      "vehicle" (only supported type)
    ASSET_ID        unique id, must match pipeline.yaml + docker service name
    ASSET_LABEL     human-readable label
    SERVER_HOST     backend URL (defaults via utils.DEFAULT_HOST)
    UPSTREAM_URL    optional URL of upstream asset's /object GET (future)
    DOWNSTREAM_URL  optional URL of downstream asset's /object POST (future)
    TRAVEL_TIME     vehicle — seconds per traversal
    SPEED_FACTOR    multiplier applied to controller speed (default 1.0)

Usage
-----
    docker run -e ASSET_TYPE=vehicle -e ASSET_ID=veh-a -p 8010:8000 ...
    or via docker-compose service definition.
"""

from __future__ import annotations

import argparse
import os
import queue
import signal
import sys
import threading
import time
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request

# Make `from app.*` and shared utils resolve identically to demo-runner.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..")))

from utils import DEFAULT_HOST
from app.services.asset_controllers import VehicleController


# ── Environment ───────────────────────────────────────────────────────

ASSET_TYPE = os.environ.get("ASSET_TYPE", "vehicle").lower()
ASSET_ID = os.environ.get("ASSET_ID") or f"{ASSET_TYPE}-default"
ASSET_LABEL = os.environ.get("ASSET_LABEL", ASSET_ID)
UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "").rstrip("/")
DOWNSTREAM_URL = os.environ.get("DOWNSTREAM_URL", "").rstrip("/")
SPEED_FACTOR = float(os.environ.get("SPEED_FACTOR", "1.0"))
TRAVEL_TIME = float(os.environ.get("TRAVEL_TIME", "2.0"))


# ── Stub TransferPoint backed by a stdlib queue ──────────────────────

class QueueTransferPoint:
    """Single-slot blocking buffer used inside the asset container.

    Compatible with the AssetController contract: place / wait_and_pick /
    wait_until_empty / is_empty. Backed by `queue.Queue(maxsize=1)` so the
    semantics match the in-process `TransferPoint` from run_demo.py.
    """

    def __init__(self, name: str = "qtp"):
        self.name = name
        self._q: queue.Queue = queue.Queue(maxsize=1)

    def place(self, obj):
        self._q.put(obj)

    def wait_and_pick(self, timeout: float = 120.0, cancel_check=None):
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            try:
                return self._q.get(timeout=min(0.5, remaining))
            except queue.Empty:
                if cancel_check is not None and not cancel_check():
                    return None

    def wait_until_empty(self, timeout: float = 30.0, cancel_check=None):
        deadline = time.time() + timeout
        while True:
            if self._q.empty():
                return True
            if cancel_check is not None and not cancel_check():
                return False
            if time.time() > deadline:
                return False
            time.sleep(0.2)

    def is_empty(self) -> bool:
        return self._q.empty()


# ── Telemetry helpers ─────────────────────────────────────────────────

def _post_log(source: str, message: str) -> None:
    try:
        requests.post(
            f"{DEFAULT_HOST}/log",
            json={"source": source, "message": message},
            timeout=2,
        )
    except Exception:
        pass
    print(f"[{source}] {message}", flush=True)


def _post_object_event(obj_id: str, state: str, **extra) -> None:
    payload = {"object_id": obj_id, "state": state}
    for k, v in extra.items():
        if v is not None:
            payload[k] = v
    try:
        requests.post(f"{DEFAULT_HOST}/object_event", json=payload, timeout=2)
    except Exception:
        pass


# ── Build controller ──────────────────────────────────────────────────

input_tp = QueueTransferPoint("input")
output_tp = QueueTransferPoint("output")


def _remote_module_ready(base_url: str) -> bool:
    if not base_url:
        return True
    try:
        ready_resp = requests.get(f"{base_url}/ready", timeout=1.0)
        if ready_resp.status_code == 200:
            data = ready_resp.json() or {}
            return bool(data.get("downstream_ready"))
    except Exception:
        pass
    try:
        health_resp = requests.get(f"{base_url}/health", timeout=1.0)
        if health_resp.status_code != 200:
            return False
        state = (health_resp.json() or {}).get("state")
        return state not in ("failed", "recovering", "stopped")
    except Exception:
        return False


def _upstream_alive() -> bool:
    if not UPSTREAM_URL:
        return True
    return _remote_module_ready(UPSTREAM_URL)


def _downstream_alive() -> bool:
    if not DOWNSTREAM_URL:
        return True
    return _remote_module_ready(DOWNSTREAM_URL)

if ASSET_TYPE == "vehicle":
    controller = VehicleController(
        asset_id=ASSET_ID,
        label=ASSET_LABEL,
        input_tp=input_tp,
        output_tp=output_tp,
        speed_factor=SPEED_FACTOR,
        log_fn=_post_log,
        object_event_fn=_post_object_event,
        upstream_alive_fn=_upstream_alive,
        downstream_alive_fn=_downstream_alive,
        num_products=10**9,        # effectively infinite — service is long-lived
        keep_alive=True,
        travel_time=TRAVEL_TIME,
    )
else:
    raise SystemExit(f"Unknown ASSET_TYPE={ASSET_TYPE!r}; expected vehicle")

controller.start()


def _downstream_target_id(url: str) -> str:
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").strip()
    except Exception:
        host = ""
    if host in ("localhost", "127.0.0.1"):
        return ""
    return host


controller._downstream_target = _downstream_target_id(DOWNSTREAM_URL)


# ── Optional downstream forwarder (future TransferClient hook) ───────

# Downstream capability flag. None = unknown (probe at runtime). True = remote
# accepts POST /object (another asset_service). False = remote does not accept
# POST (e.g. an arm container) — stop forwarding so a polling consumer can
# claim the object via GET /object.
_downstream_accepts_post = None


def _forward_output_loop():
    """When DOWNSTREAM_URL is set, push emitted objects to next service.

    For asset→asset hops the downstream also runs asset_service.py and
    accepts POST /object. For asset→arm hops the arm container has no such
    endpoint; we detect that on the first failed POST and disable forwarding.
    The demo-runner's HttpInputBridge then collects via GET /object polling.
    """
    global _downstream_accepts_post
    if not DOWNSTREAM_URL:
        return
    while True:
        # Don't drain the queue when we know downstream uses polling.
        if _downstream_accepts_post is False:
            time.sleep(1.0)
            continue
        obj = output_tp.wait_and_pick(timeout=60.0, cancel_check=lambda: True)
        if obj is None:
            continue
        delivered = False
        try:
            resp = requests.post(
                f"{DOWNSTREAM_URL}/object",
                json={"object_id": obj},
                timeout=3,
            )
            if resp.status_code in (200, 201):
                delivered = True
                _downstream_accepts_post = True
            else:
                # 404 / 405 etc → downstream isn't an asset_service.
                if resp.status_code in (404, 405):
                    _downstream_accepts_post = False
                print(f"[{ASSET_ID}] forward {DOWNSTREAM_URL}/object → "
                      f"{resp.status_code}, requeue", flush=True)
        except Exception as exc:
            # Network failure — could be transient or downstream down.
            # Don't permanently disable; just requeue + retry.
            print(f"[{ASSET_ID}] forward failed: {exc}", flush=True)
        if not delivered:
            output_tp.place(obj)
            time.sleep(0.5)


threading.Thread(target=_forward_output_loop, daemon=True).start()


# ── HTTP API ──────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    """Liveness check + module state. Mirrors arm /health."""
    return jsonify({
        "status": "ok",
        "asset_id": ASSET_ID,
        "asset_type": ASSET_TYPE,
        "state": controller.state,
        "downstream_ready": controller.downstream_ready(),
        "carried_object": getattr(controller, "_current_object", None),
        "server_host": DEFAULT_HOST,
    })


@app.route("/ready", methods=["GET"])
def ready():
    return jsonify({
        "asset_id": ASSET_ID,
        "state": controller.state,
        "downstream_ready": controller.downstream_ready(),
    })


@app.route("/object", methods=["POST"])
def receive_object():
    """Receive an object from upstream. Body: {"object_id": "Obj1"}.

    Phase 8 fix: SYNCHRONOUS accept. The HTTP POST blocks until the asset
    controller has actually picked the object out of the input queue (so
    `_current_object == obj_id`). This shrinks the window in which a kill
    between POST-completion and controller-pickup would lose the object.
    Upstream's HttpOutputBridge.place() therefore knows the object is in
    the controller's hands when 2xx is returned, not just queued.
    """
    data = request.get_json(silent=True) or {}
    obj_id = data.get("object_id")
    if not obj_id:
        return jsonify({"error": "missing object_id", "code": "MISSING_FIELD"}), 400

    # Reject when not in a state that can accept new work.
    # "busy" means carrying an object already — capacity=1 enforcement.
    # We intentionally do NOT check downstream_ready() here: an idle asset
    # acts as a 1-slot TP buffer, accepting an object even when its own
    # downstream is blocked. It will drain or hold until the path clears.
    if controller.state in ("failed", "recovering", "draining", "stopped", "busy"):
        return jsonify({
            "error": f"asset {controller.state} — cannot accept",
            "asset_id": ASSET_ID,
            "state": controller.state,
        }), 503

    input_tp.place(obj_id)

    # Wait until controller takes ownership (or asset failed / timeout).
    deadline = time.time() + 30
    while time.time() < deadline:
        if controller.state == "failed":
            return jsonify({
                "error": "asset failed before accept",
                "asset_id": ASSET_ID,
            }), 503
        if getattr(controller, "_current_object", None) == obj_id:
            return jsonify({
                "status": "accepted",
                "asset_id": ASSET_ID,
                "object_id": obj_id,
            })
        time.sleep(0.05)
    return jsonify({
        "error": "controller did not accept in time",
        "asset_id": ASSET_ID,
    }), 504


@app.route("/object", methods=["GET"])
def take_object():
    """Non-blocking peek+pop of the next available output object."""
    if output_tp.is_empty():
        return jsonify({"object_id": None, "asset_id": ASSET_ID}), 204
    obj = output_tp.wait_and_pick(timeout=0.2)
    if obj is None:
        return jsonify({"object_id": None, "asset_id": ASSET_ID}), 204
    return jsonify({"object_id": obj, "asset_id": ASSET_ID})


@app.route("/fail", methods=["POST"])
def fail_route():
    data = request.get_json(silent=True) or {}
    reason = data.get("reason", "manually failed")
    controller.fail(reason)
    # Mirror to backend so SocketIO + frontend stays in sync.
    try:
        requests.post(
            f"{DEFAULT_HOST}/assets/{ASSET_ID}/fail",
            json={"reason": reason}, timeout=3,
        )
    except Exception:
        pass
    return jsonify({"status": "ok", "asset_id": ASSET_ID, "state": controller.state})


@app.route("/recover", methods=["POST"])
def recover_route():
    controller.recover()
    return jsonify({"status": "ok", "asset_id": ASSET_ID, "state": controller.state})


@app.route("/reset", methods=["POST"])
def reset_route():
    """Drain internal queues and slot list — called at the start of every demo
    so leftover objects from a prior crashed run don't poison the next flow.
    """
    drained_in = 0
    drained_out = 0
    while not input_tp.is_empty():
        if input_tp.wait_and_pick(timeout=0.1) is None:
            break
        drained_in += 1
    while not output_tp.is_empty():
        if output_tp.wait_and_pick(timeout=0.1) is None:
            break
        drained_out += 1
    controller._current_object = None
    # Also reset the cached downstream-accepts-post flag so a re-detect happens.
    global _downstream_accepts_post
    _downstream_accepts_post = None
    print(f"[{ASSET_ID}] reset: drained input={drained_in} output={drained_out}",
          flush=True)
    return jsonify({
        "status": "ok",
        "asset_id": ASSET_ID,
        "drained_in": drained_in,
        "drained_out": drained_out,
    })


# ── Shutdown handler — instant failure notification ──────────────────

_shutting_down = False


def _collect_inflight_objects():
    """Return list of object_ids currently held by this asset (best-effort)."""
    held = []
    cur = getattr(controller, "_current_object", None)
    if cur:
        held.append(cur)
    if hasattr(controller, "slots"):
        held.extend([s for s in controller.slots if s and s != cur])
    return held


def _handle_shutdown(signum, frame):
    """SIGTERM/SIGINT handler. Idempotent + hard-exits the process.

    Phase 8 fix: before exiting we report any in-flight objects to the
    backend log so they aren't silently dropped. A future implementation
    could re-POST them upstream for true at-least-once delivery; for now
    we surface the loss explicitly so it's debuggable.
    """
    global _shutting_down
    sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
    if _shutting_down:
        os._exit(0)
    _shutting_down = True
    print(f"[{ASSET_ID}] {sig_name} received — notifying backend", flush=True)

    inflight = _collect_inflight_objects()
    if inflight:
        msg = f"shutting down with in-flight objects LOST: {inflight}"
        print(f"[{ASSET_ID}] {msg}", flush=True)
        try:
            requests.post(
                f"{DEFAULT_HOST}/log",
                json={"source": ASSET_ID, "message": msg},
                timeout=2,
            )
        except Exception:
            pass

    try:
        requests.post(
            f"{DEFAULT_HOST}/assets/{ASSET_ID}/fail",
            json={
                "reason": "container shutting down",
                "lost_objects": inflight,
            },
            timeout=3,
        )
    except Exception:
        pass
    try:
        controller.stop_module()
    except Exception:
        pass
    os._exit(0)


signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"{ASSET_TYPE} asset service")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--type", help="(informational only — ASSET_TYPE env wins)")
    args = parser.parse_args()

    print(f"[{ASSET_ID}] {ASSET_TYPE} service starting on port {args.port}", flush=True)
    print(f"[{ASSET_ID}] backend={DEFAULT_HOST}, "
          f"upstream={UPSTREAM_URL or '-'}, downstream={DOWNSTREAM_URL or '-'}",
          flush=True)
    app.run(host="0.0.0.0", port=args.port)
