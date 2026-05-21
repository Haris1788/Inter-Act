"""Phase 2 event taxonomy — single source of truth.

ALL bus traffic uses one of the constants below as its `type` field.
Adding a new event? Add it here first, then commit, then use it.
"""

# ── Lifecycle ────────────────────────────────────────────────────────────────
ACTOR_ONLINE         = "ACTOR_ONLINE"          # actor boots, announces itself
ACTOR_OFFLINE        = "ACTOR_OFFLINE"         # actor shuts down cleanly
CAPABILITY_DECLARED  = "CAPABILITY_DECLARED"   # actor advertises what it can do

# ── Task lifecycle ───────────────────────────────────────────────────────────
TASK_CREATED         = "TASK_CREATED"          # Interpreter → all actors
TASK_AVAILABILITY    = "TASK_AVAILABILITY"     # actor → Interpreter (can I participate?)
PATH_PROPOSED        = "PATH_PROPOSED"         # Interpreter → named actors
PATH_ACCEPTED        = "PATH_ACCEPTED"         # actor → bus
PATH_REJECTED        = "PATH_REJECTED"         # actor → bus, with reason
TASK_COMPLETED       = "TASK_COMPLETED"        # final actor → bus

# ── Busy state ───────────────────────────────────────────────────────────────
ACTOR_BUSY           = "ACTOR_BUSY"
ACTOR_FREE           = "ACTOR_FREE"

# ── Failure ──────────────────────────────────────────────────────────────────
ACTOR_FAILED         = "ACTOR_FAILED"
REPLAN_REQUIRED      = "REPLAN_REQUIRED"


def make_envelope(event_type: str, actor_id: str, payload: dict,
                  task_id: str | None = None,
                  correlation_id: str | None = None) -> dict:
    """Build a Phase-2 envelope. Used by every publisher.

    Shape:
        {"type", "actorId", "taskId"?, "correlationId"?, "ts", "payload"}
    """
    import time, uuid
    env = {
        "type": event_type,
        "actorId": actor_id,
        "ts": time.time(),
        "msgId": uuid.uuid4().hex,
        "payload": payload,
    }
    if task_id is not None:
        env["taskId"] = task_id
    if correlation_id is not None:
        env["correlationId"] = correlation_id
    return env
