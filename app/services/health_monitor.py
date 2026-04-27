"""Background thread that pings arm containers and detects failures.

When an arm container stops responding (docker stop, crash, network),
this monitor marks it as dead and propagates to all downstream arms.
The demo-runner's ArmController threads check is_arm_allowed() every
2 seconds during wait_and_pick, so they react within 2-4 seconds.
"""

import time
import threading
import requests

from app.models import arm_state
from app.models import pipeline_config

_monitor_thread = None
_running = False


def _get_arm_url(arm_id):
    """Build the health check URL for an arm container.

    In Docker Compose, arm containers are named arm-{id} and listen on port 8000.
    Outside Docker, they're on localhost:800{id}.
    """
    import os
    # In Docker: http://arm-{id}:8000/health
    # Locally: http://localhost:800{id}/health
    docker_mode = os.environ.get('DEMO_RUNNER_HOST', '').startswith('http://demo-runner')
    if docker_mode:
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
            # Skip arms already marked as dead or stopped
            h = arm_state.get_health(arm_id)
            if h["health"] in ("dead", "stopped"):
                continue

            alive = _check_arm(arm_id, timeout)

            if alive:
                missed[arm_id] = 0
                # Only update to alive if it was previously unhealthy
                if h["health"] in ("unhealthy",):
                    arm_state.set_health(arm_id, "alive")
                elif h["health"] == "idle":
                    arm_state.set_health(arm_id, "alive")
            else:
                missed[arm_id] = missed.get(arm_id, 0) + 1

                if missed[arm_id] >= max_missed:
                    # Container is confirmed dead
                    print(f"[HealthMonitor] Arm {arm_id} container is DEAD "
                          f"({missed[arm_id]} missed checks)")
                    arm_state.mark_arm_failed(arm_id, "container stopped responding")
                    missed[arm_id] = 0  # reset counter
                elif missed[arm_id] >= 1:
                    arm_state.set_health(arm_id, "unhealthy",
                                        error=f"missed {missed[arm_id]} health checks")

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
