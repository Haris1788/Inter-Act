"""AGV Brain — tactical agent for one Automated Guided Vehicle.

Normal route bay→input→load→lane→output→unload→return→bay is DETERMINISTIC.
qwen2.5:14b (via backend POST /ai/tactical) is consulted ONLY on anomalies:
a step failing repeatedly, lane stuck, load signal not arriving, etc.
Falls back to "hold" on HTTP 409 (planning window open) or timeout.

Called by agv_server.py, which runs one instance per AGV container.

Environment:
    AGV_ID         — this AGV's id (default: "agv-1")
    SERVER_HOST    — backend Flask URL (default: http://localhost:5000)
"""

from __future__ import annotations

import os
import sys
import time
from typing import Dict, Any, Optional

import requests

AGV_ID: str = os.environ.get("AGV_ID", "agv-1")
DEFAULT_HOST: str = os.environ.get("SERVER_HOST", "http://localhost:5000")
COORDINATION_MODE: str = os.environ.get("COORDINATION_MODE", "centralized").lower()

MOVE_STEP_SLEEP: float = 0.5    # cadence between cell hops; tuned so the
                                # frontend's eased motion never catches up and
                                # stalls -> continuous vehicle-like travel
LANE_ACQUIRE_TIMEOUT: float = 60.0
LOAD_SIGNAL_TIMEOUT: float = 120.0
UNLOAD_SIGNAL_TIMEOUT: float = 120.0
MAX_STEP_RETRIES: int = 3

# AI-driven decision loop: before EVERY script the tactical brain (qwen2.5:14b)
# is consulted with live shared status and decides proceed / hold / fault.
# The deterministic _NORMAL_STEPS order is the safety rail + fallback.
MAX_DECISION_ITERS: int = 120   # cap so a permanent 'hold' can't loop forever
HOLD_SLEEP: float = 3.0         # wait between coordination re-evaluations


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _post(path: str, payload: dict, timeout: float = 10.0) -> Optional[dict]:
    try:
        r = requests.post(f"{DEFAULT_HOST}{path}", json=payload, timeout=timeout)
        return r.json()
    except Exception as exc:
        print(f"[{AGV_ID}] POST {path} failed: {exc}", file=sys.stderr)
        return None


def _get(path: str, timeout: float = 5.0) -> Optional[dict]:
    try:
        r = requests.get(f"{DEFAULT_HOST}{path}", timeout=timeout)
        return r.json()
    except Exception as exc:
        print(f"[{AGV_ID}] GET {path} failed: {exc}", file=sys.stderr)
        return None


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [{AGV_ID}] {msg}", flush=True)
    _post("/log", {"source": AGV_ID, "message": msg})


# ── Phase 3: peer signals (no-op in centralized mode) ───────────────────────
def _bus_intent(task_id: Optional[str], action: str, detail: str = "") -> None:
    if COORDINATION_MODE != "decentralized":
        return
    try:
        from agv_bus import publish_intent
        publish_intent(task_id, action, detail)
    except Exception:
        pass


def _bus_arm_approach(task_id: Optional[str], arm_station: str,
                       eta_s: float = 0.0) -> None:
    if COORDINATION_MODE != "decentralized":
        return
    try:
        from agv_bus import publish_arm_approach
        publish_arm_approach(task_id, arm_station, eta_s)
    except Exception:
        pass


def _bus_yield(task_id: Optional[str], reason: str,
                waiting_for: str = "") -> None:
    if COORDINATION_MODE != "decentralized":
        return
    try:
        from agv_bus import publish_yield
        publish_yield(task_id, reason, waiting_for)
    except Exception:
        pass


def _bus_help(task_id: Optional[str], reason: str, blocked_at: str = "") -> None:
    if COORDINATION_MODE != "decentralized":
        return
    try:
        from agv_bus import publish_help_request
        publish_help_request(task_id, reason, blocked_at)
    except Exception:
        pass


# ── Script registry ────────────────────────────────────────────────────────────
# qwen2.5:14b picks a name from SCRIPT_REGISTRY on anomaly; the state machine
# executes the function. Each returns True on success, False on fault/failure.

def _script_move_to_input(ctx: dict) -> bool:
    pid = ctx.get("package_id")
    route = ctx["route_to_input"]
    _bus_intent(pid, "move_to_input",
                f"heading to input arm via {len(route)} cells")
    for idx, cell in enumerate(route):
        # One cell out: announce arm approach so the arm + peers can react.
        if idx == len(route) - 1:
            _bus_arm_approach(pid, "input_arm",
                              eta_s=MOVE_STEP_SLEEP)
        res = _post(f"/agv/{AGV_ID}/move", {"cell": cell, "status": "moving"})
        if res is None:
            return False
        time.sleep(MOVE_STEP_SLEEP)
    return True


def _script_acquire_input_station(ctx: dict) -> bool:
    """Wait until the input arm station is free, then claim it."""
    res = _post(
        "/agv/station/acquire",
        {"agv_id": AGV_ID, "station": "input_arm", "timeout": LANE_ACQUIRE_TIMEOUT},
        timeout=LANE_ACQUIRE_TIMEOUT + 5.0,
    )
    return res is not None and res.get("status") == "acquired"


def _script_release_input_station(ctx: dict) -> bool:
    _post("/agv/station/release", {"agv_id": AGV_ID, "station": "input_arm"})
    return True


def _script_acquire_output_station(ctx: dict) -> bool:
    """Wait until the output arm station is free, then claim it."""
    res = _post(
        "/agv/station/acquire",
        {"agv_id": AGV_ID, "station": "output_arm", "timeout": LANE_ACQUIRE_TIMEOUT},
        timeout=LANE_ACQUIRE_TIMEOUT + 5.0,
    )
    return res is not None and res.get("status") == "acquired"


def _script_release_output_station(ctx: dict) -> bool:
    _post("/agv/station/release", {"agv_id": AGV_ID, "station": "output_arm"})
    return True


def _script_acquire_lane(ctx: dict) -> bool:
    pid = ctx.get("package_id")
    _bus_intent(pid, "acquire_lane", "requesting shared travel lane")
    res = _post(
        "/agv/lane/acquire",
        {"agv_id": AGV_ID, "timeout": LANE_ACQUIRE_TIMEOUT},
        timeout=LANE_ACQUIRE_TIMEOUT + 5.0,
    )
    ok = res is not None and res.get("status") == "acquired"
    if not ok:
        _bus_yield(pid, "lane busy", waiting_for="travel_lane")
    return ok


def _script_traverse_lane(ctx: dict) -> bool:
    for cell in ctx["lane_cells"]:
        res = _post(f"/agv/{AGV_ID}/move", {"cell": cell, "status": "moving"})
        if res is None:
            return False
        time.sleep(MOVE_STEP_SLEEP)
    return True


def _script_load_package(ctx: dict) -> bool:
    """Wait for the input-arm brain to signal that the package is placed."""
    _post(f"/agv/{AGV_ID}/health", {"health": "alive", "status_hint": "loading"})
    deadline = time.time() + LOAD_SIGNAL_TIMEOUT
    while time.time() < deadline:
        res = _get(f"/agv/{AGV_ID}/load_ready")
        if res and res.get("ready"):
            _post(f"/agv/{AGV_ID}/load_clear", {})
            return True
        time.sleep(2.0)
    return False


def _script_move_to_output(ctx: dict) -> bool:
    pid = ctx.get("package_id")
    route = ctx["route_to_output"]
    _bus_intent(pid, "move_to_output",
                f"heading to output arm via {len(route)} cells")
    for idx, cell in enumerate(route):
        if idx == len(route) - 1:
            _bus_arm_approach(pid, "output_arm", eta_s=MOVE_STEP_SLEEP)
        res = _post(f"/agv/{AGV_ID}/move", {"cell": cell, "status": "moving"})
        if res is None:
            return False
        time.sleep(MOVE_STEP_SLEEP)
    return True


def _script_unload_package(ctx: dict) -> bool:
    """Wait for the output-arm brain to signal it is ready to receive."""
    _post(f"/agv/{AGV_ID}/health", {"health": "alive", "status_hint": "unloading"})
    deadline = time.time() + UNLOAD_SIGNAL_TIMEOUT
    while time.time() < deadline:
        res = _get(f"/agv/{AGV_ID}/unload_ready")
        if res and res.get("ready"):
            _post(f"/agv/{AGV_ID}/unload_clear", {})
            return True
        time.sleep(2.0)
    return False


def _script_release_lane(ctx: dict) -> bool:
    res = _post("/agv/lane/release", {"agv_id": AGV_ID})
    return res is not None


def _script_return_to_bay(ctx: dict) -> bool:
    # Travel the path back to the bay cell-by-cell (was a single instant jump).
    for cell in ctx.get("route_to_bay") or [ctx["bay"]]:
        res = _post(f"/agv/{AGV_ID}/move", {"cell": cell, "status": "returning"})
        if res is None:
            return False
        time.sleep(MOVE_STEP_SLEEP)
    return True


def _script_hold(ctx: dict) -> bool:
    time.sleep(1.0)
    return True


def _script_fault(ctx: dict) -> bool:
    error = ctx.get("fault_reason", "brain declared fault")
    # In decentralized mode, BROADCAST first so peers can pick up the work
    # immediately; THEN update local /health so cell/lane locks are released.
    if COORDINATION_MODE == "decentralized":
        try:
            from agv_bus import publish_task_orphan
            assignment = ctx.get("assignment", {}) or {}
            task_id = assignment.get("task_id") or assignment.get("package_id") or ctx.get("package_id")
            if task_id:
                publish_task_orphan(
                    task_id=task_id,
                    package_id=assignment.get("package_id", task_id),
                    reason=error,
                    assignment={**assignment, "original_agv": AGV_ID},
                )
        except Exception as exc:
            print(f"[{AGV_ID}] task_orphan publish failed: {exc}", file=sys.stderr)
    _post(f"/agv/{AGV_ID}/health", {"health": "faulted", "error": error})
    return False


SCRIPT_REGISTRY: Dict[str, Any] = {
    "acquire_input_station":  _script_acquire_input_station,
    "release_input_station":  _script_release_input_station,
    "acquire_output_station": _script_acquire_output_station,
    "release_output_station": _script_release_output_station,
    "move_to_input":  _script_move_to_input,
    "acquire_lane":   _script_acquire_lane,
    "traverse_lane":  _script_traverse_lane,
    "load_package":   _script_load_package,
    "move_to_output": _script_move_to_output,
    "unload_package": _script_unload_package,
    "release_lane":   _script_release_lane,
    "return_to_bay":  _script_return_to_bay,
    "hold":           _script_hold,
    "fault":          _script_fault,
}

# Deterministic step order — no AI involved in normal execution.
# Station locks ensure only one AGV docks at each arm at a time.
_NORMAL_STEPS = [
    "acquire_input_station",  # wait for input arm to be free
    "move_to_input",          # travel bay→input cell
    "load_package",           # wait for arm to place package
    "release_input_station",  # free the input station for next AGV
    "acquire_lane",           # mutual exclusion on shared lane
    "traverse_lane",          # travel input→output along the lane
    "acquire_output_station", # wait for output arm to be free
    "move_to_output",         # dock at output cell
    "unload_package",         # wait for arm to take package
    "release_output_station", # free the output station
    "release_lane",           # release lane
    "return_to_bay",          # travel output→bay
]


# ── Tactical AI (qwen2.5:14b via backend) ─────────────────────────────────────

def _ask_tactical(situation: str) -> str:
    """Consult qwen2.5:14b about an anomalous situation.

    Returns a script name from SCRIPT_REGISTRY, or "hold" on any failure.
    The backend enforces the VRAM constraint: returns 409 while planning.
    """
    system = (
        f"You are the tactical brain of AGV {AGV_ID} in a warehouse. "
        f"You must choose ONE action script from: "
        f"{sorted(SCRIPT_REGISTRY.keys())}. "
        f"Respond with ONLY a JSON object: "
        '{"script": "<chosen_script_name>"}'
    )
    try:
        r = requests.post(
            f"{DEFAULT_HOST}/ai/tactical",
            json={"system": system, "user": situation},
            timeout=65,
        )
        if r.status_code == 409:
            return "hold"   # planning window open — wait and retry
        data = r.json()
        script = (data.get("script") or "").strip().lower()
        return script if script in SCRIPT_REGISTRY else "hold"
    except Exception:
        return "hold"   # fail-safe: deterministic wait


# ── State machine ──────────────────────────────────────────────────────────────

def _coord_snapshot() -> dict:
    """Shared coordination state: all AGVs, lane/station holders, arms, plan.

    This is how THIS AGV 'sees' the others — the basis for AI coordination
    (AGV<->AGV and AGV<->arm) instead of barging blindly into a held lane/dock.
    """
    return _get("/coord/snapshot") or {}


def _run_step(step_name: str, ctx: dict) -> bool:
    """Execute one script with bounded retries + AI recovery on failure.

    This is the anomaly path (a step actually failing). The higher-level
    'which script next' decision is made in run_task by the tactical AI.
    """
    script_fn = SCRIPT_REGISTRY[step_name]
    for attempt in range(1, MAX_STEP_RETRIES + 1):
        _log(f"step {step_name} (attempt {attempt}/{MAX_STEP_RETRIES})")
        if script_fn(ctx):
            return True
        if attempt < MAX_STEP_RETRIES:
            advice = _ask_tactical(
                f"Step '{step_name}' failed {attempt} time(s). "
                f"Package: {ctx['package_id']}. What recovery script?"
            )
            _log(f"AI recovery advice after {step_name} failure: {advice}")
            if advice == "fault":
                ctx["fault_reason"] = (
                    f"AI advised fault after {step_name} failed {attempt}x"
                )
                return False
            if advice != step_name and advice in SCRIPT_REGISTRY:
                SCRIPT_REGISTRY[advice](ctx)
    return False


def _decide_next_script(ctx: dict, completed: list, det_next: str,
                        snap: dict) -> str:
    """Tactical AI (qwen2.5:14b via backend /ai/tactical) decides the next move
    from LIVE shared status. The AI owns pacing/coordination; Python owns
    sequence integrity (the safety rail).

    Allowed answers: `det_next` (proceed), 'hold' (wait — another AGV holds
    the lane/station I need), 'fault' (abort). Unknown / HTTP 409 (planner
    running) / timeout -> `det_next`, so the run NEVER deadlocks on the AI.
    """
    agvs = snap.get("agvs", {})
    others = {a: {"cell": v.get("cell"), "status": v.get("status")}
              for a, v in agvs.items() if a != AGV_ID}
    system = (
        f"You are the tactical brain of AGV {AGV_ID} in a shared warehouse. "
        f"ONLY one AGV may hold the travel lane at a time, and only one AGV "
        f"may dock at each arm station at a time — a collision is a failure. "
        f"Use the live status to coordinate with the other AGVs. Respond with "
        f'ONLY JSON {{"script":"<name>"}} where <name> is exactly one of: '
        f'"{det_next}" = proceed with the planned next step; '
        f'"hold" = another AGV holds the lane/station I need, wait; '
        f'"fault" = unrecoverable, abort.'
    )
    user = (
        f"Package {ctx.get('package_id')} "
        f"(sequence {ctx.get('assignment', {}).get('sequence')}). "
        f"Steps I have completed: {completed}. "
        f"My planned next step: '{det_next}'. "
        f"Live status — lane_holder: {snap.get('lane_holder')}; "
        f"station_holders: {snap.get('station_holders')}; "
        f"other AGVs: {others}. "
        f"Do I run '{det_next}' now, 'hold', or 'fault'?"
    )
    try:
        r = requests.post(
            f"{DEFAULT_HOST}/ai/tactical",
            json={"system": system, "user": user},
            timeout=65,
        )
        if r.status_code == 409:
            return det_next  # planning window open — proceed deterministically
        choice = (r.json().get("script") or "").strip().lower()
    except Exception:
        return det_next  # fail-safe: never block the run on the AI
    if choice in ("hold", "fault"):
        return choice
    return det_next  # firewall: only the planned step is a valid 'proceed'


def run_task(assignment: dict, route_ctx: Optional[dict] = None) -> bool:
    """Execute one assignment. The tactical AI is consulted BEFORE every script
    with live shared status and decides proceed / hold / fault; the
    deterministic _NORMAL_STEPS order is the safety rail and the fallback.

    `assignment` (from CentralBrain / plan_validator):
        {"package_id":"pkg-1","agv_id":"agv-1","sequence":1,
         "route":["bay","input","output","bay"],"label":"red"}
    `route_ctx` optional (testing); else fetched from GET /grid.
    """
    if route_ctx is None:
        route_ctx = _fetch_route_ctx()
    if route_ctx is None:
        _post(f"/agv/{AGV_ID}/health",
              {"health": "faulted", "error": "could not fetch route from backend"})
        return False

    ctx = {
        **route_ctx,
        "assignment": assignment,
        "package_id": assignment.get("package_id", "?"),
    }

    _log(f"task start: pkg={ctx['package_id']} label={assignment.get('label', '')}")

    # In decentralized mode, broadcast our current task so peers know what
    # task to orphan if we go down. agent_status is the heartbeat-with-task.
    if COORDINATION_MODE == "decentralized":
        try:
            from agv_bus import publish_agent_status
            tid = assignment.get("task_id") or assignment.get("package_id") or ctx["package_id"]
            publish_agent_status(status="running", task_id=tid, assignment=assignment)
        except Exception:
            pass

    completed: list = []
    iterations = 0
    while iterations < MAX_DECISION_ITERS:
        iterations += 1
        remaining = [s for s in _NORMAL_STEPS if s not in completed]
        if not remaining:
            break
        det_next = remaining[0]

        snap = _coord_snapshot()
        choice = _decide_next_script(ctx, completed, det_next, snap)

        if choice == "hold":
            _log(
                f"AI decision: HOLD before '{det_next}' "
                f"(lane={snap.get('lane_holder')}, "
                f"stations={snap.get('station_holders')})"
            )
            time.sleep(HOLD_SLEEP)
            continue
        if choice == "fault":
            ctx["fault_reason"] = (
                f"AI tactical decision: fault before {det_next}"
            )
            _script_fault(ctx)
            return False

        _log(f"AI decision: run '{det_next}'")
        if not _run_step(det_next, ctx):
            ctx.setdefault(
                "fault_reason",
                f"{det_next} failed after {MAX_STEP_RETRIES} attempts",
            )
            _script_fault(ctx)
            return False
        completed.append(det_next)

    if [s for s in _NORMAL_STEPS if s not in completed]:
        ctx["fault_reason"] = "decision loop hit iteration cap before completion"
        _script_fault(ctx)
        return False

    _log(f"task complete: pkg={ctx['package_id']}")
    _post(f"/agv/{AGV_ID}/health", {"health": "alive"})
    # Tell peers we're idle again
    if COORDINATION_MODE == "decentralized":
        try:
            from agv_bus import publish_agent_status
            publish_agent_status(status="done", task_id=None, assignment={})
        except Exception:
            pass
    return True


# ── Route resolution ───────────────────────────────────────────────────────────

def _fetch_route_ctx() -> Optional[dict]:
    """Fetch warehouse config from the backend and build route context."""
    data = _get("/grid")
    if data is None:
        return None

    wh = data.get("warehouse", {})
    agvs = wh.get("agvs", [])
    agv_cfg = next((a for a in agvs if a["id"] == AGV_ID), None)
    if agv_cfg is None:
        return None

    stations = wh.get("stations", {})
    lane = wh.get("lane", {})
    lane_cells = [list(c) for c in lane.get("cells", [])]
    bay = list(agv_cfg.get("bay", [0, 0]))
    in_st = stations.get("input_arm", {})
    out_st = stations.get("output_arm", {})
    # AGV parks at the DOCK cell beside the arm — never on the arm's own cell.
    input_dock = list(in_st.get("dock") or in_st.get("cell", bay))
    output_dock = list(out_st.get("dock") or out_st.get("cell", bay))

    return {
        "bay": bay,
        "route_to_input": _straight_path(bay, input_dock),
        "lane_cells": lane_cells or _straight_path(input_dock, output_dock),
        "route_to_output": [output_dock],
        # Return goes UP the output-dock column first, then sidesteps into
        # the bay column at the AGV's own row — avoids crossing other AGVs'
        # bays when bays are stacked on the same column.
        "route_to_bay": _bay_return_path(output_dock, bay),
    }


def _straight_path(start: list, end: list) -> list:
    """Simple rectilinear path: move columns first, then rows."""
    path: list = []
    sc, sr = int(start[0]), int(start[1])
    ec, er = int(end[0]), int(end[1])

    dc = 1 if ec >= sc else -1
    for c in range(sc, ec + dc, dc):
        path.append([c, sr])

    dr = 1 if er > sr else -1
    for r in range(sr + dr, er + dr, dr):
        path.append([ec, r])

    return path if path else [list(end)]


def _bay_return_path(output_dock: list, bay: list) -> list:
    """Return-to-bay routed up the OUTPUT-DOCK column first, then sidestep
    horizontally into the bay column at the AGV's own row.

    Why this instead of _straight_path: when bays are stacked on the same
    column (e.g. agv-1=[10,3], agv-2=[10,5], agv-3=[10,7]) the naive
    cols-then-rows path walks straight through other AGVs' parked bays.
    Routing up the output-dock column first turns it into a real
    "service aisle" — each AGV sidesteps into the bay column only at its
    own row.
    """
    od_c, od_r = int(output_dock[0]), int(output_dock[1])
    by_c, by_r = int(bay[0]),         int(bay[1])
    path: list = []

    # 1. Up the output-dock column to the bay's row
    if by_r != od_r:
        rstep = 1 if by_r > od_r else -1
        for r in range(od_r + rstep, by_r + rstep, rstep):
            path.append([od_c, r])

    # 2. Sidestep horizontally to the bay column
    if by_c != od_c:
        cstep = 1 if by_c > od_c else -1
        for c in range(od_c + cstep, by_c + cstep, cstep):
            path.append([c, by_r])

    return path or [list(bay)]
