"""
Arm Microservice - lightweight Flask wrapper that makes each arm a deployable service.

Each container runs one instance with ARM_ID set via environment variable.
The service receives commands from external callers and forwards them
to the central backend via HTTP.

Usage:
    ARM_ID=1 SERVER_HOST=http://backend:5000 python scripts/arm_server.py
    ARM_ID=2 python scripts/arm_server.py --port 8002
"""

import os
import sys
import signal
import time
import argparse
import threading

from flask import Flask, request, jsonify

# Add scripts dir to path for utils import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import send_command, send_health, DEFAULT_HOST

ARM_ID = int(os.environ.get("ARM_ID", "1"))

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    """Liveness check - proves this arm container is running."""
    return jsonify(
        {
            "status": "ok",
            "arm_id": ARM_ID,
            "server_host": DEFAULT_HOST,
        }
    )


@app.route("/move", methods=["POST"])
def move():
    """Move this arm to a target angle.

    Body: {"angle": 45.0}
    Forwards to backend: POST /set_angle {arm_id: ARM_ID, angle: 45.0}
    """
    data = request.get_json(silent=True)
    if not data or "angle" not in data:
        return jsonify({"error": "Missing 'angle' in request body"}), 400

    result = send_command(DEFAULT_HOST, ARM_ID, data["angle"])
    if result:
        return jsonify({"status": "ok", "arm_id": ARM_ID, "angle": data["angle"]})
    return jsonify({"error": "Failed to send command to backend"}), 502


@app.route("/sequence", methods=["POST"])
def sequence():
    """Run a sequence of angles in a background thread.

    Body: {"angles": [0, 90, 180, 90], "delay": 1.5}
    Returns immediately while the sequence executes.
    """
    data = request.get_json(silent=True)
    if not data or "angles" not in data:
        return jsonify({"error": "Missing 'angles' in request body"}), 400

    angles = data["angles"]
    delay = data.get("delay", 1.5)

    def run_sequence():
        for angle in angles:
            send_command(DEFAULT_HOST, ARM_ID, angle)
            time.sleep(delay)

    thread = threading.Thread(target=run_sequence, daemon=True)
    thread.start()

    return jsonify(
        {
            "status": "started",
            "arm_id": ARM_ID,
            "steps": len(angles),
        }
    )


def _handle_shutdown(signum, frame):
    """Immediately notify backend that this arm is shutting down, then exit.

    Docker sends SIGTERM on 'docker stop'. This handler fires instantly,
    so the browser gets a notification in ~50ms instead of waiting 6 seconds
    for the health monitor to detect missed pings.
    """
    sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
    print(f"[Arm {ARM_ID}] {sig_name} received - notifying backend...")
    send_health(DEFAULT_HOST, ARM_ID, "dead", "container shutting down")
    sys.exit(0)


# Install handlers at module level so they're active as soon as the process starts
signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)


def _announce_self() -> None:
    """Publish ACTOR_ONLINE then CAPABILITY_DECLARED with retry."""
    import bus_client
    from events import (
        ACTOR_ONLINE, CAPABILITY_DECLARED, make_envelope,
    )

    actor_id = f"arm-{ARM_ID}"
    online_env = make_envelope(ACTOR_ONLINE, actor_id, {"kind": "arm"})
    cap_env = make_envelope(CAPABILITY_DECLARED, actor_id, {
        "kind": "arm",
        "role": os.environ.get("ARM_ROLE", "generic"),
        "reach_radius_deg": 180,
        "status": "FREE",
    })

    for attempt in range(30):
        if bus_client.publish(online_env):
            bus_client.publish(cap_env)
            print(f"[arm-{ARM_ID}] announced on bus (attempt {attempt+1})", flush=True)
            return
        time.sleep(2.0)
    print(f"[arm-{ARM_ID}] WARN: could not announce on bus after 30 attempts", flush=True)


threading.Thread(target=_announce_self, daemon=True).start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"Arm {ARM_ID} Microservice")
    parser.add_argument(
        "--port", type=int, default=8000, help="Port to listen on (default: 8000)"
    )
    args = parser.parse_args()

    print(f"Arm {ARM_ID} microservice starting on port {args.port}")
    print(f"Backend: {DEFAULT_HOST}")
    app.run(host="0.0.0.0", port=args.port)
