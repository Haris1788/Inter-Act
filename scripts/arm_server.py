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


# ── FDAS Step 3 groundwork: arm capability declaration ───────────────────────
# Arms declare what they can do via env vars, mirroring the AGV capability
# model, so they can become contract-net peers. NOTE: full arm-side bidding (an
# arm BusListener that bids on load/unload tasks) is NOT here yet — today the
# load/unload choreography is still driven backend-side by
# dispatch_routes._auto_arm_signals. This block only exposes the declaration.
def _parse_csv(raw, default):
    if not raw:
        return list(default)
    parts = [p.strip().lower() for p in str(raw).split(",") if p.strip()]
    return parts or list(default)


ARM_CAPABILITIES = {
    "arm_id": ARM_ID,
    "arm_type": os.environ.get("ARM_TYPE", "pick_place"),
    "actions": _parse_csv(os.environ.get("ARM_ACTIONS"), ["load", "unload"]),
    # Which station this arm serves: "input_arm" | "output_arm" | "" (either).
    "station": os.environ.get("ARM_STATION", ""),
}

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


@app.route("/capabilities", methods=["GET"])
def capabilities():
    """FDAS Step 3 groundwork: this arm's capability declaration (from env)."""
    return jsonify(ARM_CAPABILITIES)


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"Arm {ARM_ID} Microservice")
    parser.add_argument(
        "--port", type=int, default=8000, help="Port to listen on (default: 8000)"
    )
    args = parser.parse_args()

    print(f"Arm {ARM_ID} microservice starting on port {args.port}")
    print(f"Backend: {DEFAULT_HOST}")

    # FDAS Step 3: start the decentralized arm-peer brain when ARM_AUTONOMY=on.
    # Default OFF -> the arm stays a backend-driven actuator and the proven
    # load/unload path is completely unchanged. When on, the arm becomes a
    # decision-making peer that hears AGV approaches, decides locally, and
    # announces `arm_decision` on the shared bus (additive, non-conflicting).
    if os.environ.get("ARM_AUTONOMY", "off").lower() in ("on", "1", "true", "yes"):
        try:
            from arm_bus import ArmBusListener, ARM_STATION
            ArmBusListener().start()
            print(f"[Arm {ARM_ID}] ARM_AUTONOMY on — peer brain started "
                  f"(station={ARM_STATION})", flush=True)
        except Exception as exc:
            print(f"[Arm {ARM_ID}] ArmBusListener failed to start: {exc}",
                  file=sys.stderr)

    app.run(host="0.0.0.0", port=args.port)
