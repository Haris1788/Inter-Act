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

    app.register_blueprint(page_bp)
    app.register_blueprint(arm_bp)
    app.register_blueprint(agv_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(dispatch_bp)

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

    # Phase 2 bus + registry + central brain. Loud on failure — no try/except.
    from app.agents.message_bus import SocketIOBus, set_bus
    from app.agents.central_brain import CentralBrain
    from app.services import actor_registry

    bus = SocketIOBus()                  # SocketIOBus() — no args; uses app.socketio internally
    set_bus(bus)                          # module-level singleton (used by get_bus())
    actor_registry.attach(bus)            # subscribe to ACTOR_ONLINE / CAPABILITY_DECLARED / ACTOR_OFFLINE

    from app.services import task_availability
    task_availability.attach(bus)

    from app.services import path_negotiation
    path_negotiation.attach(bus)

    # Phase 2 Step 7-9: failure tracking + replan + completion
    from app.services import task_tracker
    task_tracker.attach(bus)

    from app.services import replan_handler
    replan_handler.attach(bus)

    app.extensions["message_bus"] = bus
    app.extensions["central_brain"] = CentralBrain(bus)

    # Start health monitor for arm + asset containers (runs in background thread)
    from app.services.health_monitor import start as start_health_monitor

    start_health_monitor()

    return app
