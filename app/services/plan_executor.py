"""plan_executor — delivers the Master-AI plan to the AGV / arm agents.

THE GAP THIS CLOSES
===================
`POST /plan` (qwq:32b) produces `{"assignments":[...]}` and publishes it on the
message bus for the browser, but until now NOTHING delivered those assignments
to the AGV containers — the plan died in memory. `dispatch_routes` had the only
working `/assign` wiring, but it is AI-free (parses a number from text).

`dispatch_plan()` is the bridge: it takes a validated Master-AI plan and, per
assignment, POSTs it to that AGV's container `/assign` and starts the
arm-load/unload choreography. From there each AGV brain runs its own
tactical-AI decision loop (agv_brain) and the arms confirm load/unload via
qwen14b — so the master plan actually drives the robots.

VRAM note: this runs AFTER the planning_window has closed (caller's
responsibility), so the first tactical call is allowed to swap qwq:32b ->
qwen14b and qwen14b then stays resident for the whole execution run.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List

# Reuse the proven container wiring + arm choreography from dispatch_routes
# instead of duplicating it (single source of truth for /assign + arm signals).
from app.routes.dispatch_routes import (
    _agv_url,
    _assign_agv,
    _auto_arm_signals,
    _log,
)

# Rainbow-ish label -> package colour so the visual matches sort ordering.
_LABEL_COLORS = {
    "red": "#e74c3c",
    "orange": "#e67e22",
    "yellow": "#f1c40f",
    "green": "#27ae60",
    "blue": "#2980b9",
    "indigo": "#3f51b5",
    "violet": "#8e44ad",
    "purple": "#8e44ad",
}
_FALLBACK_COLORS = ["#e74c3c", "#e67e22", "#f1c40f", "#27ae60", "#2980b9", "#8e44ad"]


def _color_for(label: str, idx: int) -> str:
    return _LABEL_COLORS.get((label or "").strip().lower(),
                             _FALLBACK_COLORS[idx % len(_FALLBACK_COLORS)])


def dispatch_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Deliver a Master-AI plan to the AGV/arm agents.

    For each assignment: POST it to the AGV container's /assign (the AGV brain
    then runs its tactical-AI loop) and start the AI-confirmed arm choreography.

    Returns {"status","dispatched":[...],"skipped":[...]}.
    Never raises — a bad assignment is skipped and reported.
    """
    assignments: List[dict] = list(plan.get("assignments") or [])
    if not assignments:
        return {"status": "failed", "error": "plan has no assignments"}

    # Honour the planner's sequence so packages enter in the intended order.
    assignments.sort(key=lambda a: a.get("sequence", 0))

    dispatched: List[dict] = []
    skipped: List[dict] = []

    for idx, a in enumerate(assignments):
        agv_id = a.get("agv_id")
        package_id = a.get("package_id")
        label = a.get("label", "")
        if not agv_id or not package_id:
            skipped.append({**a, "_reason": "missing agv_id/package_id"})
            continue
        # Dynamic runtime discovery: an AGV is dispatchable iff it is registered
        # in agv_state (resolved via _agv_url). Replaces the removed static
        # _AGV_CONTAINER_URLS registry.
        if _agv_url(agv_id) is None:
            skipped.append({**a, "_reason": f"unknown/unregistered AGV {agv_id}"})
            continue

        color = _color_for(label, idx)
        ok = _assign_agv(agv_id, package_id, label)
        if not ok:
            _log("planner", f"{agv_id} busy/unreachable — skipping {package_id}")
            skipped.append({**a, "_reason": "assign failed (busy/unreachable)"})
            continue

        dispatched.append({"agv_id": agv_id, "package_id": package_id,
                           "label": label, "sequence": a.get("sequence")})
        _log("planner",
             f"AI plan -> {agv_id} : {package_id} ({label or 'no-label'})")
        # Arm choreography (table -> arm -> AGV, then AGV -> arm -> table),
        # AI-confirmed inside _auto_arm_signals.
        threading.Thread(
            target=_auto_arm_signals,
            args=(agv_id, package_id, color),
            daemon=True,
        ).start()
        # Small stagger so three AGVs don't all leave their bays on the exact
        # same tick — the tactical AI still arbitrates lane/station access.
        time.sleep(0.4)

    status = "dispatched" if dispatched else "failed"
    _log("planner",
         f"Plan dispatched: {len(dispatched)} assignment(s)"
         + (f", {len(skipped)} skipped" if skipped else ""))
    return {"status": status, "dispatched": dispatched, "skipped": skipped}
