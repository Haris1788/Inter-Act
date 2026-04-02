"""
INTER.act Application Entry Point.

Starts the Flask-SocketIO server on port 5000.
Usage: python run.py
"""

from app import create_app, socketio

app = create_app()

if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
