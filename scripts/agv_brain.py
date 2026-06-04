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

# US 34.1: per-AGV speed differentiation. Base cadence is 0.5s/cell; scale
# down for faster vehicles (AGV_SPEED_FACTOR>1) and up for slower ones (<1)
# so the canvas visibly shows AGV-1 sprinting and AGV-3 lumbering. Floor at
# 0.05s/cell so even the fastest AGV doesn't outrun the frontend's eased
# motion — the original 0.5s tuning note still holds.
def _resolve_move_step_sleep() -> float:
    base = 0.5
    try:
        sf = float(os.environ.get("AGV_SPEED_FACTOR", "1.0"))
    except (TypeError, ValueError):
        sf = 1.0
    if sf <= 0:
        sf = 1.0
    return max(0.05, base / sf)


MOVE_STEP_SLEEP: float = _resolve_move_step_sleep()
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
def _bus_intent(task_id: Optional[str], action: str, detail: str = "",
                path_cells: Optional[list] = None) -> None:
    if COORDINATION_MODE not in ("decentralized", "smart"):
        return
    try:
        from agv_bus import publish_intent
        publish_intent(task_id, action, detail, path_cells=path_cells)
    except Exception:
        pass


def _bus_arm_approach(task_id: Optional[str], arm_station: str,
                       eta_s: float = 0.0) -> None:
    if COORDINATION_MODE not in ("decentralized", "smart"):
        return
    try:
        from agv_bus import publish_arm_approach
        publish_arm_approach(task_id, arm_station, eta_s)
    except Exception:
        pass


def _bus_yield(task_id: Optional[str], reason: str,
                waiting_for: str = "") -> None:
    if COORDINATION_MODE not in ("decentralized", "smart"):
        return
    try:
        from agv_bus import publish_yield
        publish_yield(task_id, reason, waiting_for)
    except Exception:
        pass


def _bus_help(task_id: Optional[str], reason: str, blocked_at: str = "") -> None:
    if COORDINATION_MODE not in ("decentralized", "smart"):
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
                f"heading to input arm via {len(route)} cells",
                path_cells=route)
    for idx, cell in enumerate(route):
        # Two cells out: send the "I'm coming" early-warning signal so the
        # input arm can prepare. eta_s > 0 means "not docked yet, hold off."
        if idx == len(route) - 2 and len(route) >= 2:
            _bus_arm_approach(pid, "input_arm",
                              eta_s=MOVE_STEP_SLEEP * 2)
        res = _post(f"/agv/{AGV_ID}/move", {"cell": cell, "status": "moving"})
        if res is None:
            return False
        time.sleep(MOVE_STEP_SLEEP)
    # Dock settle: hold long enough for the frontend canvas tween (260ms)
    # to finish placing the AGV at the dock cell BEFORE we tell the arm
    # we're ready. Using a fixed 0.7s lower bound — was MOVE_STEP_SLEEP
    # which for AGV-1 (speed_factor 1.6) was only 0.31s, leaving the
    # canvas mid-tween when the arm started sweeping.
    time.sleep(max(0.7, MOVE_STEP_SLEEP))
    _post(f"/agv/{AGV_ID}/move", {"cell": route[-1], "status": "loading"})
    _bus_arm_approach(pid, "input_arm", eta_s=0.0)
    # One more guard beat — gives the operator a clear "AGV docked, then
    # arm acts" perception. The arm choreography on the backend sees the
    # status flip immediately, but starts its gate + sweep after this.
    time.sleep(0.4)
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
    pid = ctx.get("package_id")
    lane = ctx["lane_cells"]
    # US 34.1: announce the lane path up front so the canvas polyline renders
    # the full input→output leg too, not only bay→input.
    _bus_intent(pid, "traverse_lane",
                f"crossing lane via {len(lane)} cells",
                path_cells=lane)
    for cell in lane:
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
                f"heading to output arm via {len(route)} cells",
                path_cells=route)
    for idx, cell in enumerate(route):
        if idx == len(route) - 2 and len(route) >= 2:
            _bus_arm_approach(pid, "output_arm",
                              eta_s=MOVE_STEP_SLEEP * 2)
        res = _post(f"/agv/{AGV_ID}/move", {"cell": cell, "status": "moving"})
        if res is None:
            return False
        time.sleep(MOVE_STEP_SLEEP)
    # Dock settle (mirrors input arm — see _script_move_to_input).
    time.sleep(max(0.7, MOVE_STEP_SLEEP))
    _post(f"/agv/{AGV_ID}/move", {"cell": route[-1], "status": "unloading"})
    _bus_arm_approach(pid, "output_arm", eta_s=0.0)
    time.sleep(0.4)
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
    pid = ctx.get("package_id")
    route = ctx.get("route_to_bay") or [ctx["bay"]]
    _bus_intent(pid, "return_to_bay",
                f"returning to bay via {len(route)} cells",
                path_cells=route)
    for cell in route:
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
    if COORDINATION_MODE in ("decentralized", "smart"):
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
    if COORDINATION_MODE in ("decentralized", "smart"):
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
    if COORDINATION_MODE in ("decentralized", "smart"):
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

    # US 34 (S1): smart-mode uses A* over the grid (with manual blockers
    # and other-AGV occupancy as obstacles); falls back to straight-line on
    # no-path. Non-smart modes use the verbatim US 32 paths.
    return {
        "bay": bay,
        "route_to_input": _smart_path(bay, input_dock, data),
        "lane_cells": lane_cells or _smart_path(input_dock, output_dock, data),
        "route_to_output": [output_dock],
        # Return goes UP the output-dock column first, then sidesteps into
        # the bay column at the AGV's own row — avoids crossing other AGVs'
        # bays when bays are stacked on the same column. In smart mode this
        # is computed by A* with the same obstacle awareness.
        "route_to_bay": (
            _smart_path(output_dock, bay, data)
            if _mode_is_smart()
            else _bay_return_path(output_dock, bay)
        ),
    }


def _astar_path(start, end, grid_cols, grid_rows, blocked, occupied_other):
    """US 34 Phase 6: 4-neighbour A* over the warehouse grid.

    - `blocked` is a set of (col,row) cells the AGV must NOT traverse
      (manual blockers from /grid/obstacles).
    - `occupied_other` is the set of cells held by OTHER AGVs (live snapshot);
      we route around them as a courtesy. Empty set degenerates to plain
      4-neighbour A* over the grid.

    Returns a list of [col,row] from start (inclusive) to end (inclusive), or
    None if no path exists. Caller falls back to `_straight_path`.
    """
    import heapq
    sx, sy = int(start[0]), int(start[1])
    ex, ey = int(end[0]), int(end[1])

    def h(c, r):
        return abs(c - ex) + abs(r - ey)

    if not (0 <= sx < grid_cols and 0 <= sy < grid_rows):
        return None
    if not (0 <= ex < grid_cols and 0 <= ey < grid_rows):
        return None

    start_t = (sx, sy)
    goal_t = (ex, ey)
    if start_t == goal_t:
        return [[sx, sy]]

    open_heap = [(h(sx, sy), 0, start_t, None)]
    came_from = {}
    g_score = {start_t: 0}

    while open_heap:
        _, g, cur, parent = heapq.heappop(open_heap)
        if cur in came_from:
            continue
        came_from[cur] = parent
        if cur == goal_t:
            # Reconstruct path.
            path = []
            t = cur
            while t is not None:
                path.append([t[0], t[1]])
                t = came_from[t]
            path.reverse()
            return path
        cx, cy = cur
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < grid_cols and 0 <= ny < grid_rows):
                continue
            nt = (nx, ny)
            if nt in blocked and nt != goal_t:
                continue
            if nt in occupied_other and nt != goal_t:
                continue
            new_g = g + 1
            if new_g < g_score.get(nt, 10**9):
                g_score[nt] = new_g
                heapq.heappush(open_heap, (new_g + h(nx, ny), new_g, nt, cur))

    return None


def _smart_path(start, end, grid_data):
    """US 34 smart-mode router. A* with obstacle awareness; straight-line fallback.

    `grid_data` is the dict from `_get("/grid")` — provides cols, rows,
    runtime occupancy, and blockers.
    """
    if not _mode_is_smart():
        return _straight_path(start, end)
    try:
        wh = grid_data.get("warehouse", {})
        runtime = grid_data.get("runtime", {})
        grid = wh.get("grid", {})
        cols = int(grid.get("cols", 14))
        rows = int(grid.get("rows", 10))
        blocked = {(int(c[0]), int(c[1])) for c in runtime.get("blockers", []) or []}
        occupancy = runtime.get("occupancy") or {}
        # Treat OTHER AGVs as soft obstacles; never block on my own cell.
        occupied_other = set()
        for aid, c in occupancy.items():
            if aid == AGV_ID or not c or len(c) < 2:
                continue
            occupied_other.add((int(c[0]), int(c[1])))
        # Also treat OTHER AGVs' BAY cells as obstacles. Without this,
        # returning AGVs walk straight through their peers' parked bays.
        for agv_cfg in wh.get("agvs", []) or []:
            if agv_cfg.get("id") == AGV_ID:
                continue
            bay = agv_cfg.get("bay")
            if bay and len(bay) >= 2:
                occupied_other.add((int(bay[0]), int(bay[1])))
        path = _astar_path(start, end, cols, rows, blocked, occupied_other)
        if path:
            return path
    except Exception as exc:
        print(f"[{AGV_ID}] A* error: {exc}; falling back to straight-line",
              flush=True)
    return _straight_path(start, end)


def _mode_is_smart():
    """Local helper — mirrors scripts/agv_server._mode_is_smart()."""
    return COORDINATION_MODE == "smart"


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
