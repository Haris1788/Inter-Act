"""Runtime state registry for AGVs (Automated Guided Vehicles).

Deliberately mirrors ``app/models/asset_state.py`` (same RLock idiom, same
register/get/get_all/fail/recover/reset accessor shape) so the existing health
monitor, socket hydration, and REST patterns apply unchanged. The only
addition over asset_state is POSITION — an AGV is a mobile asset, so each
record carries a grid ``cell`` (and a ``target_cell`` while moving).

## Backend selection

``INTERACT_STATE=inmemory``  (default) — in-process dict, RLock-guarded.
``INTERACT_STATE=redis``     — Redis Hash per AGV: key ``agv:<id>``; list
  fields (``cell``, ``target_cell``, ``bay``) are JSON-encoded strings.
  ``get_all`` uses ``SCAN MATCH agv:*``.

All public functions have byte-identical signatures in both modes.

State machine per AGV:
    idle       — parked in its bay, nothing assigned
    moving     — travelling toward target_cell
    loading    — at the input station, waiting for / receiving a package
    unloading  — at the output station, handing the package off
    returning  — travelling back to its bay
    faulted    — controller/brain reported a fault (reassignment trigger)
    stopped    — terminal (container down / shut down)
"""

from __future__ import annotations

import json
import os
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

_MODE_KEY = "INTERACT_STATE"


def _get_mode() -> str:
    return os.environ.get(_MODE_KEY, "inmemory").lower().strip()


# ── In-memory backend ─────────────────────────────────────────────────────────
_lock = threading.RLock()
_state: Dict[str, dict] = {}

# ── Redis backend helpers ─────────────────────────────────────────────────────
_redis_client = None
_redis_client_lock = threading.Lock()
_REDIS_PREFIX = "agv:"


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
    """Test hook: inject a fake Redis client and clear the in-memory state."""
    global _redis_client
    _redis_client = client


def _rkey(agv_id: str) -> str:
    return f"{_REDIS_PREFIX}{agv_id}"


# Fields whose values are JSON-encoded lists (or null).
_LIST_FIELDS = ("cell", "target_cell", "bay")


def _redis_encode(rec: dict) -> dict:
    """Encode a record for HSET: list fields → JSON string; others → str."""
    out = {}
    for k, v in rec.items():
        if k in _LIST_FIELDS:
            out[k] = json.dumps(v)
        elif v is None:
            out[k] = "__none__"
        else:
            out[k] = str(v)
    return out


def _redis_decode(raw: dict) -> dict:
    """Decode a raw HGETALL result back to a Python dict with proper types."""
    rec: dict = {}
    for k, v in raw.items():
        if k in _LIST_FIELDS:
            decoded = json.loads(v)
            rec[k] = decoded  # list or null
        elif v == "__none__":
            rec[k] = None
        else:
            rec[k] = v
    # last_change must be float.
    if "last_change" in rec:
        try:
            rec["last_change"] = float(rec["last_change"])
        except (ValueError, TypeError):
            rec["last_change"] = time.time()
    return rec


# ── Public API ────────────────────────────────────────────────────────────────

def reset() -> None:
    """Clear all registered AGV state. Called at pipeline start."""
    if _get_mode() == "redis":
        r = _get_redis()
        keys = list(r.scan_iter(f"{_REDIS_PREFIX}*"))
        if keys:
            r.delete(*keys)
    else:
        with _lock:
            _state.clear()


def register(
    agv_id: str,
    label: Optional[str] = None,
    color: str = "#16a085",
    bay_cell: Optional[List[int]] = None,
) -> None:
    """Register an AGV. Idempotent. Starts parked in its bay, idle."""
    bay = list(bay_cell) if bay_cell else [0, 0]
    if _get_mode() == "redis":
        r = _get_redis()
        key = _rkey(agv_id)
        # Idempotent: only write if key does not exist.
        if not r.exists(key):
            rec = {
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
            r.hset(key, mapping=_redis_encode(rec))
    else:
        with _lock:
            if agv_id in _state:
                return
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


def _touch_rec(rec: dict) -> None:
    rec["last_change"] = time.time()


# ── Redis helpers for partial updates ─────────────────────────────────────────

def _redis_hset(agv_id: str, fields: dict) -> None:
    """Partial HSET: encode only the given fields and write them."""
    r = _get_redis()
    key = _rkey(agv_id)
    encoded = {}
    for k, v in fields.items():
        if k in _LIST_FIELDS:
            encoded[k] = json.dumps(v)
        elif v is None:
            encoded[k] = "__none__"
        else:
            encoded[k] = str(v)
    r.hset(key, mapping=encoded)


def _redis_get(agv_id: str) -> Optional[dict]:
    r = _get_redis()
    raw = r.hgetall(_rkey(agv_id))
    if not raw:
        return None
    return _redis_decode(raw)


# ── Mutators ──────────────────────────────────────────────────────────────────

def set_cell(agv_id: str, cell: List[int]) -> Optional[dict]:
    """Update the AGV's current grid cell. Returns the updated record."""
    if _get_mode() == "redis":
        ts = time.time()
        _redis_hset(agv_id, {"cell": list(cell), "last_change": ts})
        return _redis_get(agv_id)
    else:
        with _lock:
            rec = _state.get(agv_id)
            if rec is None:
                return None
            rec["cell"] = list(cell)
            _touch_rec(rec)
            return dict(rec)


def set_target(agv_id: str, cell: Optional[List[int]]) -> Optional[dict]:
    if _get_mode() == "redis":
        ts = time.time()
        _redis_hset(agv_id, {"target_cell": list(cell) if cell else None, "last_change": ts})
        return _redis_get(agv_id)
    else:
        with _lock:
            rec = _state.get(agv_id)
            if rec is None:
                return None
            rec["target_cell"] = list(cell) if cell else None
            _touch_rec(rec)
            return dict(rec)


def set_status(agv_id: str, status: str, error: Optional[str] = None) -> dict:
    if status not in VALID_STATUSES:
        raise ValueError(
            f"invalid status {status!r}; must be one of {VALID_STATUSES}"
        )
    if _get_mode() == "redis":
        if not _get_redis().exists(_rkey(agv_id)):
            raise KeyError(f"AGV {agv_id!r} not registered")
        ts = time.time()
        _redis_hset(agv_id, {"status": status, "error": error, "last_change": ts})
        return _redis_get(agv_id)
    else:
        with _lock:
            if agv_id not in _state:
                raise KeyError(f"AGV {agv_id!r} not registered")
            rec = _state[agv_id]
            rec["status"] = status
            rec["error"] = error
            _touch_rec(rec)
            return dict(rec)


def set_carried(agv_id: str, object_id: Optional[str]) -> None:
    if _get_mode() == "redis":
        _redis_hset(agv_id, {"carried_object": object_id, "last_change": time.time()})
    else:
        with _lock:
            rec = _state.get(agv_id)
            if rec is None:
                return
            rec["carried_object"] = object_id
            _touch_rec(rec)


# ── Accessors ─────────────────────────────────────────────────────────────────

def get(agv_id: str) -> Optional[dict]:
    if _get_mode() == "redis":
        return _redis_get(agv_id)
    with _lock:
        rec = _state.get(agv_id)
        return dict(rec) if rec else None


def get_all() -> Dict[str, dict]:
    if _get_mode() == "redis":
        r = _get_redis()
        result = {}
        for key in r.scan_iter(f"{_REDIS_PREFIX}*"):
            agv_id = key[len(_REDIS_PREFIX):]
            raw = r.hgetall(key)
            if raw:
                result[agv_id] = _redis_decode(raw)
        return result
    with _lock:
        return {k: dict(v) for k, v in _state.items()}


def get_agv_ids() -> List[str]:
    if _get_mode() == "redis":
        r = _get_redis()
        return [key[len(_REDIS_PREFIX):] for key in r.scan_iter(f"{_REDIS_PREFIX}*")]
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
    if _get_mode() == "redis":
        if not _get_redis().exists(_rkey(agv_id)):
            return None
    else:
        with _lock:
            if agv_id not in _state:
                return None
    return set_status(agv_id, "faulted", reason)


def recover(agv_id: str) -> Optional[dict]:
    """Clear a fault: faulted -> idle, parked back at its bay."""
    if _get_mode() == "redis":
        rec = _redis_get(agv_id)
        if rec is None:
            return None
        bay = rec.get("bay") or [0, 0]
        ts = time.time()
        _redis_hset(agv_id, {
            "cell": list(bay),
            "target_cell": None,
            "carried_object": None,
            "last_change": ts,
        })
        return set_status(agv_id, "idle", None)
    else:
        with _lock:
            rec = _state.get(agv_id)
            if rec is None:
                return None
            rec["cell"] = list(rec["bay"])
            rec["target_cell"] = None
            rec["carried_object"] = None
        return set_status(agv_id, "idle", None)


def mark_idle(agv_id: str) -> Optional[dict]:
    if _get_mode() == "redis":
        if not _get_redis().exists(_rkey(agv_id)):
            return None
    else:
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
