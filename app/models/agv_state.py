"""Runtime state registry for AGVs (Automated Guided Vehicles).

Deliberately mirrors `app/models/asset_state.py` (same RLock idiom, same
register/get/get_all/fail/recover/reset accessor shape) so the existing health
monitor, socket hydration, and REST patterns apply unchanged. The only
addition over asset_state is POSITION — an AGV is a mobile asset, so each
record carries a grid `cell` (and a `target_cell` while moving).

State machine per AGV:
    idle       — parked in its bay, nothing assigned
    moving     — travelling toward target_cell
    loading    — at the input station, waiting for / receiving a package
    unloading  — at the output station, handing the package off
    returning  — travelling back to its bay
    faulted    — controller/brain reported a fault (reassignment trigger)
    stopped    — terminal (container down / shut down)

Process-local, lock-guarded, reset between runs — exactly like asset_state.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

VALID_STATUSES = (
    "idle",
    "moving",
    "loading",
    "unloading",
    "returning",
    "faulted",
    "stopped",
)

_lock = threading.RLock()
_state: Dict[str, dict] = {}


def reset() -> None:
    """Clear all registered AGV state. Called at pipeline start."""
    with _lock:
        _state.clear()


def register(
    agv_id: str,
    label: Optional[str] = None,
    color: str = "#16a085",
    bay_cell: Optional[List[int]] = None,
) -> None:
    """Register an AGV. Idempotent. Starts parked in its bay, idle."""
    with _lock:
        if agv_id in _state:
            return
        bay = list(bay_cell) if bay_cell else [0, 0]
        _state[agv_id] = {
            "id": agv_id,
            "label": label or agv_id,
            "color": color,
            "status": "idle",
            "error": None,
            "bay": bay,
            "cell": list(bay),
            "target_cell": None,
            "carried_object": None,
            "last_change": time.time(),
        }


def _touch(rec: dict) -> None:
    rec["last_change"] = time.time()


def set_cell(agv_id: str, cell: List[int]) -> Optional[dict]:
    """Update the AGV's current grid cell. Returns the updated record."""
    with _lock:
        rec = _state.get(agv_id)
        if rec is None:
            return None
        rec["cell"] = list(cell)
        _touch(rec)
        return dict(rec)


def set_target(agv_id: str, cell: Optional[List[int]]) -> Optional[dict]:
    with _lock:
        rec = _state.get(agv_id)
        if rec is None:
            return None
        rec["target_cell"] = list(cell) if cell else None
        _touch(rec)
        return dict(rec)


def set_status(agv_id: str, status: str, error: Optional[str] = None) -> dict:
    if status not in VALID_STATUSES:
        raise ValueError(
            f"invalid status {status!r}; must be one of {VALID_STATUSES}"
        )
    with _lock:
        if agv_id not in _state:
            raise KeyError(f"AGV {agv_id!r} not registered")
        rec = _state[agv_id]
        rec["status"] = status
        rec["error"] = error
        _touch(rec)
        return dict(rec)


def set_carried(agv_id: str, object_id: Optional[str]) -> None:
    with _lock:
        rec = _state.get(agv_id)
        if rec is None:
            return
        rec["carried_object"] = object_id
        _touch(rec)


def get(agv_id: str) -> Optional[dict]:
    with _lock:
        rec = _state.get(agv_id)
        return dict(rec) if rec else None


def get_all() -> Dict[str, dict]:
    with _lock:
        return {k: dict(v) for k, v in _state.items()}


def get_agv_ids() -> List[str]:
    with _lock:
        return list(_state.keys())


def is_alive(agv_id: str) -> bool:
    """True when the AGV can still take/continue work."""
    rec = get(agv_id)
    if rec is None:
        return False
    return rec["status"] not in ("faulted", "stopped")


def fail(agv_id: str, reason: str) -> Optional[dict]:
    """Mark an AGV faulted (the reassignment trigger). Returns the record."""
    with _lock:
        if agv_id not in _state:
            return None
    return set_status(agv_id, "faulted", reason)


def recover(agv_id: str) -> Optional[dict]:
    """Clear a fault: faulted -> idle, parked back at its bay."""
    with _lock:
        rec = _state.get(agv_id)
        if rec is None:
            return None
        rec["cell"] = list(rec["bay"])
        rec["target_cell"] = None
        rec["carried_object"] = None
    return set_status(agv_id, "idle", None)


def mark_idle(agv_id: str) -> Optional[dict]:
    with _lock:
        if agv_id not in _state:
            return None
    return set_status(agv_id, "idle", None)


def initialize_from_config() -> None:
    """(Re)register every AGV from pipeline_config. Idempotent + safe to
    call at startup and after a config reload."""
    from app.models import pipeline_config

    for a in pipeline_config.get_agvs():
        register(
            a["id"],
            a.get("label"),
            a.get("color", "#16a085"),
            list(a.get("bay", [0, 0])),
        )
