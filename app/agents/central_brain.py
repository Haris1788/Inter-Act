"""CentralBrain — master AI coordinator (Phase 4).

Responsibilities:
  1. make_plan(spec, llm)  — call qwq:32b to produce per-AGV/arm assignments,
                             validate with plan_validator, one re-ask on failure.
  2. reassign(failed, task) — deterministic next-healthy-AGV lookup, ZERO model
                              calls (critical for VRAM-thrash prevention).
  3. _on_agv_fault(data)    — bus subscriber; auto-triggers reassign on fault events.

make_plan accepts an optional `llm` argument so the caller (POST /plan) can pass
the already-warm planning_window LLM, keeping interpret + plan in ONE GPU load.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from app.services.plan_validator import validate_plan as _validate_plan


_PLANNER_SYSTEM = """You are the Master Planner AI for INTER.act warehouse automation.

Given a TaskSpec, produce a concrete execution plan assigning packages to AGVs.

Return a SINGLE JSON object — no markdown, no explanation, JSON only:

{
  "assignments": [
    {
      "package_id": "<pkg-N>",
      "agv_id":     "<agv-id from spec.agv_ids>",
      "sequence":   <unique integer starting at 1>,
      "route":      ["bay", "input", "output", "bay"],
      "label":      "<sort label from ordering, or empty string>"
    }
  ],
  "arm_roles": {
    "<arm-id>": "input | output | relay"
  },
  "notes": "<optional one-sentence plan description>"
}

Rules:
- One assignment per package; sequence numbers must be unique integers from 1.
- route is always ["bay", "input", "output", "bay"] — do not change it.
- Distribute packages across the agv_ids in the spec as evenly as possible.
- If spec.ordering is provided, assign labels from that list in order.
- Return ONLY the JSON object."""


class CentralBrain:
    """Master AI coordinator. Created once in create_app(), used as a singleton."""

    def __init__(self, bus=None) -> None:
        self._lock = threading.Lock()
        self._current_plan: Optional[dict] = None
        self._bus = bus
        if bus is not None:
            bus.subscribe("agv_fault", self._on_agv_fault)

    # ── planning ──────────────────────────────────────────────────────────────

    def make_plan(self, spec: dict, llm=None) -> Dict[str, Any]:
        """Generate and validate an execution plan for `spec`.

        Pass `llm` (from an open planning_window) to reuse the warm qwq:32b
        load from the interpret step — no model swap, no extra warmup.
        If `llm` is None, opens its own planning_window.
        """
        user_msg = (
            f"TaskSpec:\n"
            f"  intent:        {spec.get('intent')}\n"
            f"  package_count: {spec.get('package_count')}\n"
            f"  ordering:      {spec.get('ordering')}\n"
            f"  constraints:   {spec.get('constraints')}\n"
            f"  agv_ids:       {spec.get('agv_ids')}\n"
            f"  description:   {spec.get('description')}\n\n"
            f"Produce the execution plan JSON."
        )

        raw, error = self._call_planner(user_msg, llm, num_predict=1024)
        if error:
            return {"status": "failed", "error": error}

        if raw.get("status") != "success":
            return raw

        plan, val_err = _validate_plan(raw, spec)
        if plan:
            self._store(plan)
            return {"status": "success", "plan": plan, "_model": raw.get("_model")}

        # one re-ask with the validation error
        repair_msg = (
            f"{user_msg}\n\n"
            f"Your previous response had validation errors: {val_err}\n"
            f"Fix and return ONLY valid JSON."
        )
        raw2, error2 = self._call_planner(repair_msg, llm, num_predict=1024)
        if error2:
            return {"status": "failed", "error": error2}
        if raw2.get("status") != "success":
            return raw2

        plan2, val_err2 = _validate_plan(raw2, spec)
        if plan2:
            self._store(plan2)
            return {"status": "success", "plan": plan2, "_model": raw2.get("_model")}

        return {
            "status": "failed",
            "error": f"Planner could not produce valid plan after re-ask: {val_err2}",
        }

    def _call_planner(self, user_msg: str, llm, num_predict: int):
        """Call the planner LLM, opening a window if needed.

        Returns (result_dict, None) on network success,
                (None, error_str) if a window couldn't be opened.
        """
        try:
            if llm is not None:
                return llm.plan(_PLANNER_SYSTEM, user_msg, num_predict=num_predict), None
            from app.services.llm_service import planning_window
            with planning_window() as lm:
                return lm.plan(_PLANNER_SYSTEM, user_msg, num_predict=num_predict), None
        except Exception as exc:
            return None, str(exc)

    def _store(self, plan: dict) -> None:
        with self._lock:
            self._current_plan = plan

    def get_current_plan(self) -> Optional[dict]:
        with self._lock:
            return dict(self._current_plan) if self._current_plan else None

    # ── deterministic reassignment (ZERO model calls) ─────────────────────────

    def reassign(self, failed_agv_id: str, assignment: dict) -> Dict[str, Any]:
        """Replace failed_agv_id with the next healthy idle AGV.

        Entirely deterministic Python — no LLM is called.
        Returns {"status":"success","new_agv_id":"...", "assignment":{...}}
             or {"status":"failed","error":"..."}.
        """
        from app.models import agv_state as _agv

        candidates = [
            aid
            for aid, rec in _agv.get_all().items()
            if aid != failed_agv_id
            and _agv.is_alive(aid)
            and rec.get("status") == "idle"
        ]

        if not candidates:
            return {
                "status": "failed",
                "error": f"No idle AGV available to replace {failed_agv_id}",
            }

        new_id = candidates[0]
        updated = {**assignment, "agv_id": new_id, "reassigned_from": failed_agv_id}

        if self._bus:
            self._bus.publish(
                "reassignment",
                {
                    "failed_agv": failed_agv_id,
                    "new_agv": new_id,
                    "assignment": updated,
                    "timestamp": time.strftime("%H:%M:%S"),
                },
            )

        return {"status": "success", "new_agv_id": new_id, "assignment": updated}

    # ── bus subscriber ────────────────────────────────────────────────────────

    def _on_agv_fault(self, data: dict) -> None:
        agv_id = data.get("agv_id")
        plan = self.get_current_plan()
        if plan is None or not agv_id:
            return
        for assignment in plan.get("assignments", []):
            if assignment.get("agv_id") == agv_id:
                self.reassign(agv_id, assignment)


# ── module-level singleton (replaced by create_app) ──────────────────────────
_brain: Optional[CentralBrain] = None


def get_brain() -> CentralBrain:
    global _brain
    if _brain is None:
        from app.agents.message_bus import get_bus
        _brain = CentralBrain(bus=get_bus())
    return _brain
