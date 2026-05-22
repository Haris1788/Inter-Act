"""Plan structure validator — firewall between LLM planner output and execution.

The LLM suggests; Python decides. validate_plan() ensures every assignment has
the required fields and references valid AGV IDs before the executor touches them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def validate_plan(
    raw: Dict[str, Any], spec: Optional[dict] = None
) -> Tuple[Optional[dict], Optional[str]]:
    """Validate and normalise a raw plan dict from the LLM planner.

    Returns (normalised_plan, None) on success, (None, error_message) on failure.
    """
    from app.models import pipeline_config  # late import — avoids circular at load

    assignments = raw.get("assignments")
    if not isinstance(assignments, list) or len(assignments) == 0:
        return None, "plan must have a non-empty 'assignments' list"

    known_agvs = set(pipeline_config.get_agv_ids())
    errors: List[str] = []
    seen_sequences: set = set()

    for i, a in enumerate(assignments):
        if not isinstance(a, dict):
            errors.append(f"assignments[{i}] is not an object")
            continue

        if not a.get("package_id"):
            errors.append(f"assignments[{i}] missing package_id")

        agv_id = a.get("agv_id")
        if agv_id and agv_id not in known_agvs:
            errors.append(
                f"assignments[{i}] references unknown AGV {agv_id!r}; "
                f"valid: {sorted(known_agvs)}"
            )

        seq = a.get("sequence")
        try:
            seq_int = int(seq)
            if seq_int in seen_sequences:
                errors.append(f"duplicate sequence number {seq_int}")
            seen_sequences.add(seq_int)
        except (TypeError, ValueError):
            errors.append(f"assignments[{i}] has non-integer sequence {seq!r}")

    if errors:
        return None, "; ".join(errors)

    normalised = {
        "assignments": [
            {
                "package_id": a.get("package_id", f"pkg-{i + 1}"),
                "agv_id": a.get("agv_id"),
                "sequence": int(a.get("sequence", i + 1)),
                "route": list(a.get("route", ["bay", "input", "output", "bay"])),
                "label": str(a.get("label", "")),
            }
            for i, a in enumerate(assignments)
        ],
        "arm_roles": dict(raw.get("arm_roles") or {}),
        "notes": str(raw.get("notes") or ""),
    }
    return normalised, None
