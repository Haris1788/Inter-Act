"""TaskSpec — structured interpreter output + validation firewall.

The LLM suggests; Python decides. validate() is the firewall between raw
model output and the planner. Any field the model got wrong is caught here
rather than silently propagating to downstream agents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

VALID_INTENTS = ("sort", "move", "demo", "custom")
MIN_PACKAGES = 1
MAX_PACKAGES = 10


@dataclass
class TaskSpec:
    intent: str
    package_count: int
    ordering: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    agv_ids: List[str] = field(default_factory=list)
    description: str = ""
    raw_prompt: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "package_count": self.package_count,
            "ordering": self.ordering,
            "constraints": self.constraints,
            "agv_ids": self.agv_ids,
            "description": self.description,
            "raw_prompt": self.raw_prompt,
        }


def validate(
    raw: Dict[str, Any], raw_prompt: str = ""
) -> Tuple[Optional[TaskSpec], Optional[str]]:
    """Validate a raw LLM output dict against the TaskSpec schema.

    Returns (TaskSpec, None) on success, (None, error_message) on failure.
    This is the validation firewall — keeps the planner safe from bad model output.
    """
    from app.models import pipeline_config  # late import avoids circular at module load

    errors: List[str] = []

    # intent
    intent = raw.get("intent", "")
    if not isinstance(intent, str) or intent not in VALID_INTENTS:
        errors.append(f"intent must be one of {VALID_INTENTS}, got {intent!r}")

    # package_count
    pkg_raw = raw.get("package_count")
    try:
        pkg = int(pkg_raw)
        if not (MIN_PACKAGES <= pkg <= MAX_PACKAGES):
            errors.append(
                f"package_count must be {MIN_PACKAGES}–{MAX_PACKAGES}, got {pkg}"
            )
    except (TypeError, ValueError):
        errors.append(f"package_count must be an integer, got {pkg_raw!r}")
        pkg = MIN_PACKAGES

    # ordering
    ordering = raw.get("ordering", [])
    if not isinstance(ordering, list):
        errors.append("ordering must be a list")
        ordering = []
    ordering = [str(x) for x in ordering]

    # constraints
    constraints = raw.get("constraints", [])
    if not isinstance(constraints, list):
        constraints = []
    constraints = [str(x) for x in constraints]

    # agv_ids — must be a subset of known pipeline AGV IDs
    agv_ids = raw.get("agv_ids", [])
    if not isinstance(agv_ids, list):
        errors.append("agv_ids must be a list")
        agv_ids = []
    known = set(pipeline_config.get_agv_ids())
    bad = [aid for aid in agv_ids if aid not in known]
    if bad:
        errors.append(f"unknown AGV IDs: {bad}; valid IDs are: {sorted(known)}")

    # description
    description = str(raw.get("description", ""))

    if errors:
        return None, "; ".join(errors)

    return (
        TaskSpec(
            intent=str(intent),
            package_count=int(pkg),
            ordering=ordering,
            constraints=constraints,
            agv_ids=list(agv_ids),
            description=description,
            raw_prompt=raw_prompt,
        ),
        None,
    )
