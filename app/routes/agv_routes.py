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
"""

import threading
from datetime import datetime
from typing import Dict, Any

from flask import Blueprint, jsonify, request

from app import socketio
from app.models import agv_state, grid_state, pipeline_config

# ── Load/unload signal registry (transient, cleared per task) ─────────────────
# Maps agv_id -> {"load_ready": bool, "unload_ready": bool, "object_id": str|None}
_signals: Dict[str, Dict[str, Any]] = {}
_signals_lock = threading.Lock()


def _get_signal(agv_id: str) -> dict:
    with _signals_lock:
        return dict(_signals.get(
            agv_id,
            {"load_ready": False, "unload_ready": False, "object_id": None},
        ))


def _set_signal(agv_id: str, **kw) -> None:
    with _signals_lock:
        if agv_id not in _signals:
            _signals[agv_id] = {
                "load_ready": False, "unload_ready": False, "object_id": None,
            }
        _signals[agv_id].update(kw)

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


# ── Phase 5: load/unload handshake signal endpoints ───────────────────────────

@agv_bp.route("/agv/<agv_id>/load_ready", methods=["GET"])
def get_load_ready(agv_id):
    """AGV brain polls this to know when the input arm has placed the package."""
    sig = _get_signal(agv_id)
    return jsonify({
        "agv_id": agv_id,
        "ready": sig["load_ready"],
        "object_id": sig.get("object_id"),
    })


@agv_bp.route("/agv/<agv_id>/load_ready", methods=["POST"])
def signal_load_ready(agv_id):
    """Input-arm brain calls this when a package has been placed on the AGV.

    Body: {"object_id": "pkg-1"}
    """
    rec = agv_state.get(agv_id)
    if rec is None:
        return jsonify({"error": "unknown AGV", "code": "NOT_FOUND"}), 404

    data = request.get_json(silent=True) or {}
    object_id = data.get("object_id")
    _set_signal(agv_id, load_ready=True, object_id=object_id)
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
    """AGV brain clears the load signal after acknowledging the package."""
    _set_signal(agv_id, load_ready=False, object_id=None)
    return jsonify({"status": "ok"})


@agv_bp.route("/agv/<agv_id>/unload_ready", methods=["GET"])
def get_unload_ready(agv_id):
    """AGV brain polls this to know when the output arm is ready to receive."""
    sig = _get_signal(agv_id)
    return jsonify({"agv_id": agv_id, "ready": sig["unload_ready"]})


@agv_bp.route("/agv/<agv_id>/unload_ready", methods=["POST"])
def signal_unload_ready(agv_id):
    """Output-arm brain calls this when it is ready to receive the package."""
    if agv_state.get(agv_id) is None:
        return jsonify({"error": "unknown AGV", "code": "NOT_FOUND"}), 404

    _set_signal(agv_id, unload_ready=True)
    socketio.emit("log_message", {
        "timestamp": _ts(), "source": agv_id,
        "message": f"unload signal: output arm ready for {agv_id}",
    })
    return jsonify({"status": "ok"})


@agv_bp.route("/agv/<agv_id>/unload_clear", methods=["POST"])
def clear_unload_signal(agv_id):
    """AGV brain clears the unload signal and removes the carried object."""
    _set_signal(agv_id, unload_ready=False)
    agv_state.set_carried(agv_id, None)
    updated = agv_state.get(agv_id)
    if updated:
        _emit_agv(updated)
    return jsonify({"status": "ok"})
