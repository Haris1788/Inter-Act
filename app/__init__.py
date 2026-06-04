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
    from app.routes.dispatch_routes import dispatch_bp
    from app.routes.bus_routes import bus_bp

    app.register_blueprint(page_bp)
    app.register_blueprint(arm_bp)
    app.register_blueprint(agv_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(dispatch_bp)
    app.register_blueprint(bus_bp)

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

    # Init AI planning pipeline: MessageBus + CentralBrain singletons.
    # COORDINATION_MODE selects between:
    #   centralized   — central_brain handles fault reassignment (legacy)
    #   decentralized — agents handle it themselves via /bus/* (contract-net)
    import os as _os
    coordination_mode = _os.environ.get("COORDINATION_MODE", "centralized").lower()
    app.extensions["coordination_mode"] = coordination_mode
    print(f"[startup] COORDINATION_MODE={coordination_mode}")
    try:
        from app.agents.message_bus import SocketIOBus
        from app.agents.central_brain import CentralBrain

        bus = SocketIOBus()
        brain = CentralBrain(bus, coordination_mode=coordination_mode)
        app.extensions["central_brain"] = brain
        app.extensions["message_bus"] = bus
    except Exception as exc:
        print(f"[startup] central_brain init skipped: {exc}")

    # Start health monitor for arm + asset containers (runs in background thread)
    from app.services.health_monitor import start as start_health_monitor

    start_health_monitor()

    return app
