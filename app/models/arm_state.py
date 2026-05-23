"""In-memory arm state with health tracking, initialized from pipeline.yaml.

## Backend selection

``INTERACT_STATE=inmemory``  (default) — in-process dicts, Lock-guarded.
``INTERACT_STATE=redis``     — Redis Hash per arm: key ``arm:<id>``.
  Health data stored in ``arm:health:<id>``.  ``get_all`` uses SCAN MATCH.

All public functions have byte-identical signatures in both modes.
"""

import json
import os
import threading
import time
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

_MODE_KEY = "INTERACT_STATE"


def _get_mode() -> str:
    return os.environ.get(_MODE_KEY, "inmemory").lower().strip()


# ── In-memory state ───────────────────────────────────────────────────────────
_lock = threading.Lock()
_state = generate_arm_state()

# Health status per arm: {arm_id: {"health": str, "last_seen": float, "error": str|None}}
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

# ── Redis backend ─────────────────────────────────────────────────────────────
_redis_client = None
_redis_client_lock = threading.Lock()
_REDIS_ARM_PREFIX = "arm:"
_REDIS_HEALTH_PREFIX = "arm:health:"


def _get_redis():
    global _redis_client
    if _redis_client is None:
        with _redis_client_lock:
            if _redis_client is None:
                import redis as redis_lib
                url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
                _redis_client = redis_lib.Redis.from_url(url, decode_responses=True)
    return _redis_client


def _inject_redis_client(client) -> None:
    """Test hook: inject a fake Redis client."""
    global _redis_client
    _redis_client = client


def _rkey(arm_id) -> str:
    return f"{_REDIS_ARM_PREFIX}{arm_id}"


def _hkey(arm_id) -> str:
    return f"{_REDIS_HEALTH_PREFIX}{arm_id}"


def _redis_encode_arm(rec: dict) -> dict:
    """Encode arm record fields for HSET."""
    out = {}
    for k, v in rec.items():
        if v is None:
            out[k] = "__none__"
        else:
            out[k] = str(v)
    return out


def _redis_decode_arm(raw: dict) -> dict:
    """Decode HGETALL result for an arm record."""
    rec = {}
    for k, v in raw.items():
        if v == "__none__":
            rec[k] = None
        else:
            rec[k] = v
    # angle must be float
    if "angle" in rec and rec["angle"] is not None:
        try:
            rec["angle"] = float(rec["angle"])
        except (ValueError, TypeError):
            rec["angle"] = float(DEFAULT_ANGLE)
    # arm_id must be int
    if "arm_id" in rec and rec["arm_id"] is not None:
        try:
            rec["arm_id"] = int(rec["arm_id"])
        except (ValueError, TypeError):
            pass
    return rec


def _redis_encode_health(h: dict) -> dict:
    out = {}
    for k, v in h.items():
        if v is None:
            out[k] = "__none__"
        else:
            out[k] = str(v)
    return out


def _redis_decode_health(raw: dict) -> dict:
    h = {}
    for k, v in raw.items():
        if v == "__none__":
            h[k] = None
        else:
            h[k] = v
    if "last_seen" in h:
        try:
            h["last_seen"] = float(h["last_seen"])
        except (ValueError, TypeError):
            h["last_seen"] = time.time()
    return h


def _redis_init_arm(arm_id, arm_rec: dict) -> None:
    """Write initial state + health into Redis for ``arm_id`` if absent."""
    r = _get_redis()
    key = _rkey(arm_id)
    if not r.exists(key):
        r.hset(key, mapping=_redis_encode_arm(arm_rec))
    hkey = _hkey(arm_id)
    if not r.exists(hkey):
        r.hset(hkey, mapping=_redis_encode_health({
            "health": "idle",
            "last_seen": time.time(),
            "error": None,
        }))


def _redis_ensure_initialized() -> None:
    """Ensure all arms from pipeline config exist in Redis."""
    for arm_id, arm_rec in generate_arm_state().items():
        _redis_init_arm(arm_id, arm_rec)


# ── Public API ────────────────────────────────────────────────────────────────

def set_angle(arm_id, angle):
    if _get_mode() == "redis":
        _redis_ensure_initialized()
        r = _get_redis()
        all_ids = [
            int(k[len(_REDIS_ARM_PREFIX):])
            for k in r.scan_iter(f"{_REDIS_ARM_PREFIX}[0-9]*")
            if ":" not in k[len(_REDIS_ARM_PREFIX):]
        ]
        if not all_ids:
            all_ids = get_arm_ids()
        if arm_id not in all_ids:
            valid = ", ".join(str(i) for i in sorted(all_ids))
            raise ValueError(f"Invalid arm_id: {arm_id}. Must be one of: {valid}")
    else:
        if arm_id not in _state:
            valid = ", ".join(str(i) for i in sorted(_state.keys()))
            raise ValueError(f"Invalid arm_id: {arm_id}. Must be one of: {valid}")

    if not isinstance(angle, (int, float)):
        raise ValueError(f"angle must be a number, got {type(angle).__name__}")
    angle = float(angle)
    if angle < MIN_ANGLE or angle > MAX_ANGLE:
        raise ValueError(f"angle {angle} out of range [{MIN_ANGLE}, {MAX_ANGLE}]")

    if _get_mode() == "redis":
        r = _get_redis()
        r.hset(_rkey(arm_id), mapping={"angle": str(angle)})
        # heartbeat
        r.hset(_hkey(arm_id), mapping={"last_seen": str(time.time())})
    else:
        with _lock:
            _state[arm_id]["angle"] = angle
            if arm_id in _health:
                _health[arm_id]["last_seen"] = time.time()
    return True


def get_state(arm_id):
    if _get_mode() == "redis":
        _redis_ensure_initialized()
        r = _get_redis()
        all_ids = get_arm_ids()
        if arm_id not in all_ids:
            valid = ", ".join(str(i) for i in sorted(all_ids))
            raise ValueError(f"Invalid arm_id: {arm_id}. Must be one of: {valid}")
        raw = r.hgetall(_rkey(arm_id))
        if not raw:
            raise ValueError(f"Invalid arm_id: {arm_id}. Must be one of: {', '.join(str(i) for i in sorted(all_ids))}")
        return _redis_decode_arm(raw)
    else:
        if arm_id not in _state:
            valid = ", ".join(str(i) for i in sorted(_state.keys()))
            raise ValueError(f"Invalid arm_id: {arm_id}. Must be one of: {valid}")
        with _lock:
            return dict(_state[arm_id])


def get_all():
    if _get_mode() == "redis":
        _redis_ensure_initialized()
        r = _get_redis()
        arms = []
        for key in r.scan_iter(f"{_REDIS_ARM_PREFIX}[0-9]*"):
            # Skip health keys (arm:health:N pattern uses a colon in the suffix)
            suffix = key[len(_REDIS_ARM_PREFIX):]
            if ":" in suffix:
                continue
            raw = r.hgetall(key)
            if raw:
                arms.append(_redis_decode_arm(raw))
        # Sort by arm_id for stable output
        arms.sort(key=lambda a: a.get("arm_id", 0))
        return {"arms": arms}
    else:
        with _lock:
            return {"arms": [dict(arm) for arm in _state.values()]}


# --- Health tracking ---

def set_health(arm_id, health, error=None):
    """Update health status for an arm. Health: alive, unhealthy, dead, stopped, idle."""
    if _get_mode() == "redis":
        _redis_ensure_initialized()
        r = _get_redis()
        r.hset(_hkey(arm_id), mapping=_redis_encode_health({
            "health": health,
            "last_seen": time.time(),
            "error": error,
        }))
    else:
        with _lock:
            if arm_id in _health:
                _health[arm_id]["health"] = health
                _health[arm_id]["last_seen"] = time.time()
                _health[arm_id]["error"] = error


def get_health(arm_id):
    if _get_mode() == "redis":
        _redis_ensure_initialized()
        r = _get_redis()
        raw = r.hgetall(_hkey(arm_id))
        if raw:
            return _redis_decode_health(raw)
        return {"health": "unknown", "last_seen": 0, "error": None}
    else:
        with _lock:
            if arm_id in _health:
                return dict(_health[arm_id])
            return {"health": "unknown", "last_seen": 0, "error": None}


def get_all_health():
    if _get_mode() == "redis":
        _redis_ensure_initialized()
        r = _get_redis()
        result = {}
        for key in r.scan_iter(f"{_REDIS_HEALTH_PREFIX}*"):
            arm_id_str = key[len(_REDIS_HEALTH_PREFIX):]
            try:
                arm_id = int(arm_id_str)
            except ValueError:
                arm_id = arm_id_str
            raw = r.hgetall(key)
            if raw:
                result[arm_id] = _redis_decode_health(raw)
        return result
    else:
        with _lock:
            return {arm_id: dict(h) for arm_id, h in _health.items()}


def mark_arm_stopped(arm_id, reason="manually stopped"):
    """Mark an arm and all its downstream dependents as stopped."""
    global _pipeline_stopped
    downstream = get_all_downstream(arm_id)
    critical = is_critical(arm_id)
    if _get_mode() == "redis":
        _redis_ensure_initialized()
        r = _get_redis()
        r.hset(_hkey(arm_id), mapping=_redis_encode_health({
            "health": "stopped",
            "last_seen": time.time(),
            "error": reason,
        }))
        for dep_id in downstream:
            r.hset(_hkey(dep_id), mapping=_redis_encode_health({
                "health": "stopped",
                "last_seen": time.time(),
                "error": f"upstream arm {arm_id} stopped",
            }))
        if critical:
            _pipeline_stopped = True
    else:
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
    """Mark arm as dead. Downstream arms are NOT cascade-stopped."""
    global _pipeline_stopped
    critical = is_critical(arm_id)
    if _get_mode() == "redis":
        _redis_ensure_initialized()
        r = _get_redis()
        r.hset(_hkey(arm_id), mapping=_redis_encode_health({
            "health": "dead",
            "last_seen": time.time(),
            "error": error_msg,
        }))
        if critical:
            _pipeline_stopped = True
    else:
        with _lock:
            if arm_id in _health:
                _health[arm_id]["health"] = "dead"
                _health[arm_id]["error"] = error_msg
            if critical:
                _pipeline_stopped = True


def is_pipeline_stopped():
    return _pipeline_stopped


def check_pipeline_recovery():
    """Clear pipeline-stopped flag if all critical arms are healthy again."""
    global _pipeline_stopped
    if not _pipeline_stopped:
        return
    critical_ids = [aid for aid in get_arm_ids() if is_critical(aid)]
    if _get_mode() == "redis":
        _redis_ensure_initialized()
        r = _get_redis()
        for aid in critical_ids:
            raw = r.hgetall(_hkey(aid))
            h = _redis_decode_health(raw) if raw else {}
            if h.get("health") in ("dead", "stopped", "draining"):
                return
        _pipeline_stopped = False
    else:
        with _lock:
            for aid in critical_ids:
                if _health.get(aid, {}).get("health") in ("dead", "stopped", "draining"):
                    return
            _pipeline_stopped = False


def is_arm_allowed(arm_id):
    """Check if this arm is allowed to execute (no upstream failures)."""
    h = get_health(arm_id)
    status = h.get("health", "idle")
    return status not in ("stopped", "dead")


def reset():
    """Reset all arms to default. Used in tests and pipeline restart."""
    global _pipeline_stopped
    if _get_mode() == "redis":
        _redis_ensure_initialized()
        r = _get_redis()
        for key in r.scan_iter(f"{_REDIS_ARM_PREFIX}[0-9]*"):
            suffix = key[len(_REDIS_ARM_PREFIX):]
            if ":" in suffix:
                continue
            r.hset(key, mapping={"angle": str(DEFAULT_ANGLE), "status": "idle"})
        for arm_id in get_arm_ids():
            r.hset(_hkey(arm_id), mapping=_redis_encode_health({
                "health": "idle",
                "last_seen": time.time(),
                "error": None,
            }))
        _pipeline_stopped = False
    else:
        with _lock:
            for arm_id in _state:
                _state[arm_id]["angle"] = DEFAULT_ANGLE
                _state[arm_id]["status"] = "idle"
        _pipeline_stopped = False
        _init_health()


def reinitialize():
    """Rebuild state from config. Called after pipeline.yaml changes."""
    global _state, _pipeline_stopped
    if _get_mode() == "redis":
        _pipeline_stopped = False
        # Force re-write all arms from config into Redis.
        r = _get_redis()
        for arm_id, arm_rec in generate_arm_state().items():
            r.hset(_rkey(arm_id), mapping=_redis_encode_arm(arm_rec))
            r.hset(_hkey(arm_id), mapping=_redis_encode_health({
                "health": "idle",
                "last_seen": time.time(),
                "error": None,
            }))
        _pipeline_stopped = False
    else:
        with _lock:
            _state = generate_arm_state()
        _pipeline_stopped = False
        _init_health()
