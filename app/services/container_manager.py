"""Docker control + the unified actor-runtime ownership model.

Two concerns live here:

1. Operational stop/start of a module's container (used by
   `/arms/<id>/stop|start`, `/assets/<id>/fail|recover`).

2. **Actor-runtime reconciliation.** Arms / AGVs / future actor kinds are
   NOT docker-compose services. They are ephemeral runtime entities owned
   entirely by topology reconciliation, created here with explicit
   ownership LABELS (`interact.role=actor`, `interact.actor.id=...`).
   Ownership is decided by those labels - never by parsing container
   names - so the model is future-safe for AI-generated dynamic
   topologies and a legacy container from any old naming scheme is still
   reclaimed. Core infrastructure (compose: backend/frontend/demo-runner/
   ollama) carries none of these labels and is structurally untouchable.

Relies on the host Docker socket mounted into the backend container at
`/var/run/docker.sock`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from typing import Optional

# Toggle docker control via env so unit tests / local-dev can stay no-op.
_ENABLED = os.environ.get("CONTAINER_CONTROL", "auto").lower()
_PREFIX = os.environ.get("COMPOSE_CONTAINER_PREFIX", "interact")


def _docker_available() -> bool:
    """True when the docker CLI is on PATH and the socket exists."""
    if _ENABLED == "off":
        return False
    if shutil.which("docker") is None:
        return False
    sock = "/var/run/docker.sock"
    if not os.path.exists(sock) and _ENABLED != "on":
        return False
    return True


def container_name_for(module_id: str) -> str:
    """ONE container name scheme for everything: ``interact-<module>``.
    Ownership is NOT inferred from this name - it is inferred from labels.
    The name only needs to be stable + DNS-resolvable (we also set a
    matching ``--network-alias``)."""
    return f"{_PREFIX}-{module_id}"


def _emit_log(source: str, message: str) -> None:
    """Best-effort SocketIO log fan-out for the browser log panel."""
    try:
        from app import socketio

        socketio.emit(
            "log_message",
            {
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "source": source,
                "message": message,
            },
        )
    except Exception:
        pass
    print(message, flush=True)


def _run_docker(action: str, name: str, timeout: int = 10) -> Optional[str]:
    """Run `docker <action> <name>`. None on success, else stderr."""
    if not _docker_available():
        msg = f"docker CLI/socket unavailable - skipping {action} {name}"
        _emit_log("system", f"[SYSTEM] {msg}")
        return msg
    try:
        result = subprocess.run(
            ["docker", action, name],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            _emit_log("system", f"[SYSTEM] docker {action} {name} failed: {err}")
            return err
        return None
    except subprocess.TimeoutExpired:
        msg = f"docker {action} {name} timed out"
        _emit_log("system", f"[SYSTEM] {msg}")
        return msg
    except Exception as exc:
        _emit_log("system", f"[SYSTEM] docker {action} {name}: {exc}")
        return str(exc)


def stop_container(module_id: str) -> Optional[str]:
    """Stop the container backing a module. None on success."""
    name = container_name_for(module_id)
    _emit_log("system", f"[SYSTEM] Stopping container: {name}")
    return _run_docker("stop", name)


def start_container(module_id: str) -> Optional[str]:
    """Start the container backing a module. None on success."""
    name = container_name_for(module_id)
    _emit_log("system", f"[SYSTEM] Starting container: {name}")
    return _run_docker("start", name)


def is_enabled() -> bool:
    """Expose availability so routes can decide whether to attempt control."""
    return _docker_available()


# ── shared actor images ───────────────────────────────────────────────────
# Single consolidated image for ALL actor kinds (arm + agv).
# actor_runner.py selects the agent class via ACTOR_KIND env var.
# Both kinds run from `Dockerfile.actor` — one image, one build target.
#
# Legacy images (interact-shared-arm, interact-shared-asset) are kept in
# _SHARED_IMAGE_SET so the reclaim path still removes any lingering old
# containers; they are no longer built by ensure_shared_image().
_REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_ACTOR_IMAGE = "interact-shared-actor:latest"
_ACTOR_DOCKERFILE = "Dockerfile.actor"

# Legacy image names — included in set so list_actor_containers() reclaims them.
_LEGACY_IMAGES = {
    "interact-shared-arm:latest",
    "interact-shared-arm",
    "interact-shared-asset:latest",
    "interact-shared-asset",
}
_SHARED_IMAGE_SET = {_ACTOR_IMAGE, _ACTOR_IMAGE.split(":")[0]} | _LEGACY_IMAGES

# Core infra is compose-owned and label-free; defensively denylisted too.
_CORE_MODULES = {"backend", "frontend", "demo-runner", "ollama"}

# Ownership labels - the SINGLE source of truth for "is this an actor".
LBL_ROLE = "interact.role"
LBL_ID = "interact.actor.id"
LBL_KIND = "interact.actor.kind"
ROLE_ACTOR = "actor"


def _image_exists(tag: str) -> bool:
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", tag],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return r.returncode == 0
    except Exception:
        return False


def ensure_shared_image(kind: str = "actor") -> Optional[str]:
    """Build the shared actor image once (idempotent).

    All actor kinds (arm + agv) use the single ``interact-shared-actor``
    image built from ``Dockerfile.actor``.  The ``kind`` parameter is kept
    for call-site compatibility but is ignored — there is only one image.
    """
    tag = _ACTOR_IMAGE
    if _image_exists(tag):
        return None
    dockerfile = os.path.join(_REPO_DIR, _ACTOR_DOCKERFILE)
    _emit_log("system", f"[SYSTEM] Building shared actor image {tag}")
    try:
        r = subprocess.run(
            ["docker", "build", "-f", dockerfile, "-t", tag, _REPO_DIR],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if r.returncode != 0:
            return (r.stderr or r.stdout or "").strip()[:400]
        return None
    except subprocess.TimeoutExpired:
        return f"docker build {tag} timed out"
    except Exception as exc:  # noqa: BLE001
        return str(exc)


def _infra_runtime() -> dict:
    """Network + host pipeline.yaml mount + compose project, read from the
    persistent backend infra container. New actor containers join the SAME
    network and bind the SAME daemon-resolved host config file - no
    dependency on any sibling actor existing."""
    out: dict = {"network": None, "pipeline_mount": None, "project": None}
    try:
        r = subprocess.run(
            ["docker", "inspect", "--type", "container",
             container_name_for("backend")],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if r.returncode != 0:
            return out
        info = json.loads(r.stdout)[0]
        out["network"] = (info.get("HostConfig", {}) or {}).get("NetworkMode")
        out["project"] = (
            (info.get("Config", {}) or {}).get("Labels", {}) or {}
        ).get("com.docker.compose.project")
        for m in info.get("Mounts", []) or []:
            if (m.get("Destination") or "").endswith("pipeline.yaml"):
                out["pipeline_mount"] = (m.get("Source"), m.get("Destination"))
                break
    except Exception:
        pass
    return out


def create_actor(
    module_id: str, kind: str, label: str, publish_port: Optional[int],
    *,
    x: Optional[float] = None,
    y: Optional[float] = None,
    reach_px: Optional[float] = None,
    capacity: Optional[int] = None,
    speed: Optional[float] = None,
) -> Optional[str]:
    """Create ONE labelled actor container (arm or agv). Idempotent
    (removes any same-name leftover first). None on success / honest
    error string. Never fakes.

    Both kinds use the single ``interact-shared-actor`` image built from
    ``Dockerfile.actor``; ``actor_runner.py`` selects the agent class via
    the ``ACTOR_KIND`` environment variable.

    Environment per kind
    --------------------
    arm  : ACTOR_ID, ACTOR_KIND=arm, REDIS_URL, INTERACT_BUS=redis,
           INTERACT_STATE=redis, CONFIG_URL, PIPELINE_CONFIG, PYTHONUNBUFFERED
    agv  : ACTOR_ID, ACTOR_KIND=agv, REDIS_URL, INTERACT_BUS=redis,
           INTERACT_STATE=redis, CONFIG_URL, PIPELINE_CONFIG, PYTHONUNBUFFERED
    """
    if not _docker_available():
        return "docker CLI/socket unavailable"

    # All kinds use the single consolidated actor image.
    build_err = ensure_shared_image()
    if build_err:
        return f"actor image build failed: {build_err}"

    infra = _infra_runtime()
    if not infra["network"]:
        return "infra network unknown (is interact-backend running?)"

    name = container_name_for(module_id)

    # Normalise kind: legacy callers may pass "vehicle" — map to "agv".
    normalised_kind = "agv" if kind in ("vehicle", "agv") else "arm"

    # Env vars common to all actor kinds (actor_runner.py protocol).
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    env = [
        f"ACTOR_ID={module_id}",
        f"ACTOR_KIND={normalised_kind}",
        f"REDIS_URL={redis_url}",
        "INTERACT_BUS=redis",
        "INTERACT_STATE=redis",
        "CONFIG_URL=http://backend:5000/config",
        "PIPELINE_CONFIG=/config/pipeline.yaml",
        "PYTHONUNBUFFERED=1",
    ]
    # Optional spatial env from topology (actor_runner priority 1: env > topology > legacy).
    if x is not None:
        env.append(f"ACTOR_X={x}")
    if y is not None:
        env.append(f"ACTOR_Y={y}")
    if reach_px is not None:
        env.append(f"ACTOR_REACH_PX={reach_px}")
    if capacity is not None:
        env.append(f"ACTOR_CAPACITY={capacity}")
    if speed is not None:
        env.append(f"ACTOR_SPEED={speed}")

    args = [
        "docker", "run", "-d",
        "--name", name,
        "--restart", "unless-stopped",
        "--network", infra["network"],
        # Compose-parity DNS so the health monitor and other containers can
        # resolve the actor by id regardless of container name.
        "--network-alias", module_id,
        "--label", f"{LBL_ROLE}={ROLE_ACTOR}",
        "--label", f"{LBL_ID}={module_id}",
        "--label", f"{LBL_KIND}={normalised_kind}",
    ]
    if infra["project"]:
        args += ["--label", f"com.docker.compose.project={infra['project']}"]
    if infra["pipeline_mount"]:
        src, dst = infra["pipeline_mount"]
        if src and dst:
            args += ["-v", f"{src}:{dst}:ro"]
    if publish_port:
        args += ["-p", f"{publish_port}:8000"]
    for e in env:
        args += ["-e", e]
    args.append(_ACTOR_IMAGE)

    subprocess.run(
        ["docker", "rm", "-f", name],
        capture_output=True, text=True, timeout=15, check=False,
    )
    _emit_log("system", f"[SYSTEM] Creating actor container: {name} ({normalised_kind})")
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=60, check=False
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            _emit_log("system", f"[SYSTEM] docker run {name} failed: {err}")
            return err
        return None
    except subprocess.TimeoutExpired:
        return f"docker run {name} timed out"
    except Exception as exc:  # noqa: BLE001
        return str(exc)


def _ps_rows() -> list[tuple[str, str, str]]:
    """(name, image, role-label) for every container. role is '' when the
    container carries no interact.role label."""
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--format",
             "{{.Names}}\t{{.Image}}\t{{.Label \"" + LBL_ROLE + "\"}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if r.returncode != 0:
            return []
        rows: list[tuple[str, str, str]] = []
        for ln in r.stdout.splitlines():
            parts = ln.split("\t")
            if len(parts) >= 2:
                rows.append(
                    (parts[0].strip(), parts[1].strip(),
                     parts[2].strip() if len(parts) > 2 else "")
                )
        return rows
    except Exception:
        return []


def _module_from_name(name: str) -> Optional[str]:
    """Best-effort module id from a container name (legacy reclaim only -
    ownership itself never depends on this)."""
    p = f"{_PREFIX}-"
    if not name.startswith(p):
        return None
    m = name[len(p):]
    if m.startswith("dyn-"):  # legacy bridge namespace
        m = m[len("dyn-"):]
    return m


def list_actor_containers() -> dict[str, list[str]]:
    """Discover every actor-runtime container by OWNERSHIP, returning
    {"owned": {module: name}, "legacy": [name,…]}.

    - owned   : carries interact.role=actor (the canonical model).
    - legacy  : runs a shared actor image but predates the label scheme
                (old `interact-arm-5`, `interact-dyn-*`). Reclaimed during
                reconciliation so no orphan from any old scheme survives.
    Core/compose containers have neither marker and never appear here.
    """
    owned: dict[str, str] = {}
    legacy: list[str] = []
    if not _docker_available():
        return {"owned": owned, "legacy": legacy}
    for name, image, role in _ps_rows():
        if role == ROLE_ACTOR:
            mod = _module_from_name(name) or name
            owned[mod] = name
        elif image in _SHARED_IMAGE_SET:
            legacy.append(name)
    return {"owned": owned, "legacy": legacy}


def reconcile_actors(desired: list[dict]) -> dict:
    """Single reconciliation pass: make running actor containers exactly
    match the authored topology.

      desired item = {"id": "arm-1", "kind": "arm", "label": "Source"}

    - remove stale owned actors + ALL legacy actor containers
    - (re)create / restart desired actors with ownership labels
    - infra is structurally excluded (no actor label, not a shared image)

    Honest: returns ``applied: False`` with a reason when docker is
    unavailable; never fakes runtime.
    """
    if not _docker_available():
        return {
            "applied": False,
            "created": [],
            "restarted": [],
            "removed": [],
            "failed": [],
            "detail": "docker unavailable - runtime unchanged (honest)",
        }

    inv = list_actor_containers()
    owned = inv["owned"]  # module -> container name
    legacy = inv["legacy"]  # container names
    desired_by_id = {d["id"]: d for d in desired}
    desired_ids = set(desired_by_id)

    created: list[str] = []
    restarted: list[str] = []
    removed: list[str] = []
    failed: list[str] = []

    # 1) Remove every legacy actor container (any old naming scheme). If a
    #    legacy module is still desired it is re-created cleanly + labelled.
    for name in legacy:
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True, text=True, timeout=20, check=False,
        )
        mod = _module_from_name(name) or name
        removed.append(f"{mod} (legacy)")
        _emit_log("system", f"[SYSTEM] Reclaimed legacy actor: {name}")

    # 2) Remove owned actors no longer in the topology.
    for mod, name in owned.items():
        if mod in desired_ids:
            continue
        if mod in _CORE_MODULES:
            continue  # defensive; core never carries the actor label anyway
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True, text=True, timeout=20, check=False,
        )
        removed.append(mod)
        _emit_log("system", f"[SYSTEM] Removed stale actor: {name}")

    # 3) Create missing / restart surviving desired actors.
    veh_index = 0
    for d in desired:
        mid = d["id"]
        kind = d.get("kind", "arm")
        port: Optional[int]
        if kind == "arm":
            try:
                port = 8000 + int(mid.split("-")[-1])
            except ValueError:
                port = None
        else:
            port = 8010 + veh_index * 10
            veh_index += 1

        # `removed` may contain "mod (legacy)" entries - compare on the id.
        removed_ids = {m.split(" ")[0] for m in removed}
        still_owned = mid in owned and mid not in removed_ids

        if still_owned:
            stop_err = stop_container(mid)
            start_err = start_container(mid)
            errs = [e for e in (stop_err, start_err) if e]
            if errs:
                failed.append(f"{mid}: {'; '.join(errs)}")
            else:
                restarted.append(mid)
        else:
            err = create_actor(
                mid, kind, d.get("label", mid), port,
                x=d.get("x"),
                y=d.get("y"),
                reach_px=d.get("reach_px"),
                capacity=d.get("capacity"),
                speed=d.get("speed"),
            )
            if err is None:
                created.append(mid)
            else:
                failed.append(f"{mid}: {err}")

    return {
        "applied": True,
        "created": created,
        "restarted": restarted,
        "removed": removed,
        "failed": failed,
        "detail": (
            f"reconciled · +{len(created)} ~{len(restarted)} "
            f"-{len(removed)}"
            + (f" !{len(failed)}" if failed else "")
        ),
    }


def build_desired_from_config() -> list[dict]:
    """Build the full desired actor set from the current pipeline_config.

    Returns a list of dicts suitable for ``reconcile_actors(desired)``,
    including BOTH arms (kind="arm") and AGVs (kind="agv").

    Stage 3: when a topology block is present, uses topology actors as the
    authoritative source (topology overrides warehouse). Falls back to the
    legacy arms+AGVs path when only the `warehouse:` block is present.

    Each dict carries optional spatial fields (x, y, reach_px, capacity,
    speed) so the reconciler can forward them to the actor container via env.
    """
    from app.models import pipeline_config as pc

    topology = pc.get_topology()
    if topology is not None:
        # Topology path: one entry per actor in the topology block.
        desired: list[dict] = []
        for spec in topology.actors:
            entry: dict = {
                "id": spec.id,
                "kind": spec.kind,
                "label": spec.label or spec.id,
                "x": spec.x,
                "y": spec.y,
                "reach_px": spec.reach_px,
                "capacity": spec.capacity,
                "speed": spec.speed,
            }
            desired.append(entry)
        return desired

    # Legacy path: arms + AGVs from warehouse config.
    desired = []

    # Arms from pipeline_config.get_arms()
    for arm in pc.get_arms():
        aid = f"arm-{arm['id']}"
        desired.append({
            "id": aid,
            "kind": "arm",
            "label": arm.get("label", aid),
        })

    # AGVs from pipeline_config.get_agvs()
    for agv in pc.get_agvs():
        desired.append({
            "id": agv["id"],
            "kind": "agv",
            "label": agv.get("label", agv["id"]),
        })

    return desired
