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
COORDINATION_MODE: str = os.environ.get("COORDINATION_MODE", "centralized").lower()


# ── US 34 capability env reader (mirror of app/models/agv_capabilities.from_env) ─
# Lives here because scripts/ cannot import from app/. Single source of truth
# is still pipeline.yaml — generate_compose.py propagates these env vars.
def _parse_labels_csv(raw):
    if not raw:
        return ["any"]
    parts = [p.strip().lower() for p in str(raw).split(",") if p.strip()]
    return parts or ["any"]


def _coerce_float(raw, fallback):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


CAPABILITIES = {
    "agv_id": AGV_ID,
    "kind": os.environ.get("AGV_KIND", "shuttle"),
    "weight_max_kg": _coerce_float(os.environ.get("AGV_WEIGHT_MAX_KG"), 50.0),
    "allowed_labels": _parse_labels_csv(os.environ.get("AGV_ALLOWED_LABELS")),
    "speed_factor": _coerce_float(os.environ.get("AGV_SPEED_FACTOR"), 1.0),
}


# Decentralized-family modes — both US 32 ('decentralized') and US 34 ('smart')
# run the contract-net bus protocol. Only the strict 'centralized' value
# bypasses it entirely.
def _mode_is_decentralized() -> bool:
    return COORDINATION_MODE in ("decentralized", "smart")


def _mode_is_smart() -> bool:
    return COORDINATION_MODE == "smart"


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
    """Current task status + assignment dict + US 34 capabilities."""
    return jsonify({
        "agv_id": AGV_ID,
        "task_status": _task_status,
        "current_task": _current_task,
        "capabilities": CAPABILITIES,
        "coordination_mode": COORDINATION_MODE,
    })


@app.route("/capabilities", methods=["GET"])
def capabilities():
    """US 34: return this AGV's capability declaration (read from env at boot)."""
    return jsonify(CAPABILITIES)


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


# ── Decentralized coordination hooks (used by agv_bus.BusListener) ───────────
def _is_busy() -> bool:
    """True if this AGV is currently executing a task. The bus listener uses
    this to decide whether to bid on incoming task_orphan / task_announce."""
    with _task_lock:
        return _task_status == "running"


def _current_state() -> dict:
    """Return a snapshot of (status, task_id, assignment) for heartbeat use.

    Once a task ends (status != running), task_id is reported as None and the
    assignment cleared — otherwise peers' fault detector would see the stale
    'running pkg-X' heartbeat and orphan a completed task.
    """
    with _task_lock:
        if _task_status == "running":
            return {
                "status": "running",
                "task_id": (_current_task or {}).get("package_id"),
                "assignment": dict(_current_task) if _current_task else {},
            }
        return {"status": _task_status, "task_id": None, "assignment": {}}


def _take_task(assignment: dict) -> None:
    """Called by the BusListener when this AGV has WON a claim (bid accepted).
    Behaves exactly like POST /assign internally — same code path."""
    global _current_task, _task_status
    with _task_lock:
        if _task_status == "running":
            print(f"[{AGV_ID}] _take_task ignored: already running", flush=True)
            return
        _current_task = dict(assignment)
        _task_status = "running"

    def _run() -> None:
        global _task_status
        try:
            ok = _brain.run_task(assignment)
            with _task_lock:
                _task_status = "done" if ok else "faulted"
        except Exception as exc:
            print(f"[{AGV_ID}] takeover exception: {exc}", file=sys.stderr)
            with _task_lock:
                _task_status = "faulted"
            send_agv_health(DEFAULT_HOST, AGV_ID, "faulted", str(exc))

    threading.Thread(target=_run, daemon=True).start()


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
    print(f"COORDINATION_MODE: {COORDINATION_MODE}", flush=True)

    # Start the decentralized bus listener (US 32 contract-net) for both
    # 'decentralized' (US 32) and 'smart' (US 34) modes — smart is a superset.
    # In 'centralized' mode the listener simply isn't started; fault handling
    # then falls back to central_brain.reassign on the backend.
    if _mode_is_decentralized():
        try:
            from agv_bus import BusListener
            BusListener(
                is_busy_fn=_is_busy,
                take_task_fn=_take_task,
                current_state_fn=_current_state,
            ).start()
            mode_tag = "smart (US 34)" if _mode_is_smart() else "decentralized (US 32)"
            print(f"[{AGV_ID}] {mode_tag} BusListener started", flush=True)
            print(f"[{AGV_ID}] capabilities: {CAPABILITIES}", flush=True)
        except Exception as exc:
            print(f"[{AGV_ID}] BusListener failed to start: {exc}",
                  file=sys.stderr)

    app.run(host="0.0.0.0", port=args.port)
