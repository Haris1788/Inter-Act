"""
Arm control routes - REST API for controlling robotic arms.

POST /set_angle      - Set target angle for an arm
POST /object_event   - Update object position in the pipeline
GET  /state          - Get current state of all arms
GET  /state/<id>     - Get current state of a single arm
GET  /objects        - Get current object states (for reconnection)
GET  /config         - Get full scene configuration
"""

import os
import threading
from datetime import datetime
from flask import Blueprint, request, jsonify
from app.models import arm_state
from app.models import asset_state
from app.models.scene_config import get_full_config
from app.models import pipeline_config
from app.services import container_manager
from app.services import deploy_bridge
from app import socketio

# ═══════════════════════════════════════════════════════════════════════════
# IMPORTS FOR STEP 4: Execute task with Qwen brain
# ═══════════════════════════════════════════════════════════════════════════
# Import executor and notification services (created in STEP 2 and 3)
from app.services.arm_execution import get_arm_executor
from app.services.notifications import get_notification_service
from app.services.arm_brain import get_arm_brain
from app.services.arm_scripts import list_scripts, run_script

arm_bp = Blueprint("arms", __name__)

# In-memory object state for the pipeline visualization (lock-protected)
_object_states = (
    {}
)  # {"Obj1": {"object_id": "Obj1", "state": "intake", "carrier_arm": None}}
_object_lock = threading.Lock()
_object_versions = {}  # {"Obj1": last_event_time_ms}
_object_owners = {}  # {"Obj1": "arm-1" | "TP arm-1->arm-2" | "veh-a" | ...}
_event_seq = 0  # monotonic backend sequence for object_update ordering


def _topology_snapshot():
    flow = pipeline_config.get_module_flow_order()
    assets = {a["id"]: a for a in pipeline_config.get_all_assets()}
    transfer_points = {}
    for i in range(len(flow) - 1):
        src = flow[i]
        dst = flow[i + 1]
        tp_id = f"TP {src}->{dst}"
        transfer_points[tp_id] = {"from": src, "to": dst}
    last_arm = None
    for sid in reversed(flow):
        if isinstance(sid, str) and sid.startswith("arm-"):
            last_arm = sid
            break
    return {
        "flow": flow,
        "assets": assets,
        "transfer_points": transfer_points,
        "last_arm": last_arm,
        "asset_index": {sid: i for i, sid in enumerate(flow)},
    }


def _canonical_owner(owner_type, owner_id):
    if owner_type == "zone":
        if owner_id == "intake-zone":
            return "intake"
        if owner_id == "output-zone":
            return "output"
    if owner_type == "transfer_point":
        return "transfer_point"
    if owner_type == "arm":
        return "arm"
    if owner_type in ("asset", "vehicle"):
        return "asset"
    return None


def _owner_exists(topo, owner_type, owner_id):
    canonical = _canonical_owner(owner_type, owner_id)
    if canonical in ("intake", "output"):
        return True
    if canonical == "transfer_point":
        return owner_id in topo["transfer_points"]
    if canonical == "arm":
        rec = topo["assets"].get(owner_id)
        return bool(rec and rec.get("type") == "arm")
    if canonical == "asset":
        rec = topo["assets"].get(owner_id)
        return bool(rec and rec.get("type") == "vehicle")
    return False


def _adjacent_owner(topo, src_id, dst_id):
    idx = topo["asset_index"]
    if src_id not in idx or dst_id not in idx:
        return False
    return idx[dst_id] == idx[src_id] + 1


def _valid_transition(
    topo, prev_owner_type, prev_owner_id, next_owner_type, next_owner_id
):
    prev_kind = _canonical_owner(prev_owner_type, prev_owner_id)
    next_kind = _canonical_owner(next_owner_type, next_owner_id)
    if prev_kind is None or next_kind is None:
        return False, "unknown owner type"

    # Repeated updates for the same owner are valid (e.g. timing/slot refresh).
    if prev_owner_type == next_owner_type and prev_owner_id == next_owner_id:
        return True, None

    if prev_kind == "intake":
        if next_kind == "arm":
            first_arm = next(
                (sid for sid in topo["flow"] if sid.startswith("arm-")), None
            )
            return (next_owner_id == first_arm, "intake must transition to first arm")
        return False, "intake can only transition to arm"

    if prev_kind == "arm":
        # Race-tolerant fast path:
        # In concurrent arm threads, a direct arm->next-arm or arm->asset event can
        # arrive before the intermediate transfer-point event is committed.
        if next_kind in ("arm", "asset"):
            tp_id = f"TP {prev_owner_id}->{next_owner_id}"
            if tp_id in topo["transfer_points"]:
                return True, None
        if next_kind == "transfer_point":
            tp = topo["transfer_points"].get(next_owner_id)
            if not tp:
                return False, "unknown transfer point"
            if tp["from"] != prev_owner_id:
                return False, "arm can only place to its own outbound transfer point"
            return True, None
        if next_kind == "output":
            if prev_owner_id != topo["last_arm"]:
                return False, "only terminal arm may transition to output"
            return True, None
        return False, "arm can only transition to transfer_point or output"

    if prev_kind == "transfer_point":
        tp = topo["transfer_points"].get(prev_owner_id)
        if not tp:
            return False, "unknown transfer point"
        # Race-tolerant fast path:
        # If terminal-arm "carried" event was dropped/reordered, allow TP->output
        # only when this TP feeds the terminal arm.
        if next_kind == "output":
            return (
                tp["to"] == topo["last_arm"],
                "only terminal inbound transfer point may transition to output",
            )
        if next_kind not in ("arm", "asset"):
            return False, "transfer_point can only transition to arm or asset"
        if tp["to"] != next_owner_id:
            return (
                False,
                "transfer_point can only transition to declared downstream owner",
            )
        return True, None

    if prev_kind == "asset":
        if next_kind == "transfer_point":
            tp = topo["transfer_points"].get(next_owner_id)
            if not tp:
                return False, "unknown transfer point"
            if tp["from"] != prev_owner_id:
                return (
                    False,
                    "asset can only transition to its own outbound transfer point",
                )
            return True, None
        if next_kind == "arm":
            if not _adjacent_owner(topo, prev_owner_id, next_owner_id):
                return False, "asset->arm transition must follow topology adjacency"
            return True, None
        return False, "asset can only transition to transfer_point or arm"

    if prev_kind == "output":
        return False, "output is terminal owner"

    return False, "invalid owner transition"


def get_object_states():
    """Thread-safe snapshot of all currently tracked objects.

    Used by arm_scripts.py to look up "is there a real object at intake?"
    before a relay script tries to pick something that isn't there.
    """
    with _object_lock:
        return [dict(o) for o in _object_states.values()]


def find_object_at(state):
    """Return the first object whose pipeline state matches, or None.

    Args:
        state: one of "intake" / "handoff_1" / "handoff_2" / "output" / "carried"
    """
    for obj in get_object_states():
        if obj.get("state") == state:
            return obj
    return None


@arm_bp.route("/set_angle", methods=["POST"])
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
        return (
            jsonify({"error": "Request body must be JSON", "code": "INVALID_BODY"}),
            400,
        )

    arm_id = data.get("arm_id")
    angle = data.get("angle")

    if arm_id is None:
        return (
            jsonify(
                {"error": "Missing required field: arm_id", "code": "MISSING_FIELD"}
            ),
            400,
        )
    if angle is None:
        return (
            jsonify(
                {"error": "Missing required field: angle", "code": "MISSING_FIELD"}
            ),
            400,
        )

    # Validate types
    if not isinstance(arm_id, int):
        return (
            jsonify({"error": "arm_id must be an integer", "code": "VALIDATION_ERROR"}),
            400,
        )
    if not isinstance(angle, (int, float)):
        return (
            jsonify({"error": "angle must be a number", "code": "VALIDATION_ERROR"}),
            400,
        )

    try:
        arm_state.set_angle(arm_id, float(angle))
    except ValueError as e:
        return jsonify({"error": str(e), "code": "VALIDATION_ERROR"}), 400

    # Emit state update to all connected browser clients
    socketio.emit("state_update", arm_state.get_all())

    # Emit log message for the browser log panel
    timestamp = datetime.now().strftime("%H:%M:%S")
    socketio.emit(
        "log_message",
        {
            "timestamp": timestamp,
            "source": f"arm_{arm_id}",
            "message": f"Set angle to {float(angle):.1f}\u00b0",
        },
    )

    return jsonify({"status": "ok", "arm_id": arm_id, "angle": float(angle)})


# ═══════════════════════════════════════════════════════════════════════════
# STEP 4: EXECUTE TASK WITH QWEN BRAIN
# FILE: app/routes/arm_routes.py (NEW ENDPOINT)
# PURPOSE: Backend API endpoint to receive tasks and execute via Qwen brain
# ═══════════════════════════════════════════════════════════════════════════


@arm_bp.route("/execute_task", methods=["POST"])
def execute_task():
    """
    ┌─────────────────────────────────────────────────────────────────────┐
    │  EXECUTE_TASK: Execute task on arm using Qwen brain                 │
    └─────────────────────────────────────────────────────────────────────┘

    WHAT THIS ENDPOINT DOES:
    ========================
    Central entry point for task execution with AI decision-making.

    Complete flow:
    1. Receive task request from frontend/demo
    2. Validate input (arm_id, task)
    3. Send "processing" notification to frontend
    4. Get executor for the arm
    5. Execute task (1. get state → 2. ask brain → 3. execute → 4. feedback)
    6. Send notification based on result (success or failure)
    7. Return result to caller

    REQUEST BODY (JSON):
    ====================
    {
        "arm_id": 1,                          # Which arm? (1, 2, or 3)
        "task": "pick object from intake"     # What to do? (task description)
    }

    SUCCESS RESPONSE (200):
    =======================
    {
        "status": "success",
        "arm_id": 1,
        "action": "pick",                     # What did brain decide?
        "target_angle": 45,                   # Where did it move?
        "confidence": 0.95,                   # How sure was Qwen?
        "reasoning": "Object at intake...",   # Why this decision?
        "timestamp": "2026-04-22T14:32:15",  # When?
        "message": "✅ Arm 1 completed 'pick' successfully"
    }

    FAILURE RESPONSE (400 or 500):
    ==============================
    {
        "status": "failed",
        "arm_id": 1,
        "action": "pick",                     # What was it trying?
        "reason": "Arm server timeout",       # Why did it fail?
        "message": "❌ Arm 1 FAILED: Server timeout"
    }

    WHY THIS ENDPOINT EXISTS:
    =========================
    Without this endpoint:
      Frontend → ??? → No way to execute tasks with AI
      Task execution is manual (user has to calculate angles)

    With this endpoint:
      Frontend → /execute_task → Executor → Brain → Qwen → Arm moves
      Task execution is automatic (AI decides angles)

    ARCHITECTURE:
    ==============
    Frontend (UI sends task request)
         ↓
    Backend (/execute_task endpoint - THIS FILE)
         ↓
    Executor (app/services/arm_execution.py - STEP 2)
         ↓
    Brain (app/services/arm_brain.py - STEP 1)
         ↓
    Qwen LLM (via Ollama)
         ↓
    Arm Server (app/services/arm_brain.py - executes movement)

    EXAMPLE CURL COMMAND:
    =====================
    curl -X POST http://localhost:5000/execute_task \\
      -H "Content-Type: application/json" \\
      -d '{
        "arm_id": 1,
        "task": "pick object from intake"
      }'

    REAL-TIME NOTIFICATIONS:
    ========================
    This endpoint sends 3 notifications to browser via WebSocket:

    1. PROCESSING: When task starts
       ⏳ Arm 1 is picking...

    2. DECISION: When brain decides (optional, for transparency)
       🧠 Arm 1 brain decided: pick at 45° (confidence: 0.95)

    3. RESULT: When task completes
       ✅ Arm 1 completed 'pick' successfully
       ❌ Arm 1 FAILED: Motor timeout
    """

    # ====================================================================
    # STEP 4.1: VALIDATE REQUEST
    # PURPOSE: Check request has valid JSON and required fields
    # ====================================================================

    # Get JSON from request body
    data = request.get_json(silent=True)
    if not data:
        # No JSON body provided
        return jsonify({"error": "JSON required"}), 400

    # Extract fields
    arm_id = data.get("arm_id")
    task = data.get("task")

    # Validate: Both fields must be present
    if not arm_id or not task:
        return jsonify({"error": "Missing arm_id or task"}), 400

    # Validate: arm_id must be integer
    if not isinstance(arm_id, int):
        return jsonify({"error": "arm_id must be integer (1, 2, or 3)"}), 400

    # Validate: task must be string
    if not isinstance(task, str) or len(task.strip()) == 0:
        return jsonify({"error": "task must be non-empty string"}), 400

    # ====================================================================
    # STEP 4.2: SEND PROCESSING NOTIFICATION
    # PURPOSE: Tell frontend "We're starting to execute this task"
    # ====================================================================
    # Frontend shows: ⏳ Arm 1 is picking...
    # User sees task is in progress, not stuck

    notif = get_notification_service()
    notif.notify_processing(arm_id, task)

    # ====================================================================
    # STEP 4.3: GET EXECUTOR FOR THIS ARM
    # PURPOSE: Get the executor that will run the task
    # ====================================================================
    # Executor is singleton (if already exists, reuse it)
    # Each arm has its own executor with its own brain

    executor = get_arm_executor(arm_id)

    # ====================================================================
    # STEP 4.4: EXECUTE TASK ON ARM
    # PURPOSE: Call executor.execute_task() to run complete flow
    # ====================================================================
    # Executor will:
    # 1. Get current arm state
    # 2. Ask brain: "What should I do with this task?"
    # 3. Send decision to arm server
    # 4. Collect feedback
    # 5. Update arm state
    # 6. Return result

    result = executor.execute_task(task)

    # ====================================================================
    # STEP 4.5: SEND RESULT NOTIFICATION TO FRONTEND
    # PURPOSE: Tell frontend how task ended (success or failure)
    # ====================================================================

    if result["status"] == "success":
        # Task succeeded! Send success notification
        notif.notify_success(
            arm_id, result.get("action", "execute"), result.get("confidence", 1.0)
        )
        # Frontend shows: ✅ Arm 1 completed 'pick' successfully
    else:
        # Task failed! Send failure notification
        notif.notify_failure(
            arm_id,
            result.get("reason", "Unknown error"),
            result.get("action", "unknown"),
        )
        # Frontend shows: ❌ Arm 1 FAILED: Motor timeout

    # ====================================================================
    # STEP 4.6: EMIT RESULT TO FRONTEND AND RETURN
    # PURPOSE: Send task result to browser via WebSocket + HTTP response
    # ====================================================================

    # Emit via WebSocket (real-time to browser)
    socketio.emit("task_result", result)

    # Return via HTTP (to caller of REST API)
    # Status code: 200 if success, 400+ if failed
    status_code = 200 if result["status"] == "success" else 500
    return jsonify(result), status_code


# ═══════════════════════════════════════════════════════════════════════════
# SCRIPT-DISPATCHER ENDPOINTS (US 17)
# Qwen picks one named behaviour from a per-arm menu, then we execute it.
# Different from /execute_task which asks Qwen to invent an angle.
# ═══════════════════════════════════════════════════════════════════════════


@arm_bp.route("/topology", methods=["GET"])
def get_topology():
    """Return the actor flow order so the UI can walk the pipeline."""
    return jsonify({"flow": pipeline_config.get_module_flow_order()})


@arm_bp.route("/scripts/<int:arm_id>", methods=["GET"])
def get_arm_script_menu(arm_id):
    """List the named behaviours available for one arm (the Qwen menu)."""
    menu = list_scripts(arm_id)
    if not menu:
        return jsonify({"error": f"unknown arm_id {arm_id}"}), 400
    return jsonify({"arm_id": arm_id, "scripts": menu})


@arm_bp.route("/actor_scripts/<actor_id>", methods=["GET"])
def get_actor_script_menu(actor_id):
    """List menus for any actor - arms (numeric) or assets (string id)."""
    try:
        resolved = int(actor_id)
    except (TypeError, ValueError):
        resolved = actor_id
    menu = list_scripts(resolved)
    if not menu:
        return jsonify({"error": f"unknown actor_id {actor_id}"}), 400
    return jsonify({"actor_id": resolved, "scripts": menu})


# Track each actor's last chosen script - used to suppress log spam when
# an idle actor keeps polling and the answer is the same hold every time.
_actor_last_decision = {}


def _pick_for_asset(asset_id, task, menu, obj_summary):
    """Lightweight asset-side brain: same closed-menu shape as the arm brain.

    Assets only have 2 menu options (relay / hold) so we skip Qwen entirely
    and pick deterministically from the world state - which makes the
    asset's decision visible in the log without paying the 5–30 s LLM
    latency. If the asset is failed/draining/stopped, force hold.
    """
    from app.services.arm_scripts import _actor_zones

    rec = asset_state.get(asset_id)
    is_alive = (rec is None) or (
        rec.get("status") not in ("failed", "draining", "stopped")
    )
    zones = _actor_zones(asset_id)
    upstream = zones["upstream"]
    relay_options = [s["name"] for s in menu if s["name"].startswith("relay_")]
    hold_options = [s["name"] for s in menu if s["name"].startswith("hold_")]
    source_count = obj_summary.get(upstream, 0)
    if not is_alive:
        return {
            "status": "success",
            "script": hold_options[0] if hold_options else "",
            "qwen_suggested": "hold (asset not alive)",
            "reasoning": f"{asset_id} is {rec.get('status') if rec else 'unknown'}; cannot relay",
            "override_reason": f"{asset_id} not alive",
            "confidence": 1.0,
        }
    if source_count >= 1 and relay_options:
        return {
            "status": "success",
            "script": relay_options[0],
            "qwen_suggested": relay_options[0],
            "reasoning": f"object present at {upstream}; advancing",
            "override_reason": None,
            "confidence": 1.0,
        }
    return {
        "status": "success",
        "script": hold_options[0] if hold_options else "",
        "qwen_suggested": hold_options[0] if hold_options else "",
        "reasoning": f"no object at {upstream}; nothing to relay",
        "override_reason": None,
        "confidence": 1.0,
    }


@arm_bp.route("/run_script", methods=["POST"])
def run_script_endpoint():
    """Ask Qwen (arms) or the deterministic asset-brain (shuttle) to
    pick a script for this actor + task, then run it.

    Request:
        {"arm_id": 1, "task": "do your relay"}
        {"actor_id": "veh-a", "task": "do your relay"}   ← asset form
    Response:
        {"status": "success", "arm_id": 1, "chosen_script": "...", ...}
    """
    data = request.get_json(silent=True) or {}
    raw_actor = data.get("actor_id", data.get("arm_id"))
    task = data.get("task")

    if not isinstance(task, str) or not task.strip():
        return jsonify({"error": "task must be a non-empty string"}), 400

    # Resolve actor_id - int means arm, string means asset.
    if isinstance(raw_actor, int):
        actor_id = raw_actor
        is_arm = True
    elif isinstance(raw_actor, str) and raw_actor.strip():
        actor_id = raw_actor.strip()
        is_arm = False
    else:
        return jsonify({"error": "actor_id (str) or arm_id (int) required"}), 400

    if is_arm:
        valid_arm_ids = pipeline_config.get_arm_ids()
        if actor_id not in valid_arm_ids:
            return jsonify({"error": f"arm_id must be one of {valid_arm_ids}"}), 400
    else:
        valid_asset_ids = [a["id"] for a in pipeline_config.get_extra_assets()]
        if actor_id not in valid_asset_ids:
            return jsonify({"error": f"actor_id must be one of {valid_asset_ids}"}), 400

    notif = get_notification_service()

    # Build the per-actor menu and a count summary of where objects currently
    # sit (upstream zones for arms + assets - covers handover_<i>, intake,
    # output, on_<asset>, carried).
    menu = list_scripts(actor_id)
    obj_summary = {s: 0 for s in pipeline_config.get_valid_handover_states()}
    # also count asset zones
    for a in pipeline_config.get_extra_assets():
        obj_summary[f"on_{a['id']}"] = 0
    for o in get_object_states():
        s = o.get("state")
        if s in obj_summary:
            obj_summary[s] += 1

    if is_arm:
        brain = get_arm_brain(actor_id)
        pick = brain.pick_script(task, menu, object_summary=obj_summary)
    else:
        pick = _pick_for_asset(actor_id, task, menu, obj_summary)

    if pick.get("status") != "success":
        if is_arm:
            notif.notify_failure(
                actor_id, pick.get("error", "brain unavailable"), action="pick_script"
            )
        return (
            jsonify(
                {
                    "status": "failed",
                    "actor_id": actor_id,
                    "reason": pick.get("error", "brain unavailable"),
                }
            ),
            500,
        )

    chosen = pick["script"]
    qwen_suggested = pick.get("qwen_suggested", chosen)
    override_reason = pick.get("override_reason")

    # ── Idle de-dupe ────────────────────────────────────────────────
    # If this is the SAME hold script as the previous decision for this
    # actor, the world has not changed for them - go silent. Skip the
    # script_chosen event, skip the script body's log lines (quiet=True),
    # skip the Task-completed notification. Only relays and *first* holds
    # of an idle streak surface to the operator. This is what makes
    # "nothing to do" actually look like nothing in the UI.
    last = _actor_last_decision.get(actor_id)
    is_repeat_idle = (chosen.startswith("hold_") or chosen == "return_to_rest") and (
        last == chosen
    )
    _actor_last_decision[actor_id] = chosen

    timestamp = datetime.now().strftime("%H:%M:%S")
    src_label = f"arm_{actor_id}" if is_arm else str(actor_id)

    if not is_repeat_idle:
        if is_arm:
            notif.notify_processing(actor_id, "thinking")
        payload = {
            "actor_id": actor_id,
            "script": chosen,
            "qwen_suggested": qwen_suggested,
            "override_reason": override_reason,
            "reasoning": pick.get("reasoning", ""),
            "fallback": pick.get("fallback", False),
            "timestamp": timestamp,
        }
        if is_arm:
            payload["arm_id"] = actor_id
        socketio.emit("script_chosen", payload)
        if override_reason:
            socketio.emit(
                "log_message",
                {
                    "timestamp": timestamp,
                    "source": src_label,
                    "level": "info",
                    "message": f"🤖 suggested: {qwen_suggested}  - {pick.get('reasoning', '')}",
                },
            )
            socketio.emit(
                "log_message",
                {
                    "timestamp": timestamp,
                    "source": src_label,
                    "level": "warning",
                    "message": f"⚖️  Override → {chosen}  ({override_reason})",
                },
            )
        else:
            # Distinguish Qwen-driven (work to do) from rule fast-path (idle).
            label = (
                "🛌 idle"
                if (chosen.startswith("hold_") or chosen == "return_to_rest")
                else ("🎬 Qwen chose" if is_arm else "🎬 AI chose")
            )
            socketio.emit(
                "log_message",
                {
                    "timestamp": timestamp,
                    "source": src_label,
                    "level": "info",
                    "message": f"{label}: {chosen}  - {pick.get('reasoning', '')}",
                },
            )

    try:
        result = run_script(actor_id, chosen, quiet=is_repeat_idle)
    except Exception as e:
        if is_arm:
            notif.notify_failure(actor_id, str(e), action=chosen)
        return (
            jsonify(
                {
                    "status": "failed",
                    "actor_id": actor_id,
                    "chosen_script": chosen,
                    "reason": str(e),
                }
            ),
            500,
        )

    if is_arm and not is_repeat_idle:
        notif.notify_success(actor_id, chosen, confidence=pick.get("confidence", 1.0))

    return jsonify(
        {
            "status": "success",
            "actor_id": actor_id,
            "arm_id": actor_id if is_arm else None,
            "chosen_script": chosen,
            "reasoning": pick.get("reasoning", ""),
            "fallback": pick.get("fallback", False),
            "result": result,
        }
    )


@arm_bp.route("/state", methods=["GET"])
def get_state():
    """Get the current state of all arms.

    Returns:
        200: {"arms": [{"arm_id": 1, "angle": 90.0, "status": "idle"}, ...]}
    """
    return jsonify(arm_state.get_all())


@arm_bp.route("/state/<int:arm_id>", methods=["GET"])
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


@arm_bp.route("/object_event", methods=["POST"])
def object_event():
    """Update an object's position in the pipeline.

    Request body (JSON):
        object_id (str): Object identifier (e.g. "Obj1")
        state (str): "intake", "carried", "handover_1"..."handover_{N-1}", or "output".
                     Valid names are generated dynamically from pipeline_config.get_valid_handover_states().
        carrier_arm (int|null): Arm ID when state is "carried", null otherwise

    Returns:
        200: {"status": "ok"}
        400: {"error": "..."}
    """
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Request body must be JSON"}), 400

    object_id = data.get("object_id")
    state = data.get("state")
    if not object_id or not state:
        return jsonify({"error": "Missing object_id or state"}), 400

    # Phase 2: also accept asset-mediated states like 'on_<asset_id>',
    # 'at_<asset_id>_exit', and 'carried' with carrier_asset payload.
    valid_states = set(pipeline_config.get_valid_handover_states())
    valid_states.add("transfer_point")
    is_asset_state = (
        state.startswith("on_") or state.startswith("at_") or state == "carried"
    )
    if state not in valid_states and not is_asset_state:
        return jsonify({"error": f"Invalid state: {state}"}), 400

    incoming_ts = data.get("event_time")
    if incoming_ts is None:
        incoming_ts = data.get("start_time")
    if incoming_ts is None:
        incoming_ts = int(datetime.now().timestamp() * 1000)
    try:
        incoming_ts = int(incoming_ts)
    except Exception:
        return jsonify({"error": "Invalid event_time"}), 400

    owner_id = data.get("owner_id")
    owner_type = data.get("owner_type")
    if owner_id is None:
        if data.get("carrier_asset"):
            owner_id = data.get("carrier_asset")
            owner_type = owner_type or "asset"
        elif data.get("carrier_arm") is not None:
            owner_id = f"arm-{data.get('carrier_arm')}"
            owner_type = owner_type or "arm"
        elif isinstance(state, str) and state.startswith("handover_"):
            owner_id = state
            owner_type = owner_type or "transfer_point"
        elif state in ("intake", "output"):
            owner_id = f"{state}-zone"
            owner_type = owner_type or "zone"

    if not owner_id or not owner_type:
        return (
            jsonify(
                {
                    "error": "missing ownership fields",
                    "code": "INVALID_OWNERSHIP",
                }
            ),
            400,
        )

    topo = _topology_snapshot()
    if not _owner_exists(topo, owner_type, owner_id):
        return (
            jsonify(
                {
                    "error": f"unknown owner {owner_type}:{owner_id}",
                    "code": "UNKNOWN_OWNER",
                }
            ),
            409,
        )

    obj = {
        "object_id": object_id,
        "state": state,
        "carrier_arm": data.get("carrier_arm"),
        "event_time": incoming_ts,
    }
    # Optional asset metadata - additive, frontend already reads carrier_asset.
    carrier_asset = data.get("carrier_asset")
    if carrier_asset:
        obj["carrier_asset"] = carrier_asset
    if owner_id is not None:
        obj["owner_id"] = owner_id
    if owner_type is not None:
        obj["owner_type"] = owner_type
    if "slot" in data:
        obj["slot"] = data["slot"]
    # Phase 3: timing fields so the frontend can interpolate positions or
    # snap deterministically based on backend timestamps. All optional.
    for ts_field in ("start_time", "duration_ms", "event_time"):
        if ts_field in data and data[ts_field] is not None:
            obj[ts_field] = data[ts_field]
    global _event_seq
    with _object_lock:
        prev_ts = _object_versions.get(object_id)
        if prev_ts is not None and incoming_ts < prev_ts:
            return jsonify({"status": "ignored_stale", "object_id": object_id}), 202
        prev_obj = _object_states.get(object_id)
        prev_owner_type = (prev_obj or {}).get("owner_type")
        prev_owner_id = (prev_obj or {}).get("owner_id")
        if prev_owner_type and prev_owner_id:
            ok, reason = _valid_transition(
                topo,
                prev_owner_type,
                prev_owner_id,
                owner_type,
                owner_id,
            )
            if not ok:
                return (
                    jsonify(
                        {
                            "error": f"invalid ownership transition {prev_owner_type}:{prev_owner_id} -> "
                            f"{owner_type}:{owner_id}: {reason}",
                            "code": "INVALID_OWNERSHIP_TRANSITION",
                        }
                    ),
                    409,
                )
        _object_versions[object_id] = incoming_ts
        _event_seq += 1
        obj["event_seq"] = _event_seq

        prev_owner = _object_owners.get(object_id)
        if owner_id is not None and prev_owner != owner_id:
            _object_owners[object_id] = owner_id
            timestamp = datetime.now().strftime("%H:%M:%S")
            socketio.emit(
                "log_message",
                {
                    "timestamp": timestamp,
                    "source": "ownership",
                    "message": f"[OWNERSHIP] {object_id} -> {owner_type}:{owner_id}",
                },
            )
        _object_states[object_id] = obj
    socketio.emit("object_update", obj)
    return jsonify({"status": "ok"})


@arm_bp.route("/objects", methods=["GET"])
def get_objects():
    """Get current state of all tracked objects (for reconnection sync).

    Returns:
        200: {"objects": [...]}
    """
    with _object_lock:
        snapshot = list(_object_states.values())
    return jsonify({"objects": snapshot})


@arm_bp.route("/objects/reset", methods=["POST"])
def reset_objects():
    """Clear all tracked objects (called at start of new demo run)."""
    with _object_lock:
        _object_states.clear()
        _object_versions.clear()
        _object_owners.clear()
    # Forget per-actor last-decision history so the next call after a
    # reset emits its log line even if the answer happens to repeat.
    _actor_last_decision.clear()
    socketio.emit("objects_reset", {})
    return jsonify({"status": "ok"})


@arm_bp.route("/objects/spawn", methods=["POST"])
def spawn_object():
    """Spawn one object at intake so the script-driven pipeline has work to do.

    Body (optional): {"object_id": "Obj1"}
    Auto-numbers Obj1, Obj2, Obj3, ... if no id is supplied.
    """
    data = request.get_json(silent=True) or {}
    explicit_id = data.get("object_id")
    with _object_lock:
        if explicit_id:
            object_id = str(explicit_id)
        else:
            n = 1
            while f"Obj{n}" in _object_states:
                n += 1
            object_id = f"Obj{n}"
        # Emit ownership in the new namespace so the frontend can position
        # the object at the intake zone (it ignores updates without
        # owner_type / owner_id).
        obj = {
            "object_id": object_id,
            "state": "intake",
            "carrier_arm": None,
            "owner_type": "zone",
            "owner_id": "intake-zone",
            "event_time": int(datetime.now().timestamp() * 1000),
        }
        _object_states[object_id] = obj
        _object_owners[object_id] = "intake-zone"
    socketio.emit("object_update", obj)
    return jsonify({"status": "ok", "object": obj})


@arm_bp.route("/config", methods=["GET"])
def get_config():
    """Get the full scene configuration for browser initialization.

    Returns:
        200: Full scene config JSON (canvas size, arms, zones, etc.)
    """
    return jsonify(get_full_config())


@arm_bp.route("/reload_config", methods=["POST"])
def reload_config():
    """Reload pipeline.yaml into memory. Does NOT reset runtime state.

    Safe to call during a running simulation - the in-memory config
    is updated, but arm angles, health, and tracked objects are untouched.
    Caller should invoke POST /restart_pipeline to apply the new config.

    Returns:
        200: {"status": "ok", "arms": N, "graph_valid": bool, "graph_errors": [...]}
    """
    pipeline_config.reload()
    n = pipeline_config.get_arm_count()
    valid, errors = pipeline_config.validate_dependency_graph()
    return jsonify(
        {
            "status": "ok",
            "arms": n,
            "graph_valid": valid,
            "graph_errors": errors,
        }
    )


@arm_bp.route("/restart_pipeline", methods=["POST"])
def restart_pipeline():
    """Reinitialize arm state and objects using the currently-loaded config.

    Does NOT re-read pipeline.yaml. Call POST /reload_config first if you
    want to apply changes on disk. This separation lets config reload and
    runtime mutation stay independent operations.

    Returns:
        200: {"status": "ok", "arms": N}
    """
    arm_state.reinitialize()
    with _object_lock:
        _object_states.clear()
    socketio.emit("state_update", arm_state.get_all())
    socketio.emit("objects_reset", {})
    n = pipeline_config.get_arm_count()
    return jsonify({"status": "ok", "arms": n})


@arm_bp.route("/deploy/apply", methods=["POST"])
def deploy_apply():
    """Apply an authored topology to the REAL runtime (Phase 15 bridge).

    Body: the frontend deploy payload {actors, zones, goal}. Translates it
    to pipeline.yaml, regenerates docker-compose.yml, brings containers up,
    reloads config and re-inits arm state so the health monitor confirms
    liveness. Honest: when the config is read-only or docker/compose is
    unavailable, ``applied`` is False and the caller must keep the actor
    stale - nothing here fakes a container.
    """
    payload = request.get_json(silent=True) or {}
    actors = payload.get("actors")
    if not isinstance(actors, list):
        return jsonify({"error": "actors[] required", "code": "BAD_PAYLOAD"}), 400

    result = deploy_bridge.apply_topology(payload)

    timestamp = datetime.now().strftime("%H:%M:%S")
    socketio.emit(
        "log_message",
        {
            "timestamp": timestamp,
            "source": "pipeline",
            "message": (
                f"Deploy apply: {result['detail']} "
                f"(arms={result['arms']}, applied={result['applied']})"
            ),
        },
    )
    if result.get("steps", {}).get("reloaded") == "ok":
        socketio.emit("state_update", arm_state.get_all())
    return jsonify(result)


# --- Arm health and lifecycle ---


@arm_bp.route("/arms/health", methods=["GET"])
def get_arms_health():
    """Return health status of all arms."""
    health = arm_state.get_all_health()
    return jsonify({"arms": health})


@arm_bp.route("/arms/<int:arm_id>/health", methods=["POST"])
def set_arm_health(arm_id):
    """Set health status for an arm (called by arm containers and demo-runner).

    When health is 'dead', marks only this arm - downstream arms are NOT
    cascade-stopped. They drain remaining products from handover zones, then
    individually report as 'stopped' via this same endpoint.
    """
    data = request.get_json(silent=True) or {}
    health = data.get("health", "alive")
    error = data.get("error")

    if health == "dead":
        arm_state.mark_arm_failed(arm_id, error or "container stopped")
        socketio.emit(
            "arm_health", {"arm_id": arm_id, "health": "dead", "error": error}
        )

        timestamp = datetime.now().strftime("%H:%M:%S")
        socketio.emit(
            "log_message",
            {
                "timestamp": timestamp,
                "source": "pipeline",
                "message": f'Arm {arm_id} FAILED: {error or "unknown"}',
            },
        )
    else:
        arm_state.set_health(arm_id, health, error)
        socketio.emit(
            "arm_health", {"arm_id": arm_id, "health": health, "error": error}
        )

    return jsonify({"status": "ok"})


@arm_bp.route("/arms/<int:arm_id>/stop", methods=["POST"])
def stop_arm(arm_id):
    """Stop an arm container AND propagate to all downstream dependents.

    Phase 7: actually stops the docker container so the failure path is
    indistinguishable from a `docker stop` issued from the CLI. State
    update remains so downstream cascades happen instantly without
    waiting for the health monitor.
    """
    data = request.get_json(silent=True) or {}
    reason = data.get("reason", "manually stopped")

    docker_err = container_manager.stop_container(f"arm-{arm_id}")

    arm_state.mark_arm_stopped(arm_id, reason)
    downstream = pipeline_config.get_all_downstream(arm_id)

    # Broadcast health update for this arm and all downstream
    all_affected = [arm_id] + downstream
    for aid in all_affected:
        h = arm_state.get_health(aid)
        socketio.emit("arm_health", {"arm_id": aid, **h})

    # Log it
    timestamp = datetime.now().strftime("%H:%M:%S")
    socketio.emit(
        "log_message",
        {
            "timestamp": timestamp,
            "source": "pipeline",
            "message": f"Arm {arm_id} stopped: {reason}. Downstream affected: {downstream}",
        },
    )

    return jsonify(
        {
            "status": "ok",
            "arm_id": arm_id,
            "downstream_stopped": downstream,
            "container_control": "ok" if docker_err is None else docker_err,
        }
    )


@arm_bp.route("/arms/<int:arm_id>/start", methods=["POST"])
def start_arm(arm_id):
    """Start an arm container. Health monitor recovers the in-memory state."""
    docker_err = container_manager.start_container(f"arm-{arm_id}")

    timestamp = datetime.now().strftime("%H:%M:%S")
    socketio.emit(
        "log_message",
        {
            "timestamp": timestamp,
            "source": "pipeline",
            "message": f"Arm {arm_id} container start requested",
        },
    )
    return jsonify(
        {
            "status": "ok",
            "arm_id": arm_id,
            "container_control": "ok" if docker_err is None else docker_err,
        }
    )


@arm_bp.route("/arms/<int:arm_id>/restart", methods=["POST"])
def restart_arm(arm_id):
    """Restart an arm container (stop then start) as one operational action.

    Phase 13: real container control for the UI fleet/inspector restart
    button. In-memory state is left to the health monitor to reconcile
    once the container reports healthy again - we do not fake an idle
    state the container has not actually reached.
    """
    stop_err = container_manager.stop_container(f"arm-{arm_id}")
    start_err = container_manager.start_container(f"arm-{arm_id}")

    timestamp = datetime.now().strftime("%H:%M:%S")
    socketio.emit(
        "log_message",
        {
            "timestamp": timestamp,
            "source": "pipeline",
            "message": f"Arm {arm_id} container restart requested",
        },
    )
    errs = [e for e in (stop_err, start_err) if e]
    return jsonify(
        {
            "status": "ok",
            "arm_id": arm_id,
            "container_control": "ok" if not errs else "; ".join(errs),
        }
    )


@arm_bp.route("/runtime/status", methods=["GET"])
def runtime_status():
    """Container/runtime snapshot for frontend reconnect reconciliation.

    Phase 13: lets the live driver re-derive truthful actor state after a
    socket reconnect or an operational action instead of advancing the UI
    optimistically. Backend stays the authority.
    """
    return jsonify(
        {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "pipeline_stopped": arm_state.is_pipeline_stopped(),
            "arms": arm_state.get_all_health(),
            "assets": asset_state.get_all(),
            "container_control": container_manager.is_enabled(),
        }
    )


@arm_bp.route("/arms/<int:arm_id>/allowed", methods=["GET"])
def check_arm_allowed(arm_id):
    """Check if an arm is allowed to execute (no upstream failures)."""
    allowed = arm_state.is_arm_allowed(arm_id)
    return jsonify({"arm_id": arm_id, "allowed": allowed})


@arm_bp.route("/pipeline/status", methods=["GET"])
def pipeline_status():
    """Return full pipeline status: arm health, dependency graph, stopped flag."""
    valid, errors = pipeline_config.validate_dependency_graph()
    return jsonify(
        {
            "pipeline_stopped": arm_state.is_pipeline_stopped(),
            "arms": arm_state.get_all_health(),
            "dependency_graph_valid": valid,
            "graph_errors": errors,
            "topological_order": pipeline_config.get_topological_order(),
        }
    )


@arm_bp.route("/log", methods=["POST"])
def post_log():
    """Receive a log message from external services and broadcast to browser.

    Body: {"source": "arm_1", "message": "Picking Obj1 from intake"}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON required"}), 400

    timestamp = datetime.now().strftime("%H:%M:%S")
    socketio.emit(
        "log_message",
        {
            "timestamp": timestamp,
            "source": data.get("source", "system"),
            "message": data.get("message", ""),
        },
    )
    return jsonify({"status": "ok"})


# ── Phase 2: non-arm asset runtime state ───────────────


@arm_bp.route("/assets/state", methods=["GET"])
def get_assets_runtime_state():
    """Return runtime status for every registered non-arm asset."""
    return jsonify({"assets": asset_state.get_all()})


@arm_bp.route("/assets/<asset_id>/state", methods=["GET"])
def get_asset_runtime_state(asset_id):
    rec = asset_state.get(asset_id)
    if rec is None:
        return jsonify({"error": "unknown asset", "code": "NOT_FOUND"}), 404
    return jsonify(rec)


@arm_bp.route("/assets/<asset_id>/fail", methods=["POST"])
def fail_asset(asset_id):
    """Stop the asset's container, then mark it failed.

    Phase 7: failure is now driven by Docker, not an in-memory flag. The
    container is stopped first so health pings start failing immediately;
    in-memory `asset_state.fail()` mirrors the reality.
    """
    data = request.get_json(silent=True) or {}
    reason = data.get("reason", "manually failed")

    # 1) Stop the container (best-effort; soft-fails when docker not wired up).
    docker_err = container_manager.stop_container(asset_id)

    # 2) Flip in-memory state so the rest of the system reacts immediately.
    rec = asset_state.fail(asset_id, reason)
    if rec is None:
        return jsonify({"error": "unknown asset", "code": "NOT_FOUND"}), 404

    timestamp = datetime.now().strftime("%H:%M:%S")
    socketio.emit(
        "log_message",
        {
            "timestamp": timestamp,
            "source": asset_id,
            "message": f"{asset_id} FAILED: {reason}",
        },
    )
    socketio.emit("asset_health", {"asset_id": asset_id, **rec})
    return jsonify(
        {
            "status": "ok",
            "asset": rec,
            "container_control": "ok" if docker_err is None else docker_err,
        }
    )


@arm_bp.route("/assets/<asset_id>/recover", methods=["POST"])
def recover_asset(asset_id):
    """Start the asset's container, then mark it recovering.

    Phase 9: also forwards a recover signal to the container's `/recover`
    endpoint so the in-container controller transitions
    failed → recovering → idle in lockstep with the backend registry.
    Without this hop the UI shows "RECOVERING" while the container's
    controller stays idle and keeps processing - exactly the bug the user
    saw on a healthy click. The health monitor still auto-flips
    `recovering → idle` on the backend side once the container reports
    `state == "idle"` via `/health`.
    """
    current = asset_state.get(asset_id)
    if current is None:
        return jsonify({"error": "unknown asset", "code": "NOT_FOUND"}), 404
    if current.get("status") in ("idle", "busy"):
        return jsonify(
            {
                "status": "ok",
                "asset": current,
                "container_control": "not needed",
                "message": f"{asset_id} is already {current.get('status')}",
            }
        )
    if current.get("status") == "recovering":
        return jsonify(
            {
                "status": "ok",
                "asset": current,
                "container_control": "already recovering",
            }
        )

    docker_err = container_manager.start_container(asset_id)

    rec = asset_state.recover(asset_id)

    # Best-effort: notify the container's controller. Compose service name
    # equals asset id; in non-Docker dev env we hit the published port.
    docker_mode = os.path.exists("/.dockerenv") or os.environ.get(
        "DEMO_RUNNER_HOST", ""
    ).startswith("http://demo-runner")
    fallback_port = pipeline_config.get_asset_host_ports().get(asset_id, 8000)
    container_url = (
        f"http://{asset_id}:8000"
        if docker_mode
        else f"http://localhost:{fallback_port}"
    )
    try:
        http_requests.post(f"{container_url}/recover", timeout=3)
    except Exception:
        pass  # Container may still be coming up; health monitor handles sync.

    timestamp = datetime.now().strftime("%H:%M:%S")
    socketio.emit(
        "log_message",
        {
            "timestamp": timestamp,
            "source": asset_id,
            "message": f"{asset_id} recovering",
        },
    )
    socketio.emit("asset_health", {"asset_id": asset_id, **rec})
    return jsonify(
        {
            "status": "ok",
            "asset": rec,
            "container_control": "ok" if docker_err is None else docker_err,
        }
    )


# ── Demo Proxy (forwards to demo-runner container) ──────

import requests as http_requests

DEMO_RUNNER_HOST = os.environ.get("DEMO_RUNNER_HOST", "http://localhost:6000")


@arm_bp.route("/demo/start", methods=["POST"])
def demo_start():
    """Proxy demo start to the demo-runner container."""
    try:
        data = request.get_json(silent=True) or {}
        resp = http_requests.post(
            f"{DEMO_RUNNER_HOST}/run",
            json=data,
            timeout=5,
        )
        return jsonify(resp.json()), resp.status_code
    except http_requests.exceptions.ConnectionError:
        return jsonify({"error": "Demo runner not available"}), 503
    except http_requests.exceptions.Timeout:
        return jsonify({"error": "Demo runner timeout"}), 504


@arm_bp.route("/demo/status", methods=["GET"])
def demo_status():
    """Proxy demo status from the demo-runner container."""
    try:
        resp = http_requests.get(f"{DEMO_RUNNER_HOST}/status", timeout=3)
        return jsonify(resp.json())
    except Exception:
        return jsonify({"running": False, "error": "Demo runner unreachable"})


@arm_bp.route("/demo/stop", methods=["POST"])
def demo_stop():
    """Proxy demo stop to the demo-runner container."""
    try:
        resp = http_requests.post(f"{DEMO_RUNNER_HOST}/stop", timeout=3)
        return jsonify(resp.json())
    except Exception:
        return jsonify({"status": "stopped"})
