"""Asset Microservice — Flask wrapper around VehicleController / ConveyorController.

Mirrors the arm container pattern: each non-arm asset (vehicle / conveyor)
runs in its own container, exposes the same lifecycle endpoints, and can be
killed via `docker stop` to trigger the same downstream failure propagation.

Phase 6 scope: container shells + health monitoring only. Object transfer
between containers via /object endpoints is wired but not orchestrated by
the demo-runner yet (TransferClient comes in a later phase). Existing
in-process pipeline in run_demo.py continues unchanged.

Environment variables
---------------------
    ASSET_TYPE      "vehicle" or "conveyor"
    ASSET_ID        unique id, must match pipeline.yaml + docker service name
    ASSET_LABEL     human-readable label
    SERVER_HOST     backend URL (defaults via utils.DEFAULT_HOST)
    UPSTREAM_URL    optional URL of upstream asset's /object GET (future)
    DOWNSTREAM_URL  optional URL of downstream asset's /object POST (future)
    SLOT_COUNT      conveyor only — number of slots
    SHIFT_INTERVAL  conveyor only — seconds per shift
    TRAVEL_TIME     vehicle only — seconds per traversal
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

import requests
from flask import Flask, jsonify, request

# Make `from app.*` and shared utils resolve identically to demo-runner.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..")))

from utils import DEFAULT_HOST
from app.services.asset_controllers import VehicleController, ConveyorController


# ── Environment ───────────────────────────────────────────────────────

ASSET_TYPE = os.environ.get("ASSET_TYPE", "vehicle").lower()
ASSET_ID = os.environ.get("ASSET_ID") or f"{ASSET_TYPE}-default"
ASSET_LABEL = os.environ.get("ASSET_LABEL", ASSET_ID)
UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "").rstrip("/")
DOWNSTREAM_URL = os.environ.get("DOWNSTREAM_URL", "").rstrip("/")
SPEED_FACTOR = float(os.environ.get("SPEED_FACTOR", "1.0"))
SLOT_COUNT = int(os.environ.get("SLOT_COUNT", "4"))
SHIFT_INTERVAL = float(os.environ.get("SHIFT_INTERVAL", "1.2"))
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

if ASSET_TYPE == "vehicle":
    controller = VehicleController(
        asset_id=ASSET_ID,
        label=ASSET_LABEL,
        input_tp=input_tp,
        output_tp=output_tp,
        speed_factor=SPEED_FACTOR,
        log_fn=_post_log,
        object_event_fn=_post_object_event,
        num_products=10**9,        # effectively infinite — service is long-lived
        keep_alive=True,
        travel_time=TRAVEL_TIME,
    )
elif ASSET_TYPE == "conveyor":
    controller = ConveyorController(
        asset_id=ASSET_ID,
        label=ASSET_LABEL,
        input_tp=input_tp,
        output_tp=output_tp,
        speed_factor=SPEED_FACTOR,
        log_fn=_post_log,
        object_event_fn=_post_object_event,
        num_products=10**9,
        keep_alive=True,
        slot_count=SLOT_COUNT,
        shift_interval=SHIFT_INTERVAL,
    )
else:
    raise SystemExit(f"Unknown ASSET_TYPE={ASSET_TYPE!r}; expected vehicle|conveyor")

controller.start()


# ── Optional downstream forwarder (future TransferClient hook) ───────

def _forward_output_loop():
    """When DOWNSTREAM_URL is set, push every emitted object to next service."""
    if not DOWNSTREAM_URL:
        return
    while True:
        obj = output_tp.wait_and_pick(timeout=60.0, cancel_check=lambda: True)
        if obj is None:
            continue
        try:
            requests.post(f"{DOWNSTREAM_URL}/object", json={"object_id": obj}, timeout=5)
        except Exception as exc:
            print(f"[{ASSET_ID}] forward failed: {exc}", flush=True)


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
        "carried_object": getattr(controller, "_current_object", None),
        "server_host": DEFAULT_HOST,
    })


@app.route("/object", methods=["POST"])
def receive_object():
    """Receive an object from upstream. Body: {"object_id": "Obj1"}."""
    data = request.get_json(silent=True) or {}
    obj_id = data.get("object_id")
    if not obj_id:
        return jsonify({"error": "missing object_id", "code": "MISSING_FIELD"}), 400
    input_tp.place(obj_id)
    return jsonify({"status": "queued", "asset_id": ASSET_ID, "object_id": obj_id})


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
    try:
        requests.post(f"{DEFAULT_HOST}/assets/{ASSET_ID}/recover", timeout=3)
    except Exception:
        pass
    return jsonify({"status": "ok", "asset_id": ASSET_ID, "state": controller.state})


# ── Shutdown handler — instant failure notification ──────────────────

def _handle_shutdown(signum, frame):
    sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
    print(f"[{ASSET_ID}] {sig_name} received — notifying backend", flush=True)
    try:
        requests.post(
            f"{DEFAULT_HOST}/assets/{ASSET_ID}/fail",
            json={"reason": "container shutting down"},
            timeout=3,
        )
    except Exception:
        pass
    controller.stop_module()
    sys.exit(0)


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
