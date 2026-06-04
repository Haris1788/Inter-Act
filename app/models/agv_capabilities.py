"""Shared AGV capability schema (US 34).

Single source of truth for per-AGV capabilities — read by BOTH the in-process
`VehicleController` (backend asset model) AND the containerized AGV brain.
Avoids the inheritance unification refactor: the schema is data, both code paths
read it the same way.

Read order:
  1. Environment variables (highest priority — set by docker-compose per AGV)
  2. `pipeline.yaml:warehouse.agvs[].capabilities` (fallback)
  3. `pipeline.yaml:ai.capability_defaults` (fallback if no per-AGV override)
  4. Hardcoded module-level DEFAULTS (always-defined safety net)

Per-AGV env var names (set by `scripts/generate_compose.py`):
  AGV_KIND, AGV_WEIGHT_MAX_KG, AGV_ALLOWED_LABELS, AGV_SPEED_FACTOR

`ALLOWED_LABELS` is a comma-separated list, e.g. "red,green,blue" or "any"
(the literal string "any" means accept any label).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Sequence

# ── Hardcoded defaults ────────────────────────────────────────────────────────
DEFAULT_KIND = "shuttle"
DEFAULT_WEIGHT_MAX_KG = 50.0
DEFAULT_ALLOWED_LABELS: List[str] = ["any"]
DEFAULT_SPEED_FACTOR = 1.0


@dataclass
class AGVCapabilities:
    """Per-AGV capability declaration. Immutable after construction."""

    agv_id: str
    kind: str = DEFAULT_KIND
    weight_max_kg: float = DEFAULT_WEIGHT_MAX_KG
    allowed_labels: List[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_LABELS))
    speed_factor: float = DEFAULT_SPEED_FACTOR
    bay_cell: Optional[List[int]] = None

    def accepts_label(self, label: Optional[str]) -> bool:
        """True if this AGV can carry a package with the given label."""
        if not label:
            return True
        if "any" in (l.lower() for l in self.allowed_labels):
            return True
        return label.lower() in (l.lower() for l in self.allowed_labels)

    def to_dict(self) -> dict:
        d = asdict(self)
        # bay_cell is mostly a position field, separate from capabilities
        # proper. Keep it in the dict for snapshot completeness.
        return d


def _parse_labels(raw: Optional[str]) -> List[str]:
    """Parse a comma-separated label list. 'any' (case-insensitive) passes through."""
    if not raw:
        return list(DEFAULT_ALLOWED_LABELS)
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return parts or list(DEFAULT_ALLOWED_LABELS)


def _coerce_float(raw, fallback: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


def from_env(agv_id: str) -> AGVCapabilities:
    """Build capabilities from environment variables (containerized-AGV path).

    Used by the AGV containers via `scripts/agv_server.py` / `scripts/agv_bus.py`.
    Missing env vars fall back to the hardcoded defaults.
    """
    return AGVCapabilities(
        agv_id=agv_id,
        kind=os.environ.get("AGV_KIND", DEFAULT_KIND),
        weight_max_kg=_coerce_float(
            os.environ.get("AGV_WEIGHT_MAX_KG"), DEFAULT_WEIGHT_MAX_KG
        ),
        allowed_labels=_parse_labels(os.environ.get("AGV_ALLOWED_LABELS")),
        speed_factor=_coerce_float(
            os.environ.get("AGV_SPEED_FACTOR"), DEFAULT_SPEED_FACTOR
        ),
    )


def from_config(agv_cfg: dict, ai_defaults: Optional[dict] = None) -> AGVCapabilities:
    """Build capabilities from a `pipeline.yaml` agv entry + optional ai defaults.

    Used by the in-process backend path (`app/models/agv_state.initialize_from_config`).

    Priority within this function:
      agv_cfg["capabilities"]  >  ai_defaults  >  hardcoded defaults
    """
    caps_block = (agv_cfg or {}).get("capabilities") or {}
    defaults = ai_defaults or {}

    def pick(key: str, fallback):
        if key in caps_block:
            return caps_block[key]
        if key in defaults:
            return defaults[key]
        return fallback

    raw_labels = pick("allowed_labels", DEFAULT_ALLOWED_LABELS)
    if isinstance(raw_labels, str):
        allowed_labels = _parse_labels(raw_labels)
    else:
        allowed_labels = [str(l).lower() for l in (raw_labels or [])] or list(
            DEFAULT_ALLOWED_LABELS
        )

    return AGVCapabilities(
        agv_id=agv_cfg["id"],
        kind=str(pick("kind", DEFAULT_KIND)),
        weight_max_kg=_coerce_float(pick("weight_max_kg", DEFAULT_WEIGHT_MAX_KG),
                                     DEFAULT_WEIGHT_MAX_KG),
        allowed_labels=allowed_labels,
        speed_factor=_coerce_float(pick("speed_factor", DEFAULT_SPEED_FACTOR),
                                    DEFAULT_SPEED_FACTOR),
        bay_cell=list(agv_cfg.get("bay") or []) or None,
    )


def to_env_dict(caps: AGVCapabilities) -> dict:
    """Serialize capabilities to env-var pairs for docker-compose injection.

    Used by `scripts/generate_compose.py`.
    """
    return {
        "AGV_KIND": caps.kind,
        "AGV_WEIGHT_MAX_KG": str(caps.weight_max_kg),
        "AGV_ALLOWED_LABELS": ",".join(caps.allowed_labels),
        "AGV_SPEED_FACTOR": str(caps.speed_factor),
    }
