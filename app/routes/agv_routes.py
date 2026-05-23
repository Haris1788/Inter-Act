"""
AGV control routes — REST API for the warehouse AGV layer (Phase 2 + 5).

GET  /grid                        — warehouse grid config + live occupancy
GET  /agv/state                   — runtime state of all AGVs
GET  /agv/state/<agv_id>          — runtime state of one AGV
POST /agv/<agv_id>/move           — move an AGV to a grid cell  {"cell": [c, r]}
POST /agv/<agv_id>/health         — report AGV health (container/brain heartbeat)

Phase 5 additions — lane exclusion:
POST /agv/lane/acquire            — block until shared lane free {"agv_id","timeout"}
POST /agv/lane/release            — release lane {"agv_id"}

Phase 5 additions — load/unload handshake signals:
GET  /agv/<agv_id>/load_ready     — AGV brain polls: is input arm ready?
POST /agv/<agv_id>/load_ready     — input arm signals: package placed on AGV
POST /agv/<agv_id>/load_clear     — AGV brain acknowledges load signal
GET  /agv/<agv_id>/unload_ready   — AGV brain polls: is output arm ready?
POST /agv/<agv_id>/unload_ready   — output arm signals: ready to receive
POST /agv/<agv_id>/unload_clear   — AGV brain acknowledges unload signal

Phase 4 Stage 3: handshake signals are now keyed by (task_id, agv_id) so
concurrent tasks cannot cross-pollinate their load/unload state. Public
functions accept task_id as an optional keyword arg (default "") for backward
compatibility with existing tests that use the old single-key form.

HTTP routes: the original routes were wired for agv_brain (Phase 3 removed it).
No live HTTP caller exists post-Phase 3. They are preserved as deprecated stubs
that pass task_id="" to the underlying signal helpers. They are safe to delete
in a Phase 5 cleanup if Phase 5's actor_runner requires a real HTTP handshake
interface — at that point the routes would need task_id in the path/body anyway.
"""

import os
import threading
from datetime import datetime
from typing import Dict, Any, Tuple

from flask import Blueprint, jsonify, request

from app import socketio
from app.models import agv_state, arm_state, grid_state, pipeline_config

# ── Backend mode ──────────────────────────────────────────────────────────────
_MODE_KEY = "INTERACT_STATE"


def _get_mode() -> str:
    return os.environ.get(_MODE_KEY, "inmemory").lower().strip()


# ── In-memory signal registry (inmemory backend) ─────────────────────────────
# Phase 4 Stage 3: keyed by (task_id, agv_id) instead of agv_id alone.
# Old callers (tests, backward-compat) use task_id="" → key ("", agv_id).
_signals: Dict[Tuple[str, str], Dict[str, Any]] = {}
_signals_lock = threading.Lock()

_SIGNALS_EMPTY: Dict[str, Any] = {"load_ready": False, "unload_ready": False, "object_id": None}

# ── Redis signal helpers ──────────────────────────────────────────────────────
# Redis Hash key: signals:<task_id>:<agv_id>
# Fields: load_ready ("true"/"false"), unload_ready ("true"/"false"), object_id (str or "__none__")

_signals_redis_client = None
_signals_redis_lock = threading.Lock()


def _get_signals_redis():
    global _signals_redis_client
    if _signals_redis_client is None:
        with _signals_redis_lock:
            if _signals_redis_client is None:
                import redis as redis_lib
                url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
                _signals_redis_client = redis_lib.Redis.from_url(url, decode_responses=True)
    return _signals_redis_client


def _inject_signals_redis_client(client) -> None:
    """Test hook: inject a fake Redis client for signals."""
    global _signals_redis_client
    _signals_redis_client = client


def _signals_rkey(task_id: str, agv_id: str) -> str:
    """Redis Hash key for a (task_id, agv_id) signal pair."""
    return f"signals:{task_id}:{agv_id}"


def _redis_get_signal(task_id: str, agv_id: str) -> dict:
    r = _get_signals_redis()
    raw = r.hgetall(_signals_rkey(task_id, agv_id))
    if not raw:
        return dict(_SIGNALS_EMPTY)
    load_ready = raw.get("load_ready", "false") == "true"
    unload_ready = raw.get("unload_ready", "false") == "true"
    obj = raw.get("object_id", "__none__")
    return {
        "load_ready": load_ready,
        "unload_ready": unload_ready,
        "object_id": None if obj == "__none__" else obj,
    }


def _redis_set_signal(task_id: str, agv_id: str, **kw) -> None:
    r = _get_signals_redis()
    key = _signals_rkey(task_id, agv_id)
    mapping = {}
    for field, value in kw.items():
        if field in ("load_ready", "unload_ready"):
            mapping[field] = "true" if value else "false"
        elif value is None:
            mapping[field] = "__none__"
        else:
            mapping[field] = str(value)
    r.hset(key, mapping=mapping)


# ── Backend-routed signal accessors ───────────────────────────────────────────

def _get_signal(agv_id: str, task_id: str = "") -> dict:
    if _get_mode() == "redis":
        return _redis_get_signal(task_id, agv_id)
    with _signals_lock:
        return dict(_signals.get((task_id, agv_id), _SIGNALS_EMPTY))


def _set_signal(agv_id: str, task_id: str = "", **kw) -> None:
    if _get_mode() == "redis":
        _redis_set_signal(task_id, agv_id, **kw)
        return
    with _signals_lock:
        key = (task_id, agv_id)
        if key not in _signals:
            _signals[key] = dict(_SIGNALS_EMPTY)
        _signals[key].update(kw)


# ── Public in-process handshake API (used by ArmAgent / AGVAgent) ─────────────
# Phase 4 Stage 3: task_id is an optional keyword arg (default "") so existing
# callers that use the old 1/2-arg signature continue to work unchanged.
# New callers pass task_id=<actual_task_id> to get per-task signal isolation.

def signal_load(agv_id: str, object_id: str, task_id: str = "") -> None:
    """Arm signals that the package has been placed on the AGV (load ready).

    task_id scopes the signal to a specific task so concurrent tasks cannot
    cross-pollinate their load state. Defaults to "" for backward compatibility.
    """
    _set_signal(agv_id, task_id=task_id, load_ready=True, object_id=object_id)
    agv_state.set_carried(agv_id, object_id)
    rec = agv_state.get(agv_id)
    if rec:
        _safe_emit_agv(rec)


def poll_load(agv_id: str, task_id: str = "") -> bool:
    """Return True when the load signal is set (package on AGV).

    task_id scopes the poll to the matching task. Defaults to "" (old callers).
    """
    return _get_signal(agv_id, task_id=task_id)["load_ready"]


def clear_load(agv_id: str, task_id: str = "") -> None:
    """AGV acknowledges the load signal — clears it."""
    _set_signal(agv_id, task_id=task_id, load_ready=False, object_id=None)


def signal_unload(agv_id: str, task_id: str = "") -> None:
    """Place arm signals that it is ready to receive the package."""
    _set_signal(agv_id, task_id=task_id, unload_ready=True)


def poll_unload(agv_id: str, task_id: str = "") -> bool:
    """Return True when the unload signal is set (arm ready to receive)."""
    return _get_signal(agv_id, task_id=task_id)["unload_ready"]


def clear_unload(agv_id: str, task_id: str = "") -> None:
    """AGV acknowledges the unload signal — clears it and removes carried object."""
    _set_signal(agv_id, task_id=task_id, unload_ready=False)
    agv_state.set_carried(agv_id, None)
    rec = agv_state.get(agv_id)
    if rec:
        _safe_emit_agv(rec)


def _safe_emit_agv(rec: dict) -> None:
    """Best-effort agv_update emit — never raises (safe outside Flask context)."""
    try:
        socketio.emit("agv_update", _agv_payload(rec))
    except Exception:
        pass

agv_bp = Blueprint("agv", __name__)


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _px(cell):
    """Cell -> pixel centre via the single source of truth."""
    return pipeline_config.cell_to_px(cell) if cell else None


def _agv_payload(rec: dict) -> dict:
    """Augment an agv_state record with pixel coords for the frontend."""
    return {
        **rec,
        "px": _px(rec.get("cell")),
        "target_px": _px(rec.get("target_cell")),
        "bay_px": _px(rec.get("bay")),
    }


def _emit_agv(rec: dict) -> None:
    socketio.emit("agv_update", _agv_payload(rec))


@agv_bp.route("/grid", methods=["GET"])
def get_grid():
    """Return the warehouse grid scene + live occupancy/lane holder.

    200: {"warehouse": {...}, "runtime": {"occupancy": {...},
          "lane_holder": "agv-1"|null}}  (warehouse is {} if not configured)
    """
    return jsonify({
        "warehouse": pipeline_config.get_warehouse_scene(),
        "runtime": grid_state.snapshot(),
    })


@agv_bp.route("/agv/state", methods=["GET"])
def get_all_agv_state():
    """Runtime state of every registered AGV (with pixel coords)."""
    allrecs = agv_state.get_all()
    return jsonify({"agvs": {k: _agv_payload(v) for k, v in allrecs.items()}})


@agv_bp.route("/agv/state/<agv_id>", methods=["GET"])
def get_one_agv_state(agv_id):
    rec = agv_state.get(agv_id)
    if rec is None:
        return jsonify({"error": "unknown AGV", "code": "NOT_FOUND"}), 404
    return jsonify(_agv_payload(rec))


@agv_bp.route("/agv/<agv_id>/move", methods=["POST"])
def move_agv(agv_id):
    """Move an AGV to a grid cell.

    Body: {"cell": [col, row]}  — must be inside the configured grid.
    Sets the AGV's cell + 'moving' status and broadcasts agv_update. The
    frontend eases the body between its previous and new pixel position.
    """
    rec = agv_state.get(agv_id)
    if rec is None:
        return jsonify({"error": "unknown AGV", "code": "NOT_FOUND"}), 404

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Request body must be JSON",
                        "code": "INVALID_BODY"}), 400
    cell = data.get("cell")
    if (not isinstance(cell, (list, tuple)) or len(cell) < 2
            or not all(isinstance(v, int) for v in cell[:2])):
        return jsonify({"error": "cell must be [col, row] integers",
                        "code": "VALIDATION_ERROR"}), 400
    if not pipeline_config.cell_in_grid(cell):
        return jsonify({"error": f"cell {list(cell)} outside grid bounds",
                        "code": "OUT_OF_BOUNDS"}), 400

    status = data.get("status", "moving")
    try:
        agv_state.set_target(agv_id, list(cell))
        agv_state.set_cell(agv_id, list(cell))
        rec = agv_state.set_status(agv_id, status)
    except (ValueError, KeyError) as e:
        return jsonify({"error": str(e), "code": "VALIDATION_ERROR"}), 400
    grid_state.set_cell(agv_id, list(cell))

    _emit_agv(rec)
    socketio.emit("log_message", {
        "timestamp": _ts(),
        "source": agv_id,
        "message": f"move -> cell {list(cell)} ({status})",
    })
    return jsonify({"status": "ok", "agv": _agv_payload(rec)})


@agv_bp.route("/agv/<agv_id>/health", methods=["POST"])
def set_agv_health(agv_id):
    """Report AGV health. Mirrors /arms/<id>/health.

    Body: {"health": "alive"|"faulted"|"stopped", "error": "..."}
    'faulted' is the reassignment trigger consumed in Phase 5.
    """
    rec = agv_state.get(agv_id)
    if rec is None:
        return jsonify({"error": "unknown AGV", "code": "NOT_FOUND"}), 404

    data = request.get_json(silent=True) or {}
    health = data.get("health", "alive")
    error = data.get("error")

    if health in ("faulted", "dead"):
        updated = agv_state.fail(agv_id, error or "AGV fault")
        grid_state.clear_cell(agv_id)
        grid_state.lane().release(agv_id)
        socketio.emit("agv_health", {"agv_id": agv_id, "health": "faulted",
                                     "error": error})
        socketio.emit("log_message", {
            "timestamp": _ts(), "source": agv_id, "level": "error",
            "message": f"AGV {agv_id} FAULTED: {error or 'unknown'}",
        })
    elif health == "stopped":
        updated = agv_state.set_status(agv_id, "stopped", error)
        socketio.emit("agv_health", {"agv_id": agv_id, "health": "stopped",
                                     "error": error})
    else:
        # Healthy heartbeat: clear a prior fault if present.
        updated = (agv_state.recover(agv_id)
                   if rec.get("status") == "faulted"
                   else agv_state.mark_idle(agv_id))
        socketio.emit("agv_health", {"agv_id": agv_id, "health": "alive",
                                     "error": None})

    if updated:
        _emit_agv(updated)
    return jsonify({"status": "ok", "agv": _agv_payload(updated or rec)})


# ── Station docking locks — one AGV at input/output arm at a time ─────────────

@agv_bp.route("/agv/station/acquire", methods=["POST"])
def acquire_station():
    """Block until an arm station is free, then grant it to agv_id.

    Body: {"agv_id": "agv-1", "station": "input_arm", "timeout": 60}
    Returns: {"status": "acquired"|"timeout"|"cancelled"}
    """
    data = request.get_json(silent=True) or {}
    agv_id = (data.get("agv_id") or "").strip()
    station = (data.get("station") or "").strip()
    timeout = float(data.get("timeout", 60.0))

    if not agv_id or not station:
        return jsonify({"status": "error", "error": "agv_id and station required"}), 400

    lock = grid_state.station_lock(station)

    def cancel_check() -> bool:
        return agv_state.is_alive(agv_id)

    ok = lock.acquire(agv_id, timeout=timeout, cancel_check=cancel_check)
    if ok:
        socketio.emit("log_message", {
            "timestamp": _ts(), "source": agv_id,
            "message": f"{agv_id} acquired station {station}",
        })
        socketio.emit("grid_update", grid_state.snapshot())
        return jsonify({"status": "acquired"})

    if not cancel_check():
        return jsonify({"status": "cancelled"})
    return jsonify({"status": "timeout"})


@agv_bp.route("/agv/station/release", methods=["POST"])
def release_station():
    """Release an arm station held by agv_id.

    Body: {"agv_id": "agv-1", "station": "input_arm"}
    Returns: {"status": "released"|"not_holder"}
    """
    data = request.get_json(silent=True) or {}
    agv_id = (data.get("agv_id") or "").strip()
    station = (data.get("station") or "").strip()
    if not agv_id or not station:
        return jsonify({"status": "error", "error": "agv_id and station required"}), 400

    released = grid_state.station_lock(station).release(agv_id)
    socketio.emit("log_message", {
        "timestamp": _ts(), "source": agv_id,
        "message": f"{agv_id} {'released' if released else 'tried to release (not holder)'} station {station}",
    })
    socketio.emit("grid_update", grid_state.snapshot())
    return jsonify({"status": "released" if released else "not_holder"})


# ── Phase 5: shared lane mutual-exclusion endpoints ───────────────────────────

@agv_bp.route("/agv/lane/acquire", methods=["POST"])
def acquire_lane():
    """Block until the shared travel lane is free, then grant it to agv_id.

    Body: {"agv_id": "agv-1", "timeout": 60}
    Returns: {"status": "acquired"|"timeout"|"cancelled"}

    This call blocks for up to `timeout` seconds — the AGV brain's requests
    session must set its own HTTP timeout to timeout+5 so it doesn't cut off
    before the acquire decision is made.
    """
    data = request.get_json(silent=True) or {}
    agv_id = (data.get("agv_id") or "").strip()
    timeout = float(data.get("timeout", 60.0))

    if not agv_id:
        return jsonify({"status": "error", "error": "agv_id required"}), 400

    lane = grid_state.lane()

    def cancel_check() -> bool:
        return agv_state.is_alive(agv_id)

    ok = lane.acquire(agv_id, timeout=timeout, cancel_check=cancel_check)

    if ok:
        socketio.emit("log_message", {
            "timestamp": _ts(), "source": agv_id,
            "message": f"{agv_id} acquired lane",
        })
        socketio.emit("grid_update", grid_state.snapshot())
        return jsonify({"status": "acquired"})

    if not cancel_check():
        return jsonify({"status": "cancelled"})
    return jsonify({"status": "timeout"})


@agv_bp.route("/agv/lane/release", methods=["POST"])
def release_lane():
    """Release the shared travel lane held by agv_id.

    Body: {"agv_id": "agv-1"}
    Returns: {"status": "released"|"not_holder"}
    """
    data = request.get_json(silent=True) or {}
    agv_id = (data.get("agv_id") or "").strip()
    if not agv_id:
        return jsonify({"status": "error", "error": "agv_id required"}), 400

    released = grid_state.lane().release(agv_id)
    socketio.emit("log_message", {
        "timestamp": _ts(), "source": agv_id,
        "message": f"{agv_id} {'released' if released else 'tried to release (not holder)'} lane",
    })
    socketio.emit("grid_update", grid_state.snapshot())
    return jsonify({"status": "released" if released else "not_holder"})


# ── Phase 5: load/unload handshake signal endpoints (DEPRECATED) ─────────────
# These HTTP routes were originally consumed by agv_brain (removed in Phase 3).
# No live HTTP caller exists post-Phase 3 cutover. They pass task_id="" to the
# underlying signal helpers — effectively the same as the pre-Stage-3 behaviour.
# Safe to delete in Phase 5 if actor_runner requires a real HTTP handshake
# interface (which would need task_id in path/body at that point).

@agv_bp.route("/agv/<agv_id>/load_ready", methods=["GET"])
def get_load_ready(agv_id):
    """DEPRECATED — AGV brain polls this to know when the input arm has placed the package.
    No caller post-Phase 3. Passes task_id="" (backward compat)."""
    sig = _get_signal(agv_id, task_id="")
    return jsonify({
        "agv_id": agv_id,
        "ready": sig["load_ready"],
        "object_id": sig.get("object_id"),
    })


@agv_bp.route("/agv/<agv_id>/load_ready", methods=["POST"])
def signal_load_ready(agv_id):
    """DEPRECATED — Input-arm brain calls this when a package has been placed on the AGV.
    No caller post-Phase 3. Passes task_id="" (backward compat).

    Body: {"object_id": "pkg-1"}
    """
    rec = agv_state.get(agv_id)
    if rec is None:
        return jsonify({"error": "unknown AGV", "code": "NOT_FOUND"}), 404

    data = request.get_json(silent=True) or {}
    object_id = data.get("object_id")
    _set_signal(agv_id, task_id="", load_ready=True, object_id=object_id)
    agv_state.set_carried(agv_id, object_id)

    updated = agv_state.get(agv_id)
    if updated:
        _emit_agv(updated)
    socketio.emit("log_message", {
        "timestamp": _ts(), "source": agv_id,
        "message": f"load signal: {object_id} placed on {agv_id}",
    })
    return jsonify({"status": "ok"})


@agv_bp.route("/agv/<agv_id>/load_clear", methods=["POST"])
def clear_load_signal(agv_id):
    """DEPRECATED — AGV brain clears the load signal after acknowledging the package.
    No caller post-Phase 3."""
    _set_signal(agv_id, task_id="", load_ready=False, object_id=None)
    return jsonify({"status": "ok"})


@agv_bp.route("/agv/<agv_id>/unload_ready", methods=["GET"])
def get_unload_ready(agv_id):
    """DEPRECATED — AGV brain polls this to know when the output arm is ready to receive.
    No caller post-Phase 3."""
    sig = _get_signal(agv_id, task_id="")
    return jsonify({"agv_id": agv_id, "ready": sig["unload_ready"]})


@agv_bp.route("/agv/<agv_id>/unload_ready", methods=["POST"])
def signal_unload_ready(agv_id):
    """DEPRECATED — Output-arm brain calls this when it is ready to receive the package.
    No caller post-Phase 3."""
    if agv_state.get(agv_id) is None:
        return jsonify({"error": "unknown AGV", "code": "NOT_FOUND"}), 404

    _set_signal(agv_id, task_id="", unload_ready=True)
    socketio.emit("log_message", {
        "timestamp": _ts(), "source": agv_id,
        "message": f"unload signal: output arm ready for {agv_id}",
    })
    return jsonify({"status": "ok"})


@agv_bp.route("/agv/<agv_id>/unload_clear", methods=["POST"])
def clear_unload_signal(agv_id):
    """DEPRECATED — AGV brain clears the unload signal and removes the carried object.
    No caller post-Phase 3."""
    _set_signal(agv_id, task_id="", unload_ready=False)
    agv_state.set_carried(agv_id, None)
    updated = agv_state.get(agv_id)
    if updated:
        _emit_agv(updated)
    return jsonify({"status": "ok"})
