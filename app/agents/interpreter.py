"""Interpreter AI (US 28) — natural-language prompt → structured TaskSpec.

Uses qwq:32b inside a single planning_window so interpret + optional re-ask
share one warm GPU load with no model swap between them.

Flow:
    user prompt
      → build_interpreter_prompt()  (live pipeline + AGV state)
      → llm.plan()                  (qwq:32b, planning window)
      → task_spec.validate()        (Python firewall)
      → on failure: one re-ask with the validation errors attached
      → TaskSpec dict  or  structured error dict
"""

from __future__ import annotations

import time
from typing import Any, Dict

from app.services.llm_service import planning_window
from app.services.prompt_builder import build_interpreter_prompt
from app.services import task_spec as ts


def interpret(user_prompt: str) -> Dict[str, Any]:
    """Interpret a natural-language prompt into a validated TaskSpec.

    Returns a dict with:
        {"status": "success", "task_spec": {...}, "_model": "qwq:..."}
    or:
        {"status": "failed|timeout|exception", "error": "..."}
    """
    system = build_interpreter_prompt()

    with planning_window() as llm:
        # ── first attempt ────────────────────────────────────────────────────
        result = llm.plan(system, user_prompt, num_predict=512)

        if result.get("status") != "success":
            return result  # timeout / HTTP error — pass through as-is

        spec, error = ts.validate(result, raw_prompt=user_prompt)
        if spec:
            return {
                "status": "success",
                "task_spec": spec.to_dict(),
                "_model": result.get("_model"),
            }

        # ── one re-ask (same planning window — no model swap) ────────────────
        repair_msg = (
            f"{user_prompt}\n\n"
            f"Your previous response had these validation errors:\n  {error}\n\n"
            f"Please fix and return ONLY the corrected JSON object."
        )
        result2 = llm.plan(system, repair_msg, num_predict=512)

        if result2.get("status") != "success":
            return result2

        spec2, error2 = ts.validate(result2, raw_prompt=user_prompt)
        if spec2:
            return {
                "status": "success",
                "task_spec": spec2.to_dict(),
                "_model": result2.get("_model"),
            }

    return {
        "status": "failed",
        "error": (
            f"Interpreter could not produce a valid TaskSpec after re-ask. "
            f"Last error: {error2}"
        ),
        "raw": result2.get("raw", ""),
    }
