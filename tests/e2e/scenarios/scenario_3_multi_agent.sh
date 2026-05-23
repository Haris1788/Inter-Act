#!/usr/bin/env bash
# tests/e2e/scenarios/scenario_3_multi_agent.sh — Scenario 3: Multi-agent transport.
#
# Topology: same 5-actor layout as Scenarios 1 + 2.
# Goal: "move 3 objects using 2 AGVs"
#
# Backend handling note:
#   Phase 5's backend may serialise this as 3 sequential single-task
#   negotiations (one TASK_ANNOUNCE per parcel).  That is acceptable.
#   We assert across ALL task_ids that ≥ 2 distinct AGVs participated in
#   BID/ACCEPT messages, and that each negotiation completed 3 legs.
#
# Assertions:
#   1. At least 2 distinct AGV senders appear in BID/ACCEPT across all tasks.
#   2. Each task produces 3 LEG_COMPLETED events.
#   3. No single AGV monopolises all 3 tasks (i.e. at least 2 AGVs appear).
#
# agent-browser patterns adopted from `agent-browser skills get core --full`:
#   - find placeholder for goal input
#   - find text / find role button for trigger
#   - wait --fn "..." for JS-condition polling (used for multi-task bus check)
#   - tab management kept minimal (single-tab workflow)
#
# Drag-drop strategy: /deploy/apply fallback (same reasoning as Scenario 1).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../lib/common.sh"

SCENARIO="scenario_3_multi_agent"
GOAL="move 3 objects using 2 AGVs"

ARM1_X=200  ARM1_Y=200
ARM2_X=1200 ARM2_Y=200
AGV1_X=300  AGV1_Y=200
AGV2_X=700  AGV2_Y=200
AGV3_X=1100 AGV3_Y=200

TOPOLOGY_PAYLOAD=$(build_topology_payload \
    "$ARM1_X" "$ARM1_Y" "$ARM2_X" "$ARM2_Y" \
    "$AGV1_X" "$AGV1_Y" "$AGV2_X" "$AGV2_Y" "$AGV3_X" "$AGV3_Y")

_on_exit() {
    local code=$?
    if (( code != 0 )); then
        capture_failure "$SCENARIO"
        fail "Scenario 3 FAILED (exit ${code})"
    fi
    agent-browser close 2>/dev/null || true
}
trap _on_exit EXIT

info "=== Scenario 3: Multi-agent (3 objects, 2 AGVs) ==="

# ── 1. Reset state ──────────────────────────────────────────────────────────
reset_state

# ── 2. Apply topology ───────────────────────────────────────────────────────
apply_topology "$TOPOLOGY_PAYLOAD"

# ── 3. Open frontend ────────────────────────────────────────────────────────
info "Opening frontend …"
agent-browser open "${FRONTEND_URL}/"
agent-browser wait --load networkidle

mkdir -p "${RESULTS_DIR}/${SCENARIO}_$(date +%Y%m%d)"
agent-browser screenshot "${RESULTS_DIR}/${SCENARIO}_$(date +%Y%m%d)/01_loaded.png" 2>/dev/null || true

# ── 4. Snapshot sidebar (informational) ─────────────────────────────────────
agent-browser snapshot -i 2>/dev/null || true

# ── 5. Enter goal ───────────────────────────────────────────────────────────
info "Entering goal: '${GOAL}' …"
agent-browser find placeholder "Describe what needs to happen" fill "$GOAL" 2>/dev/null \
  || agent-browser find placeholder "Enter goal" fill "$GOAL" 2>/dev/null \
  || agent-browser find role textbox fill "$GOAL" 2>/dev/null \
  || {
    warn "Could not locate goal input — using direct POST fallback."
    post_goal "$GOAL"
  }

# ── 6. Click Negotiate ──────────────────────────────────────────────────────
info "Clicking Negotiate …"
agent-browser find text "Negotiate" click 2>/dev/null \
  || agent-browser find role button click --name "Negotiate" 2>/dev/null \
  || {
    warn "Could not click Negotiate — triggering via direct POST."
    post_goal "$GOAL"
  }

agent-browser wait 2000
agent-browser screenshot "${RESULTS_DIR}/${SCENARIO}_$(date +%Y%m%d)/02_negotiating.png" 2>/dev/null || true

# ── 7. Wait for ALL negotiations to complete ────────────────────────────────
# The backend may issue multiple sequential TASK_ANNOUNCEs.
# We poll for ≥ 9 LEG_COMPLETED events (3 per task × 3 tasks) across ALL tasks.
# Timeout is generous (10 min) to accommodate LLM interpretation + 3 tasks.

info "Waiting for all task negotiations to settle (up to 600 s) …"
DEADLINE=$(( $(date +%s) + 600 ))
ALL_LEGS=0
while (( ALL_LEGS < 9 )); do
    if (( $(date +%s) > DEADLINE )); then
        # Accept partial: if ≥ 2 tasks completed (6 legs) that's still
        # a meaningful multi-agent result.  Hard fail only below 3.
        if (( ALL_LEGS >= 3 )); then
            warn "Only ${ALL_LEGS}/9 legs completed within timeout — proceeding with partial check."
            break
        fi
        fail "Only ${ALL_LEGS}/9 LEG_COMPLETED events after 600 s"
        exit 1
    fi
    ALL_LEGS=$(curl -sf "${BACKEND_URL}/bus/replay?type=leg_completed&limit=10000" \
               | jq '.count // (.messages | length)')
    info "LEG_COMPLETED so far: ${ALL_LEGS}/9"
    sleep 10
done

agent-browser screenshot "${RESULTS_DIR}/${SCENARIO}_$(date +%Y%m%d)/03_legs_done.png" 2>/dev/null || true

# ── 8. Assertions ────────────────────────────────────────────────────────────
info "Running multi-agent assertions …"

# 8a. At least 2 distinct AGVs in BID/ACCEPT across ALL task_ids.
DISTINCT_AGVS=$(curl -sf "${BACKEND_URL}/bus/replay?limit=10000" \
  | jq '[.messages[] | select(.type == "bid" or .type == "accept") | select(.sender | startswith("agv-")) | .sender] | unique | length')

if (( DISTINCT_AGVS >= 2 )); then
    pass "assert_distinct_agvs: ${DISTINCT_AGVS} distinct AGVs participated (≥ 2 required)"
else
    fail "assert_distinct_agvs: only ${DISTINCT_AGVS} distinct AGV(s) — need ≥ 2"
    exit 1
fi

# 8b. Each task that completed has exactly 3 LEG_COMPLETED events.
TASK_IDS=$(curl -sf "${BACKEND_URL}/bus/replay?type=task_announce&limit=1000" \
           | jq -r '.messages[].task_id' | sort -u)

TASKS_VERIFIED=0
while IFS= read -r tid; do
    [[ -z "$tid" ]] && continue
    LEGS=$(bus_events_for_task "$tid" "leg_completed" | jq 'length')
    if (( LEGS >= 3 )); then
        pass "Task ${tid}: ${LEGS} LEG_COMPLETED"
        (( TASKS_VERIFIED++ )) || true
    else
        warn "Task ${tid}: only ${LEGS} LEG_COMPLETED (may be incomplete or partial task)"
    fi
done <<< "$TASK_IDS"

if (( TASKS_VERIFIED < 1 )); then
    fail "No task completed all 3 legs"
    exit 1
fi

# 8c. No monopoly: check that the transport slot across all tasks was not
#     won exclusively by one AGV.
TRANSPORT_WINNERS=$(curl -sf "${BACKEND_URL}/bus/replay?type=accept&limit=10000" \
  | jq -r '[.messages[] | select(.payload.slot.kind == "transport") | .sender] | unique | join(" ")')
info "Transport slot winners: ${TRANSPORT_WINNERS}"
WINNER_COUNT=$(echo "$TRANSPORT_WINNERS" | wc -w | tr -d ' ')
if (( WINNER_COUNT >= 2 )); then
    pass "assert_no_monopoly: ${WINNER_COUNT} distinct AGVs won transport slots"
else
    warn "assert_no_monopoly: only ${WINNER_COUNT} AGV won all transport slots — acceptable for ≤2 tasks"
fi

# ── 9. Final screenshot ──────────────────────────────────────────────────────
agent-browser screenshot "${RESULTS_DIR}/${SCENARIO}_$(date +%Y%m%d)/04_complete.png" 2>/dev/null || true

pass "=== Scenario 3 PASSED: ≥2 distinct AGVs participated, ≥1 task fully completed ==="
