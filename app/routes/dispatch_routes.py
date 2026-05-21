"""
POST /demo/dispatch — Direct AGV workflow dispatch (no AI planning required).

Parses a natural-language goal for package count, assigns packages to idle AGVs,
and auto-fires the arm load/unload signals so the full bay→input→lane→output→bay
cycle runs visually even without real arm containers.

This is the wiring behind the frontend "Dispatch" button for demo purposes.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from typing import List

import requests
from flask import Blueprint, jsonify, request

from app import socketio
from app.models import agv_state, pipeline_config

dispatch_bp = Blueprint("dispatch", __name__)

# Map of AGV container ports (host-side) so the backend can reach them.
# Container-to-container: agv-1 listens on port 8100 inside docker network.
_AGV_CONTAINER_URLS = {
    "agv-1": "http://agv-1:8100",
    "agv-2": "http://agv-2:8100",
    "agv-3": "http://agv-3:8100",
}


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _log(source: str, msg: str) -> None:
    socketio.emit("log_message", {
        "timestamp": _ts(),
        "source": source,
        "message": msg,
    })


def _animate_arm(arm_id: int, angles: list, delay: float = 0.25) -> None:
    """Emit arm_angle_update events in sequence to animate the arm sweeping."""
    for angle in angles:
        socketio.emit("arm_angle_update", {"arm_id": arm_id, "angle": angle})
        time.sleep(delay)


def _emit_table(station: str, packages: list) -> None:
    """Tell the frontend which packages are visible on an arm's table."""
    socketio.emit("table_update", {"station": station, "packages": packages})


def _arm_ai_confirm(arm_id: int, agv_id: str, action: str) -> bool:
    """The arm's tactical brain (qwen2.5:14b) decides whether to perform the
    load/unload NOW, coordinating with the AGV's live status.

    This is the arm<->AGV AI handshake. Returns True to proceed. Deterministic
    fallback (planner running / AI error / unknown reply) -> proceed, so the
    choreography never hangs on the model.
    """
    try:
        from app.services.llm_service import get_llm, planning_active
        if planning_active():
            return True  # qwq:32b planning — don't stall the arm
        rec = agv_state.get(agv_id) or {}
        system = (
            f"You are the tactical brain of arm-{arm_id}, the {action} arm. "
            f"Respond with ONLY JSON {{\"script\":\"<name>\"}} where <name> is "
            f'"go" (perform the {action} now — the AGV is docked and ready) '
            f'or "wait" (not safe yet).'
        )
        user = (
            f"AGV {agv_id}: status={rec.get('status')} "
            f"cell={rec.get('cell')} carrying={rec.get('carried_object')}. "
            f"Should arm-{arm_id} {action} now?"
        )
        res = get_llm().tactical(system, user, num_predict=24)
        choice = (res.get("script") or res.get("value") or "").strip().lower()
        return choice != "wait"  # 'go' / unknown / error -> proceed
    except Exception:
        return True


def _arm_ai_gate(arm_id: int, agv_id: str, action: str, tries: int = 6) -> None:
    """Poll the arm tactical AI a few times; proceed on 'go' or after the
    bounded wait (deterministic fallback — never deadlock the demo)."""
    for _ in range(tries):
        if _arm_ai_confirm(arm_id, agv_id, action):
            return
        _log(f"{action}-arm", f"arm-{arm_id} AI: holding {action} for {agv_id}")
        time.sleep(2.5)


def _parse_package_count(text: str) -> int:
    """Extract a number from free-form goal text; default 1."""
    nums = re.findall(r"\b(\d+)\b", text)
    for n in nums:
        v = int(n)
        if 1 <= v <= 10:
            return v
    return 1


def _idle_agvs() -> List[str]:
    """Return IDs of AGVs currently in idle state."""
    return [
        agv_id
        for agv_id, rec in agv_state.get_all().items()
        if rec.get("status") == "idle"
    ]


def _assign_agv(agv_id: str, package_id: str, label: str) -> bool:
    """POST assignment to the AGV container's /assign endpoint."""
    url = _AGV_CONTAINER_URLS.get(agv_id)
    if not url:
        return False
    try:
        r = requests.post(
            f"{url}/assign",
            json={
                "package_id": package_id,
                "agv_id": agv_id,
                "sequence": 1,
                "route": ["bay", "input", "output", "bay"],
                "label": label,
            },
            timeout=5,
        )
        return r.status_code in (200, 202)
    except Exception as exc:
        print(f"[dispatch] assign {agv_id} failed: {exc}")
        return False


def _auto_arm_signals(agv_id: str, package_id: str, pkg_color: str = "#e74c3c") -> None:
    """Background thread: simulate arm load/unload with visual animation.

    1. Show package on input table.
    2. Wait for AGV to arrive at input station.
    3. Animate input arm picking from table → depositing on AGV.
    4. Signal load_ready; remove package from input table (it's on the AGV).
    5. Wait for AGV to arrive at output station.
    6. Animate output arm picking from AGV → depositing on output table.
    7. Signal unload_ready; show package on output table.
    """
    backend = "http://backend:5000"

    # Resolve station cells and arm IDs from config.
    try:
        cfg = requests.get(f"{backend}/config", timeout=5).json()
        stations = cfg.get("warehouse", {}).get("stations", {})
        in_st = stations.get("input_arm", {})
        out_st = stations.get("output_arm", {})
        # Poll the DOCK cell (where the AGV actually parks), not the arm cell.
        in_cell = in_st.get("dock") or in_st.get("cell", [2, 1])
        out_cell = out_st.get("dock") or out_st.get("cell", [11, 1])
        arms = {f"arm-{a['id']}": a for a in cfg.get("arms", [])}
        in_arm_id = int((in_st.get("served_by", "arm-1") or "arm-1").split("-")[1])
        out_arm_id = int((out_st.get("served_by", "arm-2") or "arm-2").split("-")[1])
    except Exception:
        in_cell, out_cell = [2, 1], [11, 1]
        in_arm_id, out_arm_id = 1, 2

    def _agv_cell():
        rec = agv_state.get(agv_id)
        return rec.get("cell", [-1, -1]) if rec else [-1, -1]

    pkg_info = [{"id": package_id, "color": pkg_color}]

    # ── Show package waiting on input table ──────────────────────────────────
    _emit_table("input_arm", pkg_info)
    _log("input-arm", f"package {package_id} ready on input table")

    # ── Wait for AGV to arrive at input station ──────────────────────────────
    deadline = time.time() + 120
    while time.time() < deadline:
        cell = _agv_cell()
        if cell[0] == in_cell[0] and cell[1] == in_cell[1]:
            break
        time.sleep(0.5)

    _log("input-arm", f"AGV {agv_id} docked — loading {package_id}")

    # Arm<->AGV AI handshake: arm-N's tactical brain confirms it is safe/ready
    # to load this AGV before the arm sweeps.
    _arm_ai_gate(in_arm_id, agv_id, "load")

    # Arm animation: sweep from resting (270°=up) → left (180°=table) → right (0°=AGV)
    _animate_arm(in_arm_id, [270, 230, 190, 180, 190, 230, 270, 310, 350, 0, 350, 310, 270])

    # Package is now on the AGV — clear from input table.
    _emit_table("input_arm", [])

    try:
        requests.post(f"{backend}/agv/{agv_id}/load_ready",
                      json={"object_id": package_id}, timeout=5)
        _log("input-arm", f"load_ready: {package_id} on {agv_id}")
    except Exception:
        pass

    # ── Wait for AGV to arrive at output station ─────────────────────────────
    deadline2 = time.time() + 180
    while time.time() < deadline2:
        cell = _agv_cell()
        if cell[0] == out_cell[0] and cell[1] == out_cell[1]:
            break
        time.sleep(0.5)

    _log("output-arm", f"AGV {agv_id} docked — unloading {package_id}")

    # Arm<->AGV AI handshake before the output arm takes the package.
    _arm_ai_gate(out_arm_id, agv_id, "unload")

    # Output arm animation: sweep from resting → left (0AGV dock) → right (table)
    _animate_arm(out_arm_id, [270, 230, 190, 180, 190, 230, 270, 310, 350, 0, 350, 310, 270])

    # Show package delivered to output table.
    _emit_table("output_arm", pkg_info)
    _log("output-arm", f"{package_id} delivered to output table")

    try:
        requests.post(f"{backend}/agv/{agv_id}/unload_ready", json={}, timeout=5)
        _log("output-arm", f"unload_ready: {package_id} cleared from {agv_id}")
    except Exception:
        pass


@dispatch_bp.route("/bus/publish", methods=["POST"])
def bus_publish_stub():
    from flask import request, jsonify
    from app.agents.message_bus import get_bus
    env = request.get_json(silent=True) or {}
    try:
        get_bus().publish(env.get("type", "unknown"), env)
        return jsonify({"status": "ok"})
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500


@dispatch_bp.route("/bus/roster", methods=["GET"])
def bus_roster():
    from app.services import actor_registry
    from flask import jsonify
    return jsonify({"actors": actor_registry.list_actors()})


@dispatch_bp.route("/demo/dispatch", methods=["POST"])
def dispatch():
    """Dispatch packages through the AGV workflow.

    Body: {"goal": "send 2 packages from input to output"}
          OR {"packages": 2}

    Response: {"status":"dispatched","assignments":[...]} on success.
    """
    data = request.get_json(silent=True) or {}
    goal_text = data.get("goal", data.get("text", ""))
    n_packages = data.get("packages") or _parse_package_count(goal_text)
    n_packages = max(1, min(int(n_packages), 3))  # clamp 1-3 (one per AGV)

    if not pipeline_config.has_warehouse():
        return jsonify({"status": "error",
                        "error": "no warehouse config — reload pipeline.yaml"}), 400

    idle = _idle_agvs()
    if not idle:
        return jsonify({"status": "busy",
                        "error": "all AGVs are currently running tasks"}), 409

    # Try every idle AGV until we have enough assignments. Skip busy containers.
    assignments = []
    pkg_num = 0
    labels = ["red", "blue", "green"]
    for agv_id in idle:
        if len(assignments) >= n_packages:
            break
        pkg_num += 1
        package_id = f"pkg-{int(time.time())}-{pkg_num}"
        label = labels[(pkg_num - 1) % 3]

        pkg_colors = ["#e74c3c", "#8e44ad", "#2980b9"]
        pkg_color = pkg_colors[(pkg_num - 1) % 3]
        ok = _assign_agv(agv_id, package_id, label)
        if ok:
            assignments.append({"agv_id": agv_id, "package_id": package_id})
            _log("dispatch", f"assigned {package_id} to {agv_id}")
            threading.Thread(
                target=_auto_arm_signals,
                args=(agv_id, package_id, pkg_color),
                daemon=True,
            ).start()
        else:
            _log("dispatch", f"{agv_id} busy — skipping")

    if not assignments:
        return jsonify({"status": "error",
                        "error": "could not assign any AGV"}), 500

    socketio.emit("log_message", {
        "timestamp": _ts(),
        "source": "system",
        "message": (
            f"Dispatched {len(assignments)} package(s): "
            + ", ".join(f"{a['package_id']} → {a['agv_id']}"
                        for a in assignments)
        ),
    })

    return jsonify({
        "status": "dispatched",
        "assignments": assignments,
        "goal": goal_text,
    })
