"""Phase 2 event taxonomy — single source of truth.

This file MUST live in scripts/ (not app/agents/) because actor containers
(Dockerfile.agv, Dockerfile.arm) only COPY scripts/ — they don't have the
backend `app/` package. Backend re-exports from here via app/agents/events.py.

Adding a new event? Add it here first, then commit, then use it.
"""

# ── Lifecycle ────────────────────────────────────────────────────────────────
ACTOR_ONLINE         = "ACTOR_ONLINE"
ACTOR_OFFLINE        = "ACTOR_OFFLINE"
CAPABILITY_DECLARED  = "CAPABILITY_DECLARED"

# ── Task lifecycle ───────────────────────────────────────────────────────────
TASK_CREATED         = "TASK_CREATED"
TASK_AVAILABILITY    = "TASK_AVAILABILITY"
PATH_PROPOSED        = "PATH_PROPOSED"
PATH_ACCEPTED        = "PATH_ACCEPTED"
PATH_REJECTED        = "PATH_REJECTED"
TASK_COMPLETED       = "TASK_COMPLETED"

# ── Busy state ───────────────────────────────────────────────────────────────
ACTOR_BUSY           = "ACTOR_BUSY"
ACTOR_FREE           = "ACTOR_FREE"

# ── Failure ──────────────────────────────────────────────────────────────────
ACTOR_FAILED         = "ACTOR_FAILED"
REPLAN_REQUIRED      = "REPLAN_REQUIRED"


def make_envelope(event_type: str, actor_id: str, payload: dict,
                  task_id: str | None = None,
                  correlation_id: str | None = None) -> dict:
    """Build a Phase-2 envelope. Used by every publisher."""
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
