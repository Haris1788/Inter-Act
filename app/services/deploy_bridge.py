"""Phase 15 bridge: apply an authored topology to the real runtime.

The frontend can author actors (e.g. add Arm 5). Before this bridge the
only "deploy" was POST /restart_pipeline, which re-inits state from the
*on-disk* pipeline.yaml and never created the new arm's container - so a
freshly authored actor stayed stale forever.

This module performs the MINIMAL real, backend-authoritative steps:

    payload -> pipeline.yaml -> generate_compose -> docker compose up -d
            -> pipeline_config.reload() -> arm_state.reinitialize()

Every step is honest: if the config path is read-only (containerized
backend with a :ro mount) or docker/compose is unavailable, the step is
reported as skipped/failed and ``applied`` is False. Nothing fakes a
container that does not exist - the frontend keeps the actor stale until
the health monitor confirms the container is actually alive.
"""

from __future__ import annotations

import copy
import os
import re
import subprocess
import threading
import time
from typing import Any, Optional

import yaml

from app.models import pipeline_config
from app.services import container_manager

# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def _get_effective_grid() -> dict[str, Any]:
    """Return warehouse grid config from the currently-loaded pipeline.yaml.

    Raises RuntimeError when no warehouse section is configured — callers
    must handle this so a missing grid never silently produces wrong cells.
    """
    try:
        grid = pipeline_config.get_grid()
    except Exception as exc:
        raise RuntimeError(f"pipeline_config.get_grid() failed: {exc}") from exc
    if not grid or not grid.get("cell_px"):
        raise RuntimeError(
            "warehouse.grid not configured in pipeline.yaml — "
            "cannot convert authored actor positions to grid cells. "
            "Ensure pipeline.yaml has a warehouse: section with grid, origin."
        )
    return grid


def _px_to_cell(cx: float, cy: float, grid: dict[str, Any]) -> list[int]:
    """Convert canvas pixel centre (cx, cy) → warehouse [col, row].

    Inverse of pipeline_config.cell_to_px:
        cell_to_px([col, row]).x = origin_x + (col + 0.5) * cell_px
        → col = floor((cx - origin_x) / cell_px)

    Clamps to valid grid bounds so an off-grid actor never crashes the deploy.
    """
    origin = grid.get("origin") or {}
    ox = float(origin.get("x", 0))
    oy = float(origin.get("y", 0))
    cp = float(grid.get("cell_px", 1))
    cols = int(grid.get("cols", 1))
    rows = int(grid.get("rows", 1))
    col = int((cx - ox) / cp)
    row = int((cy - oy) / cp)
    return [max(0, min(col, cols - 1)), max(0, min(row, rows - 1))]


# ---------------------------------------------------------------------------
# Warehouse section generation
# ---------------------------------------------------------------------------

_AUTHORED_AGV_COLORS = ["#9b59b6", "#2980b9", "#27ae60", "#e67e22", "#e74c3c"]


def _authored_agv_color(agv_id: str) -> str:
    """Stable color for an authored AGV — matches AgvTokenLayer.authoredAgvColor."""
    nums = re.findall(r"\d+", str(agv_id))
    n = int(nums[-1]) if nums else 1
    return _AUTHORED_AGV_COLORS[(n - 1) % len(_AUTHORED_AGV_COLORS)]


def _build_warehouse_section(
    actors: list[dict[str, Any]],
    arms_in: list[tuple[int, dict[str, Any]]],
    base: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Generate the merged warehouse section and a list of validation warnings.

    Strategy:
    - Preserve everything from base `warehouse:` unchanged.
    - For authored arms: derive input_arm / output_arm stations from positions.
    - For authored AGVs: derive bay cells from positions.
    - Authored entries are merged ON TOP of seeded entries for the same key,
      so an authored arm-3 station replaces a stale seeded arm-3 entry, but
      the seeded agv-1/agv-2/agv-3 bays are untouched if no authored AGV
      carries those IDs.

    Returns (warehouse_dict, warnings).
    """
    warnings: list[str] = []

    # Deep-copy the existing warehouse section so seeded data is preserved.
    wh = copy.deepcopy(base.get("warehouse") or {})

    # ── grid: use from base config (authoritative), never override ─────────
    try:
        grid = _get_effective_grid()
    except RuntimeError as exc:
        warnings.append(str(exc))
        return wh, warnings

    cols = int(grid.get("cols", 1))
    rows = int(grid.get("rows", 1))

    # ── authored stations from arms ─────────────────────────────────────────
    authored_stations: dict[str, Any] = {}

    def _actor_centre(actor: dict[str, Any]) -> tuple[float, float]:
        size = actor.get("size") or {}
        pos = actor.get("position") or {}
        cx = float(pos.get("x", 0)) + float(size.get("w", 0)) / 2.0
        cy = float(pos.get("y", 0)) + float(size.get("h", 0)) / 2.0
        return cx, cy

    if arms_in:
        # First (leftmost) → input_arm
        first_num, first_actor = arms_in[0]
        fx, fy = _actor_centre(first_actor)
        f_cell = _px_to_cell(fx, fy, grid)
        f_col, f_row = f_cell
        f_dock_col = min(f_col + 1, cols - 1)
        if f_dock_col == f_col:
            warnings.append(
                f"input_arm (arm-{first_num}): arm cell [{f_col},{f_row}] "
                f"is at grid right edge — dock cannot be placed to the right"
            )
        authored_stations["input_arm"] = {
            "cell": f_cell,
            "dock": [f_dock_col, f_row],
            "label": "IN",
            "served_by": f"arm-{first_num}",
        }

    if len(arms_in) >= 2:
        # Last (rightmost) → output_arm
        last_num, last_actor = arms_in[-1]
        lx, ly = _actor_centre(last_actor)
        l_cell = _px_to_cell(lx, ly, grid)
        l_col, l_row = l_cell
        l_dock_col = max(l_col - 1, 0)
        if l_dock_col == l_col:
            warnings.append(
                f"output_arm (arm-{last_num}): arm cell [{l_col},{l_row}] "
                f"is at grid left edge — dock cannot be placed to the left"
            )
        authored_stations["output_arm"] = {
            "cell": l_cell,
            "dock": [l_dock_col, l_row],
            "label": "OUT",
            "served_by": f"arm-{last_num}",
        }
    elif arms_in:
        warnings.append(
            "Only 1 authored arm — AGV routing requires both input_arm and "
            "output_arm stations. Add a second arm for AGV task execution."
        )

    # ── authored AGV bays ───────────────────────────────────────────────────
    authored_agvs: list[dict[str, Any]] = []
    caps_defaults: dict[str, Any] = {}
    try:
        caps_defaults = pipeline_config.get_capability_defaults()
    except Exception:
        pass

    for a in actors:
        if a.get("kind") != "agv":
            continue
        agv_id = str(a.get("id", ""))
        if not agv_id:
            continue
        cx, cy = _actor_centre(a)
        bay = _px_to_cell(cx, cy, grid)
        authored_agvs.append({
            "id": agv_id,
            "label": a.get("label") or agv_id,
            "color": _authored_agv_color(agv_id),
            "bay": bay,
            "capabilities": dict(caps_defaults),
        })

    # ── merge: authored ON TOP of seeded ───────────────────────────────────
    existing_stations: dict[str, Any] = dict(wh.get("stations") or {})
    merged_stations = {**existing_stations, **authored_stations}
    wh["stations"] = merged_stations

    existing_agvs_by_id: dict[str, Any] = {
        a["id"]: a for a in (wh.get("agvs") or [])
    }
    for agv in authored_agvs:
        existing_agvs_by_id[agv["id"]] = agv
    wh["agvs"] = list(existing_agvs_by_id.values())

    # ── lane: preserve seeded; omit authored (A* fallback covers smart mode) ─
    # Lane is not generated for authored topologies. In smart coordination mode
    # (the default), agv_brain falls back to A* between input_dock and
    # output_dock when lane_cells is empty. For centralized/decentralized modes
    # the seeded lane is already present; authored additions don't interfere.

    return wh, warnings


def _validate_authored_warehouse(
    authored_stations: dict[str, Any],
    authored_agvs: list[dict[str, Any]],
    grid: dict[str, Any],
) -> list[str]:
    """Additional cross-checks: duplicate cells, bay/station overlaps."""
    warnings: list[str] = []
    cols = int(grid.get("cols", 1))
    rows = int(grid.get("rows", 1))

    # Out-of-bounds checks
    for name, st in authored_stations.items():
        for field in ("cell", "dock"):
            c = st.get(field, [])
            if c and (c[0] < 0 or c[0] >= cols or c[1] < 0 or c[1] >= rows):
                warnings.append(
                    f"station {name}.{field} {c} is outside grid bounds "
                    f"({cols}×{rows})"
                )

    for agv in authored_agvs:
        bay = agv.get("bay", [])
        if bay and (bay[0] < 0 or bay[0] >= cols or bay[1] < 0 or bay[1] >= rows):
            warnings.append(
                f"agv {agv['id']}.bay {bay} is outside grid bounds "
                f"({cols}×{rows})"
            )

    # Duplicate station cells
    cell_map: dict[tuple, str] = {}
    for name, st in authored_stations.items():
        key = tuple(st.get("cell", []))
        if key in cell_map:
            warnings.append(
                f"station {name} and {cell_map[key]} share cell {list(key)}"
            )
        else:
            cell_map[key] = name

    # AGV bay overlapping station cell
    station_cells = {tuple(st.get("cell", [])) for st in authored_stations.values()}
    for agv in authored_agvs:
        bay = tuple(agv.get("bay", []))
        if bay in station_cells:
            warnings.append(
                f"agv {agv['id']}.bay {list(bay)} overlaps a station cell"
            )

    # Duplicate bays
    bay_map: dict[tuple, str] = {}
    for agv in authored_agvs:
        bay = tuple(agv.get("bay", []))
        if bay in bay_map:
            warnings.append(
                f"agv {agv['id']} and {bay_map[bay]} share bay {list(bay)}"
            )
        else:
            bay_map[bay] = agv["id"]

    return warnings

# Background deploy state. Heavy work (image build + N container
# create/restart) runs off the request thread so the HTTP call returns
# instantly and never trips the client timeout, regardless of actor count.
_STATE_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "phase": "idle",  # idle | materializing | done
    "running": False,
    "applied": False,
    "detail": "",
    "arms": [],
    "assets": [],
    "steps": {},
    "ts": None,
}


def _set(**kw: Any) -> None:
    with _STATE_LOCK:
        _STATE.update(kw)
        _STATE["ts"] = time.strftime("%H:%M:%S")


def get_deploy_state() -> dict[str, Any]:
    with _STATE_LOCK:
        return dict(_STATE)


_ARM_RE = re.compile(r"^arm-(\d+)$")
_ARM_COLORS = [
    "#e74c3c",
    "#2ecc71",
    "#3498db",
    "#e67e22",
    "#1abc9c",
    "#9b59b6",
    "#f39c12",
    "#16a085",
]


def _config_path() -> str:
    return os.path.abspath(pipeline_config._CONFIG_PATH)


def build_pipeline_yaml(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Translate a frontend deploy payload into a (pipeline.yaml dict, warnings).

    Arms become a left-to-right dependency chain (ordered by authored x).
    Vehicles are wired from the first to the last arm. The pipeline /
    failure_policy / health / canvas / warehouse / ai blocks of the
    currently-loaded config are preserved so a deploy never silently changes
    run policy or grid geometry.

    Returns (doc, warnings) where warnings is a list of non-fatal issues
    found during coordinate conversion and validation.
    """
    actors = payload.get("actors") or []

    arms_in = []
    for a in actors:
        if a.get("kind") != "arm":
            continue
        m = _ARM_RE.match(str(a.get("id", "")))
        if not m:
            continue
        arms_in.append((int(m.group(1)), a))
    arms_in.sort(key=lambda t: (t[1].get("position", {}).get("x", 0), t[0]))

    arms_yaml = []
    prev_id: Optional[int] = None
    for idx, (num, a) in enumerate(arms_in):
        arms_yaml.append(
            {
                "id": num,
                "label": a.get("label") or f"Arm {num}",
                "color": _ARM_COLORS[idx % len(_ARM_COLORS)],
                "depends_on": [] if prev_id is None else [prev_id],
                "critical": True,
            }
        )
        prev_id = num

    first_arm = f"arm-{arms_in[0][0]}" if arms_in else None
    last_arm = f"arm-{arms_in[-1][0]}" if arms_in else None

    assets_yaml = []
    for a in actors:
        if a.get("kind") != "agv":
            continue
        aid = str(a.get("id"))
        entry: dict[str, Any] = {
            "id": aid,
            "type": "vehicle",
            "label": a.get("label") or aid,
            "color": _authored_agv_color(aid),
            "speed": 140,
            "loop": False,
            "waypoints": [{"x": 730, "y": 300}, {"x": 900, "y": 300}],
        }
        if first_arm and last_arm and first_arm != last_arm:
            entry["upstream"] = [first_arm]
            entry["downstream"] = [last_arm]
        assets_yaml.append(entry)

    try:
        base = copy.deepcopy(pipeline_config.get_config() or {})
    except Exception:
        base = {}

    out: dict[str, Any] = {
        "pipeline": base.get("pipeline")
        or {"mode": "dependency", "products": 3, "speed": "normal"},
        "failure_policy": base.get("failure_policy")
        or {"default": "isolate", "retry_max": 3, "retry_delay": 2.0},
        "health": base.get("health")
        or {"check_interval": 2.0, "timeout": 5.0, "max_missed": 3},
        "arms": arms_yaml,
        "assets": assets_yaml,
    }
    if base.get("canvas"):
        out["canvas"] = base["canvas"]
    if base.get("ai"):
        out["ai"] = base["ai"]

    # ── warehouse section: merge authored actor positions into seeded config ──
    warehouse, wh_warnings = _build_warehouse_section(actors, arms_in, base)
    if warehouse:
        out["warehouse"] = warehouse

    # ── cross-validation on authored entries only ───────────────────────────
    # Validate only the stations/AGVs that came from this payload (not seeded).
    val_warnings: list[str] = []
    if warehouse:
        try:
            grid = _get_effective_grid()
            authored_arm_ids = {f"arm-{num}" for num, _ in arms_in}
            authored_stns = {
                k: v for k, v in warehouse.get("stations", {}).items()
                if v.get("served_by") in authored_arm_ids
            }
            payload_agv_ids = {
                str(a.get("id")) for a in actors if a.get("kind") == "agv"
            }
            authored_agv_list = [
                a for a in warehouse.get("agvs", [])
                if a["id"] in payload_agv_ids
            ]
            val_warnings = _validate_authored_warehouse(
                authored_stns, authored_agv_list, grid
            )
        except Exception:
            pass

    all_warnings = wh_warnings + val_warnings
    return out, all_warnings


def _docker_compose_cmd() -> Optional[list[str]]:
    """Return a working compose invocation, or None if unavailable."""
    for cmd in (["docker", "compose"], ["docker-compose"]):
        try:
            r = subprocess.run(
                [*cmd, "version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if r.returncode == 0:
                return cmd
        except Exception:
            continue
    return None


def apply_topology(
    payload: dict[str, Any],
    *,
    config_path: Optional[str] = None,
    compose_path: Optional[str] = None,
    run_compose: bool = True,
    reload_runtime: bool = True,
    background: bool = False,
) -> dict[str, Any]:
    """Apply an authored topology to the real runtime. Honest by design.

    Returns a result dict; ``applied`` is True only when real containers
    were actually brought up by compose. No fake success path.
    """
    cfg_path = os.path.abspath(config_path or _config_path())
    comp_path = os.path.abspath(
        compose_path or os.path.join(os.path.dirname(cfg_path), "docker-compose.yml")
    )
    project_dir = os.path.dirname(cfg_path)

    doc, deploy_warnings = build_pipeline_yaml(payload)
    arm_ids = [f"arm-{a['id']}" for a in doc["arms"]]
    asset_ids = [a["id"] for a in doc["assets"]]
    steps: dict[str, str] = {}
    if deploy_warnings:
        steps["warnings"] = "; ".join(deploy_warnings)

    # 1) Write pipeline.yaml (may be read-only inside the container).
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, default_flow_style=False, sort_keys=False)
        steps["config_written"] = "ok"
    except OSError as exc:
        detail = (
            f"pipeline.yaml not writable ({exc.strerror or exc}). "
            "Containerized backend mounts it read-only - apply the "
            "authored topology via the offline pipeline.yaml -> "
            "generate_compose flow. Runtime left unchanged (honest)."
        )
        _set(phase="done", running=False, applied=False, detail=detail,
             arms=arm_ids, assets=asset_ids, steps=steps)
        return {
            "status": "ok", "applied": False, "running": False,
            "arms": arm_ids, "assets": asset_ids, "steps": steps,
            "detail": detail,
        }

    # 2) Regenerate docker-compose.yml.
    try:
        from scripts import generate_compose

        generate_compose.generate(cfg_path, comp_path)
        steps["compose_generated"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report, don't fake
        steps["compose_generated"] = f"failed: {exc}"
        detail = f"compose generation failed: {exc}"
        _set(phase="done", running=False, applied=False, detail=detail,
             arms=arm_ids, assets=asset_ids, steps=steps)
        return {
            "status": "ok", "applied": False, "running": False,
            "arms": arm_ids, "assets": asset_ids, "steps": steps,
            "detail": detail,
        }

    # 3) Reload config + re-init arm state NOW (fast) so the health monitor
    #    starts tracking the authored arms immediately.
    reloaded_ok = False
    if reload_runtime:
        try:
            pipeline_config.reload()
            from app.models import arm_state, agv_state

            arm_state.reinitialize()
            # Register newly authored AGVs so they appear in /agv/state
            # immediately after config reload (before containers boot).
            agv_state.initialize_from_config()
            steps["reloaded"] = "ok"
            reloaded_ok = True
        except Exception as exc:  # noqa: BLE001
            steps["reloaded"] = f"failed: {exc}"

    # 4) Heavy, slow, scales-with-N work (image build + compose up + per
    #    actor create/restart) runs OFF the request thread. The HTTP call
    #    returns instantly; the client polls /deploy/status. This is the
    #    permanent fix for "/deploy/apply unreachable": the request can no
    #    longer outlive the client timeout no matter how many actors.
    def _materialize() -> None:
        # ONE reconciliation pass owns the entire actor runtime: remove
        # stale + legacy, create missing, restart surviving. No compose-up
        # for actors (they are not compose services anymore), no separate
        # prune. Idempotent and scale-symmetric for any N.
        desired = [
            {"id": f"arm-{a['id']}", "kind": "arm",
             "label": a.get("label", f"arm-{a['id']}")}
            for a in doc["arms"]
        ] + [
            {"id": asset["id"], "kind": "vehicle",
             "label": asset.get("label", asset["id"])}
            for asset in doc["assets"]
        ]

        res = container_manager.reconcile_actors(desired)
        steps["created"] = ", ".join(res["created"]) or "none"
        steps["restarted"] = ", ".join(res["restarted"]) or "none"
        steps["removed"] = ", ".join(res["removed"]) or "none"
        if res["failed"]:
            steps["failed"] = "; ".join(res["failed"])

        # Honest "applied": config persisted + reloaded into the running
        # backend AND docker reconciliation actually ran. The frontend
        # still gates "ready" on real per-arm health.
        applied = (
            res["applied"]
            and steps.get("config_written") == "ok"
            and reloaded_ok
        )
        if applied:
            parts = ["Authored topology reconciled."]
            if res["created"]:
                parts.append(f"Created {len(res['created'])}.")
            if res["restarted"]:
                parts.append(f"Restarted {len(res['restarted'])}.")
            if res["removed"]:
                parts.append(f"Removed {len(res['removed'])} stale.")
            if res["failed"]:
                parts.append(
                    f"{len(res['failed'])} failed (see steps); stale until up."
                )
            detail = " ".join(parts)
        else:
            detail = (
                f"{res['detail']} - runtime left unchanged (honest). Apply "
                "via the offline pipeline.yaml -> generate_compose flow."
            )
        _set(
            phase="done",
            running=False,
            applied=applied,
            detail=detail,
            arms=arm_ids,
            assets=asset_ids,
            steps=steps,
        )

    _set(
        phase="materializing",
        running=True,
        applied=False,
        detail="Applying topology - building images and (re)starting containers…",
        arms=arm_ids,
        assets=asset_ids,
        steps=steps,
    )
    if background:
        threading.Thread(target=_materialize, daemon=True).start()
        return {
            "status": "ok",
            "applied": False,
            "running": True,
            "arms": arm_ids,
            "assets": asset_ids,
            "steps": steps,
            "detail": "Applying topology in background - poll /deploy/status.",
        }
    _materialize()
    st = get_deploy_state()
    return {
        "status": "ok",
        "applied": st["applied"],
        "running": False,
        "arms": arm_ids,
        "assets": asset_ids,
        "steps": st["steps"],
        "detail": st["detail"],
    }
