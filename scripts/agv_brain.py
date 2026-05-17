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

MOVE_STEP_SLEEP: float = 0.8    # seconds between cell-move POSTs
LANE_ACQUIRE_TIMEOUT: float = 60.0
LOAD_SIGNAL_TIMEOUT: float = 120.0
UNLOAD_SIGNAL_TIMEOUT: float = 120.0
MAX_STEP_RETRIES: int = 3


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


# ── Script registry ────────────────────────────────────────────────────────────
# qwen2.5:14b picks a name from SCRIPT_REGISTRY on anomaly; the state machine
# executes the function. Each returns True on success, False on fault/failure.

def _script_move_to_input(ctx: dict) -> bool:
    for cell in ctx["route_to_input"]:
        res = _post(f"/agv/{AGV_ID}/move", {"cell": cell, "status": "moving"})
        if res is None:
            return False
        time.sleep(MOVE_STEP_SLEEP)
    return True


def _script_acquire_lane(ctx: dict) -> bool:
    res = _post(
        "/agv/lane/acquire",
        {"agv_id": AGV_ID, "timeout": LANE_ACQUIRE_TIMEOUT},
        timeout=LANE_ACQUIRE_TIMEOUT + 5.0,
    )
    return res is not None and res.get("status") == "acquired"


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
    for cell in ctx["route_to_output"]:
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
    res = _post(
        f"/agv/{AGV_ID}/move",
        {"cell": ctx["bay"], "status": "returning"},
    )
    if res is None:
        return False
    time.sleep(MOVE_STEP_SLEEP)
    return True


def _script_hold(ctx: dict) -> bool:
    time.sleep(1.0)
    return True


def _script_fault(ctx: dict) -> bool:
    error = ctx.get("fault_reason", "brain declared fault")
    _post(f"/agv/{AGV_ID}/health", {"health": "faulted", "error": error})
    return False


SCRIPT_REGISTRY: Dict[str, Any] = {
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
_NORMAL_STEPS = [
    "move_to_input",
    "load_package",
    "acquire_lane",
    "traverse_lane",
    "move_to_output",
    "unload_package",
    "release_lane",
    "return_to_bay",
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

def run_task(assignment: dict, route_ctx: Optional[dict] = None) -> bool:
    """Execute one full assignment cycle. Returns True on success, False on fault.

    `assignment` is the dict produced by CentralBrain / plan_validator:
        {"package_id": "pkg-1", "agv_id": "agv-1", "sequence": 1,
         "route": ["bay","input","output","bay"], "label": "red"}

    `route_ctx` (optional, for testing) provides the resolved grid cells:
        {"bay": [c,r], "route_to_input": [[c,r],...],
         "lane_cells": [[c,r],...], "route_to_output": [[c,r],...]}
    If None, fetched from GET /grid.
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

    for step_name in _NORMAL_STEPS:
        script_fn = SCRIPT_REGISTRY[step_name]
        ok = False

        for attempt in range(1, MAX_STEP_RETRIES + 1):
            _log(f"step {step_name} (attempt {attempt}/{MAX_STEP_RETRIES})")
            ok = script_fn(ctx)
            if ok:
                break

            if attempt < MAX_STEP_RETRIES:
                situation = (
                    f"Step '{step_name}' failed {attempt} time(s). "
                    f"Package: {ctx['package_id']}. "
                    f"What script should I run to recover?"
                )
                advice = _ask_tactical(situation)
                _log(f"AI advice after {step_name} failure: {advice}")
                if advice == "fault":
                    ctx["fault_reason"] = (
                        f"AI advised fault after {step_name} failed {attempt} time(s)"
                    )
                    _script_fault(ctx)
                    return False
                # Run the suggested recovery script once, then retry the step.
                if advice != step_name and advice in SCRIPT_REGISTRY:
                    SCRIPT_REGISTRY[advice](ctx)

        if not ok:
            ctx["fault_reason"] = (
                f"{step_name} failed after {MAX_STEP_RETRIES} attempts"
            )
            _script_fault(ctx)
            return False

    _log(f"task complete: pkg={ctx['package_id']}")
    _post(f"/agv/{AGV_ID}/health", {"health": "alive"})
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
    lane_cells = wh.get("lanes", [])
    bay = list(agv_cfg.get("bay_cell", [0, 0]))
    input_station = list(stations.get("input_arm", bay))
    output_station = list(stations.get("output_arm", bay))

    return {
        "bay": bay,
        "route_to_input": _straight_path(bay, input_station),
        "lane_cells": [list(c) for c in lane_cells] or [input_station],
        "route_to_output": _straight_path(input_station, output_station),
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
