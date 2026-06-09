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
# All arms share one image, all assets share one (parametrised by env).
# The backend image bakes the repo at /interact, so the build context is
# present without compose or a host mount.
_REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SHARED_IMAGE = {
    "arm": "interact-shared-arm:latest",
    "asset": "interact-shared-asset:latest",
}
_DOCKERFILE = {
    "arm": "docker/Dockerfile.arm",
    "asset": "docker/Dockerfile.asset",
}
_SHARED_IMAGE_SET = set(_SHARED_IMAGE.values()) | {
    v.split(":")[0] for v in _SHARED_IMAGE.values()
}

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


def ensure_shared_image(kind: str) -> Optional[str]:
    """Build the shared arm/asset image once (idempotent)."""
    tag = _SHARED_IMAGE[kind]
    if _image_exists(tag):
        return None
    dockerfile = os.path.join(_REPO_DIR, _DOCKERFILE[kind])
    _emit_log("system", f"[SYSTEM] Building shared {kind} image {tag}")
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


def _kind_key(kind: str) -> str:
    return "arm" if kind == "arm" else "asset"


def create_actor(
    module_id: str, kind: str, label: str, publish_port: Optional[int]
) -> Optional[str]:
    """Create ONE labelled actor container (arm or vehicle). Idempotent
    (removes any same-name leftover first). None on success / honest
    error string. Never fakes."""
    if not _docker_available():
        return "docker CLI/socket unavailable"
    kk = _kind_key(kind)
    build_err = ensure_shared_image(kk)
    if build_err:
        return f"{kk} image build failed: {build_err}"

    infra = _infra_runtime()
    if not infra["network"]:
        return "infra network unknown (is interact-backend running?)"

    name = container_name_for(module_id)
    if kind == "arm":
        try:
            arm_n = int(module_id.split("-")[-1])
        except ValueError:
            arm_n = 0
        env = [
            f"ARM_ID={arm_n}",
            "SERVER_HOST=http://backend:5000",
            "PIPELINE_CONFIG=/config/pipeline.yaml",
            "PYTHONUNBUFFERED=1",
            # FDAS Step 3: propagate arm autonomy from the backend's own env
            # (set by generate_compose.py from pipeline.yaml ai.arm_autonomy)
            # down to each spawned arm, so arm_server.py starts arm_bus.py.
            f"ARM_AUTONOMY={os.environ.get('ARM_AUTONOMY', 'off')}",
        ]
    else:
        env = [
            f"ASSET_ID={module_id}",
            "ASSET_TYPE=vehicle",
            f"ASSET_LABEL={label or module_id}",
            "SERVER_HOST=http://backend:5000",
            "PIPELINE_CONFIG=/config/pipeline.yaml",
            "SPEED_FACTOR=1.0",
            "PYTHONUNBUFFERED=1",
        ]

    args = [
        "docker", "run", "-d",
        "--name", name,
        "--restart", "unless-stopped",
        "--network", infra["network"],
        # Compose-parity DNS so the health monitor's http://<module>:8000
        # resolves regardless of container name.
        "--network-alias", module_id,
        "--label", f"{LBL_ROLE}={ROLE_ACTOR}",
        "--label", f"{LBL_ID}={module_id}",
        "--label", f"{LBL_KIND}={kind}",
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
    args.append(_SHARED_IMAGE[kk])

    subprocess.run(
        ["docker", "rm", "-f", name],
        capture_output=True, text=True, timeout=15, check=False,
    )
    _emit_log("system", f"[SYSTEM] Creating actor container: {name} ({kind})")
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
            err = create_actor(mid, kind, d.get("label", mid), port)
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
