"""Docker container control for the failure/recovery flow.

Phase 7: frontend Kill/Recover and backend `/assets/<id>/fail` /
`/assets/<id>/recover` (and the analogous `/arms/<id>/stop` /
`/arms/<id>/start`) must actually stop and start the corresponding
Docker container - not merely flip in-memory state.

This module wraps `docker stop|start <container>` via subprocess. It
relies on:

  * the host Docker socket mounted into the backend container at
    `/var/run/docker.sock`, and
  * each pipeline service having a stable `container_name` in
    docker-compose.yml (so we can address it without parsing labels).

Asset/arm id → container name mapping uses the convention
``interact-<id>``. The mapping can be overridden via
`COMPOSE_CONTAINER_PREFIX` env var if a different project name is in use.
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
    """Map a pipeline module id (`arm-1`, `veh-a`) to its
    docker-compose container name. Customise via `container_name:` in
    docker-compose.yml so this stays predictable across `docker compose up`
    invocations.
    """
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
    """Run `docker <action> <name>`. Returns None on success or stderr on failure."""
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
    """Stop the container backing a pipeline module. Returns None on success."""
    name = container_name_for(module_id)
    _emit_log("system", f"[SYSTEM] Stopping container: {name}")
    return _run_docker("stop", name)


def start_container(module_id: str) -> Optional[str]:
    """Start the container backing a pipeline module. Returns None on success."""
    name = container_name_for(module_id)
    _emit_log("system", f"[SYSTEM] Starting container: {name}")
    return _run_docker("start", name)


def container_exists(module_id: str) -> bool:
    """True when a container (running or stopped) exists for this module."""
    if not _docker_available():
        return False
    try:
        r = subprocess.run(
            ["docker", "inspect", container_name_for(module_id)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return r.returncode == 0
    except Exception:
        return False


# Repo root inside the backend container (Dockerfile: WORKDIR /interact +
# COPY . .) - the build context for new actor images. Resolved from this
# file so it also works when run locally outside Docker.
_REPO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)

_SHARED_IMAGE = {
    "arm": "interact-shared-arm:latest",
    "asset": "interact-shared-asset:latest",
}
_DOCKERFILE = {
    "arm": "docker/Dockerfile.arm",
    "asset": "docker/Dockerfile.asset",
}


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
    """Build the shared arm/asset image once (idempotent).

    All arms come from one Dockerfile (likewise all assets), so a single
    clean image - NOT a copy of arm-1 - backs every new container. The
    backend image bakes the repo at /interact, so the build context is
    present without compose or a host mount.
    """
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


def _sibling_runtime(module_id: str) -> dict:
    """Network + pipeline.yaml host mount + compose project from a running
    sibling, so a new container joins the SAME network and binds the SAME
    host config file (daemon-resolved paths stay correct)."""
    out = {"network": None, "pipeline_mount": None, "project": None}
    try:
        r = subprocess.run(
            ["docker", "inspect", container_name_for(module_id)],
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


def _run_new_container(
    new_name: str,
    image: str,
    env: list[str],
    sibling: str,
    service_label: str,
    publish_port: Optional[int],
) -> Optional[str]:
    sib = _sibling_runtime(sibling)
    args = [
        "docker",
        "run",
        "-d",
        "--name",
        new_name,
        "--restart",
        "unless-stopped",
    ]
    if sib["network"]:
        args += ["--network", sib["network"]]
    if sib["project"]:
        args += [
            "--label",
            f"com.docker.compose.project={sib['project']}",
            "--label",
            f"com.docker.compose.service={service_label}",
        ]
    if sib["pipeline_mount"]:
        src, dst = sib["pipeline_mount"]
        if src and dst:
            args += ["-v", f"{src}:{dst}:ro"]
    if publish_port:
        args += ["-p", f"{publish_port}:8000"]
    for e in env:
        args += ["-e", e]
    args.append(image)

    _emit_log("system", f"[SYSTEM] Creating container: {new_name} ({image})")
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=60, check=False
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            _emit_log("system", f"[SYSTEM] docker run {new_name} failed: {err}")
            return err
        return None
    except subprocess.TimeoutExpired:
        return f"docker run {new_name} timed out"
    except Exception as exc:  # noqa: BLE001
        return str(exc)


def create_arm_container(
    arm_id: int, template_module: str = "arm-1"
) -> Optional[str]:
    """Create a NEW, first-class arm container (own clean image + port +
    ARM_ID) - not a copy of arm-1. Returns None or an honest error."""
    if not _docker_available():
        return "docker CLI/socket unavailable"
    build_err = ensure_shared_image("arm")
    if build_err:
        return f"arm image build failed: {build_err}"
    env = [
        f"ARM_ID={arm_id}",
        "SERVER_HOST=http://backend:5000",
        "PIPELINE_CONFIG=/config/pipeline.yaml",
        "PYTHONUNBUFFERED=1",
    ]
    return _run_new_container(
        new_name=container_name_for(f"arm-{arm_id}"),
        image=_SHARED_IMAGE["arm"],
        env=env,
        sibling=template_module,
        service_label=f"arm-{arm_id}",
        publish_port=8000 + int(arm_id),
    )


def create_asset_container(
    asset_id: str,
    label: str = "",
    asset_type: str = "vehicle",
    template_module: str = "arm-1",
    publish_port: Optional[int] = None,
) -> Optional[str]:
    """Create a NEW, first-class asset (AGV) container. Returns None or an
    honest error string."""
    if not _docker_available():
        return "docker CLI/socket unavailable"
    build_err = ensure_shared_image("asset")
    if build_err:
        return f"asset image build failed: {build_err}"
    env = [
        f"ASSET_ID={asset_id}",
        f"ASSET_TYPE={asset_type}",
        f"ASSET_LABEL={label or asset_id}",
        "SERVER_HOST=http://backend:5000",
        "PIPELINE_CONFIG=/config/pipeline.yaml",
        "SPEED_FACTOR=1.0",
        "PYTHONUNBUFFERED=1",
    ]
    return _run_new_container(
        new_name=container_name_for(asset_id),
        image=_SHARED_IMAGE["asset"],
        env=env,
        sibling=template_module,
        service_label=asset_id,
        publish_port=publish_port,
    )


def is_enabled() -> bool:
    """Expose availability so routes can decide whether to attempt control."""
    return _docker_available()
