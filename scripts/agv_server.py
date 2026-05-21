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
import time
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


@app.route("/evaluate_task", methods=["POST"])
def evaluate_task():
    """Backend asks: 'can you do this task?' AGV checks locally and replies
    via the bus (TASK_AVAILABILITY). Returns 202 immediately."""
    from events import TASK_AVAILABILITY, make_envelope
    import bus_client

    data = request.get_json(silent=True) or {}
    task_id = data.get("taskId")
    task = data.get("task", {})
    if not task_id:
        return jsonify({"error": "missing taskId"}), 400

    def _evaluate_and_reply():
        with _task_lock:
            busy = _task_status in ("reserved", "running")
        required_stations = task.get("requiredStations") or []
        can_reach = all(s in ["input_arm", "output_arm"]
                        for s in required_stations)
        available = (not busy) and can_reach
        reason = ("busy" if busy
                  else "cannot reach required station" if not can_reach
                  else "ready")
        bus_client.publish(make_envelope(
            TASK_AVAILABILITY, AGV_ID,
            {"available": available, "score": 1.0 if available else 0.0,
             "reason": reason},
            task_id=task_id,
        ))

    threading.Thread(target=_evaluate_and_reply, daemon=True).start()
    return jsonify({"status": "accepted"}), 202


@app.route("/propose_path", methods=["POST"])
def propose_path():
    """Backend proposes a path; AGV publishes PATH_ACCEPTED or PATH_REJECTED."""
    from events import PATH_ACCEPTED, PATH_REJECTED, make_envelope
    import bus_client

    data = request.get_json(silent=True) or {}
    task_id = data.get("taskId")
    path = data.get("path", [])              # e.g. ["arm-1","agv-2","arm-2"]
    if not task_id:
        return jsonify({"error": "missing taskId"}), 400

    def _decide_and_reply():
        from events import ACTOR_BUSY  # locally imported so we only pay if needed
        global _task_status
        with _task_lock:
            busy = _task_status in ("reserved", "running")
        in_path = AGV_ID in path

        if in_path and not busy:
            # Mark BUSY *before* publishing accept so subsequent TASK_CREATEDs
            # arriving in the same tick see the busy flag.
            with _task_lock:
                _task_status = "reserved"
                _current_task = {"taskId": task_id, "path": path}
            bus_client.publish(make_envelope(
                PATH_ACCEPTED, AGV_ID,
                {"path": path, "reason": "free and in path"},
                task_id=task_id,
            ))
            bus_client.publish(make_envelope(
                ACTOR_BUSY, AGV_ID,
                {"path": path}, task_id=task_id,
            ))
        else:
            bus_client.publish(make_envelope(
                PATH_REJECTED, AGV_ID,
                {"path": path,
                 "reason": "busy" if busy else "not in proposed path"},
                task_id=task_id,
            ))

    threading.Thread(target=_decide_and_reply, daemon=True).start()
    return jsonify({"status": "deciding"}), 202


@app.route("/release", methods=["POST"])
def release():
    """Admin: clear busy flag. Step 9 will replace this with TASK_COMPLETED."""
    global _task_status, _current_task
    with _task_lock:
        _task_status = "idle"
        _current_task = None
    return jsonify({"status": "idle"})


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


# ── Phase 2: announce on the bus at startup (with retry until backend ready) ──
def _announce_self() -> None:
    """Publish ACTOR_ONLINE then CAPABILITY_DECLARED.

    Retries every 2s until backend's /bus/publish accepts the message — this
    covers the boot race where the AGV starts before backend's routes register.
    """
    import bus_client
    from events import (
        ACTOR_ONLINE, CAPABILITY_DECLARED, make_envelope,
    )

    online_env = make_envelope(ACTOR_ONLINE, AGV_ID, {"kind": "agv"})

    # Pull real config from backend (with one short retry).
    import requests
    grid = None
    for _ in range(5):
        try:
            r = requests.get(f"{DEFAULT_HOST}/grid", timeout=2.0)
            if r.ok:
                grid = r.json()
                break
        except Exception:
            pass
        time.sleep(2.0)

    wh = (grid or {}).get("warehouse", {})
    my_cfg = next((a for a in wh.get("agvs", []) if a.get("id") == AGV_ID), {})
    stations = wh.get("stations", {})
    # In this layout, lane connects every AGV bay to both input_arm and
    # output_arm. If the layout grows, derive reachability from the lane graph.
    reachable_stations = list(stations.keys())

    cap_env = make_envelope(CAPABILITY_DECLARED, AGV_ID, {
        "kind": "agv",
        "bay": my_cfg.get("bay"),
        "color": my_cfg.get("color"),
        "label": my_cfg.get("label", AGV_ID),
        "canReach": reachable_stations,
        "speed": float(os.environ.get("SPEED_FACTOR", "1.0")),
        "capacity": 1,
        "status": "FREE",
    })

    # Retry loop — gives up after ~60s but keeps the AGV alive either way.
    for attempt in range(30):
        if bus_client.publish(online_env):
            bus_client.publish(cap_env)
            print(f"[{AGV_ID}] announced on bus (attempt {attempt+1})", flush=True)
            return
        time.sleep(2.0)
    print(f"[{AGV_ID}] WARN: could not announce on bus after 30 attempts", flush=True)


# Run the announce in a daemon thread so Flask startup isn't blocked.
threading.Thread(target=_announce_self, daemon=True).start()


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
