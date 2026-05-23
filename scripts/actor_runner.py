"""Per-actor process entrypoint for isolated actor containers.

Run one actor agent (ArmAgent or AGVAgent) in a dedicated process backed by
Redis state + Redis bus.  No Flask, no HTTP server, no decisions — load profile,
start agent, wait.

Required environment variables
-------------------------------
ACTOR_ID    e.g. "arm-1" or "agv-1"
ACTOR_KIND  "arm" or "agv"
REDIS_URL   e.g. "redis://redis:6379/0"

Implicit requirements (set by the reconciler before launching the container)
-----------------------------------------------------------------------------
INTERACT_BUS=redis     — get_bus() returns RedisStreamsBus
INTERACT_STATE=redis   — state models use Redis

Optional
--------
CONFIG_URL  Backend /config URL.  Not used for profile loading in this
            implementation (pipeline_config reads from the mounted
            pipeline.yaml directly via PIPELINE_CONFIG env or default path).

Shutdown
--------
SIGTERM / SIGINT → agent.stop() → bus.shutdown() → sys.exit(0)
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

# ── Logging setup (stdout, prefixed per actor) ─────────────────────────────

_PREFIX = f"[actor-runner {os.environ.get('ACTOR_ID', '?')}]"


def _make_logger() -> logging.Logger:
    actor_id = os.environ.get("ACTOR_ID", "unknown")
    logger = logging.getLogger(f"actor_runner.{actor_id}")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                f"[actor-runner {actor_id}] %(levelname)s %(message)s"
            )
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


# ── Env validation ──────────────────────────────────────────────────────────

_REQUIRED_ENV = ("ACTOR_ID", "ACTOR_KIND", "REDIS_URL")


def _validate_env() -> tuple[str, str, str]:
    """Validate required env vars.  Writes to stderr and sys.exits(1) on error.

    Returns (actor_id, actor_kind, redis_url) on success.
    """
    missing = [k for k in _REQUIRED_ENV if not os.environ.get(k, "").strip()]
    if missing:
        print(
            f"[actor-runner] ERROR: missing required environment variables: "
            f"{', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)

    actor_id = os.environ["ACTOR_ID"].strip()
    actor_kind = os.environ["ACTOR_KIND"].strip().lower()
    redis_url = os.environ["REDIS_URL"].strip()

    if actor_kind not in ("arm", "agv"):
        print(
            f"[actor-runner] ERROR: ACTOR_KIND must be 'arm' or 'agv', got {actor_kind!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    return actor_id, actor_kind, redis_url


# ── Profile loading ─────────────────────────────────────────────────────────

def _load_profile(actor_id: str, logger: logging.Logger):
    """Load CapabilityProfile for actor_id from pipeline_config.

    Calls capability.load_profiles() (which reads pipeline.yaml) and returns
    the matching profile.  Exits nonzero if the profile is not found.
    """
    from app.agents.capability import load_profiles

    profiles = load_profiles()
    for profile in profiles:
        if profile.actor_id == actor_id:
            logger.info("profile loaded: kind=%s zones=%s", profile.kind, profile.reachable_zones)
            return profile

    print(
        f"[actor-runner] ERROR: no profile found for ACTOR_ID={actor_id!r}. "
        f"Available: {[p.actor_id for p in profiles]}",
        file=sys.stderr,
    )
    sys.exit(1)


# ── Signal handling ─────────────────────────────────────────────────────────

def _install_signal_handlers(agent, bus, logger: logging.Logger) -> None:
    """Install SIGTERM and SIGINT handlers for clean shutdown.

    Order: agent.stop() → bus.shutdown() → sys.exit(0).
    """
    def _shutdown(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("received %s — shutting down", sig_name)
        try:
            agent.stop()
            logger.info("agent stopped")
        except Exception as exc:
            logger.warning("agent.stop() raised: %s", exc)
        try:
            if hasattr(bus, "shutdown"):
                bus.shutdown()
                logger.info("bus shutdown complete")
        except Exception as exc:
            logger.warning("bus.shutdown() raised: %s", exc)
        logger.info("exit 0")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    """Entrypoint: validate env → load profile → connect bus → start agent → idle."""
    actor_id, actor_kind, redis_url = _validate_env()

    logger = _make_logger()
    logger.info("starting (kind=%s, redis_url=%s)", actor_kind, redis_url)

    # Enforce redis backends (belt-and-suspenders in case the caller didn't set them).
    os.environ.setdefault("INTERACT_BUS", "redis")
    os.environ.setdefault("INTERACT_STATE", "redis")

    # Load profile from pipeline.yaml (via pipeline_config).
    profile = _load_profile(actor_id, logger)

    # Verify ACTOR_KIND matches the profile's kind as a sanity check.
    if profile.kind != actor_kind:
        print(
            f"[actor-runner] ERROR: ACTOR_KIND={actor_kind!r} but profile kind "
            f"is {profile.kind!r} for {actor_id!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Get the process-wide bus (RedisStreamsBus because INTERACT_BUS=redis).
    from app.agents.bus import get_bus
    bus = get_bus()
    logger.info("bus connected (type=%s)", type(bus).__name__)

    # Instantiate the right agent class.
    if actor_kind == "arm":
        from app.agents.arm_agent import ArmAgent
        agent = ArmAgent(profile, bus)
    else:
        from app.agents.agv_agent import AGVAgent
        agent = AGVAgent(profile, bus)

    logger.info("agent instantiated (%s)", type(agent).__name__)

    # Install signal handlers before starting the agent so SIGTERM during
    # startup is handled cleanly.
    _install_signal_handlers(agent, bus, logger)

    # Start the agent event-loop thread.
    agent.start()
    logger.info("agent started — entering idle loop")

    # Idle loop: monitor the agent thread.  If it dies unexpectedly log and
    # exit nonzero — the container reconciler will restart the container.
    try:
        while agent.is_running():
            time.sleep(1)
    except SystemExit:
        raise  # signal handlers call sys.exit — let it propagate
    except Exception as exc:
        logger.exception("unexpected error in idle loop: %s", exc)
        sys.exit(2)

    # Agent thread stopped without a signal (unexpected).
    logger.error(
        "agent thread exited unexpectedly (state=%s) — exiting nonzero for reconciler restart",
        agent.state,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
