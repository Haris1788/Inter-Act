"""Negotiate routes — decentralized negotiation entry point (Phase 3 Task 2).

POST /negotiate  {"prompt": "...[, "sync": true]"}
    Interprets a natural-language prompt into a TaskSpec, derives structural
    slots for the transport task, and publishes a TASK_ANNOUNCE on the
    NegotiationBus.  Returns 202 immediately (or 200 when sync=true).

The route ONLY interprets intent and publishes the announce.  It does NOT
assign actors, create plans, call CentralBrain / plan_executor, or wait for
consensus.  Everything after the publish is owned by the decentralized runtime
(ArmAgent / AGVAgent instances subscribed to the bus).

Source / dest zone resolution
------------------------------
TaskSpec (from interpreter.py) exposes intent, package_count, agv_ids, and
description — it does NOT carry source/dest station zone names.  Rather than
inventing a fake planner, we read the canonical station zones directly from
pipeline_config.get_stations(): the input station ("input_arm") is always the
source zone and the output station ("output_arm") is always the dest zone for
a transport task.  This matches the structural fact modelled by derive_slots()
and the zone names used throughout the negotiation integration tests.  A future
phase can refine zone selection once the TaskSpec carries station fields.
"""

from __future__ import annotations

import threading
import time
import uuid

from flask import Blueprint, jsonify, request

from app import socketio
from app.agents.bus import get_bus
from app.agents.negotiation import (
    BID_WINDOW_S,
    CONSENSUS_WINDOW_S,
    Message,
    MessageType,
    derive_slots,
    derive_slots_by_actor,
)

negotiate_bp = Blueprint("negotiate", __name__)

# Station zone names as they exist in pipeline_config and the negotiation tests.
_SOURCE_ZONE = "input_arm"
_DEST_ZONE = "output_arm"


def _emit_log(level: str, message: str, source: str = "negotiator") -> None:
    socketio.emit(
        "log_message",
        {
            "timestamp": time.strftime("%H:%M:%S"),
            "source": source,
            "level": level,
            "message": message,
        },
    )


@negotiate_bp.route("/negotiate", methods=["POST"])
def negotiate_prompt():
    """POST /negotiate {"prompt": "...[, "sync": true]"}

    Interprets the prompt, derives structural slots, and publishes a
    TASK_ANNOUNCE on the negotiation bus.  Returns 202 unless sync=true.
    """
    body = request.get_json(force=True, silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"status": "failed", "error": "prompt is required"}), 400

    sync = bool(body.get("sync", False))

    def _run() -> dict:
        _emit_log(
            "info",
            f"Negotiate: {prompt[:80]}{'…' if len(prompt) > 80 else ''}",
        )

        # ── Step 1: interpret prompt → TaskSpec ──────────────────────────────
        from app.agents.interpreter import interpret as _interpret

        result = _interpret(prompt)
        if result.get("status") != "success":
            err = result.get("error", "interpreter failed")
            _emit_log("error", f"Interpret failed: {err}")
            return result

        spec = result["task_spec"]

        # ── Step 2: resolve source/dest zones ────────────────────────────────
        # TaskSpec does not carry station zone names (see module docstring).
        # We derive them from pipeline_config.get_stations() — the canonical
        # source is "input_arm" and the destination is "output_arm".
        try:
            from app.models import pipeline_config

            stations = pipeline_config.get_stations()
            source_zone = next(
                (k for k in stations if "input" in k.lower()),
                _SOURCE_ZONE,
            )
            dest_zone = next(
                (k for k in stations if "output" in k.lower()),
                _DEST_ZONE,
            )
        except Exception:
            # Fallback to the well-known defaults if config is unavailable.
            source_zone = _SOURCE_ZONE
            dest_zone = _DEST_ZONE

        # ── Step 3: derive structural slots ──────────────────────────────────
        # When a topology block is present, use spatial derive_slots_by_actor
        # which populates target_pos / from_pos / to_pos for distance-aware
        # bidding. Otherwise keep the legacy zone-string path.
        try:
            from app.models import pipeline_config as _pc
            topology = _pc.get_topology()
        except Exception:
            topology = None

        if topology is not None:
            # Pick first arm as source and second arm as dest (default for Stage 4).
            arm_ids = [a.id for a in topology.arms()]
            source_arm_id = arm_ids[0] if len(arm_ids) >= 1 else None
            dest_arm_id = arm_ids[1] if len(arm_ids) >= 2 else None
            if source_arm_id and dest_arm_id:
                try:
                    slots = derive_slots_by_actor(source_arm_id, dest_arm_id, topology)
                except Exception:
                    slots = derive_slots(source_zone, dest_zone)
            else:
                slots = derive_slots(source_zone, dest_zone)
        else:
            slots = derive_slots(source_zone, dest_zone)

        # Convert Slot dataclasses to dicts matching the shape expected by agents
        # (see _announce() in tests/test_negotiation_integration.py):
        # arm slots  → {slot_id, kind, zone[, target_pos, served_by]}
        # agv slot   → {slot_id, kind, from_zone, to_zone[, from_pos, to_pos]}
        slot_dicts = []
        for s in slots:
            d: dict = {"slot_id": s.slot_id, "kind": s.kind}
            if s.zone is not None:
                d["zone"] = s.zone
            if s.from_zone is not None:
                d["from_zone"] = s.from_zone
            if s.to_zone is not None:
                d["to_zone"] = s.to_zone
            if s.target_pos is not None:
                d["target_pos"] = list(s.target_pos)
            if s.from_pos is not None:
                d["from_pos"] = list(s.from_pos)
            if s.to_pos is not None:
                d["to_pos"] = list(s.to_pos)
            if s.served_by is not None:
                d["served_by"] = s.served_by
            slot_dicts.append(d)

        # ── Step 4: build and publish TASK_ANNOUNCE ───────────────────────────
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        constraint = spec.get("constraints", [""])[0] if spec.get("constraints") else ""
        now = time.time()

        announce = Message(
            type=MessageType.TASK_ANNOUNCE,
            task_id=task_id,
            negotiation_round=0,
            sender="interpreter",
            payload={
                "source_zone": source_zone,
                "dest_zone": dest_zone,
                "constraint": constraint,
                "slots": slot_dicts,
                "announce_ts": now,
                "bid_deadline_ts": now + BID_WINDOW_S,
                "consensus_deadline_ts": now + BID_WINDOW_S + CONSENSUS_WINDOW_S,
            },
        )

        get_bus().publish(announce)

        # Phase 6 — emit PAYLOAD_SPAWN so frontend renders a visible object
        # at the source zone immediately on task start. Pos is taken from the
        # AGV `from_pos` of the transport slot (i.e. the arm-1 dock boundary
        # facing the AGV) so the spawn is in a sensible canvas location even
        # if frontend has no zone-position data. Frontend may override with
        # exact zone center when it knows it.
        try:
            transport = next((s for s in slot_dicts if s.get("kind") == "agv"), None)
            spawn_pos = transport.get("from_pos") if transport else None
            spawn_zone = transport.get("from_zone") if transport else source_zone
            get_bus().publish(
                Message(
                    type=MessageType.PAYLOAD_SPAWN,
                    task_id=task_id,
                    negotiation_round=0,
                    sender="coordinator",
                    payload={
                        "obj_id": f"pkg-{task_id}",
                        "zone_id": spawn_zone,
                        "pos": spawn_pos,
                    },
                )
            )
        except Exception:
            # Spawn is visual-only; never fail negotiation start because of it.
            pass

        _emit_log(
            "info",
            (
                f"TASK_ANNOUNCE published: task_id={task_id}  "
                f"{source_zone} → {dest_zone}  constraint={constraint!r}"
            ),
        )

        return {
            "status": "success",
            "task_id": task_id,
            "source_zone": source_zone,
            "dest_zone": dest_zone,
            "slots": slot_dicts,
        }

    if sync:
        result = _run()
        code = 200 if result.get("status") == "success" else 500
        return jsonify(result), code

    threading.Thread(target=_run, daemon=True).start()
    return (
        jsonify(
            {
                "status": "accepted",
                "message": "negotiation starting in background — watch bus_message SocketIO event",
            }
        ),
        202,
    )
