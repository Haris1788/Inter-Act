"""Background thread that pings arm containers and detects failures.

When an arm container stops responding (docker stop, crash, network),
this monitor marks it as dead and propagates to all downstream arms.
The demo-runner's ArmController threads check is_arm_allowed() every
2 seconds during wait_and_pick, so they react within 2-4 seconds.

Health changes are broadcast to the browser via SocketIO so the
frontend can show real-time notifications and visual indicators.
"""

import os
import time
import threading
from datetime import datetime
import requests

from app.models import arm_state
from app.models import pipeline_config

_monitor_thread = None
_running = False

# Cache Docker mode once at import time (env var never changes at runtime)
_DOCKER_MODE = os.environ.get('DEMO_RUNNER_HOST', '').startswith('http://demo-runner')


def _get_arm_url(arm_id):
    """Build the health check URL for an arm container.

    In Docker Compose, arm containers are named arm-{id} and listen on port 8000.
    Outside Docker, they're on localhost:800{id}.
    """
    if _DOCKER_MODE:
        return f"http://arm-{arm_id}:8000/health"
    return f"http://localhost:{8000 + arm_id}/health"


def _check_arm(arm_id, timeout):
    """Ping an arm container. Returns True if alive, False if dead."""
    url = _get_arm_url(arm_id)
    try:
        resp = requests.get(url, timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def _emit_health(arm_id, health, error=None):
    """Broadcast arm health change to all connected browsers via SocketIO."""
    from app import socketio
    socketio.emit('arm_health', {
        "arm_id": arm_id,
        "health": health,
        "error": error,
    })


def _emit_log(source, message):
    """Broadcast a log message to the browser log panel via SocketIO."""
    from app import socketio
    timestamp = datetime.now().strftime('%H:%M:%S')
    socketio.emit('log_message', {
        'timestamp': timestamp,
        'source': source,
        'message': message,
    })


def _monitor_loop():
    """Continuously check arm container health."""
    health_cfg = pipeline_config.get_health_config()
    interval = health_cfg["check_interval"]
    timeout = health_cfg["timeout"]
    max_missed = health_cfg["max_missed"]

    # Track consecutive misses per arm
    missed = {}

    while _running:
        arm_ids = pipeline_config.get_arm_ids()

        for arm_id in arm_ids:
            h = arm_state.get_health(arm_id)
            current = h["health"]

            # ── Recovery path: dead/stopped/draining arms might have come back ──
            if current in ("dead", "stopped", "draining"):
                alive = _check_arm(arm_id, timeout)
                if alive:
                    # Recovery = container health + upstream availability.
                    # Downstream health is NOT checked — an arm is healthy
                    # if its container responds, even if the line can't flow.
                    # Line availability is a separate concern from arm health.
                    deps = pipeline_config.get_dependencies(arm_id)
                    all_deps_ok = all(
                        arm_state.get_health(d)["health"] in ("alive", "idle")
                        for d in deps
                    ) if deps else True

                    if all_deps_ok:
                        arm_state.set_health(arm_id, "alive")
                        arm_state.check_pipeline_recovery()
                        missed[arm_id] = 0
                        _emit_health(arm_id, "alive")
                        _emit_log("system", f"Arm {arm_id} recovered")
                        print(f"[HealthMonitor] Arm {arm_id} RECOVERED")
                continue  # skip normal failure detection for dead/stopped

            # ── Normal health check path ──
            alive = _check_arm(arm_id, timeout)

            if alive:
                missed[arm_id] = 0
                # Only update + notify on actual state transitions
                if current in ("unhealthy", "idle"):
                    arm_state.set_health(arm_id, "alive")
                    _emit_health(arm_id, "alive")
                    if current == "unhealthy":
                        _emit_log("system", f"Arm {arm_id} recovered")
            else:
                missed[arm_id] = missed.get(arm_id, 0) + 1

                if missed[arm_id] >= max_missed:
                    # Container is confirmed dead
                    print(f"[HealthMonitor] Arm {arm_id} container is DEAD "
                          f"({missed[arm_id]} missed checks)")
                    arm_state.mark_arm_failed(arm_id, "container stopped responding")
                    missed[arm_id] = 0

                    # Notify browser: this arm failed
                    _emit_health(arm_id, "dead", "container stopped responding")
                    _emit_log("pipeline",
                              f"Arm {arm_id} FAILED: container stopped responding")
                    # Downstream arms are NOT stopped immediately — they drain
                    # remaining products from handoff zones, then stop naturally

                elif missed[arm_id] >= 1:
                    error_msg = f"missed {missed[arm_id]} health check(s)"
                    arm_state.set_health(arm_id, "unhealthy", error=error_msg)
                    _emit_health(arm_id, "unhealthy", error_msg)

        time.sleep(interval)


def start():
    """Start the health monitor background thread."""
    global _monitor_thread, _running

    if _running:
        return

    _running = True
    _monitor_thread = threading.Thread(target=_monitor_loop, name="HealthMonitor", daemon=True)
    _monitor_thread.start()
    print("[HealthMonitor] Started")


def stop():
    """Stop the health monitor."""
    global _running
    _running = False
    print("[HealthMonitor] Stopped")    