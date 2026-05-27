"""
bus_routes — cross-container message bus (contract-net / decentralized).

Envelope (brief spec):

    {
      "seq": <int>,
      "ts":  "<iso8601-ish>",
      "from": "agv-2" | "arm-1" | "master" | "observer",
      "type": "task_announce" | "task_orphan" | "bid" | "claim"
            | "intent" | "arm_approach" | "arm_ready" | "arm_busy"
            | "yield" | "help_request" | "ack" | "complete"
            | "agent_status",
      "task_id": "<pkg id or null>",
      "payload": { ... }   # type-specific
    }

Endpoints:

    POST /bus/publish     {type, from, task_id?, payload?}
                          → ring + SocketIO mirror + in-process subscribers
                          → idempotent for task_orphan by task_id

    GET  /bus/events      ?since=<seq>[&types=<csv>][&limit=<n>]
                          → events strictly after `since`, oldest first

    GET  /bus/recent      ?n=<n>     debug helper

Browser listens for SocketIO event name "bus" (also broadcast to room
"coord-bus" — agents that speak SocketIO can join that room directly).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Iterable, List, Optional

from flask import Blueprint, jsonify, request

bus_bp = Blueprint("bus", __name__)

# ── Ring buffer (thread-safe) ────────────────────────────────────────────────
BUS_RING_SIZE = 1000            # brief: last 1000 events
BUS_FETCH_LIMIT = 200

_lock = threading.Lock()
_ring: deque = deque(maxlen=BUS_RING_SIZE)
_seq: int = 0

# Per-task_id idempotency for `task_orphan`. First successful publish wins;
# later attempts return {"status": "duplicate"} without appending. This is
# the "first healthy AGV to notice publishes the orphan" rule.
_orphaned_task_ids: set[str] = set()


def _now_iso() -> str:
    """ISO 8601 timestamp, local clock — readable in log panel."""
    lt = time.localtime()
    ms = int((time.time() % 1) * 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", lt) + f".{ms:03d}"


def _make_event(type_: str, from_: Optional[str],
                task_id: Optional[str], payload: dict) -> dict:
    global _seq
    _seq += 1
    return {
        "seq": _seq,
        "ts": _now_iso(),
        "from": from_,
        "type": type_,
        "task_id": task_id,
        "payload": payload or {},
    }


def _fetch_events(since: int, types: Optional[Iterable[str]],
                  limit: int) -> List[dict]:
    types_set = set(types) if types else None
    out: List[dict] = []
    with _lock:
        for ev in _ring:
            if ev["seq"] <= since:
                continue
            if types_set is not None and ev["type"] not in types_set:
                continue
            out.append(ev)
            if len(out) >= limit:
                break
    return out


def publish_local(
    type_: str,
    from_: Optional[str] = None,
    task_id: Optional[str] = None,
    payload: Optional[dict] = None,
) -> Optional[dict]:
    """Publish an event from inside the backend process.

    Returns the published event, or None if duplicate (only meaningful for
    task_orphan idempotency).

    Mirrors to:
      • SocketIO event name `bus`, room `coord-bus` and default broadcast
      • the existing in-process MessageBus (so subscribers in central_brain
        etc. keep working — used for the COORDINATION_MODE=centralized path)
    """
    with _lock:
        # Lifecycle-aware idempotency:
        #  • task_orphan is dedup'd by task_id within a lifecycle.
        #  • A new agent_status with status=running resets that dedup, so the
        #    NEXT fault on the same package id can be orphaned again.
        if type_ == "task_orphan" and task_id:
            if task_id in _orphaned_task_ids:
                return None
            _orphaned_task_ids.add(task_id)
        elif type_ == "agent_status" and task_id:
            status = ((payload or {}).get("status") or "").lower()
            if status == "running":
                _orphaned_task_ids.discard(task_id)
        event = _make_event(type_, from_, task_id, payload or {})
        _ring.append(event)

    # mirror to SocketIO (best-effort; lets the browser log panel render)
    try:
        from app import socketio
        # coord-bus room for explicit subscribers
        socketio.emit("bus", event, room="coord-bus")
        # default broadcast so existing UI sees it without joining the room
        socketio.emit("bus", event)
    except Exception:
        pass

    # in-process MessageBus — keeps centralized path subscribers alive
    try:
        from app.agents.message_bus import get_bus
        get_bus().publish(type_, event)
    except Exception:
        pass

    return event


@bus_bp.route("/bus/publish", methods=["POST"])
def http_publish():
    """Container-side agents post events here.

    Body: {"type": "...", "from": "agv-2", "task_id": "pkg-2",
           "payload": {...}}
    Returns:
        {"status": "ok",        "seq": N}
        {"status": "duplicate", "seq": null}     (task_orphan re-publish)
        {"status": "failed",    "error": "..."}  (HTTP 400)
    """
    body = request.get_json(silent=True) or {}
    # Accept both the new field names (brief) and the previous in-flight
    # names (topic/agent_id/data) so a half-deployed mix still works.
    type_ = (body.get("type") or body.get("topic") or "").strip()
    from_ = (body.get("from") or body.get("agent_id") or "").strip() or None
    task_id = body.get("task_id")
    payload = body.get("payload")
    if payload is None:
        payload = body.get("data")
    if not isinstance(payload, dict):
        payload = {}

    if not type_:
        return jsonify({"status": "failed", "error": "type is required"}), 400

    event = publish_local(type_, from_, task_id, payload)
    if event is None:
        return jsonify({"status": "duplicate", "seq": None}), 200
    return jsonify({"status": "ok", "seq": event["seq"]})


@bus_bp.route("/bus/events", methods=["GET"])
def http_events():
    """Container-side listeners poll this.

    Query: since=<seq>[&types=<csv>][&topics=<csv>][&limit=<n>]
    Returns: {"events": [...], "next_since": <last seq>, "head": <ring head>}
    """
    try:
        since = int(request.args.get("since", "0"))
    except ValueError:
        since = 0

    # Accept both `types` (brief) and `topics` (previous in-flight name)
    types_csv = (request.args.get("types") or request.args.get("topics") or "").strip()
    types = [t.strip() for t in types_csv.split(",") if t.strip()] if types_csv else None

    try:
        limit = max(1, min(int(request.args.get("limit", str(BUS_FETCH_LIMIT))), BUS_FETCH_LIMIT))
    except ValueError:
        limit = BUS_FETCH_LIMIT

    events = _fetch_events(since, types, limit)
    next_since = events[-1]["seq"] if events else since
    with _lock:
        head = _seq
    return jsonify({"events": events, "next_since": next_since, "head": head})


@bus_bp.route("/bus/recent", methods=["GET"])
def http_recent():
    """Debug: dump the last N events (any type)."""
    try:
        n = max(1, min(int(request.args.get("n", "50")), BUS_FETCH_LIMIT))
    except ValueError:
        n = 50
    with _lock:
        tail = list(_ring)[-n:]
        head = _seq
    return jsonify({"events": tail, "head": head})


# ── SocketIO room join (lets a browser or container join coord-bus) ──────────
# This is registered alongside the blueprint, but it lives in app.sockets.events
# to keep the SocketIO event handlers all in one module.
# (See app/sockets/events.py — `join_bus` handler appended there.)
