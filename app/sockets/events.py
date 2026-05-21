"""SocketIO event handlers for real-time communication."""

import logging
from flask_socketio import emit
from app import socketio
from app.models import arm_state

logger = logging.getLogger(__name__)


@socketio.on('connect')
def handle_connect():
    """Handle new client connection. Sends current arm state immediately."""
    print('[SocketIO] Browser connected')
    emit('state_update', arm_state.get_all())

    # Hydrate AGV positions so the frontend tokens start at their bay cells.
    try:
        from app.models import agv_state, pipeline_config

        def _px(cell):
            return pipeline_config.cell_to_px(cell) if cell else None

        for rec in agv_state.get_all().values():
            emit("agv_update", {
                **rec,
                "px": _px(rec.get("cell")),
                "target_px": _px(rec.get("target_cell")),
                "bay_px": _px(rec.get("bay")),
            })
    except Exception:
        pass


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    print('[SocketIO] Browser disconnected')
