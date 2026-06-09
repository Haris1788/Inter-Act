"""arm_bus — FDAS Step 3: makes one Arm an autonomous decision-making PEER.

Runs as a background thread inside the arm container (started by arm_server.py
when ARM_AUTONOMY=on). Mirrors the AGV-side `agv_bus` pattern.

Today the arm is a backend-puppeted actuator: dispatch_routes._auto_arm_signals
drives its load/unload. This module makes the arm a real peer on the shared
bus — it hears AGVs approaching its station, decides LOCALLY (its own qwen14b
tactical brain, deterministic fail-safe) whether to engage, and ANNOUNCES that
decision on the bus, so AGV<->Arm coordination is peer-driven and visible.

SAFETY:
- DEFAULT OFF via the ARM_AUTONOMY env flag — dormant until explicitly enabled.
- Strictly ADDITIVE — it does NOT touch load_ready / arm_ready / the existing
  choreography. It only publishes a NEW event type, `arm_decision`, which no
  other component consumes, so it cannot conflict with the proven path.
- The arm here owns the DECISION + ANNOUNCEMENT. Physical actuation handover
  (the arm itself running the animation + setting load_ready, instead of
  _auto_arm_signals) is a later refinement to be done on a verified stack —
  it touches the delicate load-signal flow and must not be done blind.

UNVERIFIED until run on a free GPU. See the FDAS build log.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Optional

import requests

ARM_ID = str(os.environ.get("ARM_ID", "1"))
BACKEND = os.environ.get("SERVER_HOST", "http://backend:5000")
ARM_TYPE = os.environ.get("ARM_TYPE", "pick_place")

# Which station this arm serves. Default by id (arm-1 = input, arm-2 = output,
# matching pipeline.yaml stations.served_by); ARM_STATION env overrides.
_DEFAULT_STATION = "input_arm" if ARM_ID == "1" else "output_arm"
ARM_STATION = os.environ.get("ARM_STATION", "").strip() or _DEFAULT_STATION

AGENT = f"arm-{ARM_ID}"

BUS_POLL_INTERVAL = 1.0
TACTICAL_ENDPOINT = f"{BACKEND}/ai/tactical"
TACTICAL_TIMEOUT = 65.0
# arm_approach = act on it; task_announce = cache package facts to reason over;
# agent_status kept for context.
LISTEN_TYPES = "arm_approach,arm_busy,arm_ready,agent_status,task_announce"

# FDAS Step 3b: the handling scripts the arm can choose from (mirror of the
# backend ARM_ACTION_REGISTRY — scripts/ cannot import app/, so the names are
# duplicated here; the backend is the source of truth that actually executes).
ARM_ACTIONS = ("normal", "gentle", "careful", "expedite", "inspect")


# ── HTTP + bus helpers (mirror of agv_bus) ───────────────────────────────────
def _post(path: str, payload: dict, timeout: float = 8.0) -> Optional[dict]:
    try:
        r = requests.post(f"{BACKEND}{path}", json=payload, timeout=timeout)
        return r.json() if r.status_code < 500 else None
    except Exception as exc:
        print(f"[{AGENT}/bus] POST {path} failed: {exc}", file=sys.stderr)
        return None


def _get(path: str, timeout: float = 6.0) -> Optional[dict]:
    try:
        r = requests.get(f"{BACKEND}{path}", timeout=timeout)
        return r.json() if r.status_code < 500 else None
    except Exception as exc:
        print(f"[{AGENT}/bus] GET {path} failed: {exc}", file=sys.stderr)
        return None


def _log(action: str, detail: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [{AGENT}/bus] {action}: {detail}",
          flush=True)
    _post("/log", {"source": f"{AGENT}/bus",
                   "message": f"{action}: {detail}"}, timeout=3.0)


def publish(type_: str, payload: dict, task_id: Optional[str] = None):
    body = {"type": type_, "from": AGENT, "task_id": task_id, "payload": payload}
    res = _post("/bus/publish", body)
    return (res or {}).get("seq")


def poll_events(since: int, types: str = LISTEN_TYPES, limit: int = 100):
    res = _get(f"/bus/events?since={since}&types={types}&limit={limit}")
    if not res:
        return [], since
    head = int(res.get("head", since))
    if head < since:  # bus reset (backend restarted) — re-sync from 0
        res = _get(f"/bus/events?since=0&types={types}&limit={limit}")
        if not res:
            return [], 0
    return res.get("events", []), int(res.get("next_since", since))


# ── deterministic fail-safe action choice (mirror of backend) ────────────────
def _deterministic_action(ctx: dict) -> str:
    """Pure-function fallback so the arm always names a valid script even with
    no GPU. Mirrors the backend's _deterministic_arm_action."""
    h = str(ctx.get("handling") or "").lower()
    if any(k in h for k in ("fragile", "delicate", "brittle", "glass")):
        return "gentle"
    if any(k in h for k in ("hazard", "danger", "toxic", "chemical", "explosive", "inspect")):
        return "inspect"
    if any(k in h for k in ("priority", "urgent", "rush", "express", "asap", "expedite")):
        return "expedite"
    try:
        w = float(ctx.get("weight_kg")) if ctx.get("weight_kg") is not None else None
    except (TypeError, ValueError):
        w = None
    if (w is not None and w >= 60.0) or "heavy" in h or "bulky" in h:
        return "careful"
    return "normal"


# ── arm's own tactical decision (qwen14b; deterministic fail-safe) ────────────
def _decide_action(agv_id: str, action: str, ctx: dict) -> tuple[str, str]:
    """Ask the arm's own qwen14b which handling script fits this package. The
    arm reasons over the package facts (label/weight/handling). Fail-safe = the
    deterministic choice, so the arm always names a valid script and never
    stalls the line on AI being unreachable."""
    suggestion = _deterministic_action(ctx)
    system = (
        f"You are the tactical brain of {AGENT}, the {ARM_STATION} robotic arm "
        f"in a decentralized warehouse. An AGV docked to be {action}ed. Choose "
        f"the BEST handling script for THIS package. Reply with ONLY JSON: "
        '{"script":"<name>","rationale":"<one short reason>"} where <name> is '
        "one of: normal, gentle (fragile), careful (heavy), expedite "
        "(urgent/priority), inspect (hazardous/uncertain)."
    )
    user = (f"AGV {agv_id} at {ARM_STATION} for {action}. Package "
            f"label={ctx.get('label')!r}, weight_kg={ctx.get('weight_kg')}, "
            f"handling_hint={ctx.get('handling')!r}. Safe default '{suggestion}'. "
            f"Best script?")
    try:
        r = requests.post(TACTICAL_ENDPOINT,
                          json={"system": system, "user": user},
                          timeout=TACTICAL_TIMEOUT)
        if r.status_code == 409:
            return suggestion, "planner busy -> deterministic"
        body = r.json()
        parsed = body.get("parsed") if isinstance(body.get("parsed"), dict) else {}
        if not parsed:
            raw = body.get("result") or body.get("raw") or ""
            if isinstance(raw, str) and "{" in raw and "}" in raw:
                try:
                    parsed = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
                except Exception:
                    parsed = {}
        choice = str(parsed.get("script") or parsed.get("value") or "").strip().lower()
        rationale = str(parsed.get("rationale", "")).strip()
        if choice in ARM_ACTIONS:
            return choice, (rationale or f"chose {choice}")
        return suggestion, (rationale or "AI reply invalid -> deterministic")
    except Exception as exc:
        return suggestion, f"AI unavailable ({exc}) -> deterministic"


# ── ArmBusListener ───────────────────────────────────────────────────────────
class ArmBusListener:
    """Decentralized bus participant for one arm. One per arm container."""

    def __init__(self) -> None:
        self._since = 0
        self._stop = threading.Event()
        self._handled: set[str] = set()  # dedupe keys already decided
        # task_id -> {label, weight_kg, handling}, cached from task_announce so
        # the arm has the package facts to reason over when the AGV arrives.
        self._pkg_ctx: dict = {}

    def start(self) -> None:
        head = _get("/bus/events?limit=1") or {}
        self._since = int(head.get("head", 0))
        threading.Thread(target=self._loop, name=f"{AGENT}-bus",
                         daemon=True).start()
        _log("started",
             f"peer brain online for {ARM_STATION} (additive, decision-only)")

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                events, self._since = poll_events(self._since)
                for ev in events:
                    self._dispatch(ev)
            except Exception as exc:
                print(f"[{AGENT}/bus] loop error: {exc}", file=sys.stderr)
            time.sleep(BUS_POLL_INTERVAL)

    def _dispatch(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "task_announce":
            # Cache the package facts so the arm can reason when the AGV docks.
            task_id = ev.get("task_id")
            p = ev.get("payload") or {}
            if task_id:
                self._pkg_ctx[task_id] = {
                    "label": p.get("label"),
                    "weight_kg": p.get("weight_kg"),
                    "handling": p.get("handling"),
                }
            return
        if t != "arm_approach":
            return
        payload = ev.get("payload") or {}
        if payload.get("arm_station") != ARM_STATION:
            return  # not my station — ignore
        # eta_s > 0 = AGV still inbound; decide only on the docked signal (eta 0).
        try:
            if float(payload.get("eta_s", 0) or 0) > 0:
                return
        except (TypeError, ValueError):
            pass
        agv_id = payload.get("agv_id") or ev.get("from")
        task_id = ev.get("task_id")
        action = "load" if ARM_STATION == "input_arm" else "unload"
        key = f"{agv_id}:{task_id}:{action}"
        if key in self._handled:
            return
        self._handled.add(key)
        threading.Thread(target=self._decide_and_announce,
                         args=(agv_id, action, task_id),
                         name=f"{AGENT}-decide", daemon=True).start()

    def _decide_and_announce(self, agv_id: str, action: str,
                             task_id: Optional[str]) -> None:
        """The arm container's own brain CHOOSES a handling script for the
        package and announces it as `arm_intent` (the peer's view, before the
        backend renders the executed `arm_decision`). Keeps each arm a real
        AI peer with its own qwen14b, in the same action vocabulary the backend
        executes — so intent and execution line up."""
        ctx = self._pkg_ctx.get(task_id) or {}
        script, rationale = _decide_action(agv_id, action, ctx)
        publish("arm_intent", {
            "arm_id": ARM_ID,
            "station": ARM_STATION,
            "agv_id": agv_id,
            "action": action,
            "script": script,
            "rationale": rationale,
        }, task_id=task_id)
        _log("intent",
             f"{action} {agv_id}: would run '{script}' — {rationale}")
