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


def poll_events(since: int, types: str = LISTEN_TYPES, limit: int = 100) -> tuple[list, int]:
    res = _get(f"/bus/events?since={since}&types={types}&limit={limit}")
    if not res:
        return [], since
    # Bus-reset detection: if the backend restarted, its ring `head` is far
    # below our cursor. Re-poll from 0 so we don't get stuck waiting for a
    # seq the new bus will never reach.
    head = int(res.get("head", since))
    if head < since:
        res = _get(f"/bus/events?since=0&types={types}&limit={limit}")
        if not res:
            return [], 0
    return res.get("events", []), int(res.get("next_since", since))


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
def publish_intent(task_id: Optional[str], action: str, detail: str = "") -> None:
    """Announce what we're about to do — peers see this ≥1 cell before the
    action so they can yield if there's a conflict."""
    publish("intent", {"action": action, "detail": detail,
                       "agv_id": AGV_ID}, task_id=task_id)


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


# ── qwen14b prompt for bid decision ──────────────────────────────────────────
def _ask_for_bid(task_id: str, announce_type: str, payload: dict,
                 my_state: dict, others: dict) -> tuple[bool, float, str, float]:
    """Consult qwen14b. Returns (should_bid, score, rationale, eta_s).

    Brief: score is the AGV's tactical brain's weighted assessment of
    distance, current load, and proximity to the package. Higher = better fit.
    """
    package_id = payload.get("package_id", task_id)
    original = payload.get("original_agv", "?")
    reason = payload.get("reason", "")

    if announce_type == "task_orphan":
        situation = (
            f"AGV {original} faulted and dropped task {task_id} "
            f"(package {package_id}). Reason: {reason}. "
            f"Other healthy AGVs may bid to take it over."
        )
    else:  # task_announce
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

    # /ai/tactical packs the LLM JSON variously; try several shapes
    parsed: dict = {}
    # 1. New 'parsed' field — backend already json.loads()'d the model output
    if isinstance(body.get("parsed"), dict) and body["parsed"]:
        parsed = dict(body["parsed"])
    # 2. Fall back to raw text
    if not parsed:
        raw = body.get("result") or body.get("script") or body.get("raw") or ""
        if isinstance(raw, str):
            txt = raw.strip()
            # strip ```json fences if present
            if txt.startswith("```"):
                txt = txt.split("```", 2)[-1].lstrip("json").strip()
                if txt.endswith("```"):
                    txt = txt[:-3].strip()
            # find first {...} block
            if "{" in txt and "}" in txt:
                start = txt.find("{")
                end = txt.rfind("}") + 1
                try:
                    parsed = json.loads(txt[start:end])
                except json.JSONDecodeError:
                    parsed = {}
    # 3. Maybe the body itself has the fields (legacy)
    if not parsed:
        for k in ("bid", "score", "rationale", "eta_s"):
            if k in body:
                parsed[k] = body[k]
    # 4. If we got a successful response but no parseable fields, treat AI
    # silence as "bid yes with a heuristic score" — peers are guaranteed at
    # least one bidder per orphan, which is the protocol's whole point.
    if not parsed and body.get("status") == "success":
        parsed = {"bid": True, "score": 50.0,
                  "rationale": "AI unparseable; heuristic accept",
                  "eta_s": 30.0}

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

        # Track tasks that have been claimed already (so we ignore stale events)
        self._claimed_tasks: set[str] = set()

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

    # ── bus poll loop ─────────────────────────────────────────────────────
    def _bus_loop(self) -> None:
        while not self._stop.is_set():
            try:
                events, self._since = poll_events(self._since)
                for ev in events:
                    self._dispatch(ev)
                self._resolve_announcer_deadlines()
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
        # Terminal statuses clear the peer task regardless of task_id —
        # task_id may still be set as "the task that just ended", which
        # would otherwise keep the peer marked alive after completion.
        if status in {"done", "idle", "faulted", "stopped", "dead"}:
            self._peer_tasks.pop(who, None)
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
        if is_retry:
            # Master re-announced this task because the previous winner could
            # not actually take it. Clear stale claim/bid state so we evaluate
            # fresh — peers that originally lost the bid get another shot.
            self._claimed_tasks.discard(task_id)
            self._my_bids.pop(task_id, None)
            _log_bus("re-announce-seen",
                     f"{task_id} retry={payload.get('retry')} "
                     f"prev_winner={payload.get('previous_winner')}")
        elif task_id in self._my_bids:
            return  # original announce, already processing
        # Reserve the slot so duplicate processing on bus poll bounces.
        self._my_bids[task_id] = {"my_score": None, "my_bid_seq": None}
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
        record = self._announced.get(task_id)
        if not record or record.get("claimed"):
            return  # not my announce, or already resolved
        try:
            score = float((ev.get("payload") or {}).get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        record["bids"][bidder] = {
            "score": score,
            "rationale": (ev.get("payload") or {}).get("rationale", ""),
            "eta_s": (ev.get("payload") or {}).get("eta_s", 0),
        }
        _log_bus("bid-recv",
                 f"{task_id}: {bidder} score={score:.1f}")

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
