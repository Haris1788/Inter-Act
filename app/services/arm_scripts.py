"""
Named actor behaviours — generic, config-driven, for arms AND assets.

A "script" here is a small Python function that performs a complete physical
behaviour for one actor (an arm or a shuttle). It is the unit Qwen picks
(instead of inventing single angles or shifts).

Why this exists
---------------
A 0.5 B model is unreliable at inventing angles or making continuous-time
decisions. It is far more reliable at picking 1 of 3 names from a closed
menu. This module exposes that menu and runs the chosen behaviour.

Generalisation
--------------
The registry is built from `pipeline.yaml` so it scales to any number of
arms and any combination of assets in between. For each actor, three
scripts are generated automatically:
  - relay_<upstream>_to_<downstream>  — pick from upstream zone, drop downstream
  - hold_at_<upstream>                — stay put, do nothing
  - return_to_rest                    — go to neutral position (arms only)

Zones are derived from the flow order in `pipeline.yaml`:
  - 'intake' (before the first actor)
  - 'on_<asset_id>' (between any pair where one side is an asset)
  - 'handover_<n>' (between two arms, n = next arm's id - 1)
  - 'output' (after the last actor)

Visibility
----------
Every script step emits a `log_message` over WebSocket so the browser's log
panel shows the whole sequence. Object events are emitted in the new
owner_type / owner_id namespace so the frontend renders correctly.

Usage
-----
    from app.services.arm_scripts import list_scripts, run_script
    menu = list_scripts(actor_id=1)              # arm: int
    menu = list_scripts(actor_id="veh-a")        # asset: str
    result = run_script(actor_id=1, script_name="relay_intake_to_handover_1")
"""

import time
from datetime import datetime
from typing import Dict, Any, Callable, List, Union

from app import socketio
from app.models import arm_state
from app.models import asset_state
from app.models import pipeline_config


REST_ANGLE = 90.0
PICK_ANGLE = 180.0
DROP_ANGLE = 0.0
STEP_PAUSE = 1.2
SHUTTLE_TRAVEL_MS = 2000   # shuttle animation duration


ActorId = Union[int, str]   # arm = int, asset = str


# ────────────────────────────────────────────────────────────────────────────
# Logging + event helpers
# ────────────────────────────────────────────────────────────────────────────


def _source_label(actor_id: ActorId) -> str:
    """Stable log-source string for the browser log panel."""
    if isinstance(actor_id, int):
        return f"arm_{actor_id}"
    return str(actor_id)


def _log(actor_id: ActorId, message: str, level: str = "info") -> None:
    src = _source_label(actor_id)
    socketio.emit("log_message", {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "source": src,
        "message": message,
        "level": level,
    })
    print(f"[{src}] {message}")


def _rotate_arm(arm_id: int, target_angle: float) -> None:
    arm_state.set_angle(arm_id, target_angle)
    socketio.emit("state_update", arm_state.get_all())


def _move_arm_step(arm_id: int, target_angle: float, label: str) -> None:
    _log(arm_id, f"→ {label} (rotate to {target_angle:.0f}°)")
    _rotate_arm(arm_id, target_angle)
    time.sleep(STEP_PAUSE)


def _zone_to_owner(state: str, carrier_arm: int = None, carrier_asset: str = None):
    """Map a zone/state name to (owner_type, owner_id) for the rendering namespace."""
    if state == "intake":
        return "zone", "intake-zone"
    if state == "output":
        return "zone", "output-zone"
    if state == "carried":
        if carrier_arm is not None:
            return "arm", f"arm-{carrier_arm}"
        if carrier_asset is not None:
            return "asset", carrier_asset
    if state.startswith("on_"):
        # 'on_veh-a' → owner asset 'veh-a'
        return "asset", state[3:]
    if state.startswith("handover_"):
        return "transfer_point", state
    return "zone", state


def _emit_object(object_id: str,
                 state: str,
                 carrier_arm: int = None,
                 carrier_asset: str = None,
                 extra_fields: dict = None) -> None:
    """Update an object's position and emit object_update.

    Bypasses /object_event validation — the manual AI demo path manages
    its own state transitions.
    """
    from app.routes import arm_routes

    owner_type, owner_id = _zone_to_owner(state, carrier_arm=carrier_arm,
                                          carrier_asset=carrier_asset)
    payload = {
        "object_id": object_id,
        "state": state,
        "carrier_arm": carrier_arm,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "event_time": int(time.time() * 1000),
    }
    if carrier_asset is not None:
        payload["carrier_asset"] = carrier_asset
    if extra_fields:
        payload.update(extra_fields)
    with arm_routes._object_lock:
        arm_routes._object_states[object_id] = payload
        arm_routes._object_owners[object_id] = owner_id
    socketio.emit("object_update", payload)


def _find_object_at(zone: str):
    from app.routes import arm_routes
    return arm_routes.find_object_at(zone)


# ────────────────────────────────────────────────────────────────────────────
# Topology — derive each actor's upstream / downstream from pipeline.yaml
# ────────────────────────────────────────────────────────────────────────────


def _flow_order():
    return pipeline_config.get_module_flow_order()


def _zone_between(prev: str, this: str) -> str:
    """Name the zone that sits between two adjacent actors in the flow.

    Rule: if `this` is an arm, use the standard `handover_<arm_n - 1>`
    name (so arm-3 always picks from `handover_2`, regardless of what
    sits behind it). If `this` is an asset, use `on_<asset_id>`.
    """
    if this.startswith("arm-"):
        n = int(this.split("-", 1)[1])
        return f"handover_{n - 1}"
    return f"on_{this}"


def _actor_zones(actor_id: ActorId) -> Dict[str, str]:
    """Return {'upstream': <zone>, 'downstream': <zone>} for any actor."""
    flow = _flow_order()
    actor_str = f"arm-{actor_id}" if isinstance(actor_id, int) else actor_id
    if actor_str not in flow:
        return {"upstream": "", "downstream": ""}
    i = flow.index(actor_str)
    upstream = "intake" if i == 0 else _zone_between(flow[i - 1], actor_str)
    downstream = "output" if i == len(flow) - 1 else _zone_between(actor_str, flow[i + 1])
    return {"upstream": upstream, "downstream": downstream}


def _pretty(zone: str) -> str:
    if zone == "intake":
        return "intake"
    if zone == "output":
        return "output"
    if zone.startswith("handover_"):
        return zone.replace("_", " ")
    if zone.startswith("on_"):
        return f"on {zone[3:]}"
    return zone


# ────────────────────────────────────────────────────────────────────────────
# Generic script bodies — arms
# ────────────────────────────────────────────────────────────────────────────


def _relay_arm(arm_id: int, **_kwargs) -> Dict[str, Any]:
    """Pick the REAL object at this arm's upstream zone and drop downstream."""
    zones = _actor_zones(arm_id)
    upstream, downstream = zones["upstream"], zones["downstream"]
    script_name = f"relay_{upstream}_to_{downstream}"

    obj = _find_object_at(upstream)
    if obj is None:
        _log(arm_id, f"✋ no object at {_pretty(upstream)} — nothing to relay", level="warning")
        return {"script": script_name, "object_id": None, "status": "no_object"}
    object_id = obj["object_id"]

    _log(arm_id, f"▶ START {script_name} (object={object_id})")

    _move_arm_step(arm_id, PICK_ANGLE, f"face {_pretty(upstream)}")
    _emit_object(object_id, "carried", carrier_arm=arm_id)
    _log(arm_id, f"  • picked {object_id}")
    time.sleep(0.4)

    _move_arm_step(arm_id, DROP_ANGLE, f"carry to {_pretty(downstream)}")
    # If we're handing off to an asset, ownership transfers to that asset;
    # otherwise we drop into the abstract handover/output zone.
    if downstream.startswith("on_"):
        asset_id = downstream[3:]
        _emit_object(object_id, downstream, carrier_asset=asset_id)
    else:
        _emit_object(object_id, downstream)
    _log(arm_id, f"  • dropped {object_id} at {_pretty(downstream)}")
    time.sleep(0.4)

    _move_arm_step(arm_id, REST_ANGLE, "return to rest")
    _log(arm_id, f"■ DONE {script_name}", level="success")
    return {"script": script_name, "object_id": object_id, "status": "done"}


def _hold_arm(arm_id: int, quiet: bool = False, **_kwargs) -> Dict[str, Any]:
    zones = _actor_zones(arm_id)
    upstream = zones["upstream"]
    script_name = f"hold_at_{upstream}"
    if quiet:
        # Repeat-idle: arm is already where it needs to be. Don't log,
        # don't even call set_angle (skip the 1.2 s sleep). The world is
        # quiet; we keep it quiet.
        return {"script": script_name, "status": "done", "quiet": True}
    _log(arm_id, f"▶ START {script_name}")
    _move_arm_step(arm_id, PICK_ANGLE, f"face {_pretty(upstream)} (waiting)")
    _log(arm_id, f"■ DONE {script_name}", level="success")
    return {"script": script_name, "status": "done"}


def _return_to_rest(arm_id: int, quiet: bool = False, **_kwargs) -> Dict[str, Any]:
    if quiet:
        return {"script": "return_to_rest", "status": "done", "quiet": True}
    _log(arm_id, "▶ START return_to_rest")
    _move_arm_step(arm_id, REST_ANGLE, "return to rest")
    _log(arm_id, "■ DONE return_to_rest", level="success")
    return {"script": "return_to_rest", "status": "done"}


# ────────────────────────────────────────────────────────────────────────────
# Generic script bodies — shuttle (vehicle)
# ────────────────────────────────────────────────────────────────────────────


def _relay_vehicle(asset_id: str, **_kwargs) -> Dict[str, Any]:
    """Carry an object across the shuttle's waypoints to its downstream zone."""
    zones = _actor_zones(asset_id)
    upstream, downstream = zones["upstream"], zones["downstream"]
    script_name = f"relay_{upstream}_to_{downstream}"

    obj = _find_object_at(upstream)
    if obj is None:
        _log(asset_id, f"✋ no object at {_pretty(upstream)} — nothing to relay", level="warning")
        return {"script": script_name, "object_id": None, "status": "no_object"}
    object_id = obj["object_id"]

    _log(asset_id, f"▶ START {script_name} (object={object_id})")

    # Pickup — object is carried by this shuttle. Setting start_time +
    # duration_ms makes the frontend animate the shuttle from waypoint[0]
    # to waypoint[-1] over `duration_ms` ms.
    start_ms = int(time.time() * 1000)
    _emit_object(
        object_id,
        upstream,
        carrier_asset=asset_id,
        extra_fields={"start_time": start_ms, "duration_ms": SHUTTLE_TRAVEL_MS},
    )
    asset_state.set_carried(asset_id, object_id)
    socketio.emit("asset_health", asset_state.get(asset_id) or {})
    _log(asset_id, f"  • picked {object_id} from {_pretty(upstream)}")

    # Travel — wait the animation duration so the canvas can interpolate.
    time.sleep(SHUTTLE_TRAVEL_MS / 1000)

    # Drop — hand off to downstream zone (could be another asset or a handover).
    if downstream.startswith("on_"):
        next_asset = downstream[3:]
        _emit_object(object_id, downstream, carrier_asset=next_asset)
    else:
        _emit_object(object_id, downstream)
    asset_state.set_carried(asset_id, None)
    socketio.emit("asset_health", asset_state.get(asset_id) or {})
    _log(asset_id, f"  • dropped {object_id} at {_pretty(downstream)}")

    _log(asset_id, f"■ DONE {script_name}", level="success")
    return {"script": script_name, "object_id": object_id, "status": "done"}


def _hold_vehicle(asset_id: str, quiet: bool = False, **_kwargs) -> Dict[str, Any]:
    zones = _actor_zones(asset_id)
    upstream = zones["upstream"]
    script_name = f"hold_at_{upstream}"
    if quiet:
        return {"script": script_name, "status": "done", "quiet": True}
    _log(asset_id, f"▶ START {script_name}")
    time.sleep(0.4)
    _log(asset_id, f"■ DONE {script_name}", level="success")
    return {"script": script_name, "status": "done"}


# ────────────────────────────────────────────────────────────────────────────
# Registry — one menu per actor, built from pipeline.yaml
# ────────────────────────────────────────────────────────────────────────────


def _build_arm_menu(arm_id: int) -> Dict[str, Dict[str, Any]]:
    zones = _actor_zones(arm_id)
    up, down = zones["upstream"], zones["downstream"]
    return {
        f"relay_{up}_to_{down}": {
            "fn": _relay_arm,
            "desc": f"Pick the object at {_pretty(up)} and drop it at {_pretty(down)}.",
        },
        f"hold_at_{up}": {
            "fn": _hold_arm,
            "desc": f"Face {_pretty(up)} and wait. Use when no work to do yet.",
        },
        "return_to_rest": {
            "fn": _return_to_rest,
            "desc": "Return to rest position (90°). Use for abort or reset.",
        },
    }


def _build_asset_menu(asset_id: str, asset_type: str) -> Dict[str, Dict[str, Any]]:
    zones = _actor_zones(asset_id)
    up, down = zones["upstream"], zones["downstream"]
    if asset_type == "vehicle":
        relay_fn, hold_fn = _relay_vehicle, _hold_vehicle
        rest_desc = "ferry-ride from start waypoint to end waypoint"
    else:
        return {}
    return {
        f"relay_{up}_to_{down}": {
            "fn": relay_fn,
            "desc": (
                f"Move the object from {_pretty(up)} to {_pretty(down)}"
                f" via the {rest_desc}."
            ),
        },
        f"hold_at_{up}": {
            "fn": hold_fn,
            "desc": f"Stay at {_pretty(up)}, do not advance the object.",
        },
    }


_actor_scripts_cache: Dict[ActorId, Dict[str, Dict[str, Any]]] = {}


def _build_all() -> Dict[ActorId, Dict[str, Dict[str, Any]]]:
    out: Dict[ActorId, Dict[str, Dict[str, Any]]] = {}
    for arm_id in pipeline_config.get_arm_ids():
        out[arm_id] = _build_arm_menu(arm_id)
    for asset in pipeline_config.get_extra_assets():
        out[asset["id"]] = _build_asset_menu(asset["id"], asset["type"])
    return out


def _scripts_for(actor_id: ActorId) -> Dict[str, Dict[str, Any]]:
    global _actor_scripts_cache
    if not _actor_scripts_cache:
        _actor_scripts_cache = _build_all()
    return _actor_scripts_cache.get(actor_id, {})


def list_scripts(actor_id: ActorId) -> List[Dict[str, str]]:
    """Return the menu for an arm (int) or asset (string id)."""
    scripts = _scripts_for(actor_id)
    if not scripts:
        return []
    return [{"name": k, "desc": v["desc"]} for k, v in scripts.items()]


def run_script(actor_id: ActorId, script_name: str, **kwargs) -> Dict[str, Any]:
    """Execute a script by name. Raises ValueError if unknown."""
    scripts = _scripts_for(actor_id)
    if not scripts:
        raise ValueError(f"Unknown actor_id: {actor_id}")
    if script_name not in scripts:
        raise ValueError(f"Unknown script for actor {actor_id}: {script_name}. "
                         f"Valid: {list(scripts.keys())}")
    return scripts[script_name]["fn"](actor_id, **kwargs)
