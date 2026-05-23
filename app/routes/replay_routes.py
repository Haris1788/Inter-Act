"""Replay and actor-inspection endpoints — Phase 4 Stage 6 Track A.

Pure-read, no mutation, no decisions.

GET /bus/replay  — filtered slice of the bus message history.
GET /bus/actors  — per-actor heartbeat summary derived from bus history.

All filters are applied in Python after ``get_bus().history()`` — the bus
history is small at Phase 4 demo scale so no premature XRANGE optimisation
is needed.

``from`` / ``to`` parameters are interpreted as **0-based numeric indices**
into the history list for both InProcessBus and RedisStreamsBus.  Full XID
range slicing (Redis Stream IDs like ``1234567890-0``) is a future enhancement
documented in the endpoint docstring.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from flask import Blueprint, jsonify, request

from app.agents.bus import get_bus
from app.agents.heartbeat import is_presumed_dead, last_seen
from app.agents.negotiation import MessageType

replay_bp = Blueprint("replay", __name__)

# Maximum messages that a single /bus/replay call may return.
_MAX_LIMIT = 10_000
_DEFAULT_LIMIT = 1_000

# Actors are identified by these prefixes; all other senders are excluded from
# the /bus/actors response (e.g. "interpreter").
_ACTOR_PREFIXES = ("arm-", "agv-")

# Threshold used for presumed-dead classification (seconds).
_DEAD_THRESHOLD_S = 15.0


def _bus_mode() -> str:
    """Return the active bus mode string ("inprocess" or "redis")."""
    return os.environ.get("INTERACT_BUS", "inprocess").lower().strip()


@replay_bp.route("/bus/replay", methods=["GET"])
def bus_replay():
    """GET /bus/replay — filtered slice of bus message history.

    Query parameters (all optional):
        from    — integer index (0-based, inclusive) to start the slice.
                  Absent → beginning of history.
        to      — integer index (0-based, inclusive) to end the slice.
                  Absent → end of history.
                  Note: XID slicing for RedisStreamsBus is best-effort via
                  index; full XID range support is a future enhancement.
        task_id — exact match filter on the ``task_id`` field.
        type    — exact MessageType value filter (e.g. ``bid``).
        limit   — maximum number of messages to return.  Default 1000, max 10000.

    Returns:
        {"messages": [...], "count": N, "bus": "inprocess|redis"}
    """
    # ── Parse query params ────────────────────────────────────────────────────
    from_param: Optional[str] = request.args.get("from")
    to_param: Optional[str] = request.args.get("to")
    task_id_filter: Optional[str] = request.args.get("task_id")
    type_filter: Optional[str] = request.args.get("type")

    try:
        limit = int(request.args.get("limit", _DEFAULT_LIMIT))
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400
    limit = max(1, min(limit, _MAX_LIMIT))

    # ── Fetch history ─────────────────────────────────────────────────────────
    history = get_bus().history()

    # ── Range filter (index-based for both bus types) ─────────────────────────
    from_idx: int = 0
    to_idx: int = len(history) - 1

    if from_param is not None:
        try:
            from_idx = int(from_param)
        except ValueError:
            return jsonify({"error": "'from' must be an integer index"}), 400

    if to_param is not None:
        try:
            to_idx = int(to_param)
        except ValueError:
            return jsonify({"error": "'to' must be an integer index"}), 400

    # Clamp to valid range.
    from_idx = max(0, from_idx)
    to_idx = min(to_idx, len(history) - 1)

    if from_idx > to_idx or len(history) == 0:
        messages = []
    else:
        messages = history[from_idx : to_idx + 1]

    # ── task_id filter ────────────────────────────────────────────────────────
    if task_id_filter is not None:
        messages = [m for m in messages if m.task_id == task_id_filter]

    # ── type filter ───────────────────────────────────────────────────────────
    if type_filter is not None:
        # Accept both the enum value string ("bid") and any casing.
        type_filter_lower = type_filter.lower()
        messages = [m for m in messages if m.type.value == type_filter_lower]

    # ── limit ─────────────────────────────────────────────────────────────────
    messages = messages[:limit]

    payload = {
        "messages": [m.to_dict() for m in messages],
        "count": len(messages),
        "bus": _bus_mode(),
    }
    return jsonify(payload), 200


@replay_bp.route("/bus/actors", methods=["GET"])
def bus_actors():
    """GET /bus/actors — per-actor heartbeat summary.

    Iterates ``bus.history()`` once to collect every distinct sender that looks
    like an actor (starts with ``arm-`` or ``agv-``).  For each actor:

        actor_id       — the sender string (e.g. "arm-1").
        last_seen      — float epoch of most-recent AVAILABILITY_UPDATE payload
                         ``ts`` field, or null if no heartbeat present.
        state          — string from most-recent AVAILABILITY_UPDATE payload
                         ``state`` field, or null.
        presumed_dead  — bool; true when last heartbeat is older than
                         ``threshold_s`` seconds or when no heartbeat exists.

    Returns:
        {"actors": [...], "threshold_s": 15.0}
    """
    history = get_bus().history()

    # Collect distinct actor senders in publish order (for deterministic output).
    seen_ids: list[str] = []
    seen_set: set[str] = set()
    for msg in history:
        sender = msg.sender
        if sender not in seen_set and any(
            sender.startswith(pfx) for pfx in _ACTOR_PREFIXES
        ):
            seen_ids.append(sender)
            seen_set.add(sender)

    # For state extraction: find the most-recent AVAILABILITY_UPDATE per actor.
    latest_avail: dict[str, dict] = {}
    for msg in history:
        if msg.type == MessageType.AVAILABILITY_UPDATE and msg.sender in seen_set:
            prev = latest_avail.get(msg.sender)
            if prev is None or msg.ts > prev["ts"]:
                latest_avail[msg.sender] = {"ts": msg.ts, "payload": msg.payload}

    actors = []
    for actor_id in seen_ids:
        ls = last_seen(history, actor_id)
        state_val = None
        if actor_id in latest_avail:
            state_val = latest_avail[actor_id]["payload"].get("state")
        actors.append(
            {
                "actor_id": actor_id,
                "last_seen": ls,
                "state": state_val,
                "presumed_dead": is_presumed_dead(
                    history, actor_id, threshold_s=_DEAD_THRESHOLD_S
                ),
            }
        )

    return jsonify({"actors": actors, "threshold_s": _DEAD_THRESHOLD_S}), 200


@replay_bp.route("/reservations", methods=["GET"])
def reservations():
    """GET /reservations — snapshot of all current cell reservations.

    Pure read; no mutation; no side effects.

    Returns:
        {
            "reservations": [
                {"task_id": str, "actor_id": str, "epoch": int,
                 "cells": [[col, row], ...]},
                ...
            ],
            "ts": float   # server wall-clock (time.time())
        }
    """
    from app.spatial.reservation import get_store
    store = get_store()
    snapshot = store.snapshot_all()
    return jsonify({"reservations": snapshot, "ts": time.time()}), 200
