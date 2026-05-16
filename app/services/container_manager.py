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


def create_arm_container(
    arm_id: int, template_module: str = "arm-1"
) -> Optional[str]:
    """Materialise a NEW arm container by cloning an existing sibling.

    All arms share one image (docker/Dockerfile.arm, parametrised only by
    ARM_ID), so a brand-new authored arm can run by reusing the template
    arm's image, network and volume mounts with ARM_ID overridden. This is
    the real path that lets deploy add arm-5+ at runtime without an offline
    `docker compose up` (the backend container has no build context).

    Returns None on success or an honest error string. Never fakes.
    """
    if not _docker_available():
        return "docker CLI/socket unavailable"

    template_name = container_name_for(template_module)
    new_name = container_name_for(f"arm-{arm_id}")

    try:
        ins = subprocess.run(
            ["docker", "inspect", template_name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if ins.returncode != 0:
            return (
                f"template container {template_name} not found - cannot "
                f"clone arm-{arm_id} (need at least one existing arm)"
            )
        info = json.loads(ins.stdout)[0]
    except Exception as exc:  # noqa: BLE001
        return f"inspect {template_name} failed: {exc}"

    cfg = info.get("Config", {}) or {}
    host_cfg = info.get("HostConfig", {}) or {}
    image = cfg.get("Image")
    if not image:
        return f"template {template_name} has no image"
    network = host_cfg.get("NetworkMode") or "bridge"
    labels = cfg.get("Labels", {}) or {}
    project = labels.get("com.docker.compose.project")

    args = [
        "docker",
        "run",
        "-d",
        "--name",
        new_name,
        "--network",
        network,
        "--restart",
        "unless-stopped",
    ]
    # Keep it inside the compose project so normal tooling/cleanup sees it.
    if project:
        args += [
            "--label",
            f"com.docker.compose.project={project}",
            "--label",
            f"com.docker.compose.service=arm-{arm_id}",
        ]
    # Reuse the template's volume mounts (daemon-resolved host sources, so
    # the backend never needs to see the host repo itself).
    for m in info.get("Mounts", []) or []:
        src, dst = m.get("Source"), m.get("Destination")
        if src and dst:
            ro = "" if m.get("RW", True) else ":ro"
            args += ["-v", f"{src}:{dst}{ro}"]
    # Carry env, but force this arm's identity.
    for e in cfg.get("Env", []) or []:
        if not e.startswith("ARM_ID="):
            args += ["-e", e]
    args += ["-e", f"ARM_ID={arm_id}", image]

    _emit_log("system", f"[SYSTEM] Creating container: {new_name} (clone of {template_name})")
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=40, check=False
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


def is_enabled() -> bool:
    """Expose availability so routes can decide whether to attempt control."""
    return _docker_available()
