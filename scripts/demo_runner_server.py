"""
Demo Runner Microservice — executes the pipeline via HTTP calls to the backend.

This runs in its own container, separate from the Flask API server.
When triggered, it calls the backend's /set_angle and /object_event
endpoints as an external HTTP client — no self-calling, no thread starvation.

Usage:
    SERVER_HOST=http://backend:5000 python scripts/demo_runner_server.py
"""

import os
import sys
import threading

from flask import Flask, request, jsonify

# Add scripts dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_demo import run_pipeline, SPEED_FACTORS, stop_pipeline
from utils import DEFAULT_HOST

app = Flask(__name__)

# Pipeline state
_status = {"running": False, "error": None}
_thread = None


def _run_demo(num_products, speed_factor, use_brain=False):
    """Execute the pipeline in a background thread via HTTP."""
    global _status
    host = DEFAULT_HOST  # From SERVER_HOST env var
    try:
        _status = {"running": True, "error": None, "use_brain": use_brain}
        print(f'Demo started: {num_products} products, speed={speed_factor}x, host={host}, brain={use_brain}')
        run_pipeline(host, speed_factor, num_products, use_brain=use_brain)
        _status = {"running": False, "error": None}
        print('Demo completed successfully')
    except Exception as e:
        _status = {"running": False, "error": str(e)}
        print(f'Demo failed: {e}')


@app.route('/health', methods=['GET'])
def health():
    """Health check for Docker."""
    return jsonify({"status": "ok", "backend": DEFAULT_HOST})


@app.route('/run', methods=['POST'])
def run():
    """Start the pipeline demo.

    Body: {"products": 3, "speed": "normal"}
    Returns immediately while demo runs in background.
    """
    global _thread

    if _status.get("running"):
        return jsonify({"error": "Demo already running"}), 409

    data = request.get_json(silent=True) or {}
    num_products = data.get('products', 3)
    speed_name = data.get('speed', 'normal')
    speed_factor = SPEED_FACTORS.get(speed_name, 1.0)
    # Body flag wins; otherwise honour USE_QWEN_BRAIN env var.
    if 'use_brain' in data:
        use_brain = bool(data.get('use_brain'))
    else:
        use_brain = os.environ.get('USE_QWEN_BRAIN', '').lower() in ('1', 'true', 'yes')

    _thread = threading.Thread(
        target=_run_demo,
        args=(num_products, speed_factor, use_brain),
        daemon=True,
    )
    _thread.start()

    return jsonify({
        "status": "started",
        "products": num_products,
        "speed": speed_name,
        "use_brain": use_brain,
    })


@app.route('/status', methods=['GET'])
def status():
    """Check if a demo is currently running."""
    return jsonify(_status)


@app.route('/stop', methods=['POST'])
def stop():
    """Stop the running demo by signaling every module to halt."""
    global _status
    stop_pipeline()
    _status = {"running": False, "error": None}
    return jsonify({"status": "stopped"})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '6000'))
    print(f'Demo Runner starting on port {port}')
    print(f'Backend: {DEFAULT_HOST}')
    app.run(host='0.0.0.0', port=port)
