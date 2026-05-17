"""Docker container control for the failure/recovery flow.

Phase 7: frontend Kill/Recover and backend `/assets/<id>/fail` /
`/assets/<id>/recover` (and the analogous `/arms/<id>/stop` /
`/arms/<id>/start`) must actually stop and start the corresponding
Docker container — not merely flip in-memory state.

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
        socketio.emit("log_message", {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "source": source,
            "message": message,
        })
    except Exception:
        pass
    print(message, flush=True)


def _run_docker(action: str, name: str, timeout: int = 10) -> Optional[str]:
    """Run `docker <action> <name>`. Returns None on success or stderr on failure."""
    if not _docker_available():
        msg = f"docker CLI/socket unavailable — skipping {action} {name}"
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


def is_enabled() -> bool:
    """Expose availability so routes can decide whether to attempt control."""
    return _docker_available()
