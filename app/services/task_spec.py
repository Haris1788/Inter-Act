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
    # US 34 (M4): when True, master holds back announce N+1 until announce N
    # has reached `arm_ready loaded`. Auto-set by the interpreter when the
    # prompt implies ordering ("sort by", "in order", "sequentially",
    # explicit sequence terms). Off by default — concurrency stays the demo's
    # main feature.
    enforce_sequence: bool = False
    # US 34.1: per-package colour + weight, parsed from the prompt by the
    # interpreter or inferred from colour using `ai.weight_bands` in
    # pipeline.yaml. One entry per package, indices align with `ordering` /
    # planner sequence. Each entry: {"label": "<color>", "weight_kg": <float>}.
    # Empty when the prompt didn't mention either; the planner then falls
    # back to colour-only allocation (US 34 behaviour).
    packages: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "package_count": self.package_count,
            "ordering": self.ordering,
            "constraints": self.constraints,
            "agv_ids": self.agv_ids,
            "description": self.description,
            "raw_prompt": self.raw_prompt,
            "enforce_sequence": self.enforce_sequence,
            "packages": self.packages,
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

    # US 34 (M4): enforce_sequence — read from raw if present, else infer
    # from the prompt (heuristic: ordering implied by sort/order/sequence words).
    raw_enforce = raw.get("enforce_sequence")
    if isinstance(raw_enforce, bool):
        enforce_sequence = raw_enforce
    else:
        prompt_lower = (raw_prompt or "").lower()
        sequence_signals = (
            "sort by", "in order", "sequentially", "one after another",
            "one at a time", "in sequence", "rainbow order", "follow the order",
        )
        enforce_sequence = any(sig in prompt_lower for sig in sequence_signals)

    # US 34.1: packages[] — list of {label, weight_kg} per package. Read from
    # raw if the model gave it, else fall back to a colour-keyword scan of
    # the prompt with weight inferred from `ai.weight_bands` (midpoint).
    raw_packages = raw.get("packages")
    if isinstance(raw_packages, list) and raw_packages:
        packages = [_normalize_package(p) for p in raw_packages]
    else:
        packages = _infer_packages_from_prompt(raw_prompt, pkg)

    # FDAS Step 3b: if the model gave no per-package handling, fall back to a
    # prompt-level scan ("send a fragile package" → handling=fragile on all),
    # so the arm brain still has a hint to reason over.
    prompt_handling = _canon_handling(raw_prompt)
    if prompt_handling:
        for p in packages:
            if not p.get("handling"):
                p["handling"] = prompt_handling

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
            enforce_sequence=enforce_sequence,
            packages=packages,
        ),
        None,
    )


def _normalize_package(raw: Any) -> Dict[str, Any]:
    """Coerce one raw package dict into {label, weight_kg, handling}."""
    if not isinstance(raw, dict):
        return {"label": "", "weight_kg": None, "handling": ""}
    label = str(raw.get("label") or raw.get("color") or "").strip().lower()
    weight = raw.get("weight_kg")
    if weight is None:
        weight = raw.get("weight")
    try:
        weight_kg = float(weight) if weight is not None else None
    except (TypeError, ValueError):
        weight_kg = None
    # FDAS Step 3b: handling hint the arm brain uses to pick its handling script.
    handling = _canon_handling(raw.get("handling") or raw.get("handling_hint") or "")
    return {"label": label, "weight_kg": weight_kg, "handling": handling}


# FDAS Step 3b: map free-text handling words → a canonical hint the arm reads.
_HANDLING_KEYWORDS = (
    ("fragile", ("fragile", "delicate", "brittle", "glass", "handle with care")),
    ("hazardous", ("hazard", "hazardous", "dangerous", "toxic", "chemical", "explosive")),
    ("priority", ("urgent", "priority", "rush", "express", "asap", "expedite", "immediately")),
    ("heavy", ("heavy", "bulky", "oversized")),
)


def _canon_handling(text: str) -> str:
    """Return a canonical handling hint (fragile/hazardous/priority/heavy) found
    in `text`, or '' if none. First match wins, in keyword-table order."""
    t = str(text or "").lower()
    for canon, words in _HANDLING_KEYWORDS:
        if canon == t or any(w in t for w in words):
            return canon
    return ""


def _infer_packages_from_prompt(prompt: str, count: int) -> List[Dict[str, Any]]:
    """Heuristic fallback: scan the prompt for colour words + weight numbers.

    Used when the LLM didn't return a structured `packages` list. Walks the
    prompt left-to-right looking for blue/green/yellow keywords; for each
    occurrence picks up the nearest weight number (\"30kg\", \"25 kilos\", etc.)
    if present, else falls back to the midpoint of the colour's band from
    `ai.weight_bands` in pipeline.yaml. Returns up to `count` entries; pads
    with empty packages so the planner can still iterate.
    """
    import re
    from app.models import pipeline_config

    text = (prompt or "").lower()
    bands = pipeline_config.get_weight_bands()

    # First pass: pull every colour word in order, with the weight number
    # that immediately surrounds it (looks both ±20 chars).
    colour_re = re.compile(r"\b(blue|green|yellow)\b")
    weight_re = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:kg|kilo|kilos|kilogram|kilograms)\b")

    found: List[Dict[str, Any]] = []
    for m in colour_re.finditer(text):
        colour = m.group(1)
        window = text[max(0, m.start() - 25): m.end() + 25]
        wm = weight_re.search(window)
        if wm:
            try:
                weight_kg = float(wm.group(1))
            except ValueError:
                weight_kg = None
        else:
            band = bands.get(colour) or {}
            lo = band.get("min")
            hi = band.get("max")
            weight_kg = (float(lo) + float(hi)) / 2.0 if (lo is not None and hi is not None) else None
        found.append({"label": colour, "weight_kg": weight_kg, "handling": ""})

    # Pad / truncate to count
    while len(found) < count:
        found.append({"label": "", "weight_kg": None, "handling": ""})
    return found[:count]
