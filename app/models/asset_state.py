"""Runtime state registry for non-arm assets (vehicle, conveyor).

Mirrors `arm_state.py` for the rest of the asset graph. Arms continue to use
their existing health model — this module only tracks vehicles and conveyors
so the frontend, REST routes, and demo runner share one source of truth.

State machine per asset:
    idle    — registered, nothing in flight
    busy    — processing an object
    failed  — controller has been told to fail; downstream sees this
    recovering — fail flag cleared, waiting for next cycle
    stopped — drain-stopped after upstream completion (terminal)

The registry is process-local, lock-guarded, and reset between demo runs.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional

VALID_STATUSES = ("idle", "busy", "draining", "failed", "recovering", "stopped")

_lock = threading.RLock()
_state: Dict[str, dict] = {}


def reset() -> None:
    """Clear all registered runtime state. Called at pipeline start."""
    with _lock:
        _state.clear()


def register(asset_id: str, asset_type: str, label: str | None = None) -> None:
    """Register an asset in the runtime registry. Idempotent."""
    with _lock:
        if asset_id in _state:
            return
        _state[asset_id] = {
            "id": asset_id,
            "type": asset_type,
            "label": label or asset_id,
            "status": "idle",
            "error": None,
            "carried_object": None,
            "last_change": time.time(),
        }


def set_status(asset_id: str, status: str, error: Optional[str] = None) -> dict:
    """Update an asset's status. Returns the updated record."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}; must be one of {VALID_STATUSES}")
    with _lock:
        if asset_id not in _state:
            raise KeyError(f"asset {asset_id!r} not registered")
        rec = _state[asset_id]
        rec["status"] = status
        rec["error"] = error
        rec["last_change"] = time.time()
        return dict(rec)


def set_carried(asset_id: str, object_id: Optional[str]) -> None:
    """Record which object an asset currently holds (or None when empty)."""
    with _lock:
        if asset_id not in _state:
            return
        _state[asset_id]["carried_object"] = object_id
        _state[asset_id]["last_change"] = time.time()


def get(asset_id: str) -> Optional[dict]:
    """Return a snapshot dict for an asset, or None if unknown."""
    with _lock:
        rec = _state.get(asset_id)
        return dict(rec) if rec else None


def get_all() -> Dict[str, dict]:
    """Return snapshots of every registered asset, keyed by id."""
    with _lock:
        return {k: dict(v) for k, v in _state.items()}


def is_alive(asset_id: str) -> bool:
    """True when asset is in a state that can still process objects."""
    rec = get(asset_id)
    if rec is None:
        return True   # unknown id treated as transparent (back-compat with arms-only)
    return rec["status"] not in ("draining", "failed", "stopped")


def fail(asset_id: str, reason: str) -> Optional[dict]:
    """Mark asset as failed. Returns updated record (or None if unknown)."""
    with _lock:
        if asset_id not in _state:
            return None
    return set_status(asset_id, "failed", reason)


def recover(asset_id: str) -> Optional[dict]:
    """Clear failure flag, transition to recovering then idle on next cycle."""
    with _lock:
        if asset_id not in _state:
            return None
    return set_status(asset_id, "recovering", None)


def mark_idle(asset_id: str) -> Optional[dict]:
    """Transition recovering/busy back to idle."""
    with _lock:
        if asset_id not in _state:
            return None
    return set_status(asset_id, "idle", None)
