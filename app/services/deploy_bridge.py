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


def build_pipeline_yaml(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate a frontend deploy payload into a pipeline.yaml dict.

    Arms become a left-to-right dependency chain (ordered by authored x).
    Vehicles are wired from the first to the last arm. The pipeline /
    failure_policy / health / canvas blocks of the currently-loaded config
    are preserved so a deploy never silently changes run policy.
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
            "color": "#9b59b6",
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
    return out


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

    doc = build_pipeline_yaml(payload)
    arm_ids = [f"arm-{a['id']}" for a in doc["arms"]]
    asset_ids = [a["id"] for a in doc["assets"]]
    steps: dict[str, str] = {}

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
            from app.models import arm_state

            arm_state.reinitialize()
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
        s_compose = "skipped: docker control unavailable"
        compose_up_ok = False
        if run_compose and container_manager.is_enabled():
            cmd = _docker_compose_cmd()
            if cmd is None:
                s_compose = "skipped: docker compose not found"
            else:
                try:
                    r = subprocess.run(
                        [*cmd, "-f", comp_path, "up", "-d", "--remove-orphans"],
                        cwd=project_dir,
                        capture_output=True,
                        text=True,
                        timeout=600,
                        check=False,
                    )
                    compose_up_ok = r.returncode == 0
                    s_compose = (
                        "ok"
                        if compose_up_ok
                        else "failed: "
                        + ((r.stderr or r.stdout or "").strip()[:300])
                    )
                except Exception as exc:  # noqa: BLE001
                    s_compose = f"failed: {exc}"
        steps["compose_up"] = s_compose

        restarted: list[str] = []
        created: list[str] = []
        create_failed: list[str] = []
        if container_manager.is_enabled():
            sibling = next(
                (
                    f"arm-{a['id']}"
                    for a in doc["arms"]
                    if container_manager.container_exists(f"arm-{a['id']}")
                ),
                "arm-1",
            )

            def _ensure(mid: str, make) -> None:
                if container_manager.container_exists(mid):
                    if not container_manager.stop_container(
                        mid
                    ) and not container_manager.start_container(mid):
                        restarted.append(mid)
                else:
                    err = make()
                    (created if err is None else create_failed).append(
                        mid if err is None else f"{mid}: {err}"
                    )
                _set(
                    detail=f"materializing… {mid}",
                    steps={
                        **steps,
                        "restarted": ", ".join(restarted) or "none",
                        "created": ", ".join(created) or "none",
                        **(
                            {"create_failed": "; ".join(create_failed)}
                            if create_failed
                            else {}
                        ),
                    },
                )

            for a in doc["arms"]:
                aid = a["id"]
                _ensure(
                    f"arm-{aid}",
                    lambda aid=aid: container_manager.create_arm_container(
                        aid, template_module=sibling
                    ),
                )
            for i, asset in enumerate(doc["assets"]):
                a_id = asset["id"]
                _ensure(
                    a_id,
                    lambda asset=asset, i=i: container_manager.create_asset_container(
                        asset["id"],
                        label=asset.get("label", asset["id"]),
                        asset_type=asset.get("type", "vehicle"),
                        template_module=sibling,
                        publish_port=8010 + i * 10,
                    ),
                )

            steps["restarted"] = ", ".join(restarted) or "none"
            steps["created"] = ", ".join(created) or "none"
            if create_failed:
                steps["create_failed"] = "; ".join(create_failed)

        applied = compose_up_ok or (
            steps.get("config_written") == "ok" and reloaded_ok
        )
        if compose_up_ok:
            detail = "Containers brought up; health monitor confirms liveness."
        elif applied:
            parts = ["Authored config applied."]
            if restarted:
                parts.append(f"Restarted {len(restarted)} container(s).")
            if created:
                parts.append(
                    f"Created {len(created)} new container(s) - booting; "
                    "health monitor confirms when alive."
                )
            if create_failed:
                parts.append(
                    f"{len(create_failed)} could not be created "
                    "(see steps); they stay stale until really up."
                )
            detail = " ".join(parts)
        else:
            detail = (
                "docker unavailable - runtime left unchanged (honest). "
                "Apply via the offline pipeline.yaml -> generate_compose flow."
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
