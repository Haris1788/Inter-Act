"""Single Source of Truth for pipeline configuration.

This module is the SOLE AUTHORITY for:
    - arm count, IDs, and labels
    - dependency graph / topology
    - handover zone positions and names
    - valid object states (intake, carried, handover_1..N-1, output)
    - scene layout (canvas size, arm geometry, zone rects)
    - failure policies and health check settings

All other modules MUST consume from this module - never recompute
or hardcode these values independently:
    - app/models/scene_config.py         → delegates to generate_scene_config()
    - app/models/arm_state.py            → initializes from generate_arm_state()
    - app/routes/arm_routes.py           → validates against get_valid_handover_states()
    - scripts/run_demo.py                → builds controllers from get_arms()
    - scripts/generate_compose.py        → reads arm list from pipeline.yaml
    - app/static/js/simulation.js        → fetches /config (populated from here)

Changing pipeline.yaml + calling reload() is the ONLY way to change
the above values at runtime. Adding a hardcoded arm count or handover
name elsewhere is a violation of this contract.
"""

import os
import yaml

_CONFIG_PATH = os.environ.get(
    "PIPELINE_CONFIG",
    os.path.join(os.path.dirname(__file__), "..", "..", "pipeline.yaml"),
)

_config = None

MIN_ANGLE = 0.0
MAX_ANGLE = 180.0
DEFAULT_ANGLE = 90.0
ASSET_PORT_BASE = 8010
ASSET_PORT_STEP = 10


def load_config(path=None):
    """Read and return the parsed pipeline.yaml."""
    global _config
    p = path or _CONFIG_PATH
    p = os.path.abspath(p)
    with open(p) as f:
        _config = yaml.safe_load(f)
    return _config


def get_config():
    """Return cached config, loading from disk on first call."""
    if _config is None:
        load_config()
    return _config


def get_arms():
    """Return the list of arm dicts from config."""
    return get_config()["arms"]


def get_arm_count():
    return len(get_arms())


def get_arm_ids():
    return [arm["id"] for arm in get_arms()]


def get_asset_host_ports(base_port=ASSET_PORT_BASE, step=ASSET_PORT_STEP):
    """Deterministic host port assignment for non-arm asset services.

    Ports are derived from the order in `assets:` so every subsystem
    (compose generation, demo runner localhost fallback, backend proxy)
    uses the same mapping without hardcoded IDs.
    """
    extras = get_extra_assets()
    return {a["id"]: base_port + i * step for i, a in enumerate(extras)}


def get_pipeline_mode():
    return get_config().get("pipeline", {}).get("mode", "sequential")


def get_failure_policy(arm_id=None):
    """Return failure policy for an arm. Falls back to default if not set per-arm."""
    cfg = get_config()
    default = cfg.get("failure_policy", {"default": "stop"})

    if arm_id is not None:
        for arm in get_arms():
            if arm["id"] == arm_id:
                per_arm = arm.get("failure_policy")
                if per_arm:
                    return {"action": per_arm, **default}
                break

    return {"action": default.get("default", "stop"), **default}


def generate_scene_config():
    """Build the full scene config dict using graph-driven layout.

    The layout engine:
    1. Builds a flow order from the asset graph
    2. Assigns each asset a visual block with computed positions
    3. Derives handover zones from connection gaps
    4. Computes canvas dimensions from total layout width

    This single algorithm handles all cases: arms-only, arms+vehicle,
    multiple vehicles in series, without conditional branching.
    """
    cfg = get_config()
    arms_cfg = cfg["arms"]
    extra_assets = get_extra_assets()

    layout = _compute_graph_layout(arms_cfg, extra_assets)

    return {
        "canvas_width": layout["canvas_width"],
        "canvas_height": layout["canvas_height"],
        "arms": layout["arms"],
        "assets": layout["assets"],
        "reach_zones": layout["reach_zones"],
        "handover_zones": layout["handover_zones"],
        "intake_zone": layout["intake_zone"],
        "output_zone": layout["output_zone"],
        "labels": layout["labels"],
        "min_angle": MIN_ANGLE,
        "max_angle": MAX_ANGLE,
        "default_angle": DEFAULT_ANGLE,
    }


def _compute_graph_layout(arms_cfg, extra_assets):
    """Graph-driven layout engine.

    Computes positions, handover zones, and canvas size from the asset graph
    without conditional logic based on asset counts.
    """
    # Layout constants
    CANVAS_HEIGHT = 600
    CENTER_Y = 300
    LEFT_PADDING = 120
    RIGHT_PADDING = 120
    ARM_SPACING = 300
    DEFAULT_ARM_LENGTH = 150
    MIN_GAP = 40
    ZONE_WIDTH = 105
    ZONE_HEIGHT = 180

    # Build unified asset list with IDs
    all_assets = []
    asset_by_id = {}

    # Add arms
    for i, arm in enumerate(arms_cfg):
        arm_id = f"arm-{arm['id']}"
        asset = {
            "id": arm_id,
            "numeric_id": arm["id"],
            "type": "arm",
            "config": arm,
            "upstream": [f"arm-{d}" for d in arm.get("depends_on", [])],
            "downstream": [],
        }
        all_assets.append(asset)
        asset_by_id[arm_id] = asset

    # Add extra assets (vehicles)
    for ea in extra_assets:
        asset = {
            "id": ea["id"],
            "type": ea["type"],
            "config": ea,
            "upstream": ea.get("upstream", []),
            "downstream": ea.get("downstream", []),
        }
        all_assets.append(asset)
        asset_by_id[ea["id"]] = asset

    # Wire downstream links from upstream declarations
    for asset in all_assets:
        for up_id in asset["upstream"]:
            if up_id in asset_by_id:
                up = asset_by_id[up_id]
                if asset["id"] not in up["downstream"]:
                    up["downstream"].append(asset["id"])

    # Build flow order: topological sort with stable arm ordering
    flow_order = _build_flow_order(all_assets, asset_by_id)

    # Position assets using flow order with boundary-based positioning
    positioned = []
    x = LEFT_PADDING
    prev_asset = None

    for i, asset in enumerate(flow_order):
        # Determine gap based on adjacent asset types
        if prev_asset:
            # Arm-to-arm: circles should touch exactly (no gap)
            if prev_asset["type"] == "arm" and asset["type"] == "arm":
                gap = 0
            # Non-arm assets need spacing for handover zones
            else:
                gap = MIN_GAP
        else:
            gap = 0

        if asset["type"] == "arm":
            arm_cfg = asset["config"]
            length = int(arm_cfg.get("length", DEFAULT_ARM_LENGTH))

            # For arm-to-arm: pivot at x + length so circles touch
            # For asset-to-arm: pivot at x + gap + length
            pivot_x = x + gap + length

            positioned.append(
                {
                    "type": "arm",
                    "id": asset["id"],
                    "numeric_id": asset["numeric_id"],
                    "pivot_x": pivot_x,
                    "pivot_y": CENTER_Y,
                    "length": length,
                    "color": arm_cfg.get("color", "#888"),
                    "label": arm_cfg.get("label", f"Arm {asset['numeric_id']}"),
                    "depends_on": arm_cfg.get("depends_on", []),
                }
            )

            # Next asset starts at right edge of this arm's reach
            x = pivot_x + length

        elif asset["type"] == "vehicle":
            waypoints = asset["config"]["geometry"]["waypoints"]
            if waypoints:
                path_length = abs(waypoints[-1]["x"] - waypoints[0]["x"])
                start_x = x + gap

                # Rewrite waypoints to fit current layout position
                offset = start_x - waypoints[0]["x"]
                adjusted_waypoints = [
                    {"x": wp["x"] + offset, "y": wp.get("y", CENTER_Y)}
                    for wp in waypoints
                ]

                positioned.append(
                    {
                        "type": "vehicle",
                        "id": asset["id"],
                        "label": asset["config"]["label"],
                        "color": asset["config"]["color"],
                        "upstream": asset["upstream"],
                        "downstream": asset["downstream"],
                        "geometry": {
                            **asset["config"]["geometry"],
                            "waypoints": adjusted_waypoints,
                        },
                    }
                )

                x = start_x + path_length

        prev_asset = asset

    canvas_width = x + RIGHT_PADDING

    # Extract positioned arms for legacy arms array
    arms = [p for p in positioned if p["type"] == "arm"]

    # Extract extra assets
    assets = [p for p in positioned if p["type"] != "arm"]

    # Build reach zones
    reach_zones = [
        {
            "id": arm["numeric_id"],
            "cx": arm["pivot_x"],
            "cy": arm["pivot_y"],
            "radius": arm["length"],
            "color": arm["color"],
        }
        for arm in arms
    ]

    # Derive handover zones from connections (frontend will draw these dynamically)
    # Legacy backend expects arm-to-arm handover zones only
    handover_zones = []
    for i in range(len(arms) - 1):
        mid_x = (arms[i]["pivot_x"] + arms[i + 1]["pivot_x"]) / 2
        handover_zones.append(
            {
                "id": f"handover_{i + 1}",
                "label": f"Handover {i + 1}",
                "x": int(mid_x - 90 // 2),
                "y": CENTER_Y - 190 // 2,
                "width": 90,
                "height": 190,
                "color": "#f39c12",
            }
        )

    # Intake and output zones
    first_arm = arms[0]
    last_arm = arms[-1]

    intake_x = first_arm["pivot_x"] - first_arm["length"] - ZONE_WIDTH // 2
    intake_y = CENTER_Y - ZONE_HEIGHT // 2
    intake_zone = {
        "x": intake_x,
        "y": intake_y,
        "width": ZONE_WIDTH,
        "height": ZONE_HEIGHT,
        "color": "#95a5a6",
        "label": "INTAKE",
        "label_x": intake_x + ZONE_WIDTH // 2,
        "label_y": intake_y - 8,
        "entry_x": intake_x + ZONE_WIDTH // 2,
        "entry_y": intake_y + 15,
    }

    output_x = last_arm["pivot_x"] + last_arm["length"] - ZONE_WIDTH // 2
    output_y = CENTER_Y - ZONE_HEIGHT // 2
    output_zone = {
        "x": output_x,
        "y": output_y,
        "width": ZONE_WIDTH,
        "height": ZONE_HEIGHT,
        "color": "#27ae60",
        "label": "OUTPUT",
        "label_x": output_x + ZONE_WIDTH // 2,
        "label_y": output_y - 8,
    }

    labels = {
        "intake": {"text": "INTAKE", "x": intake_zone["label_x"], "y": 200},
        "output": {"text": "OUTPUT", "x": output_zone["label_x"], "y": 200},
    }

    # Normalize arms array to remove internal fields
    normalized_arms = [
        {
            "id": arm["numeric_id"],
            "pivot_x": arm["pivot_x"],
            "pivot_y": arm["pivot_y"],
            "length": arm["length"],
            "color": arm["color"],
            "label": arm["label"],
        }
        for arm in arms
    ]

    return {
        "canvas_width": canvas_width,
        "canvas_height": CANVAS_HEIGHT,
        "arms": normalized_arms,
        "assets": assets,
        "reach_zones": reach_zones,
        "handover_zones": handover_zones,
        "intake_zone": intake_zone,
        "output_zone": output_zone,
        "labels": labels,
    }


# Hook generate_scene_config so /config also exposes the unified asset graph
# without changing existing keys. Defined as a wrapper to avoid mutating the
# layout return shape used by older consumers.
_orig_generate_scene_config = generate_scene_config


def generate_scene_config():  # noqa: F811 - intentional override
    """Augment the scene config with `asset_graph` for backend consumers.

    Existing keys (`arms`, `assets`, `reach_zones`, `handover_zones`,
    `intake_zone`, `output_zone`, `labels`, `canvas_*`, `min_angle`,
    `max_angle`, `default_angle`) are preserved verbatim. New top-level
    `asset_graph` key carries the unified registry from `get_all_assets()`.
    """
    base = _orig_generate_scene_config()
    base["asset_graph"] = get_all_assets()
    # Canonical linear topology used by runtime ownership/transfer semantics.
    # Frontend should build handover zones from adjacent pairs in this list,
    # not from dependency edges embedded in asset_graph.
    base["module_flow_order"] = get_module_flow_order()
    valid, errors = validate_asset_graph()
    base["graph_valid"] = valid
    base["graph_errors"] = errors
    return base


def _build_flow_order(all_assets, asset_by_id):
    """Build visual flow order from asset graph using topological sort.

    Preserves stable arm ordering while inserting vehicles at their
    logical connection points.
    """
    # Separate arms and non-arms
    arms = [a for a in all_assets if a["type"] == "arm"]
    non_arms = [a for a in all_assets if a["type"] != "arm"]

    # Sort arms by numeric ID for stable ordering
    arms.sort(key=lambda a: a["numeric_id"])

    # Build flow by inserting non-arms between their connected arms
    flow = []
    arm_positions = {a["id"]: i for i, a in enumerate(arms)}

    # Assign each non-arm to the gap between its upstream and downstream arms
    gaps = {
        i: [] for i in range(len(arms))
    }  # gap[i] = assets between arm[i] and arm[i+1]

    non_arm_order = {a["id"]: idx for idx, a in enumerate(non_arms)}
    for na in non_arms:
        # Find which arms this connects
        upstream_arms = [aid for aid in na["upstream"] if aid in arm_positions]
        downstream_arms = [aid for aid in na["downstream"] if aid in arm_positions]

        if upstream_arms and downstream_arms:
            # Place in gap after the max upstream arm
            max_up_pos = max(arm_positions[aid] for aid in upstream_arms)
            min_down_pos = min(arm_positions[aid] for aid in downstream_arms)

            # Insert in the gap if it's sequential
            if max_up_pos + 1 == min_down_pos:
                gaps[max_up_pos].append(na)
            else:
                # Non-sequential connection - place at upstream position
                gaps[max_up_pos].append(na)
        elif upstream_arms:
            # Only upstream defined - place after max upstream
            max_up_pos = max(arm_positions[aid] for aid in upstream_arms)
            gaps[max_up_pos].append(na)
        elif downstream_arms:
            # Only downstream defined - place before min downstream
            min_down_pos = min(arm_positions[aid] for aid in downstream_arms)
            if min_down_pos > 0:
                gaps[min_down_pos - 1].append(na)

    def _toposort_gap_assets(nodes):
        if len(nodes) <= 1:
            return nodes

        ids = {n["id"] for n in nodes}
        by_id = {n["id"]: n for n in nodes}
        in_degree = {n["id"]: 0 for n in nodes}
        down = {n["id"]: [] for n in nodes}

        for n in nodes:
            nid = n["id"]
            for up_id in n.get("upstream", []):
                if up_id in ids:
                    in_degree[nid] += 1
                    down[up_id].append(nid)

        queue = sorted(
            [nid for nid, deg in in_degree.items() if deg == 0],
            key=lambda nid: non_arm_order.get(nid, 0),
        )
        ordered = []
        while queue:
            nid = queue.pop(0)
            ordered.append(by_id[nid])
            for did in down[nid]:
                in_degree[did] -= 1
                if in_degree[did] == 0:
                    queue.append(did)
            queue.sort(key=lambda x: non_arm_order.get(x, 0))

        if len(ordered) == len(nodes):
            return ordered

        # Defensive fallback for malformed/cyclic non-arm subgraphs.
        remaining = [n for n in nodes if n["id"] not in {a["id"] for a in ordered}]
        remaining.sort(key=lambda n: non_arm_order.get(n["id"], 0))
        return ordered + remaining

    # Build final flow order
    for i, arm in enumerate(arms):
        flow.append(arm)
        flow.extend(_toposort_gap_assets(gaps.get(i, [])))

    return flow


def get_module_flow_order():
    """Return list of asset string ids in pipeline flow order.

    Combines arms (sorted by numeric id) with non-arm assets inserted at
    their connection points, using the same topological algorithm as the
    layout engine (`_build_flow_order`). Used by the Phase 4 pipeline
    builder so it can wire modules linearly without any type-specific
    branching.

    Example: ['arm-1', 'arm-2', 'veh-a', 'arm-3', 'arm-4', 'veh-b', 'arm-5'].
    """
    arms_cfg = get_config().get("arms") or []
    extras = get_extra_assets()

    all_assets = []
    asset_by_id = {}
    for arm in arms_cfg:
        sid = f"arm-{arm['id']}"
        a = {
            "id": sid,
            "numeric_id": arm["id"],
            "type": "arm",
            "config": arm,
            "upstream": [f"arm-{d}" for d in arm.get("depends_on", [])],
            "downstream": [],
        }
        all_assets.append(a)
        asset_by_id[sid] = a
    for ea in extras:
        a = {
            "id": ea["id"],
            "type": ea["type"],
            "config": ea,
            "upstream": list(ea.get("upstream", [])),
            "downstream": list(ea.get("downstream", [])),
        }
        all_assets.append(a)
        asset_by_id[ea["id"]] = a

    # Wire downstream from upstream declarations.
    for asset in all_assets:
        for up_id in asset["upstream"]:
            up = asset_by_id.get(up_id)
            if up and asset["id"] not in up["downstream"]:
                up["downstream"].append(asset["id"])

    return [a["id"] for a in _build_flow_order(all_assets, asset_by_id)]


def get_extra_assets():
    """Return pass-through `assets:` entries for non-arm types (vehicle).

    Arms remain defined under `arms:` and are consumed by the backend. This
    helper only surfaces additional asset types for the frontend scene config.
    Conveyor entries are rejected - see `validate_asset_graph`.
    """
    cfg = get_config()
    raw = cfg.get("assets") or []
    out = []
    for entry in raw:
        t = entry.get("type")
        if t == "arm":
            continue  # Arms are defined under `arms:` - skip duplicates.
        if t == "conveyor":
            raise ValueError(
                f"Asset {entry.get('id')!r}: type 'conveyor' is no longer supported. "
                f"Remove it from pipeline.yaml."
            )
        if t == "vehicle":
            g = {
                "waypoints": entry.get("waypoints", []),
                "loop": bool(entry.get("loop", False)),
                "speed": float(entry.get("speed", 100)),
            }
        else:
            continue  # Unknown type - ignore (validate_asset_graph reports it).
        out.append(
            {
                "id": entry["id"],
                "type": t,
                "label": entry.get("label", entry["id"]),
                "color": entry.get("color", "#888"),
                "upstream": entry.get("upstream", []),
                "downstream": entry.get("downstream", []),
                "geometry": g,
            }
        )
    return out


def generate_arm_state():
    """Build initial arm state dict: {1: {arm_id:1, angle:90, status:idle}, ...}."""
    return {
        arm["id"]: {"arm_id": arm["id"], "angle": DEFAULT_ANGLE, "status": "idle"}
        for arm in get_arms()
    }


def get_valid_handover_states():
    """Return tuple of valid object states based on arm count.

    For 3 arms: ('intake', 'carried', 'handover_1', 'handover_2', 'output')
    For 4 arms: ('intake', 'carried', 'handover_1', 'handover_2', 'handover_3', 'output')
    """
    n = get_arm_count()
    handover_states = tuple(f"handover_{i}" for i in range(1, n))
    return ("intake", "carried") + handover_states + ("output",)


def get_handover_name(index):
    """Return handover state name for the transfer point at index (0-based).

    Index 0 = between arm 1 and arm 2 = 'handover_1'
    """
    return f"handover_{index + 1}"


def get_dependencies(arm_id):
    """Return list of arm IDs this arm depends on."""
    for arm in get_arms():
        if arm["id"] == arm_id:
            return arm.get("depends_on", [])
    return []


def get_dependents(arm_id):
    """Return list of arm IDs that depend on this arm (downstream)."""
    return [arm["id"] for arm in get_arms() if arm_id in arm.get("depends_on", [])]


def get_all_downstream(arm_id):
    """Return all arms transitively downstream of arm_id (full cascade)."""
    visited = set()
    queue = get_dependents(arm_id)
    while queue:
        current = queue.pop(0)
        if current not in visited:
            visited.add(current)
            queue.extend(get_dependents(current))
    return sorted(visited)


def is_critical(arm_id):
    """Return whether this arm is marked critical in config."""
    for arm in get_arms():
        if arm["id"] == arm_id:
            return arm.get("critical", False)
    return False


def validate_dependency_graph():
    """Check for cycles and missing references in the dependency graph.

    Returns (valid: bool, errors: list[str]).
    """
    arm_ids = set(get_arm_ids())
    errors = []

    for arm in get_arms():
        for dep in arm.get("depends_on", []):
            if dep not in arm_ids:
                errors.append(f"Arm {arm['id']} depends on non-existent arm {dep}")

    # Cycle detection via topological sort
    in_degree = {a["id"]: 0 for a in get_arms()}
    for arm in get_arms():
        for dep in arm.get("depends_on", []):
            if dep in in_degree:
                in_degree[arm["id"]] += 1

    queue = [aid for aid, deg in in_degree.items() if deg == 0]
    sorted_count = 0
    while queue:
        current = queue.pop(0)
        sorted_count += 1
        for dependent in get_dependents(current):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if sorted_count != len(arm_ids):
        errors.append("Dependency cycle detected in arm configuration")

    return len(errors) == 0, errors


def get_topological_order():
    """Return arm IDs in dependency order (sources first)."""
    in_degree = {a["id"]: 0 for a in get_arms()}
    for arm in get_arms():
        for dep in arm.get("depends_on", []):
            if dep in in_degree:
                in_degree[arm["id"]] += 1

    queue = sorted([aid for aid, deg in in_degree.items() if deg == 0])
    result = []
    while queue:
        current = queue.pop(0)
        result.append(current)
        for dependent in sorted(get_dependents(current)):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)
    return result


# ---------------------------------------------------------------------------
# Unified asset registry (Phase 1 backend foundation).
# Arms remain backend-controlled; vehicles are registered but passive
# (no controller, no socket events). All assets are exposed here as
# plain dicts so consumers can iterate the full graph without recomputing.
# ---------------------------------------------------------------------------

ASSET_TYPES = ("arm", "vehicle")


def _arm_string_id(numeric_id):
    return f"arm-{numeric_id}"


def get_all_assets():
    """Return the unified asset registry as a list of dicts.

    Shape per entry:
        {
          'id':         str,        # 'arm-1' | 'veh-a'
          'type':       str,        # 'arm' | 'vehicle'
          'label':      str,
          'color':      str,
          'upstream':   list[str],  # asset IDs
          'downstream': list[str],  # asset IDs
          'status':     str,        # 'idle' (initial)
          'geometry':   dict,       # type-specific
          'critical':   bool,       # arms only; False for non-arm
          'numeric_id': int|None,   # arms only
        }

    Order: arms first by numeric id, then non-arm assets in flow order.
    Geometry for arms is the positioned pivot (after layout engine), so
    consumers see exactly the same coordinates the frontend renders.
    """
    cfg = get_config()
    arms_cfg = cfg.get("arms") or []
    extras = get_extra_assets()
    scene = _compute_graph_layout(arms_cfg, extras)

    out = []
    arms_cfg_by_id = {a["id"]: a for a in arms_cfg}

    for arm in scene["arms"]:
        aid = arm["id"]
        sid = _arm_string_id(aid)
        raw = arms_cfg_by_id.get(aid, {})

        upstream = [_arm_string_id(d) for d in raw.get("depends_on", [])]
        downstream = [
            _arm_string_id(other["id"])
            for other in arms_cfg
            if aid in other.get("depends_on", [])
        ]

        # Bridge with non-arm graph so a full upstream/downstream view exists.
        for ea in extras:
            if sid in ea.get("upstream", []) and ea["id"] not in downstream:
                downstream.append(ea["id"])
            if sid in ea.get("downstream", []) and ea["id"] not in upstream:
                upstream.append(ea["id"])

        out.append(
            {
                "id": sid,
                "type": "arm",
                "label": arm["label"],
                "color": arm["color"],
                "upstream": upstream,
                "downstream": downstream,
                "status": "idle",
                "critical": bool(raw.get("critical", False)),
                "numeric_id": aid,
                "geometry": {
                    "pivot": {"x": arm["pivot_x"], "y": arm["pivot_y"]},
                    "length": arm["length"],
                    "min_angle": MIN_ANGLE,
                    "max_angle": MAX_ANGLE,
                },
            }
        )

    for ea in scene["assets"]:
        out.append(
            {
                "id": ea["id"],
                "type": ea["type"],
                "label": ea["label"],
                "color": ea["color"],
                "upstream": list(ea.get("upstream", [])),
                "downstream": list(ea.get("downstream", [])),
                "status": "idle",
                "critical": False,
                "numeric_id": None,
                "geometry": ea["geometry"],
            }
        )

    return out


def get_asset(asset_id):
    """Lookup unified asset by string id. Returns None if not found."""
    for a in get_all_assets():
        if a["id"] == asset_id:
            return a
    return None


def get_asset_ids():
    """Return list of all asset ids in registry order."""
    return [a["id"] for a in get_all_assets()]


def get_assets_by_type(asset_type):
    """Return assets of a given type (arm | vehicle)."""
    return [a for a in get_all_assets() if a["type"] == asset_type]


def validate_asset_graph():
    """Validate the full asset graph (arms + extras).

    Checks:
    - Arm `depends_on` references valid arm numeric ids (existing rule).
    - Non-arm `upstream`/`downstream` references resolve to known asset ids.
    - No cycles across the unified graph (string id space).
    - Asset type is one of ASSET_TYPES.

    Returns (valid: bool, errors: list[str]).
    """
    cfg = get_config()
    arms_cfg = cfg.get("arms") or []
    extras_raw = cfg.get("assets") or []
    errors = []

    arm_numeric_ids = {a["id"] for a in arms_cfg}
    arm_string_ids = {_arm_string_id(aid) for aid in arm_numeric_ids}
    extra_ids = []
    for ea in extras_raw:
        eid = ea.get("id")
        etype = ea.get("type")
        if etype == "arm":
            continue  # legacy: arms live under arms:
        if etype == "conveyor":
            errors.append(
                f"Asset {eid!r}: unsupported asset type 'conveyor' - "
                f"conveyor support has been removed"
            )
            continue
        if etype not in ASSET_TYPES:
            errors.append(f"Asset {eid!r} has unknown type {etype!r}")
            continue
        if not eid:
            errors.append("Asset entry missing id")
            continue
        extra_ids.append(eid)
    string_ids = arm_string_ids | set(extra_ids)

    # Arm depends_on must point to valid arm numeric ids.
    for arm in arms_cfg:
        for dep in arm.get("depends_on", []):
            if dep not in arm_numeric_ids:
                errors.append(f"Arm {arm['id']} depends on non-existent arm {dep}")

    # Non-arm refs must resolve to known asset ids (string space).
    for ea in extras_raw:
        if ea.get("type") == "arm":
            continue
        eid = ea.get("id", "?")
        for ref in ea.get("upstream", []):
            if ref not in string_ids:
                errors.append(
                    f"Asset {eid!r} upstream references unknown asset {ref!r}"
                )
        for ref in ea.get("downstream", []):
            if ref not in string_ids:
                errors.append(
                    f"Asset {eid!r} downstream references unknown asset {ref!r}"
                )

    # Cycle detection across the full graph.
    in_degree = {sid: 0 for sid in string_ids}
    out_adj = {sid: [] for sid in string_ids}

    def add_edge(src, dst):
        if src in in_degree and dst in in_degree:
            in_degree[dst] += 1
            out_adj[src].append(dst)

    for arm in arms_cfg:
        sid = _arm_string_id(arm["id"])
        for dep in arm.get("depends_on", []):
            add_edge(_arm_string_id(dep), sid)

    for ea in extras_raw:
        if ea.get("type") == "arm" or ea.get("type") not in ASSET_TYPES:
            continue
        eid = ea["id"]
        for u in ea.get("upstream", []):
            add_edge(u, eid)
        for d in ea.get("downstream", []):
            add_edge(eid, d)

    queue = [n for n, deg in in_degree.items() if deg == 0]
    visited = 0
    while queue:
        cur = queue.pop(0)
        visited += 1
        for nxt in out_adj[cur]:
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    if visited != len(in_degree):
        errors.append("Cycle detected in asset graph")

    return len(errors) == 0, errors


def get_health_config():
    """Return health check settings from config."""
    cfg = get_config()
    health = cfg.get("health", {})
    return {
        "check_interval": health.get("check_interval", 2.0),
        "timeout": health.get("timeout", 5.0),
        "max_missed": health.get("max_missed", 3),
    }


def reload():
    """Re-read pipeline.yaml. Call this after editing the config."""
    global _config
    _config = None
    load_config()
