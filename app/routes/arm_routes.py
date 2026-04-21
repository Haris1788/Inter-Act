"""
Arm control routes — REST API for controlling robotic arms.

POST /set_angle      — Set target angle for an arm
POST /object_event   — Update object position in the pipeline
GET  /state          — Get current state of all arms
GET  /state/<id>     — Get current state of a single arm
GET  /objects        — Get current object states (for reconnection)
GET  /config         — Get full scene configuration
"""

import os
import threading
from datetime import datetime
from flask import Blueprint, request, jsonify
from app.models import arm_state
from app.models.scene_config import get_full_config
from app.models import pipeline_config
from app import socketio

arm_bp = Blueprint('arms', __name__)

# In-memory object state for the pipeline visualization (lock-protected)
_object_states = {}  # {"Obj1": {"object_id": "Obj1", "state": "intake", "carrier_arm": None}}
_object_lock = threading.Lock()


@arm_bp.route('/set_angle', methods=['POST'])
def set_angle():
    """Set the target angle for a specific arm.

    Request body (JSON):
        arm_id (int): Arm identifier (1, 2, or 3)
        angle (float): Target angle in degrees (0.0 to 180.0)

    Returns:
        200: {"status": "ok", "arm_id": N, "angle": X.X}
        400: {"error": "...", "code": "VALIDATION_ERROR"}
    """
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Request body must be JSON", "code": "INVALID_BODY"}), 400

    arm_id = data.get('arm_id')
    angle = data.get('angle')

    if arm_id is None:
        return jsonify({"error": "Missing required field: arm_id", "code": "MISSING_FIELD"}), 400
    if angle is None:
        return jsonify({"error": "Missing required field: angle", "code": "MISSING_FIELD"}), 400

    # Validate types
    if not isinstance(arm_id, int):
        return jsonify({"error": "arm_id must be an integer", "code": "VALIDATION_ERROR"}), 400
    if not isinstance(angle, (int, float)):
        return jsonify({"error": "angle must be a number", "code": "VALIDATION_ERROR"}), 400

    try:
        arm_state.set_angle(arm_id, float(angle))
    except ValueError as e:
        return jsonify({"error": str(e), "code": "VALIDATION_ERROR"}), 400

    # Emit state update to all connected browser clients
    socketio.emit('state_update', arm_state.get_all())

    # Emit log message for the browser log panel
    timestamp = datetime.now().strftime('%H:%M:%S')
    socketio.emit('log_message', {
        'timestamp': timestamp,
        'source': f'arm_{arm_id}',
        'message': f'Set angle to {float(angle):.1f}\u00b0',
    })

    return jsonify({"status": "ok", "arm_id": arm_id, "angle": float(angle)})


@arm_bp.route('/state', methods=['GET'])
def get_state():
    """Get the current state of all arms.

    Returns:
        200: {"arms": [{"arm_id": 1, "angle": 90.0, "status": "idle"}, ...]}
    """
    return jsonify(arm_state.get_all())


@arm_bp.route('/state/<int:arm_id>', methods=['GET'])
def get_single_state(arm_id):
    """Get the current state of a single arm.

    Args:
        arm_id: Integer arm identifier from URL path.

    Returns:
        200: {"arm_id": N, "angle": X.X, "status": "..."}
        400: {"error": "...", "code": "VALIDATION_ERROR"}
    """
    try:
        return jsonify(arm_state.get_state(arm_id))
    except ValueError as e:
        return jsonify({"error": str(e), "code": "VALIDATION_ERROR"}), 400


@arm_bp.route('/object_event', methods=['POST'])
def object_event():
    """Update an object's position in the pipeline.

    Request body (JSON):
        object_id (str): Object identifier (e.g. "Obj1")
        state (str): One of "intake", "carried", "handoff_a", "handoff_b", "output"
        carrier_arm (int|null): Arm ID when state is "carried", null otherwise

    Returns:
        200: {"status": "ok"}
        400: {"error": "..."}
    """
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Request body must be JSON"}), 400

    object_id = data.get('object_id')
    state = data.get('state')
    if not object_id or not state:
        return jsonify({"error": "Missing object_id or state"}), 400

    valid_states = pipeline_config.get_valid_handoff_states()
    if state not in valid_states:
        return jsonify({"error": f"Invalid state: {state}"}), 400

    obj = {
        "object_id": object_id,
        "state": state,
        "carrier_arm": data.get('carrier_arm'),
    }
    with _object_lock:
        _object_states[object_id] = obj
    socketio.emit('object_update', obj)
    return jsonify({"status": "ok"})


@arm_bp.route('/objects', methods=['GET'])
def get_objects():
    """Get current state of all tracked objects (for reconnection sync).

    Returns:
        200: {"objects": [...]}
    """
    with _object_lock:
        snapshot = list(_object_states.values())
    return jsonify({"objects": snapshot})


@arm_bp.route('/objects/reset', methods=['POST'])
def reset_objects():
    """Clear all tracked objects (called at start of new demo run)."""
    with _object_lock:
        _object_states.clear()
    socketio.emit('objects_reset', {})
    return jsonify({"status": "ok"})


@arm_bp.route('/config', methods=['GET'])
def get_config():
    """Get the full scene configuration for browser initialization.

    Returns:
        200: Full scene config JSON (canvas size, arms, zones, etc.)
    """
    return jsonify(get_full_config())


@arm_bp.route('/reload_config', methods=['POST'])
def reload_config():
    """Hot-reload pipeline.yaml and reinitialize arm state."""
    pipeline_config.reload()
    arm_state.reinitialize()
    n = pipeline_config.get_arm_count()
    socketio.emit('state_update', arm_state.get_all())
    return jsonify({"status": "ok", "arms": n})


# --- Arm health and lifecycle ---

@arm_bp.route('/arms/health', methods=['GET'])
def get_arms_health():
    """Return health status of all arms."""
    health = arm_state.get_all_health()
    return jsonify({"arms": health})


@arm_bp.route('/arms/<int:arm_id>/health', methods=['POST'])
def set_arm_health(arm_id):
    """Set health status for an arm (called by arm containers and demo-runner).

    When health is 'dead', marks only this arm — downstream arms are NOT
    cascade-stopped. They drain remaining products from handoff zones, then
    individually report as 'stopped' via this same endpoint.
    """
    data = request.get_json(silent=True) or {}
    health = data.get('health', 'alive')
    error = data.get('error')

    if health == 'dead':
        arm_state.mark_arm_failed(arm_id, error or 'container stopped')
        socketio.emit('arm_health', {"arm_id": arm_id, "health": "dead", "error": error})

        timestamp = datetime.now().strftime('%H:%M:%S')
        socketio.emit('log_message', {
            'timestamp': timestamp,
            'source': 'pipeline',
            'message': f'Arm {arm_id} FAILED: {error or "unknown"}',
        })
    else:
        arm_state.set_health(arm_id, health, error)
        socketio.emit('arm_health', {"arm_id": arm_id, "health": health, "error": error})

    return jsonify({"status": "ok"})


@arm_bp.route('/arms/<int:arm_id>/stop', methods=['POST'])
def stop_arm(arm_id):
    """Stop an arm and propagate to all downstream dependents."""
    data = request.get_json(silent=True) or {}
    reason = data.get('reason', 'manually stopped')

    arm_state.mark_arm_stopped(arm_id, reason)
    downstream = pipeline_config.get_all_downstream(arm_id)

    # Broadcast health update for this arm and all downstream
    all_affected = [arm_id] + downstream
    for aid in all_affected:
        h = arm_state.get_health(aid)
        socketio.emit('arm_health', {"arm_id": aid, **h})

    # Log it
    timestamp = datetime.now().strftime('%H:%M:%S')
    socketio.emit('log_message', {
        'timestamp': timestamp,
        'source': 'pipeline',
        'message': f'Arm {arm_id} stopped: {reason}. Downstream affected: {downstream}',
    })

    return jsonify({
        "status": "ok",
        "arm_id": arm_id,
        "downstream_stopped": downstream,
    })


@arm_bp.route('/arms/<int:arm_id>/allowed', methods=['GET'])
def check_arm_allowed(arm_id):
    """Check if an arm is allowed to execute (no upstream failures)."""
    allowed = arm_state.is_arm_allowed(arm_id)
    return jsonify({"arm_id": arm_id, "allowed": allowed})


@arm_bp.route('/pipeline/status', methods=['GET'])
def pipeline_status():
    """Return full pipeline status: arm health, dependency graph, stopped flag."""
    valid, errors = pipeline_config.validate_dependency_graph()
    return jsonify({
        "pipeline_stopped": arm_state.is_pipeline_stopped(),
        "arms": arm_state.get_all_health(),
        "dependency_graph_valid": valid,
        "graph_errors": errors,
        "topological_order": pipeline_config.get_topological_order(),
    })


@arm_bp.route('/log', methods=['POST'])
def post_log():
    """Receive a log message from external services and broadcast to browser.

    Body: {"source": "arm_1", "message": "Picking Obj1 from intake"}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON required"}), 400

    timestamp = datetime.now().strftime('%H:%M:%S')
    socketio.emit('log_message', {
        'timestamp': timestamp,
        'source': data.get('source', 'system'),
        'message': data.get('message', ''),
    })
    return jsonify({"status": "ok"})


# ── Demo Proxy (forwards to demo-runner container) ──────

import requests as http_requests

DEMO_RUNNER_HOST = os.environ.get('DEMO_RUNNER_HOST', 'http://localhost:6000')


@arm_bp.route('/demo/start', methods=['POST'])
def demo_start():
    """Proxy demo start to the demo-runner container."""
    try:
        data = request.get_json(silent=True) or {}
        resp = http_requests.post(
            f'{DEMO_RUNNER_HOST}/run',
            json=data,
            timeout=5,
        )
        return jsonify(resp.json()), resp.status_code
    except http_requests.exceptions.ConnectionError:
        return jsonify({"error": "Demo runner not available"}), 503
    except http_requests.exceptions.Timeout:
        return jsonify({"error": "Demo runner timeout"}), 504


@arm_bp.route('/demo/status', methods=['GET'])
def demo_status():
    """Proxy demo status from the demo-runner container."""
    try:
        resp = http_requests.get(f'{DEMO_RUNNER_HOST}/status', timeout=3)
        return jsonify(resp.json())
    except Exception:
        return jsonify({"running": False, "error": "Demo runner unreachable"})


@arm_bp.route('/demo/stop', methods=['POST'])
def demo_stop():
    """Proxy demo stop to the demo-runner container."""
    try:
        resp = http_requests.post(f'{DEMO_RUNNER_HOST}/stop', timeout=3)
        return jsonify(resp.json())
    except Exception:
        return jsonify({"status": "stopped"})
