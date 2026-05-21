"""In-memory arm state with health tracking, initialized from pipeline.yaml."""

import time
import threading
from app.models.pipeline_config import (
    generate_arm_state,
    get_arm_count,
    get_arm_ids,
    get_all_downstream,
    is_critical,
    MIN_ANGLE,
    MAX_ANGLE,
    DEFAULT_ANGLE,
)

_lock = threading.Lock()
_state = generate_arm_state()

# Health status per arm: {arm_id: {"health": "alive|unhealthy|dead|stopped", "last_seen": timestamp, "error": str|None}}
_health = {}

# Pipeline-wide stop flag - set when a critical arm fails
_pipeline_stopped = False


def _init_health():
    global _health
    _health = {
        arm_id: {"health": "idle", "last_seen": time.time(), "error": None}
        for arm_id in _state
    }


_init_health()


def set_angle(arm_id, angle):
    if arm_id not in _state:
        valid = ", ".join(str(i) for i in sorted(_state.keys()))
        raise ValueError(f"Invalid arm_id: {arm_id}. Must be one of: {valid}")
    if not isinstance(angle, (int, float)):
        raise ValueError(f"angle must be a number, got {type(angle).__name__}")
    angle = float(angle)
    if angle < MIN_ANGLE or angle > MAX_ANGLE:
        raise ValueError(f"angle {angle} out of range [{MIN_ANGLE}, {MAX_ANGLE}]")

    with _lock:
        _state[arm_id]["angle"] = angle
        # Any successful command counts as a heartbeat
        if arm_id in _health:
            _health[arm_id]["last_seen"] = time.time()
    return True


def get_state(arm_id):
    if arm_id not in _state:
        valid = ", ".join(str(i) for i in sorted(_state.keys()))
        raise ValueError(f"Invalid arm_id: {arm_id}. Must be one of: {valid}")
    with _lock:
        return dict(_state[arm_id])


def get_all():
    with _lock:
        return {"arms": [dict(arm) for arm in _state.values()]}


# --- Health tracking ---


def set_health(arm_id, health, error=None):
    """Update health status for an arm. Health: alive, unhealthy, dead, stopped, idle."""
    with _lock:
        if arm_id in _health:
            _health[arm_id]["health"] = health
            _health[arm_id]["last_seen"] = time.time()
            _health[arm_id]["error"] = error


def get_health(arm_id):
    with _lock:
        if arm_id in _health:
            return dict(_health[arm_id])
        return {"health": "unknown", "last_seen": 0, "error": None}


def get_all_health():
    with _lock:
        return {arm_id: dict(h) for arm_id, h in _health.items()}


def mark_arm_stopped(arm_id, reason="manually stopped"):
    """Mark an arm and all its downstream dependents as stopped."""
    global _pipeline_stopped
    # Compute graph traversal outside lock to avoid blocking set_angle
    downstream = get_all_downstream(arm_id)
    critical = is_critical(arm_id)
    with _lock:
        if arm_id in _health:
            _health[arm_id]["health"] = "stopped"
            _health[arm_id]["error"] = reason
        for dep_id in downstream:
            if dep_id in _health:
                _health[dep_id]["health"] = "stopped"
                _health[dep_id]["error"] = f"upstream arm {arm_id} stopped"
        if critical:
            _pipeline_stopped = True


def mark_arm_failed(arm_id, error_msg):
    """Mark arm as dead. Downstream arms are NOT cascade-stopped.

    Downstream arms drain remaining products from handover zones naturally.
    The demo runner detects upstream death and stops each arm individually
    once its input handover zone is empty.
    """
    global _pipeline_stopped
    critical = is_critical(arm_id)
    with _lock:
        if arm_id in _health:
            _health[arm_id]["health"] = "dead"
            _health[arm_id]["error"] = error_msg
        if critical:
            _pipeline_stopped = True


def is_pipeline_stopped():
    return _pipeline_stopped


def check_pipeline_recovery():
    """Clear pipeline-stopped flag if all critical arms are healthy again.

    A critical arm in 'draining' state still counts as not-recovered:
    the pipeline is winding down, not ready for new work.
    """
    global _pipeline_stopped
    if not _pipeline_stopped:
        return
    # is_critical reads from config (no lock needed for config)
    critical_ids = [aid for aid in _state if is_critical(aid)]
    with _lock:
        for aid in critical_ids:
            if _health.get(aid, {}).get("health") in ("dead", "stopped", "draining"):
                return  # still have a degraded critical arm
        _pipeline_stopped = False


def is_arm_allowed(arm_id):
    """Check if this arm is allowed to execute (no upstream failures)."""
    with _lock:
        h = _health.get(arm_id, {})
        status = h.get("health", "idle")
        return status not in ("stopped", "dead")


def reset():
    """Reset all arms to default. Used in tests and pipeline restart."""
    global _pipeline_stopped
    with _lock:
        for arm_id in _state:
            _state[arm_id]["angle"] = DEFAULT_ANGLE
            _state[arm_id]["status"] = "idle"
    _pipeline_stopped = False
    _init_health()


def reinitialize():
    """Rebuild state from config. Called after pipeline.yaml changes."""
    global _state, _pipeline_stopped
    with _lock:
        _state = generate_arm_state()
    _pipeline_stopped = False
    _init_health()
