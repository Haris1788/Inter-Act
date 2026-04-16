"""Loads pipeline.yaml and generates all derived config dynamically.

This is the single source of truth. scene_config.py, arm_state.py,
and arm_routes.py all read from here instead of hardcoding arm counts.
"""

import os
import yaml

_CONFIG_PATH = os.environ.get(
    'PIPELINE_CONFIG',
    os.path.join(os.path.dirname(__file__), '..', '..', 'pipeline.yaml')
)

_config = None

MIN_ANGLE = 0.0
MAX_ANGLE = 180.0
DEFAULT_ANGLE = 90.0


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
    """Build the full scene config dict that the browser needs.

    Computes handoff zones as midpoints between adjacent arms.
    Intake zone is left of arm 1, output zone is right of the last arm.
    """
    cfg = get_config()
    arms_cfg = cfg["arms"]
    n = len(arms_cfg)

    # Keep each arm's visual geometry stable regardless of arm count.
    # Only grow the canvas width horizontally as more arms are added.
    arm_spacing = 300
    default_arm_length = 150
    canvas_h = 600
    pivot_y = 300

    resolved_arms = []
    for i, arm in enumerate(arms_cfg):
        resolved_arms.append({
            "id": arm["id"],
            "pivot_x": int(arm.get("pivot_x", arm_spacing * (i + 1))),
            "pivot_y": int(arm.get("pivot_y", pivot_y)),
            "length": int(arm.get("length", default_arm_length)),
            "color": arm.get("color", "#888"),
            "label": arm.get("label", f"Arm {arm['id']}"),
        })

    first_arm = resolved_arms[0]
    last_arm = resolved_arms[-1]

    # Keep zone dimensions fixed for consistent readability.
    zone_w = 105
    zone_h = 180

    # Compute width from absolute right extent so output is never clipped.
    scene_right = last_arm["pivot_x"] + last_arm["length"] + zone_w // 2
    canvas_w = int(scene_right + 30)

    # Arms
    arms = resolved_arms

    # Reach zones
    reach_zones = [
        {
            "id": arm["id"],
            "cx": arm["pivot_x"],
            "cy": arm["pivot_y"],
            "radius": arm["length"],
            "color": arm.get("color", "#888"),
        }
        for arm in resolved_arms
    ]

    # Handoff zones
    hz_width = 90
    hz_height = 190
    hz_y = pivot_y - hz_height // 2

    handoff_zones = []
    for i in range(n - 1):
        mid_x = (resolved_arms[i]["pivot_x"] + resolved_arms[i + 1]["pivot_x"]) / 2
        handoff_zones.append({
            "id": f"handoff_{i + 1}",
            "label": f"Handoff {i + 1}",
            "x": int(mid_x - hz_width // 2),
            "y": hz_y,
            "width": hz_width,
            "height": hz_height,
            "color": "#f39c12",
        })

    # Intake zone — left of the first arm
    intake_x = first_arm["pivot_x"] - first_arm["length"] - zone_w // 2
    intake_y = pivot_y - zone_h // 2
    intake_zone = {
        "x": intake_x,
        "y": intake_y,
        "width": zone_w,
        "height": zone_h,
        "color": "#95a5a6",
        "label": "INTAKE",
        "label_x": intake_x + zone_w // 2,
        "label_y": intake_y - 8,
        "entry_x": intake_x + zone_w // 2,
        "entry_y": intake_y + 15,
    }

    # Output zone — mirror intake behavior on the right side
    output_x = last_arm["pivot_x"] + last_arm["length"] - zone_w // 2
    output_y = pivot_y - zone_h // 2
    output_zone = {
        "x": output_x,
        "y": output_y,
        "width": zone_w,
        "height": zone_h,
        "color": "#27ae60",
        "label": "OUTPUT",
        "label_x": output_x + zone_w // 2,
        "label_y": output_y - 8,
    }

    labels = {
        "intake": {"text": "INTAKE", "x": intake_zone["label_x"], "y": 200},
        "output": {"text": "OUTPUT", "x": output_zone["label_x"], "y": 200},
    }

    return {
        "canvas_width": canvas_w,
        "canvas_height": canvas_h,
        "arms": arms,
        "reach_zones": reach_zones,
        "handoff_zones": handoff_zones,
        "intake_zone": intake_zone,
        "output_zone": output_zone,
        "labels": labels,
        "min_angle": MIN_ANGLE,
        "max_angle": MAX_ANGLE,
        "default_angle": DEFAULT_ANGLE,
    }


def generate_arm_state():
    """Build initial arm state dict: {1: {arm_id:1, angle:90, status:idle}, ...}."""
    return {
        arm["id"]: {"arm_id": arm["id"], "angle": DEFAULT_ANGLE, "status": "idle"}
        for arm in get_arms()
    }


def get_valid_handoff_states():
    """Return tuple of valid object states based on arm count.

    For 3 arms: ('intake', 'carried', 'handoff_1', 'handoff_2', 'output')
    For 4 arms: ('intake', 'carried', 'handoff_1', 'handoff_2', 'handoff_3', 'output')
    """
    n = get_arm_count()
    handoffs = tuple(f"handoff_{i}" for i in range(1, n))
    return ('intake', 'carried') + handoffs + ('output',)


def get_handoff_name(index):
    """Return handoff state name for the transfer point at index (0-based).

    Index 0 = between arm 1 and arm 2 = 'handoff_1'
    """
    return f"handoff_{index + 1}"


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
