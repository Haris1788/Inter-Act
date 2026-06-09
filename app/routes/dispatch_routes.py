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


def _animate_arm(arm_id: int, angles: list, delay: float = 0.18) -> None:
    """Emit arm_angle_update events in sequence to animate the arm sweeping."""
    for angle in angles:
        socketio.emit("arm_angle_update", {"arm_id": arm_id, "angle": angle})
        time.sleep(delay)


def _sweep_angles(from_deg: float, to_deg: float, steps: int = 6) -> list:
    """Build a smooth arc from from_deg → to_deg taking the SHORT way around.
    Without picking the short way, a 270°→0° sweep would walk backwards
    through 180° (visible as the arm rotating the wrong direction)."""
    diff = ((to_deg - from_deg + 540.0) % 360.0) - 180.0   # signed shortest delta
    return [(from_deg + diff * (i / steps)) % 360.0 for i in range(1, steps + 1)]


def _emit_table(station: str, packages: list) -> None:
    """Tell the frontend which packages are visible on an arm's table."""
    socketio.emit("table_update", {"station": station, "packages": packages})


def _emit_object_owner(object_id: str, owner_type: str, owner_id: str,
                       carrier_arm: int | None = None,
                       carrier_asset: str | None = None,
                       color: str | None = None) -> None:
    """Tell the frontend who currently owns this object — table, arm, or AGV.
    The color travels with the event so the frontend's unified PayloadLayer
    can paint the right colour regardless of which carrier the package is on."""
    socketio.emit("object_update", {
        "object_id": object_id,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "carrier_arm": carrier_arm,
        "carrier_asset": carrier_asset,
        "color": color,
        "event_seq": int(time.time() * 1000),
    })


# ── FDAS Step 3b: arm action registry — the scripts an arm can choose from ────
# The arm's tactical brain (qwen2.5:14b) picks ONE of these per package; the
# choreography below executes the chosen variant. Every action still completes
# the transfer and fires load_ready/unload_ready — the AGV contract is never
# broken; only HOW the arm moves changes (speed, smoothness, an extra beat).
#   steps = (rest→pick, pick→drop, drop→rest) arc resolution (more = smoother)
#   delay = per-frame delay  (higher = slower sweep)
#   dwell = pause while gripping/releasing
#   pre_beat = a small inspection/stabilisation wiggle before the pick
ARM_ACTION_REGISTRY = {
    "normal":   {"steps": (6, 8, 6),    "delay": 0.18, "dwell": 0.8,
                 "pre_beat": False, "desc": "standard handling"},
    "gentle":   {"steps": (10, 12, 10), "delay": 0.30, "dwell": 1.3,
                 "pre_beat": False, "desc": "slow, smooth handling for fragile cargo"},
    "careful":  {"steps": (8, 10, 8),   "delay": 0.24, "dwell": 1.1,
                 "pre_beat": True,  "desc": "extra-stabilised handling for heavy cargo"},
    "expedite": {"steps": (4, 5, 4),    "delay": 0.09, "dwell": 0.4,
                 "pre_beat": False, "desc": "fast handling for priority cargo"},
    "inspect":  {"steps": (6, 8, 6),    "delay": 0.20, "dwell": 0.9,
                 "pre_beat": True,  "desc": "inspection pass before handling hazardous cargo"},
}
DEFAULT_ARM_ACTION = "normal"


def _deterministic_arm_action(label, weight_kg, handling) -> str:
    """Fail-safe choice — used when the AI is busy/unreachable, and as the
    grounded default the AI is asked to confirm or override. Pure function of
    the package facts, so the arm always has a sensible script even with no GPU."""
    h = str(handling or "").lower()
    if any(k in h for k in ("fragile", "delicate", "brittle", "glass")):
        return "gentle"
    if any(k in h for k in ("hazard", "danger", "toxic", "chemical", "explosive", "inspect")):
        return "inspect"
    if any(k in h for k in ("priority", "urgent", "rush", "express", "asap", "expedite")):
        return "expedite"
    try:
        w = float(weight_kg) if weight_kg is not None else None
    except (TypeError, ValueError):
        w = None
    if (w is not None and w >= 60.0) or "heavy" in h or "bulky" in h:
        return "careful"
    return "normal"


def _arm_ai_choose_action(arm_id: int, agv_id: str, action: str,
                          ctx: dict) -> tuple[str, str, str]:
    """The arm's tactical brain (qwen2.5:14b) chooses the BEST handling script
    from ARM_ACTION_REGISTRY for the docked package. Grounded: a deterministic
    suggestion is computed from the package facts first; the AI may confirm it
    or override with a reason. Returns (action_name, rationale, source).

    Fail-safe: planner busy / AI error / invalid reply -> deterministic choice,
    so the arm always picks a valid script and never stalls the line."""
    label = ctx.get("label")
    weight_kg = ctx.get("weight_kg")
    handling = ctx.get("handling")
    suggestion = _deterministic_arm_action(label, weight_kg, handling)
    try:
        from app.services.llm_service import get_llm, planning_active
        if planning_active():
            return suggestion, "planner busy → deterministic", "deterministic"
        verb = "place onto the AGV" if action == "load" else "take off the AGV"
        system = (
            f"You are the tactical brain of robotic arm-{arm_id} in a warehouse. "
            f"An AGV is docked and you must {verb} a package. Choose the BEST "
            f"handling script for THIS package. Respond with ONLY JSON "
            f'{{"script":"<name>","rationale":"<one short reason>"}} where '
            f"<name> is exactly one of: "
            f"normal (standard), gentle (fragile/delicate — slow & smooth), "
            f"careful (heavy — extra stabilisation), expedite (urgent/priority "
            f"— fast), inspect (hazardous/uncertain — inspection pass first)."
        )
        user = (
            f"Package={ctx.get('package_id')} at the "
            f"{'input' if action == 'load' else 'output'} arm. "
            f"label={label!r}, weight_kg={weight_kg}, handling_hint={handling!r}. "
            f"A safe default is '{suggestion}'. Pick the best script."
        )
        res = get_llm().tactical(system, user, num_predict=48)
        choice = (res.get("script") or res.get("value") or "").strip().lower()
        rationale = str(res.get("rationale", "")).strip()
        if choice in ARM_ACTION_REGISTRY:
            return choice, (rationale or ARM_ACTION_REGISTRY[choice]["desc"]), "ai"
        # unknown / invalid reply -> grounded deterministic choice
        return suggestion, (rationale or "AI reply invalid → deterministic"), "deterministic"
    except Exception as exc:
        return suggestion, f"AI unavailable ({exc}) → deterministic", "deterministic"


def _arm_ai_gate(arm_id: int, agv_id: str, action: str,
                 ctx: dict) -> tuple[str, str]:
    """Run the arm's AI action choice ONCE (fail-fast) and return the chosen
    script. The decision is logged and (by the caller) announced on the bus, so
    the audience sees the arm reason about each package — and it actually drives
    the choreography that follows. Never blocks: on any failure it returns a
    valid deterministic script."""
    try:
        chosen, rationale, source = _arm_ai_choose_action(arm_id, agv_id, action, ctx)
    except Exception as exc:
        chosen, rationale, source = DEFAULT_ARM_ACTION, f"gate error ({exc})", "deterministic"
    _log(f"{action}-arm",
         f"arm-{arm_id} AI [{source}] chose '{chosen}' for {agv_id} "
         f"({ARM_ACTION_REGISTRY.get(chosen, {}).get('desc', '')}) — {rationale}")
    return chosen, rationale


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


def _auto_arm_signals(agv_id: str, package_id: str, pkg_color: str = "#e74c3c",
                      label: str | None = None, weight_kg=None,
                      handling: str | None = None) -> None:
    """Background thread: simulate arm load/unload with visual animation.

    1. Show package on input table.
    2. Wait for AGV to arrive at input station.
    3. Input arm's qwen14b CHOOSES a handling script (normal/gentle/careful/
       expedite/inspect) for this package → that choice drives the sweep.
    4. Animate input arm picking from table → depositing on AGV (chosen variant).
    5. Signal load_ready; remove package from input table (it's on the AGV).
    6. Wait for AGV to arrive at output station.
    7. Output arm's qwen14b chooses its handling script → drives the sweep.
    8. Animate output arm picking from AGV → depositing on output table.
    9. Signal unload_ready; show package on output table.

    FDAS Step 3b: `label`/`weight_kg`/`handling` are the package facts the arm
    brain reasons over to pick the best script. None → the arm defaults to a
    weight/handling-based deterministic choice (still valid)."""
    backend = "http://backend:5000"
    pkg_ctx = {"package_id": package_id, "label": label,
               "weight_kg": weight_kg, "handling": handling}

    def _publish_arm_decision(arm_id: int, station: str, action: str,
                              chosen: str, rationale: str) -> None:
        """Announce the arm's executed handling decision on the shared bus so
        the mesh + frontend see the arm reason — and act — per package."""
        try:
            from app.routes.bus_routes import publish_local
            publish_local("arm_decision", from_=arm_id, task_id=package_id,
                          payload={"arm_id": arm_id, "station": station,
                                   "agv_id": agv_id, "action": action,
                                   "script": chosen, "rationale": rationale,
                                   "executed": True})
        except Exception:
            pass

    def _run_sweep(arm_id: int, rest: float, pick: float, drop: float,
                   chosen: str, on_pick, on_drop) -> None:
        """Execute the chosen handling script's sweep. on_pick/on_drop are the
        ownership-transfer callbacks fired at the exact touch moments."""
        p = ARM_ACTION_REGISTRY.get(chosen, ARM_ACTION_REGISTRY[DEFAULT_ARM_ACTION])
        s1, s2, s3 = p["steps"]
        d, dwell = p["delay"], p["dwell"]
        if p["pre_beat"]:
            # small inspection/stabilisation wiggle before committing to the pick
            _animate_arm(arm_id, _sweep_angles(rest, (rest + 14) % 360.0, 2)
                         + _sweep_angles((rest + 14) % 360.0, rest, 2), d)
        _animate_arm(arm_id, _sweep_angles(rest, pick, s1), d)   # rest → pick
        on_pick()
        time.sleep(dwell)
        _animate_arm(arm_id, _sweep_angles(pick, drop, s2), d)   # pick → drop (carry)
        on_drop()
        time.sleep(dwell)
        _animate_arm(arm_id, _sweep_angles(drop, rest, s3), d)   # drop → rest

    # Resolve station cells and arm IDs from config.
    # Also derive each arm's PICK angle (toward its table) and DROP angle
    # (toward its AGV dock) from station geometry — the input arm has its
    # dock to the EAST (angle 0°) while the output arm has its dock to the
    # WEST (angle 180°). Hard-coding 0/180 broke the output arm: it rotated
    # toward the empty table to "pick", then toward the AGV to "drop" — exactly
    # backwards.
    import math
    def _arm_angles_for(station: dict) -> tuple[float, float, float]:
        """Return (rest_angle, pick_angle, drop_angle) in degrees.
        - drop = points at the AGV dock
        - pick = points at the table (mirror of dock across pivot)
        - rest = 270° (up), classic neutral
        Defaults handle missing geometry cleanly."""
        px = station.get("px") or {}
        dock_px = station.get("dock_px") or station.get("px") or {}
        ax, ay = px.get("x", 0.0), px.get("y", 0.0)
        dx, dy = dock_px.get("x", ax + 1), dock_px.get("y", ay)
        drop_deg = (math.degrees(math.atan2(dy - ay, dx - ax)) + 360.0) % 360.0
        pick_deg = (drop_deg + 180.0) % 360.0
        return (270.0, pick_deg, drop_deg)

    try:
        cfg = requests.get(f"{backend}/config", timeout=5).json()
        stations = cfg.get("warehouse", {}).get("stations", {})
        in_st = stations.get("input_arm", {})
        out_st = stations.get("output_arm", {})
        in_cell = in_st.get("dock") or in_st.get("cell", [2, 1])
        out_cell = out_st.get("dock") or out_st.get("cell", [11, 1])
        arms = {f"arm-{a['id']}": a for a in cfg.get("arms", [])}
        in_arm_id = int((in_st.get("served_by", "arm-1") or "arm-1").split("-")[1])
        out_arm_id = int((out_st.get("served_by", "arm-2") or "arm-2").split("-")[1])
        in_rest, in_pick, in_drop = _arm_angles_for(in_st)
        out_rest, out_pick, out_drop = _arm_angles_for(out_st)
    except Exception:
        in_cell, out_cell = [2, 1], [11, 1]
        in_arm_id, out_arm_id = 1, 2
        in_rest, in_pick, in_drop = 270.0, 180.0, 0.0
        out_rest, out_pick, out_drop = 270.0, 0.0, 180.0

    def _agv_cell():
        rec = agv_state.get(agv_id)
        return rec.get("cell", [-1, -1]) if rec else [-1, -1]

    def _agv_docked_at(target_cell, expected_status: str) -> bool:
        """True only when the AGV reports the right cell AND a settled status
        (loading at input dock, unloading at output dock). The AGV brain flips
        to this status AFTER the settle delay — this is the "I am here, you
        may load/unload" signal. Cell match alone is not enough: an AGV
        passing through a cell mid-route reports status=moving."""
        rec = agv_state.get(agv_id) or {}
        cell = rec.get("cell", [-1, -1])
        status = rec.get("status", "")
        return (cell[0] == target_cell[0]
                and cell[1] == target_cell[1]
                and status == expected_status)

    pkg_info = [{"id": package_id, "color": pkg_color}]

    # ── Show package waiting on input table ──────────────────────────────────
    _emit_table("input_arm", pkg_info)
    _emit_object_owner(package_id, "zone", "input_arm", color=pkg_color)
    _log("input-arm", f"package {package_id} ready on input table")

    # ── Wait for AGV to signal DOCKED at input station ───────────────────────
    # We wait for status=docked (the AGV brain's explicit "I am parked, you
    # may load" signal) — not just cell match. This avoids the bug where the
    # arm started loading while the AGV was still in motion through the cell.
    docked = False
    # Sequential per-package dispatch (FDAS) can make a later AGV HOLD for the
    # single shared lane well past a minute before it docks — it was claimed
    # early (which spawned this loader) but waits its turn behind earlier AGVs.
    # Wait generously so the loader doesn't abort 2 s before it arrives.
    # (Proper Phase-2 fix: trigger this loader on ARRIVAL, not on claim.)
    deadline = time.time() + 300
    while time.time() < deadline:
        if _agv_docked_at(in_cell, "loading"):
            docked = True
            break
        time.sleep(0.3)

    if not docked:
        # AGV never reached the dock cell — abort cleanly. Do NOT POST
        # load_ready blindly (that would put a phantom package on a
        # non-existent AGV). AGV's own LOAD_SIGNAL_TIMEOUT will fault
        # the task cleanly so a peer can re-bid.
        _log("input-arm",
             f"ABORT: {agv_id} never reached input dock {in_cell} "
             f"within 300s — aborting load choreography")
        return

    _log("input-arm", f"AGV {agv_id} docked — loading {package_id}")

    # Arm<->AGV AI decision (fail-fast): the input arm's qwen14b CHOOSES the
    # handling script for this package; that choice drives the sweep below and
    # is announced on the bus. Never blocks — falls back to a valid script.
    in_action, in_rationale = _arm_ai_gate(in_arm_id, agv_id, "load", pkg_ctx)
    _publish_arm_decision(in_arm_id, "input_arm", "load", in_action, in_rationale)

    # Arm sweep choreographed so the package transfers EXACTLY when the
    # gripper touches each surface — not all-at-once before/after the sweep.
    # The CHOSEN script (normal/gentle/careful/expedite/inspect) sets the sweep
    # speed/smoothness/extra-beat; the transfer logic is identical for all.
    try:
        _run_sweep(
            in_arm_id, in_rest, in_pick, in_drop, in_action,
            on_pick=lambda: (
                # PICK: package leaves table, attaches to gripper
                _emit_object_owner(package_id, "arm", f"arm-{in_arm_id}",
                                   carrier_arm=in_arm_id, color=pkg_color),
                _emit_table("input_arm", []),
            ),
            on_drop=lambda: (
                # DROP: package leaves gripper, lands on AGV
                _emit_object_owner(package_id, "asset", agv_id,
                                   carrier_asset=agv_id, color=pkg_color),
            ),
        )
    except Exception as exc:
        _log("input-arm", f"sweep failed: {exc}")
        # Still try to POST load_ready below — the AGV is waiting.

    # CRITICAL: load_ready MUST fire or AGV times out. Retry on transient
    # backend errors instead of silently swallowing them.
    posted = False
    for attempt in range(3):
        try:
            r = requests.post(f"{backend}/agv/{agv_id}/load_ready",
                              json={"object_id": package_id}, timeout=5)
            if r.status_code == 200:
                posted = True
                break
            _log("input-arm",
                 f"load_ready POST attempt {attempt+1} returned "
                 f"{r.status_code} — retrying")
        except Exception as exc:
            _log("input-arm",
                 f"load_ready POST attempt {attempt+1} raised {exc} — retrying")
        time.sleep(1.0)
    if posted:
        _log("input-arm", f"load_ready: {package_id} on {agv_id}")
    else:
        _log("input-arm",
             f"FATAL: load_ready never delivered for {agv_id}/{package_id}")
        return

    # Phase 3 bus signal — arm tells the swarm it loaded an AGV (the peer
    # AGVs use this to know when the input dock will free up next).
    try:
        from app.routes.bus_routes import publish_local
        publish_local("arm_ready", from_=in_arm_id, task_id=package_id,
                      payload={"arm_station": "input_arm", "for_agv": agv_id,
                               "action": "loaded"})
    except Exception:
        pass

    # ── Wait for AGV to signal DOCKED at output station ──────────────────────
    docked_out = False
    deadline2 = time.time() + 300   # generous: AGV may hold for the output lane
    while time.time() < deadline2:
        if _agv_docked_at(out_cell, "unloading"):
            docked_out = True
            break
        time.sleep(0.3)

    if not docked_out:
        _log("output-arm",
             f"ABORT: {agv_id} never reached output dock {out_cell} "
             f"within 300s — aborting unload choreography")
        return

    _log("output-arm", f"AGV {agv_id} docked — unloading {package_id}")

    # Output arm's qwen14b chooses its handling script for this package.
    out_action, out_rationale = _arm_ai_gate(out_arm_id, agv_id, "unload", pkg_ctx)
    _publish_arm_decision(out_arm_id, "output_arm", "unload", out_action, out_rationale)

    try:
        # Output order: rest → AGV (pick package off AGV) → table (drop) → rest.
        # _run_sweep's "pick"/"drop" are just first/second waypoints, so here
        # first=out_drop (AGV), second=out_pick (table).
        _run_sweep(
            out_arm_id, out_rest, out_drop, out_pick, out_action,
            on_pick=lambda: (
                # PICK: package leaves AGV, attaches to output gripper
                _emit_object_owner(package_id, "arm", f"arm-{out_arm_id}",
                                   carrier_arm=out_arm_id, color=pkg_color),
            ),
            on_drop=lambda: (
                # DROP: package lands on output table (carrier flips to zone)
                _emit_object_owner(package_id, "zone", "output_arm", color=pkg_color),
                _emit_table("output_arm", pkg_info),
                _log("output-arm", f"{package_id} delivered to output table"),
            ),
        )
    except Exception as exc:
        _log("output-arm", f"sweep failed: {exc}")

    # CRITICAL: unload_ready MUST fire or AGV times out and orphans the task.
    posted_unload = False
    for attempt in range(3):
        try:
            r = requests.post(f"{backend}/agv/{agv_id}/unload_ready",
                              json={}, timeout=5)
            if r.status_code == 200:
                posted_unload = True
                break
            _log("output-arm",
                 f"unload_ready POST attempt {attempt+1} returned "
                 f"{r.status_code} — retrying")
        except Exception as exc:
            _log("output-arm",
                 f"unload_ready POST attempt {attempt+1} raised {exc} — retrying")
        time.sleep(1.0)
    if posted_unload:
        _log("output-arm", f"unload_ready: {package_id} cleared from {agv_id}")
    else:
        _log("output-arm",
             f"FATAL: unload_ready never delivered for {agv_id}/{package_id}")
        return

    # Phase 3 bus signal — arm tells the swarm it took the package off the AGV.
    try:
        from app.routes.bus_routes import publish_local
        publish_local("arm_ready", from_=out_arm_id, task_id=package_id,
                      payload={"arm_station": "output_arm", "for_agv": agv_id,
                               "action": "unloaded"})
    except Exception:
        pass


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
