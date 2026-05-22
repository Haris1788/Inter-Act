"""
AGV Microservice — lightweight Flask wrapper that makes each AGV a deployable service.

Each container runs ONE instance with AGV_ID set via environment variable.
Receives task assignments from the backend (CentralBrain / POST /plan) and
delegates execution to agv_brain.run_task() in a background thread.

Usage:
    AGV_ID=agv-1 SERVER_HOST=http://backend:5000 python scripts/agv_server.py
    AGV_ID=agv-2 python scripts/agv_server.py --port 8101
"""

import os
import sys
import signal
import threading
import argparse

from flask import Flask, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agv_brain as _brain
from utils import send_agv_health, DEFAULT_HOST

AGV_ID: str = os.environ.get("AGV_ID", "agv-1")

app = Flask(__name__)

# ── Task state (process-local, one task at a time) ────────────────────────────
_task_lock = threading.Lock()
_current_task: dict | None = None
_task_status: str = "idle"   # idle | running | done | faulted


@app.route("/health", methods=["GET"])
def health():
    """Liveness check — proves this AGV container is running."""
    return jsonify({
        "status": "ok",
        "agv_id": AGV_ID,
        "task_status": _task_status,
        "server_host": DEFAULT_HOST,
    })


@app.route("/status", methods=["GET"])
def status():
    """Current task status + assignment dict."""
    return jsonify({
        "agv_id": AGV_ID,
        "task_status": _task_status,
        "current_task": _current_task,
    })


@app.route("/assign", methods=["POST"])
def assign():
    """Assign a package task to this AGV.

    Body: assignment dict from CentralBrain / plan_validator:
        {"package_id": "pkg-1", "agv_id": "agv-1", "sequence": 1,
         "route": ["bay","input","output","bay"], "label": "red"}
    Or wrapped: {"assignment": {...}}

    Returns immediately (202); the brain runs in a daemon thread.
    Check GET /status for progress.
    """
    global _current_task, _task_status

    data = request.get_json(silent=True) or {}
    # Accept both flat assignment and wrapped {"assignment": {...}}
    assignment = data.get("assignment") or data

    if not assignment.get("package_id"):
        return jsonify({"status": "failed", "error": "missing package_id"}), 400

    with _task_lock:
        if _task_status == "running":
            return jsonify({
                "status": "busy",
                "error": f"AGV {AGV_ID} already running a task",
            }), 409
        _current_task = dict(assignment)
        _task_status = "running"

    def _run() -> None:
        global _task_status
        try:
            ok = _brain.run_task(assignment)
            with _task_lock:
                _task_status = "done" if ok else "faulted"
        except Exception as exc:
            print(f"[{AGV_ID}] task exception: {exc}", file=sys.stderr)
            with _task_lock:
                _task_status = "faulted"
            send_agv_health(DEFAULT_HOST, AGV_ID, "faulted", str(exc))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({
        "status": "accepted",
        "agv_id": AGV_ID,
        "package_id": assignment.get("package_id"),
    }), 202


def _handle_shutdown(signum, frame) -> None:
    """On SIGTERM/SIGINT: notify backend immediately, then exit.

    Docker sends SIGTERM on 'docker stop'. Firing instantly means the browser
    gets an agv_health event in ~50ms instead of waiting for the health monitor.
    """
    sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
    print(f"[{AGV_ID}] {sig_name} received — notifying backend...", flush=True)
    send_agv_health(DEFAULT_HOST, AGV_ID, "stopped", "container shutting down")
    sys.exit(0)


signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"AGV {AGV_ID} Microservice")
    parser.add_argument(
        "--port", type=int, default=8100,
        help="Port to listen on (default: 8100)",
    )
    args = parser.parse_args()

    print(f"AGV {AGV_ID} microservice starting on port {args.port}", flush=True)
    print(f"Backend: {DEFAULT_HOST}", flush=True)
    app.run(host="0.0.0.0", port=args.port)
