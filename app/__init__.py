"""
INTER.act Flask Application Factory.

Creates and configures the Flask application with SocketIO support.
"""

from flask import Flask
from flask_socketio import SocketIO

socketio = SocketIO()


def create_app(config_class=None):
    """Create and configure the Flask application.

    Args:
        config_class: Optional configuration class. Defaults to app.config.Config.

    Returns:
        Configured Flask application instance.
    """
    app = Flask(__name__)

    if config_class:
        app.config.from_object(config_class)
    else:
        app.config["SECRET_KEY"] = "dev-secret-key-change-in-production"

    # Disable static file caching so Docker rebuilds always serve fresh assets
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    # Use threading mode to avoid eventlet monkey-patching issues at import time.
    socketio.init_app(app, cors_allowed_origins="*", async_mode="threading")

    # Register blueprints
    from app.routes.page_routes import page_bp
    from app.routes.arm_routes import arm_bp
    from app.routes.agv_routes import agv_bp
    from app.routes.ai_routes import ai_bp
    from app.routes.negotiate_routes import negotiate_bp
    from app.routes.replay_routes import replay_bp

    app.register_blueprint(page_bp)
    app.register_blueprint(arm_bp)
    app.register_blueprint(agv_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(negotiate_bp)
    app.register_blueprint(replay_bp)

    # Import SocketIO event handlers so they register with the socketio instance
    from app.sockets import events  # noqa: F401

    # Pre-register non-arm assets from pipeline_config so the health monitor
    # has something to poll without waiting for asset containers to register
    # themselves. Idempotent - safe to call repeatedly.
    try:
        from app.models import asset_state, pipeline_config

        for ea in pipeline_config.get_extra_assets():
            asset_state.register(ea["id"], ea["type"], ea.get("label"))
    except Exception as exc:
        print(f"[startup] asset pre-register skipped: {exc}")

    # Pre-register AGV assets so health monitor and state are ready at startup.
    try:
        from app.models import agv_state

        agv_state.initialize_from_config()
    except Exception as exc:
        print(f"[startup] AGV pre-register skipped: {exc}")

    # Phase 4 Stage 2: actors run in isolated containers, NOT in-backend
    # threads. The reconciler owns their lifecycle via reconcile_actors().
    # agent_registry.start_agents() is intentionally NOT called here; it
    # remains available for tests that prefer in-process actors.
    try:
        import os
        from app.agents.bus import get_bus

        # Log which bus backend is active so operators can confirm at startup.
        bus_mode = os.environ.get("INTERACT_BUS", "inprocess").lower().strip()
        print(f"[startup] NegotiationBus backend: {bus_mode}")

        # Redis path only: start the SocketIO mirror consumer.
        # InProcessBus._mirror() handles the in-process case; there is no
        # separate mirror for it.
        if bus_mode == "redis":
            from app.services.bus_mirror import start_mirror
            start_mirror(get_bus())

    except Exception as exc:
        print(f"[startup] bus setup skipped: {exc}")

    # Reconcile actor containers at startup so arms + AGVs spin up
    # automatically when the backend boots.  Wrapped in try/except so a
    # missing Docker socket (dev mode) is a non-fatal log line, not a crash.
    try:
        from app.services.container_manager import (
            build_desired_from_config,
            reconcile_actors,
        )

        desired = build_desired_from_config()
        print(
            f"[startup] reconcile_actors: {len(desired)} actor(s) desired "
            f"({sum(1 for d in desired if d['kind'] == 'arm')} arms, "
            f"{sum(1 for d in desired if d['kind'] == 'agv')} agvs)"
        )
        res = reconcile_actors(desired)
        print(f"[startup] reconcile_actors: {res['detail']}")
    except Exception as exc:
        print(f"[startup] reconcile_actors skipped: {exc}")

    # Start health monitor for arm + asset containers (runs in background thread)
    from app.services.health_monitor import start as start_health_monitor

    start_health_monitor()

    return app
