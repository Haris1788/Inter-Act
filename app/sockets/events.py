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


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    print('[SocketIO] Browser disconnected')
