"""
agv_bus — decentralized contract-net listener for one AGV container.

This is the AGV-side of the bus. It runs as background threads inside each
AGV container (started by agv_server.py at boot). Three responsibilities:

  1. **Heartbeat watch** — polls /coord/snapshot every ~1s. When a peer AGV's
     status goes to {faulted, stopped, dead} AND we have a last-known
     current task for that peer, publish `task_orphan` (idempotent by
     task_id at the bus). First-AGV-to-notice becomes the announcer.

  2. **Bus loop** — polls /bus/events every 250 ms. Dispatches incoming
     events. On task_orphan (or task_announce, Phase 2): if idle + healthy,
     consult qwen14b for a bid score; if positive, publish `bid`.

  3. **Announcer** — when this AGV published the task_orphan, after the
     BID_WINDOW (2.5 s) collects all bids on the bus and publishes `claim`
     naming the winner. Highest score wins; tie-break = lowest agent id.

A `claim` naming this AGV → calls the `take_task` hook on the server.

All decisions are local; no central brain involvement.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Callable, Optional

import requests

AGV_ID = os.environ.get("AGV_ID", "agv-1")
BACKEND = os.environ.get("SERVER_HOST", "http://backend:5000")

# ── US 34: this AGV's own capabilities (mirror of agv_server.CAPABILITIES) ───
# Re-read here because scripts/ cannot import from app/; agv_server and agv_bus
# both parse the same env vars independently. Source of truth is pipeline.yaml.
_COORDINATION_MODE = os.environ.get("COORDINATION_MODE", "centralized").lower()

# FDAS Step 2 (master-gone): when ON, peers resolve INITIAL task_announce
# themselves — collect bids, elect the lowest-id bidder as the single resolver,
# publish the claim. Default OFF — the US 32/34 backend-resolver path is
# unchanged.
_FDAS_PEER_RESOLVE = (
    os.environ.get("FDAS_PEER_RESOLVE", "off").lower() in ("on", "1", "true", "yes")
)


def _parse_labels_csv(raw):
    if not raw:
        return ["any"]
    parts = [p.strip().lower() for p in str(raw).split(",") if p.strip()]
    return parts or ["any"]


def _coerce_float(raw, fallback):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


MY_CAPABILITIES = {
    "agv_id": AGV_ID,
    "kind": os.environ.get("AGV_KIND", "shuttle"),
    "weight_max_kg": _coerce_float(os.environ.get("AGV_WEIGHT_MAX_KG"), 50.0),
    "allowed_labels": _parse_labels_csv(os.environ.get("AGV_ALLOWED_LABELS")),
    "speed_factor": _coerce_float(os.environ.get("AGV_SPEED_FACTOR"), 1.0),
}


def _mode_is_smart() -> bool:
    return _COORDINATION_MODE == "smart"


def _accepts_label(allowed_labels, label):
    """Does this AGV's allowed_labels accept the given package label?"""
    if not label:
        return True
    labels = [str(l).lower() for l in (allowed_labels or [])]
    if "any" in labels:
        return True
    return str(label).lower() in labels

# Polling cadence — brief specified 250ms but that overloads single-threaded
# Flask with 3 AGVs × 2 threads polling. 1s is responsive enough; fault
# detection deadline (3s) is still satisfied easily.
BUS_POLL_INTERVAL = 1.0
SNAPSHOT_POLL_INTERVAL = 2.0  # heartbeat watch — brief: > 3 s missing → faulted

# A peer is considered FAULTED when no agent_status from them for this long.
# Brief said "> 3 s"; with 1s bus poll + 1s loop jitter, 8s is a safe floor.
PEER_STALE_S = 8.0

# Announcer
# Brief specified 2.5s. qwen14b on a shared TUC GPU responds in ~8-15s, so
# 2.5s closes before bids arrive. 20s leaves room for slow GPU; protocol
# semantics are unchanged.
BID_WINDOW_S = 20.0

# Heartbeat publish interval (only while task_status == running). Peers use
# this to know we're alive. Must be < PEER_STALE_S - some slack.
HEARTBEAT_INTERVAL_S = 3.0

# qwen14b call
TACTICAL_ENDPOINT = f"{BACKEND}/ai/tactical"
TACTICAL_TIMEOUT = 65.0

# Event topics we care about (HTTP filter)
LISTEN_TYPES = "task_announce,task_orphan,bid,claim,agent_status,arm_approach,arm_ready,arm_busy,intent,yield,help_request"

# Statuses that mean "this AGV is out"
DEAD_STATUSES = {"faulted", "stopped", "dead"}


# ── HTTP helpers ─────────────────────────────────────────────────────────────
def _post(path: str, payload: dict, timeout: float = 8.0) -> Optional[dict]:
    try:
        r = requests.post(f"{BACKEND}{path}", json=payload, timeout=timeout)
        return r.json() if r.status_code < 500 else None
    except Exception as exc:
        print(f"[{AGV_ID}/bus] POST {path} failed: {exc}", file=sys.stderr)
        return None


def _get(path: str, timeout: float = 6.0) -> Optional[dict]:
    try:
        r = requests.get(f"{BACKEND}{path}", timeout=timeout)
        return r.json() if r.status_code < 500 else None
    except Exception as exc:
        print(f"[{AGV_ID}/bus] GET {path} failed: {exc}", file=sys.stderr)
        return None


def _log_bus(action: str, detail: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] [{AGV_ID}/bus] {action}: {detail}"
    print(line, flush=True)
    _post("/log", {"source": f"{AGV_ID}/bus",
                   "message": f"{action}: {detail}"}, timeout=3.0)


# ── Bus client API (brief envelope) ──────────────────────────────────────────
def publish(type_: str, payload: dict, task_id: Optional[str] = None) -> Optional[int]:
    """Post an event to the bus. Returns the seq number, or None if duplicate
    (only task_orphan dedupes server-side)."""
    body = {"type": type_, "from": AGV_ID, "task_id": task_id, "payload": payload}
    res = _post("/bus/publish", body)
    if not res:
        return None
    if res.get("status") == "duplicate":
        return None
    if res.get("status") == "ok":
        return res.get("seq")
    return None


def poll_events(since: int, types: str = LISTEN_TYPES,
                limit: int = 100) -> tuple[list, int, bool]:
    """Returns (events, next_since, did_reset). `did_reset` is True when the
    backend ring re-based below our cursor (a restart) and we re-polled from 0,
    so the caller can drop stale per-task state before processing the new ring."""
    res = _get(f"/bus/events?since={since}&types={types}&limit={limit}")
    if not res:
        return [], since, False
    # Bus-reset detection: if the backend restarted, its ring `head` is far
    # below our cursor. Re-poll from 0 so we don't get stuck waiting for a
    # seq the new bus will never reach.
    reset = False
    head = int(res.get("head", since))
    if head < since:
        reset = True
        res = _get(f"/bus/events?since=0&types={types}&limit={limit}")
        if not res:
            return [], 0, True
    return res.get("events", []), int(res.get("next_since", since)), reset


def publish_agent_status(status: str, task_id: Optional[str] = None,
                         assignment: Optional[dict] = None) -> None:
    """Heartbeat / state-change announcement so peers know what task we're on."""
    publish("agent_status", {
        "status": status,
        "current_task": task_id,
        "assignment": assignment or {},
    }, task_id=task_id)


def publish_task_orphan(task_id: str, package_id: str, reason: str,
                        assignment: Optional[dict] = None) -> Optional[int]:
    """Self-report fault. Called from agv_brain._script_fault. The bus dedupes
    by task_id so concurrent self-report + peer-detection both safely run."""
    return publish("task_orphan", {
        "package_id": package_id,
        "reason": reason,
        "assignment": assignment or {},
        "original_agv": AGV_ID,
    }, task_id=task_id)


# ── Phase 3: peer signals during execution ───────────────────────────────────
def publish_intent(task_id: Optional[str], action: str, detail: str = "",
                   path_cells: Optional[list] = None) -> None:
    """Announce what we're about to do — peers see this ≥1 cell before the
    action so they can yield if there's a conflict.

    US 34.1: path_cells (optional) is the computed cell sequence the AGV will
    walk for this leg. Carried verbatim into the payload so the frontend can
    render the polyline on the canvas. Empty/omitted on legs that don't move
    (acquire_lane, etc.).
    """
    payload = {"action": action, "detail": detail, "agv_id": AGV_ID}
    if path_cells:
        # Coerce to plain lists of two ints — bus payloads are JSON.
        try:
            payload["path_cells"] = [[int(c[0]), int(c[1])] for c in path_cells
                                      if c and len(c) >= 2]
        except (TypeError, ValueError, IndexError):
            pass
    publish("intent", payload, task_id=task_id)


def publish_arm_approach(task_id: Optional[str], arm_station: str,
                          eta_s: float = 0.0) -> None:
    """Announce approach to an arm station so the arm and other AGVs can react.
    `arm_station` is 'input_arm' or 'output_arm'."""
    publish("arm_approach", {"arm_station": arm_station, "eta_s": eta_s,
                              "agv_id": AGV_ID}, task_id=task_id)


def publish_yield(task_id: Optional[str], reason: str,
                  waiting_for: str = "") -> None:
    """Tell peers we're holding because of a conflict (lane/station busy)."""
    publish("yield", {"reason": reason, "waiting_for": waiting_for,
                      "agv_id": AGV_ID}, task_id=task_id)


def publish_help_request(task_id: Optional[str], reason: str,
                          blocked_at: str = "") -> None:
    """Ask the swarm for assistance (e.g., stuck after retries)."""
    publish("help_request", {"reason": reason, "blocked_at": blocked_at,
                              "agv_id": AGV_ID}, task_id=task_id)


# ── US 34: deterministic composite score helpers ─────────────────────────────
def _manhattan(a, b):
    """Manhattan distance between two grid cells; safe on None / short lists."""
    if not a or not b or len(a) < 2 or len(b) < 2:
        return 0
    return abs(int(a[0]) - int(b[0])) + abs(int(a[1]) - int(b[1]))


def _compute_deterministic_score(
    my_cell, package_origin, package_label, current_load_kg, meta,
    package_weight_kg=None,
):
    """US 34: composite bid score from real warehouse facts.

        score = w_distance   * (1 - dist / diameter)
              + w_capability  * capability_match
              + w_load        * (1 - current_load_fraction)
              [+ w_best_fit   * (package_weight / my_weight_max)]   # FDAS Step 5

    FDAS Step 5 (overflow / best-fit): when the package weight is KNOWN, an
    extra best-fit term rewards the AGV whose capacity is closest to the
    package weight (best_fit = pw / weight_max → 1.0 = perfect fit, small =
    lots of wasted capacity). This makes a heavy-capacity AGV score lower on a
    light job than a tight-fit AGV, so the heavy AGV only wins it when the
    lighter AGVs are busy (overflow). When the weight is UNKNOWN the term is
    omitted and the score is byte-for-byte the US 34 result (safe / additive).

    Returns (score_0_100, breakdown_dict).
    """
    grid = (meta or {}).get("grid") or {}
    diameter = max(1, int(grid.get("diameter", 22)))

    dist = _manhattan(my_cell, package_origin)
    dist_norm = max(0.0, 1.0 - (dist / diameter))

    cap_match = 1.0 if _accepts_label(MY_CAPABILITIES["allowed_labels"],
                                       package_label) else 0.0

    wmax = max(0.001, MY_CAPABILITIES["weight_max_kg"])
    load_frac = min(1.0, max(0.0, float(current_load_kg) / wmax))
    load_norm = 1.0 - load_frac

    # Best-fit term — only computed when a real package weight is supplied.
    best_fit_norm = None
    if package_weight_kg is not None:
        try:
            pw = float(package_weight_kg)
        except (TypeError, ValueError):
            pw = None
        if pw is not None and pw > 0:
            best_fit_norm = min(1.0, max(0.0, pw / wmax))

    if best_fit_norm is None:
        # US 34 path — unchanged 3-term formula.
        weights = (meta or {}).get("bid_weights") or {
            "distance": 0.6, "capability": 0.3, "load": 0.1
        }
        composite = (
            float(weights.get("distance", 0.6)) * dist_norm
            + float(weights.get("capability", 0.3)) * cap_match
            + float(weights.get("load", 0.1)) * load_norm
        )
    else:
        # FDAS path — 4-term formula with best-fit. Weights re-balanced so the
        # best-fit term carries real influence without dominating distance.
        weights = (meta or {}).get("bid_weights_bestfit") or {
            "distance": 0.5, "capability": 0.25, "load": 0.1, "best_fit": 0.15
        }
        composite = (
            float(weights.get("distance", 0.5)) * dist_norm
            + float(weights.get("capability", 0.25)) * cap_match
            + float(weights.get("load", 0.1)) * load_norm
            + float(weights.get("best_fit", 0.15)) * best_fit_norm
        )
    score = round(max(0.0, min(1.0, composite)) * 100.0, 2)
    breakdown = {
        "distance_cells": dist,
        "grid_diameter": diameter,
        "distance_norm": round(dist_norm, 3),
        "capability_match": bool(cap_match),
        "load_norm": round(load_norm, 3),
        "best_fit_norm": round(best_fit_norm, 3) if best_fit_norm is not None else None,
        "weights": dict(weights),
        "composite": score,
    }
    return score, breakdown


def _eta_seconds_from_distance(my_cell, package_origin):
    """Rough ETA in seconds, assuming MOVE_STEP_SLEEP=0.5s per cell + 10s for load."""
    dist = _manhattan(my_cell, package_origin)
    speed = max(0.1, float(MY_CAPABILITIES["speed_factor"]))
    return round(dist * (0.5 / speed) + 10.0, 1)


def _legacy_us32_ask_for_bid(task_id, announce_type, payload, my_state, others):
    """US 32 flat-AI bid path. Kept verbatim as the fallback when smart mode is OFF."""
    package_id = payload.get("package_id", task_id)
    original = payload.get("original_agv", "?")
    reason = payload.get("reason", "")

    if announce_type == "task_orphan":
        situation = (
            f"AGV {original} faulted and dropped task {task_id} "
            f"(package {package_id}). Reason: {reason}. "
            f"Other healthy AGVs may bid to take it over."
        )
    else:
        situation = (
            f"New task announced: {task_id} (package {package_id}). "
            f"All available AGVs may bid for it."
        )

    system = (
        f"You are the tactical brain of AGV {AGV_ID} in a decentralized "
        f"warehouse. Reply with ONLY JSON: "
        f'{{"bid": true|false, "score": <float 0-100>, '
        f'"rationale": "<short reason>", "eta_s": <float seconds estimate>}}. '
        f"Higher score = better fit. Weigh: distance to the package, "
        f"current load, battery, proximity to lanes. If I am already running "
        f"another task, BID FALSE. Do not invent — be honest about my state."
    )
    user = (
        f"{situation}\n"
        f"My state: cell={my_state.get('cell')} "
        f"status={my_state.get('status')} "
        f"carrying={my_state.get('carried_object')}\n"
        f"Other AGVs right now: {json.dumps(others)}"
    )
    return _send_to_tactical(system, user)


def _send_to_tactical(system: str, user: str) -> tuple[bool, float, str, float]:
    """Shared HTTP call to /ai/tactical with the legacy parse logic."""
    try:
        r = requests.post(
            TACTICAL_ENDPOINT,
            json={"system": system, "user": user},
            timeout=TACTICAL_TIMEOUT,
        )
        if r.status_code == 409:
            return False, 0.0, "planner busy", 0.0
        body = r.json()
    except Exception as exc:
        return False, 0.0, f"AI error: {exc}", 0.0

    parsed: dict = {}
    if isinstance(body.get("parsed"), dict) and body["parsed"]:
        parsed = dict(body["parsed"])
    if not parsed:
        raw = body.get("result") or body.get("script") or body.get("raw") or ""
        if isinstance(raw, str):
            txt = raw.strip()
            if txt.startswith("```"):
                txt = txt.split("```", 2)[-1].lstrip("json").strip()
                if txt.endswith("```"):
                    txt = txt[:-3].strip()
            if "{" in txt and "}" in txt:
                start = txt.find("{")
                end = txt.rfind("}") + 1
                try:
                    parsed = json.loads(txt[start:end])
                except json.JSONDecodeError:
                    parsed = {}
    if not parsed:
        for k in ("bid", "score", "rationale", "eta_s"):
            if k in body:
                parsed[k] = body[k]
    if not parsed and body.get("status") == "success":
        parsed = {"bid": True, "score": 50.0,
                  "rationale": "AI unparseable; heuristic accept",
                  "eta_s": 30.0}

    # US 34 judge schema alias: `accept` ↔ `bid`, `adjusted_score` ↔ `score`.
    # Lets the same parser handle US 32 ("bid") and US 34 ("accept") responses.
    if "bid" not in parsed and "accept" in parsed:
        parsed["bid"] = parsed["accept"]
    if "score" not in parsed and "adjusted_score" in parsed:
        parsed["score"] = parsed["adjusted_score"]

    bid = bool(parsed.get("bid", False))
    try:
        score = float(parsed.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    rationale = str(parsed.get("rationale", "")).strip() or ("(no reason)" if bid else "declined")
    try:
        eta_s = float(parsed.get("eta_s", 0.0))
    except (TypeError, ValueError):
        eta_s = 0.0
    return bid, score, rationale, eta_s


# ── qwen14b prompt for bid decision ──────────────────────────────────────────
def _ask_for_bid(task_id: str, announce_type: str, payload: dict,
                 my_state: dict, others: dict) -> tuple[bool, float, str, float]:
    """Consult qwen14b. Returns (should_bid, score, rationale, eta_s).

    US 34 SMART MODE: compute deterministic composite score from real grid
    distance + capability match + load, then call qwen14b as a JUDGE that can
    accept/reject and adjust the score within ±10. The AI does NOT invent the
    score; it only reasons about the deterministic input.

    US 32 FALLBACK: under any non-smart mode, delegate to the unchanged
    legacy flat-AI path.
    """
    # US 32 fallback path — leave intact for the flag-off case.
    if not _mode_is_smart():
        return _legacy_us32_ask_for_bid(task_id, announce_type, payload,
                                         my_state, others)

    # US 34 smart path — deterministic composite score.
    package_id = payload.get("package_id", task_id)
    package_label = payload.get("label") or payload.get("package_label")

    # Pull live meta from /coord/snapshot (cached by the caller via my_state
    # fetch; we re-fetch here because the meta block is small).
    snap = _get("/coord/snapshot") or {}
    meta = snap.get("meta") or {}

    # Package origin: pre-pickup, this is the input arm cell.
    stations = (meta.get("stations") or {})
    package_origin = (stations.get("input_arm") or {}).get("cell") or [1, 1]

    # Quick filter: if my capabilities don't accept this label, decline up
    # front so the master capability filter (Phase 3) doesn't even need to
    # see it. Honest behaviour: "I cannot do this, so I don't bid."
    if not _accepts_label(MY_CAPABILITIES["allowed_labels"], package_label):
        return (False, 0.0,
                f"declined: my allowed_labels={MY_CAPABILITIES['allowed_labels']}"
                f" does not include '{package_label}'",
                0.0)

    # US 34.1: early weight gate. The interpreter parses weight_kg from the
    # prompt (or infers it from the colour band); if the announce carries a
    # number above my carrying capacity, decline up front. Missing/None weight
    # means "unknown" — fall through to bid normally.
    package_weight = payload.get("weight_kg")
    if package_weight is not None:
        try:
            w = float(package_weight)
        except (TypeError, ValueError):
            w = None
        if w is not None and w > float(MY_CAPABILITIES["weight_max_kg"]):
            return (False, 0.0,
                    f"declined: package weight {w} kg > my "
                    f"weight_max_kg={MY_CAPABILITIES['weight_max_kg']}",
                    0.0)

    # Current load — derive from carried_object presence; full weight model is
    # future work (this AGV carries 1 package at a time, so 0 or wmax/2 is fine).
    current_load_kg = (
        MY_CAPABILITIES["weight_max_kg"] / 2.0
        if my_state.get("carried_object") else 0.0
    )

    deterministic_score, breakdown = _compute_deterministic_score(
        my_cell=my_state.get("cell"),
        package_origin=package_origin,
        package_label=package_label,
        current_load_kg=current_load_kg,
        meta=meta,
        package_weight_kg=package_weight,  # FDAS Step 5: enables best-fit term
    )
    eta_s_estimate = _eta_seconds_from_distance(my_state.get("cell"),
                                                 package_origin)

    # Hand the deterministic facts to qwen14b as a JUDGE.
    original = payload.get("original_agv", "?")
    if announce_type == "task_orphan":
        situation = (
            f"AGV {original} faulted and dropped task {task_id} "
            f"(package {package_id}). Reason: {payload.get('reason', '')}. "
            "Surviving AGVs may take it over."
        )
    else:
        situation = f"New task announced: {task_id} (package {package_id})."

    system = (
        "You are the JUDGE for AGV "
        f"{AGV_ID} bidding on a warehouse task. A deterministic composite score "
        "has ALREADY been computed from real grid distance, capability match, "
        "and current load. You may NOT invent the score — you may only ACCEPT "
        "it, or ADJUST it within ±10 points based on context the formula does "
        "not see (e.g. recent fault, planned maintenance, traffic risk). Reply "
        "with ONLY JSON: "
        '{"accept": true|false, "adjusted_score": <float 0-100>, '
        '"rationale": "<one short sentence>"}. '
        "If accept=false, the bid is dropped entirely. Stay close to the "
        "deterministic score — drifting more than ±10 is a protocol violation."
    )
    user = (
        f"{situation}\n"
        f"Deterministic composite score: {deterministic_score} (0-100).\n"
        f"Breakdown: {json.dumps(breakdown)}\n"
        f"My capabilities: {json.dumps(MY_CAPABILITIES)}\n"
        f"My state: cell={my_state.get('cell')} "
        f"status={my_state.get('status')} "
        f"carrying={my_state.get('carried_object')}\n"
        f"Other AGVs right now: {json.dumps(others)}\n"
        f"Estimated ETA seconds: {eta_s_estimate}"
    )

    bid, judge_score, judge_rationale, judge_eta = _send_to_tactical(system, user)

    # Tactical responded with the legacy schema (bid/score/rationale/eta_s).
    # Map "accept" / "adjusted_score" to that shape when present, else fall
    # back to the deterministic score on AI silence.
    final_score = deterministic_score
    final_rationale = (
        f"composite={deterministic_score} (dist={breakdown['distance_cells']}/"
        f"{breakdown['grid_diameter']}, cap={'+' if breakdown['capability_match'] else '-'}, "
        f"load_norm={breakdown['load_norm']})"
    )
    final_eta = eta_s_estimate

    if bid:
        # qwen14b accepted — clamp adjustment within ±10 of the deterministic score.
        if judge_score > 0:
            clamped = max(deterministic_score - 10.0,
                          min(deterministic_score + 10.0, judge_score))
            final_score = round(clamped, 2)
        if judge_rationale and judge_rationale != "(no reason)":
            final_rationale = f"{final_rationale}; judge: {judge_rationale}"
        if judge_eta > 0:
            final_eta = judge_eta
        return True, final_score, final_rationale, final_eta
    else:
        # qwen14b returned bid=false. The deterministic capability filter
        # (colour/weight/distance) already decided this AGV is ELIGIBLE, so the
        # judge is ADVISORY ONLY — it may nudge the score but must NOT veto a
        # capable bid. A veto strands packages and blocks fault recovery (it was
        # observed rejecting a takeover with "a peer faulted, so other AGVs are
        # risky"). Bid on the deterministic score; keep the judge's note in the
        # trail for transparency.
        AI_DOWN_PREFIXES = ("AI error", "planner busy")
        if any(judge_rationale.startswith(p) for p in AI_DOWN_PREFIXES):
            final_rationale = f"{final_rationale} (judge unavailable: {judge_rationale})"
        else:
            final_rationale = (f"{final_rationale} "
                               f"(judge advised reject, overridden: {judge_rationale})")
        return True, final_score, final_rationale, final_eta


# ── BusListener ──────────────────────────────────────────────────────────────
class BusListener:
    """Decentralized contract-net listener. Started once per AGV container."""

    def __init__(self, is_busy_fn: Callable[[], bool],
                 take_task_fn: Callable[[dict], None],
                 current_state_fn: Optional[Callable[[], dict]] = None):
        self._is_busy = is_busy_fn
        self._take_task = take_task_fn
        self._current_state = current_state_fn or (lambda: {"status": "idle"})
        self._since = 0
        self._stop = threading.Event()
        self._last_hb_status: Optional[str] = None
        self._last_hb_task: Optional[str] = None

        # Peer task tracking — agent_status events keep this updated.
        # agent_id -> {"task_id": str|None, "assignment": dict|None, "last_seen": ts}
        self._peer_tasks: dict[str, dict] = {}

        # Tasks I've announced (I sent the task_orphan, so I'm the announcer):
        #   task_id -> {"deadline": ts, "bids": {agent: {score,rationale,eta_s}}, "claimed": bool}
        self._announced: dict[str, dict] = {}

        # Tasks I've bid on (waiting for a claim):
        #   task_id -> {"my_score": float, "my_bid_seq": int|None}
        self._my_bids: dict[str, dict] = {}

        # task_ids I already orphaned (own dedupe; bus also dedupes)
        self._already_orphaned: set[str] = set()

        # FDAS Step 2: INITIAL announces I'm locally resolving (leaderless).
        #   task_id -> {"deadline": ts, "bids": {agent: {...}},
        #               "resolved": bool, "payload": dict}
        self._fdas_tasks: dict[str, dict] = {}

        # Track tasks that have been claimed already (so we ignore stale events)
        self._claimed_tasks: set[str] = set()

        # AGVs that just WON a claim (seen on the bus) but whose "running"
        # heartbeat may not have propagated yet. The resolver treats these as
        # unavailable, so two independent resolvers (e.g. when one AGV declined a
        # heavy package, splitting the resolver role) don't both hand a package
        # to the same AGV. Cleared when the AGV reports done/idle/faulted.
        self._recent_winners: set[str] = set()

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> None:
        # Jump to current bus head — we don't replay backlog
        head_info = _get("/bus/events?limit=1") or {}
        self._since = int(head_info.get("head", 0))
        threading.Thread(target=self._bus_loop, name=f"{AGV_ID}-bus",
                         daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, name=f"{AGV_ID}-hb",
                         daemon=True).start()
        threading.Thread(target=self._publish_heartbeat_loop,
                         name=f"{AGV_ID}-hbpub", daemon=True).start()
        _log_bus("started",
                 f"listening at {BACKEND}/bus/events from seq={self._since}")

    def stop(self) -> None:
        self._stop.set()

    # ── per-task state reset (reused package ids) ─────────────────────────────
    def _reset_task_state(self, task_id: str) -> None:
        """Purge all per-task state for one task_id so a reused package id (a new
        plan announcing the same pkg-N this session) is bid + resolved from
        scratch. Without this, leftover _claimed_tasks/_fdas_tasks/_announced
        entries from the previous plan make us bid but never claim — the resolver
        sees the id as already resolved/claimed and skips it. `_recent_winners`
        and `_peer_tasks` are keyed by AGV id (not task id) and are left alone."""
        self._claimed_tasks.discard(task_id)
        self._my_bids.pop(task_id, None)
        self._fdas_tasks.pop(task_id, None)
        self._announced.pop(task_id, None)
        self._already_orphaned.discard(task_id)

    def _reset_all_task_state(self) -> None:
        """Full wipe of per-task bookkeeping — used after a bus reset (backend
        restart), where every prior task id is stale."""
        self._claimed_tasks.clear()
        self._my_bids.clear()
        self._fdas_tasks.clear()
        self._announced.clear()
        self._already_orphaned.clear()
        self._recent_winners.clear()
        self._peer_tasks.clear()
        _log_bus("bus-reset", "backend ring re-based; cleared stale task state")

    # ── bus poll loop ─────────────────────────────────────────────────────
    def _bus_loop(self) -> None:
        while not self._stop.is_set():
            try:
                events, self._since, did_reset = poll_events(self._since)
                if did_reset:
                    # Backend restarted: its ring is fresh, so old plans' events
                    # are gone. Drop all stale per-task state — otherwise a
                    # reused package id (pkg-1 again next plan) stays in
                    # _claimed_tasks/_fdas_tasks and we bid but never claim.
                    self._reset_all_task_state()
                for ev in events:
                    self._dispatch(ev)
                self._resolve_announcer_deadlines()
                if _FDAS_PEER_RESOLVE:
                    self._resolve_fdas_deadlines()
            except Exception as exc:
                print(f"[{AGV_ID}/bus] loop error: {exc}", file=sys.stderr)
            time.sleep(BUS_POLL_INTERVAL)

    # ── heartbeat watch loop ──────────────────────────────────────────────
    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._scan_for_faults()
            except Exception as exc:
                print(f"[{AGV_ID}/bus] hb error: {exc}", file=sys.stderr)
            time.sleep(SNAPSHOT_POLL_INTERVAL)

    # ── periodic self-heartbeat ──────────────────────────────────────────
    def _publish_heartbeat_loop(self) -> None:
        """Publish agent_status every HEARTBEAT_INTERVAL_S while running.
        Re-publish on status/task transitions for peers' fault detector."""
        while not self._stop.is_set():
            try:
                state = self._current_state() or {}
                status = state.get("status", "idle")
                task_id = state.get("task_id")
                changed = (status != self._last_hb_status
                           or task_id != self._last_hb_task)
                if status == "running" or changed:
                    publish_agent_status(
                        status=status,
                        task_id=task_id,
                        assignment=state.get("assignment") or {},
                    )
                    self._last_hb_status = status
                    self._last_hb_task = task_id
            except Exception as exc:
                print(f"[{AGV_ID}/bus] hb-pub error: {exc}", file=sys.stderr)
            time.sleep(HEARTBEAT_INTERVAL_S)

    # ── dispatcher ────────────────────────────────────────────────────────
    def _dispatch(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "task_orphan":
            self._on_orphan(ev)
        elif t == "task_announce":
            self._on_announce(ev)
        elif t == "bid":
            self._on_bid(ev)
        elif t == "claim":
            self._on_claim(ev)
        elif t == "agent_status":
            self._on_agent_status(ev)
        # arm_* / intent / yield / help_request — silent for Phase 1

    # ── agent_status: update peer-task table ───────────────────────────────
    def _on_agent_status(self, ev: dict) -> None:
        who = ev.get("from")
        if not who or who == AGV_ID:
            return
        payload = ev.get("payload") or {}
        task_id = payload.get("current_task")
        status = (payload.get("status") or "").lower()
        # Clean completion — peer finished or went idle; just forget it. A
        # task_id may still be present ("the task that just ended"); we drop it
        # so a completed task is never mistaken for one still in flight.
        if status in {"done", "idle"}:
            self._peer_tasks.pop(who, None)
            self._recent_winners.discard(who)  # free to win again
            return
        # FAULT mid-task — the peer died/stopped while it still OWNED a task.
        # Orphan that last-known task so a healthy survivor re-bids, THEN forget
        # the peer. This is the fast path (fires within HEARTBEAT_INTERVAL_S of
        # the peer's status flip); the PEER_STALE_S silence sweep in
        # _scan_for_faults stays as the slow fallback for a peer that crashes
        # WITHOUT emitting any terminal status. The peer that detects this (us)
        # becomes the announcer — the faulted peer never announces its own task.
        if status in {"faulted", "stopped", "dead"}:
            prev = self._peer_tasks.pop(who, None)
            self._recent_winners.discard(who)
            prev_task = (prev or {}).get("task_id")
            if prev_task and prev_task not in self._already_orphaned:
                self._already_orphaned.add(prev_task)
                seq = publish_task_orphan(
                    task_id=prev_task,
                    package_id=prev_task,
                    reason=f"{who} reported '{status}' mid-task",
                    assignment={**((prev or {}).get("assignment") or {}),
                                "original_agv": who},
                )
                _log_bus(
                    "fault-status",
                    f"{who} {status} mid-task -> orphan {prev_task} "
                    f"({'announcer' if seq is not None else 'duplicate'})",
                )
            return
        if task_id:
            self._peer_tasks[who] = {
                "task_id": task_id,
                "assignment": payload.get("assignment") or {},
                "last_seen": time.time(),
            }

    # ── task_orphan: bid if able ──────────────────────────────────────────
    def _on_orphan(self, ev: dict) -> None:
        task_id = ev.get("task_id")
        announcer = ev.get("from")
        if not task_id:
            return
        payload = ev.get("payload") or {}
        if announcer == AGV_ID:
            # I am the announcer — set up bid collection
            self._announced.setdefault(task_id, {
                "deadline": time.time() + BID_WINDOW_S,
                "bids": {},
                "claimed": False,
                "payload": payload,
            })
            _log_bus("announce-orphan",
                     f"{task_id} — waiting {BID_WINDOW_S}s for bids")
            return
        # Someone else announced — should I bid?
        if self._is_busy():
            _log_bus("orphan-seen", f"{task_id} (busy — not bidding)")
            return
        if task_id in self._my_bids:
            return  # already bid (or in-flight)
        # Reserve slot then evaluate in a thread (qwen14b is slow on shared GPU).
        self._my_bids[task_id] = {"my_score": None, "my_bid_seq": None}
        threading.Thread(
            target=self._evaluate_and_bid,
            args=(task_id, "task_orphan", payload),
            name=f"{AGV_ID}-bid-{task_id}",
            daemon=True,
        ).start()

    # ── task_announce (Phase 2): bid same as orphan ────────────────────────
    def _on_announce(self, ev: dict) -> None:
        task_id = ev.get("task_id")
        if not task_id or self._is_busy():
            return
        payload = ev.get("payload") or {}
        is_retry = int(payload.get("retry", 0)) > 0
        # A previous generation of this task_id is FINISHED if we claimed it,
        # orphaned it, or resolved it as an FDAS task. That's the signal that a
        # fresh (non-retry) announce for the same id is a REUSED package id from
        # a later plan — not a within-plan redelivery — so we must wipe stale
        # per-task state before bidding again.
        prior_done = (
            task_id in self._claimed_tasks
            or task_id in self._already_orphaned
            or (self._fdas_tasks.get(task_id) or {}).get("resolved")
        )
        if is_retry:
            # Master re-announced this task because the previous winner could
            # not actually take it. Clear ALL stale state (incl. the resolved
            # _fdas_tasks record) so we evaluate fresh — peers that originally
            # lost the bid get another shot AND the FDAS resolver re-runs.
            self._reset_task_state(task_id)
            _log_bus("re-announce-seen",
                     f"{task_id} retry={payload.get('retry')} "
                     f"prev_winner={payload.get('previous_winner')}")
        elif prior_done:
            # Same package id, new plan this session. Without this wipe the bid
            # is published but no claim ever follows (id looks already resolved).
            self._reset_task_state(task_id)
            _log_bus("re-announce-reset",
                     f"{task_id} reused id from an earlier plan — cleared stale state")
        elif task_id in self._my_bids:
            return  # original announce this plan, already processing
        # Reserve the slot so duplicate processing on bus poll bounces.
        self._my_bids[task_id] = {"my_score": None, "my_bid_seq": None}
        # FDAS Step 2: register this announce for leaderless local resolution.
        # Every bidding AGV collects all bids; the lowest-id bidder publishes the
        # claim. No-op when the flag is off (the backend resolves as before).
        if _FDAS_PEER_RESOLVE and task_id not in self._fdas_tasks:
            self._fdas_tasks[task_id] = {
                "deadline": time.time() + BID_WINDOW_S,
                "bids": {},
                "resolved": False,
                "payload": payload,
            }
        # Evaluate the bid in a thread so the bus loop keeps processing other
        # announces while qwen14b crunches (one call is ~10-15s on shared GPU).
        threading.Thread(
            target=self._evaluate_and_bid,
            args=(task_id, "task_announce", payload),
            name=f"{AGV_ID}-bid-{task_id}",
            daemon=True,
        ).start()

    def _evaluate_and_bid(self, task_id: str, announce_type: str,
                          payload: dict) -> None:
        try:
            my_state = _get(f"/agv/state/{AGV_ID}") or {}
            snap = _get("/coord/snapshot") or {}
            others = {a: {"cell": v.get("cell"), "status": v.get("status")}
                      for a, v in (snap.get("agvs") or {}).items()
                      if a != AGV_ID}
            bid, score, rationale, eta_s = _ask_for_bid(
                task_id, announce_type, payload, my_state, others)
            if not bid:
                _log_bus("announce-decline", f"{task_id} — {rationale}")
                self._my_bids.pop(task_id, None)
                return
            _log_bus("announce-bid",
                     f"{task_id} score={score:.1f} — {rationale}")
            seq = publish("bid", {
                "score": score,
                "rationale": rationale,
                "eta_s": eta_s,
            }, task_id=task_id)
            self._my_bids[task_id] = {"my_score": score, "my_bid_seq": seq}
        except Exception as exc:
            _log_bus("bid-error", f"{task_id}: {exc}")
            self._my_bids.pop(task_id, None)

    # ── bid (announcer collects) ──────────────────────────────────────────
    def _on_bid(self, ev: dict) -> None:
        task_id = ev.get("task_id")
        bidder = ev.get("from")
        if not task_id or not bidder:
            return
        try:
            score = float((ev.get("payload") or {}).get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        bid_rec = {
            "score": score,
            "rationale": (ev.get("payload") or {}).get("rationale", ""),
            "eta_s": (ev.get("payload") or {}).get("eta_s", 0),
        }
        # Orphan path: I published the orphan, so I'm its announcer.
        record = self._announced.get(task_id)
        if record and not record.get("claimed"):
            record["bids"][bidder] = bid_rec
            _log_bus("bid-recv", f"{task_id}: {bidder} score={score:.1f}")
        # FDAS Step 2: every bidder collects all bids for tasks it is locally
        # resolving, so the elected resolver sees the full bid set.
        frec = self._fdas_tasks.get(task_id)
        if frec and not frec.get("resolved"):
            frec["bids"][bidder] = bid_rec

    # ── claim (winner executes, others stand down) ────────────────────────
    def _on_claim(self, ev: dict) -> None:
        task_id = ev.get("task_id")
        if not task_id:
            return
        if task_id in self._claimed_tasks:
            return  # claim is authoritative; first wins
        self._claimed_tasks.add(task_id)
        winner = (ev.get("payload") or {}).get("winner")
        if not winner:
            return
        # Remember this winner so a resolver won't also hand it ANOTHER package
        # before its "running" heartbeat propagates (prevents two resolvers
        # double-booking the same AGV while peers sit idle).
        self._recent_winners.add(winner)
        # Mark my own announcer/bid record as resolved
        if task_id in self._announced:
            self._announced[task_id]["claimed"] = True
        if winner == AGV_ID:
            payload = ev.get("payload") or {}
            assignment = payload.get("assignment") or {}
            assignment["agv_id"] = AGV_ID
            assignment.setdefault("task_id", task_id)
            assignment.setdefault("package_id", task_id)
            _log_bus("won", f"{task_id} — beginning takeover")
            try:
                self._take_task(assignment)
            except Exception as exc:
                _log_bus("takeover-fail", f"{task_id}: {exc}")
        else:
            _log_bus("yield", f"{task_id} → {winner}")
        # Cleanup my-bid bookkeeping
        self._my_bids.pop(task_id, None)

    # ── announcer's bid window expires → pick winner + publish claim ──────
    def _resolve_announcer_deadlines(self) -> None:
        now = time.time()
        for task_id, record in list(self._announced.items()):
            if record.get("claimed"):
                continue
            if now < record.get("deadline", 0):
                continue
            bids = record.get("bids") or {}
            if not bids:
                # Self-rescue: if I (the announcer) am idle and no peer bid,
                # claim it myself. Better one healthy AGV completes it than
                # the package gets stranded.
                if not self._is_busy():
                    _log_bus("self-rescue",
                             f"{task_id} no peer bids; taking it myself")
                    winner = AGV_ID
                    winning = {"score": 50.0,
                               "rationale": "announcer self-rescue (no peer bids)",
                               "eta_s": 0.0}
                else:
                    _log_bus("no-bidders", f"{task_id} — orphan unresolved")
                    record["claimed"] = True
                    continue
            else:
                # Brief: highest score wins; tie-break = lowest agent id
                winner = max(bids.items(),
                             key=lambda kv: (kv[1]["score"], -ord_str(kv[0])))[0]
                winning = bids[winner]
            _log_bus("claim-publish",
                     f"{task_id} winner={winner} score={winning['score']:.1f}")
            publish("claim", {
                "winner": winner,
                "winning_score": winning["score"],
                "rationale": winning.get("rationale", ""),
                "assignment": record.get("payload", {}).get("assignment") or {},
            }, task_id=task_id)
            record["claimed"] = True

    def _bidder_free(self, bidder: str) -> bool:
        """True if this bidder can start a task right now: not already running,
        AND not a just-announced winner whose heartbeat is still in flight. The
        FDAS resolver uses this to send each package to a FREE AGV and defer the
        rest — sequential nearest-first dispatch, and no two resolvers
        double-booking the same AGV while other AGVs sit idle."""
        if bidder == AGV_ID:
            return not self._is_busy()
        if bidder in self._recent_winners:
            return False  # already won a claim; its running-heartbeat may lag
        pt = self._peer_tasks.get(bidder)
        return not (pt and pt.get("task_id"))

    # ── FDAS Step 2: leaderless resolution of INITIAL announces ───────────
    def _resolve_fdas_deadlines(self) -> None:
        """Resolve initial task_announce WITHOUT the backend. Every bidding AGV
        collects the same bids; the lowest-id bidder is the single elected
        resolver and publishes the `claim` naming the highest-score winner. The
        winner then self-executes via _on_claim (the existing path) and losers
        stand down — same as the orphan flow, just master-free.

        Electing ONE resolver (vs. everyone claiming) keeps a single claim on
        the bus, avoiding split-brain. Residual risk only if peers see different
        bid sets within the window (the consistency caveat); _on_claim's "first
        wins" dedup bounds the blast radius. NEEDS RUNTIME VALIDATION."""
        now = time.time()
        # Sequential per-package dispatch: resolve at most ONE package per tick,
        # and only to a bidder that is FREE right now. This staggers starts so
        # the nearest AGV takes the shared lane FIRST (no all-at-once race), and
        # spreads the batch across AGVs as they free up. A package whose only
        # capable bidders are all busy is DEFERRED (never dropped) and retried on
        # a later tick — so when the one capable AGV (e.g. the only one passing a
        # weight gate) returns, it takes the next package.
        for task_id, rec in list(self._fdas_tasks.items()):
            if rec.get("resolved"):
                continue
            if now < rec.get("deadline", 0):
                continue
            if task_id in self._claimed_tasks:
                rec["resolved"] = True
                continue
            bids = rec.get("bids") or {}
            if not bids:
                rec["resolved"] = True
                _log_bus("fdas-no-bids", f"{task_id} — no bids in window")
                continue
            # Single elected resolver = lowest-id bidder (keeps one claim on the
            # bus, avoids split-brain). A busy resolver can still resolve — that
            # runs on the bus thread, independent of task execution.
            resolver = min(bids.keys(), key=ord_str)
            if resolver != AGV_ID:
                rec["resolved"] = True
                continue  # another peer resolves; I act on its claim
            # Highest-score bidder that is FREE right now wins; busy bidders are
            # skipped so each package goes to whoever can act on it immediately.
            ranked = sorted(bids.items(),
                            key=lambda kv: (kv[1]["score"], -ord_str(kv[0])),
                            reverse=True)
            winner = next((b for b, _ in ranked if self._bidder_free(b)), None)
            if winner is None:
                # Every capable bidder is busy — DEFER (do not drop, do not mark
                # resolved). Retried next tick; the first capable AGV to free up
                # takes it. This is the weight fallback + the no-stranding rule.
                _log_bus("fdas-defer",
                         f"{task_id} — all capable bidders busy; waiting")
                continue
            rec["resolved"] = True
            winning = bids[winner]
            _log_bus("fdas-resolve",
                     f"{task_id} resolver={AGV_ID} -> winner={winner} "
                     f"score={winning['score']:.1f}")
            publish("claim", {
                "winner": winner,
                "winning_score": winning["score"],
                "rationale": winning.get("rationale", ""),
                "assignment": {**(rec.get("payload") or {}),
                               "agv_id": winner,
                               "task_id": task_id,
                               "package_id": task_id},
            }, task_id=task_id)
            return  # ONE resolution per tick -> staggered starts, nearest first

    # ── heartbeat watch: detect peers whose agent_status went stale ───────
    def _scan_for_faults(self) -> None:
        """Bus-level heartbeat: each peer publishes agent_status at start of
        task and on completion. If a peer was running a task but we haven't
        seen any agent_status from them in PEER_STALE_S, treat them as faulted
        and publish task_orphan for their last-known task.

        Brief: > 3 s missing → faulted. We use 8 s to absorb poll jitter.

        This is bus-only — it does NOT consult /coord/snapshot.agv_state.status,
        which has unrelated semantics (the AGV's own move-status, not its
        liveness). Relying on that field produces false positives at startup
        when peers are still in default state="stopped".
        """
        now = time.time()
        for peer_id, task_info in list(self._peer_tasks.items()):
            if peer_id == AGV_ID:
                continue
            last_seen = float(task_info.get("last_seen", 0))
            if now - last_seen < PEER_STALE_S:
                continue  # still alive
            task_id = task_info.get("task_id")
            if not task_id:
                continue  # peer was idle when we last heard from them
            if task_id in self._already_orphaned:
                continue
            self._already_orphaned.add(task_id)
            seq = publish_task_orphan(
                task_id=task_id,
                package_id=task_id,
                reason=(f"{peer_id} silent for "
                        f"{now - last_seen:.1f}s (heartbeat timeout)"),
                assignment={**(task_info.get("assignment") or {}),
                            "original_agv": peer_id},
            )
            if seq is not None:
                _log_bus("fault-detected",
                         f"{peer_id} silent {now-last_seen:.1f}s → orphan {task_id} (announcer)")
            else:
                _log_bus("fault-detected",
                         f"{peer_id} silent {now-last_seen:.1f}s → orphan {task_id} (duplicate)")


def ord_str(s: str) -> int:
    """Tie-break helper: produce a stable comparable value from agent_id.
    Lower id wins, so we negate. For 'agv-1' < 'agv-2' < 'agv-3' this works."""
    return sum(ord(c) for c in (s or ""))
