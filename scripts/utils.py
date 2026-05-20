"""
Shared utilities for arm control scripts.

Provides common functions for sending commands to the Flask API
and displaying results. Used by arm_1_script.py, arm_2_script.py,
and arm_3_script.py to avoid code duplication.
"""

import os
import sys
import time
import requests

DEFAULT_HOST = os.environ.get("SERVER_HOST", "http://localhost:5000")


def send_command(host, arm_id, angle):
    """Send a set_angle command to the Flask API.

    Args:
        host: Server URL (e.g., 'http://localhost:5000').
        arm_id: Integer arm identifier (1, 2, or 3).
        angle: Float target angle in degrees (0-180).

    Returns:
        Response dict on success, or None on failure.
    """
    url = f"{host}/set_angle"
    try:
        resp = requests.post(url, json={"arm_id": arm_id, "angle": angle}, timeout=5)
        data = resp.json()
        if resp.status_code == 200:
            return data
        else:
            print(f'ERROR: {data.get("error", "Unknown error")}', file=sys.stderr)
            return None
    except requests.exceptions.ConnectionError:
        print(
            f"ERROR: Cannot connect to {host}. Is the server running?", file=sys.stderr
        )
        return None
    except requests.exceptions.Timeout:
        print(f"ERROR: Request timed out.", file=sys.stderr)
        return None
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return None


def send_brain_task(host, arm_id, task, timeout=45):
    """Ask the backend's Qwen brain to decide and execute a task.

    Sends to POST /execute_task. The backend will:
      1. Get current arm state
      2. Ask the per-arm Qwen brain for a decision
      3. Move the arm to the brain-decided angle
      4. Return {status, action, target_angle, confidence, reasoning}

    Args:
        host: Server URL.
        arm_id: Integer arm identifier.
        task: Free-form task string (e.g. "pick object from intake").
        timeout: Seconds to wait. Must exceed Ollama inference + arm-move
                 time (default 45s leaves headroom on CPU).

    Returns:
        Result dict on success, or None on failure / non-success status.
    """
    url = f"{host}/execute_task"
    try:
        resp = requests.post(
            url, json={"arm_id": arm_id, "task": task}, timeout=timeout
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("status") == "success":
            return data
        print(
            f'ERROR (brain): {data.get("reason") or data.get("error") or data}',
            file=sys.stderr,
        )
        return None
    except requests.exceptions.Timeout:
        print(f"ERROR (brain): timeout after {timeout}s", file=sys.stderr)
        return None
    except Exception as e:
        print(f"ERROR (brain): {e}", file=sys.stderr)
        return None


def send_health(host, arm_id, health, error=None):
    """Report arm health to the backend."""
    try:
        requests.post(
            f"{host}/arms/{arm_id}/health",
            json={
                "health": health,
                "error": error,
            },
            timeout=5,
        )
    except Exception:
        pass


def send_stop_arm(host, arm_id, reason="manually stopped"):
    """Tell the backend to stop an arm and propagate to dependents."""
    try:
        requests.post(
            f"{host}/arms/{arm_id}/stop",
            json={
                "reason": reason,
            },
            timeout=5,
        )
    except Exception:
        pass


def is_arm_allowed_http(host, arm_id):
    """Check if an arm is allowed to execute (no upstream failures)."""
    try:
        resp = requests.get(f"{host}/arms/{arm_id}/allowed", timeout=5)
        if resp.status_code == 200:
            return resp.json().get("allowed", True)
    except Exception:
        pass
    return True  # fail-open if backend is unreachable


def send_log(host, source, message):
    """Send a log message to the backend for browser display.

    Args:
        host: Server URL.
        source: Log source (e.g. "arm_1", "system", "pipeline").
        message: Human-readable message.
    """
    try:
        requests.post(
            f"{host}/log",
            json={
                "source": source,
                "message": message,
            },
            timeout=5,
        )
    except Exception:
        pass  # Non-critical


def send_object_event(
    host,
    object_id,
    state,
    carrier_arm=None,
    carrier_asset=None,
    slot=None,
    start_time=None,
    duration_ms=None,
    event_time=None,
    owner_id=None,
    owner_type=None,
):
    """Notify the server about an object's position in the pipeline.

    Args:
        host: Server URL.
        object_id: Object identifier (e.g. "Obj1").
        state: "intake", "carried", "handover_1"..."handover_{N-1}", "output",
               or asset-mediated states like "on_<asset_id>" / "at_<asset_id>_exit".
        carrier_arm: Arm ID when state is "carried" by an arm.
        carrier_asset: Asset id when carrier is a vehicle (Phase 2).
        slot: Reserved for asset-internal slotting; currently unused.
    """
    payload = {
        "object_id": object_id,
        "state": state,
        "carrier_arm": carrier_arm,
    }
    if carrier_asset is not None:
        payload["carrier_asset"] = carrier_asset
    if slot is not None:
        payload["slot"] = slot
    if start_time is not None:
        payload["start_time"] = start_time
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    payload["event_time"] = int(
        event_time if event_time is not None else time.time() * 1000
    )
    if owner_id is not None:
        payload["owner_id"] = owner_id
    if owner_type is not None:
        payload["owner_type"] = owner_type
    try:
        requests.post(f"{host}/object_event", json=payload, timeout=5)
    except Exception:
        pass  # Non-critical - don't break the pipeline if event fails


def reset_objects(host):
    """Clear all tracked objects on the server (start of new demo run)."""
    try:
        requests.post(f"{host}/objects/reset", timeout=5)
    except Exception:
        pass


def poll_state(host, arm_id):
    """Get the current state of an arm from the server.

    Args:
        host: Server URL.
        arm_id: Integer arm identifier.

    Returns:
        Current angle as float, or None on failure.
    """
    try:
        resp = requests.get(f"{host}/state/{arm_id}", timeout=5)
        if resp.status_code == 200:
            return resp.json().get("angle")
    except Exception:
        pass
    return None


def print_result(arm_id, angle, step=None, total=None):
    """Print a formatted result line.

    Args:
        arm_id: Arm identifier.
        angle: Target angle.
        step: Current step number (optional, for sequences).
        total: Total steps (optional, for sequences).
    """
    if step is not None and total is not None:
        print(f"[Step {step}/{total}] Arm {arm_id} -> {angle:.1f} deg")
    else:
        print(f"Arm {arm_id} -> {angle:.1f} deg")


# ---------------------------------------------------------------------------
# AGV helpers (Phase 5).
# ---------------------------------------------------------------------------

def send_agv_move(host, agv_id, cell):
    """POST /agv/<id>/move to update AGV position on the backend."""
    try:
        resp = requests.post(
            f"{host}/agv/{agv_id}/move",
            json={"cell": list(cell)},
            timeout=5,
        )
        return resp.status_code == 200
    except Exception:
        return False


def send_agv_health(host, agv_id, health, error=None):
    """POST /agv/<id>/health to report AGV container health."""
    try:
        requests.post(
            f"{host}/agv/{agv_id}/health",
            json={"health": health, "error": error},
            timeout=5,
        )
    except Exception:
        pass


def is_agv_allowed_http(host, agv_id):
    """Return True if the AGV is in a non-faulted state (fail-open)."""
    try:
        resp = requests.get(f"{host}/agv/state/{agv_id}", timeout=5)
        if resp.status_code == 200:
            return resp.json().get("status") not in ("faulted", "stopped")
    except Exception:
        pass
    return True
