# Phase 4 Stage 6 Committed Sub-Spec — Replay & Observability

Frontend becomes a distributed-systems observatory. 4 independent tracks — TRUE PARALLEL.
No track owns orchestration; replay shows history, never decides.

## Tracks (parallel, no shared files)

### Track A — Backend replay endpoint
**Files:** `app/routes/replay_routes.py` (new blueprint), `app/__init__.py` (register).
Routes:
- `GET /bus/replay?from=<xid>&to=<xid>&task_id=<id>&type=<type>` →
  `{messages: [Message.to_dict()]}`. All filters optional. Works against both bus impls
  (InProcessBus = list slice by ts/index; RedisStreamsBus = XRANGE then filter).
- `GET /bus/actors` → `{actors: [{actor_id, last_seen, state, presumed_dead}]}`.
  Uses `app/agents/heartbeat.py` `last_seen`/`is_presumed_dead` pure helpers.
- Pure-read endpoints. No mutation. No decisions.

### Track B — Frontend Replay panel + timeline
**Files:** `frontend/src/chrome/Replay/ReplayPanel.tsx` (new),
`frontend/src/chrome/Replay/Timeline.tsx` (new), route in app shell.
- `ReplayPanel`: GET `/bus/replay`; scrub bar, play/pause, step ±1; renders message list
  via existing BusPane chip/label logic (import + reuse).
- `Timeline`: horizontal lanes per `(task_id, round)`; events plotted along time axis;
  click a message → highlights in replay panel; consensus/failure markers styled distinctly.
- No fake data. Empty state when log empty.

### Track C — Frontend Fault history + actor inspector
**Files:** `frontend/src/chrome/Observability/FaultHistory.tsx` (new),
`frontend/src/chrome/Observability/ActorInspector.tsx` (new).
- `FaultHistory`: filters messages for `FAULT`, `WITHDRAW`, `TASK_FAILED`, `LEG_HALTED`;
  groups by `task_id`; shows reason payloads.
- `ActorInspector`: GET `/bus/actors`; shows actor list with `last_seen` (relative time),
  `state`, presumed-dead indicator; expands to show recent activity per actor (derived
  from `bus.history()` filtered by sender).

### Track D — Observability docs
**Files:** `docs/observability.md` (new).
- Document: the 15 MessageTypes and what each means for observation; replay endpoint
  contract; how to interpret the timeline (per-task lane semantics, round transitions);
  fault history rendering; actor inspector + presumed-dead semantics + heartbeat
  interval/threshold. Frontend-only — no orchestration claims.

## Hard invariants

- No track introduces decision authority. Replay = read. Inspector = read.
- No track caches decisions; everything derives from `bus.history()` on demand.
- Frontend remains an observer (Phase 3 invariant).
- Endpoints are filtered reads; no XADD, no XACK from replay routes.

## Verify per track

- A: pytest covers `/bus/replay` filters + `/bus/actors` against fixture-seeded bus history.
- B: `npm run typecheck && npm run build` clean.
- C: same; integration with `/bus/actors` mocked in component tests if any.
- D: doc renders, links resolve.

## Opus review gate

Frontend = observer only. No replay action mutates state. No inspector action commands
actors. No new authority leaks into the UI layer.
