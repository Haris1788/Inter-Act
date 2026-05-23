#!/usr/bin/env bash
# tests/e2e/scenarios/scenario_2_longest_time.sh — Scenario 2: Transport, longest time.
#
# Identical topology to Scenario 1.
# Goal: "move 1 parcel using longest time"
#
# Assertions:
#   1. agv-3 wins the transport slot (farthest from pickup → longest time).
#   2. 3 LEG_COMPLETED events.
#
# Together with Scenario 1, this pair proves the Phase 5 acceptance gate:
# same topology, different constraint → different winner.
#
# agent-browser patterns adopted from `agent-browser skills get core --full`:
#   - find placeholder for resilient goal input location
#   - find text + find role button for Negotiate trigger
#   - screenshot at each phase for audit trail
#   - wait --load networkidle after SPA navigation
#
# Drag-drop strategy: /deploy/apply fallback (same reasoning as Scenario 1).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../lib/common.sh"

SCENARIO="scenario_2_longest_time"
GOAL="move 1 parcel using longest time"

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
        fail "Scenario 2 FAILED (exit ${code})"
    fi
    agent-browser close 2>/dev/null || true
}
trap _on_exit EXIT

info "=== Scenario 2: Transport / longest time ==="

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

# ── 4. Snapshot sidebar (informational; drag fallback already applied) ───────
info "Snapshotting sidebar …"
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

# ── 7. Wait for negotiation ─────────────────────────────────────────────────
TASK_ID=$(wait_for_task_announce 0 120)
info "Task ID: ${TASK_ID}"

wait_for_consensus "$TASK_ID" 120

agent-browser screenshot "${RESULTS_DIR}/${SCENARIO}_$(date +%Y%m%d)/03_consensus.png" 2>/dev/null || true

# ── 8. Assertions ────────────────────────────────────────────────────────────
info "Running assertions …"

# 8a. agv-3 wins transport (farthest → longest travel time).
assert_winner "$TASK_ID" "transport" "agv-3"

# 8b. All 3 legs complete.
assert_legs_completed "$TASK_ID" 3 180

# ── 9. Final screenshot ──────────────────────────────────────────────────────
agent-browser screenshot "${RESULTS_DIR}/${SCENARIO}_$(date +%Y%m%d)/04_complete.png" 2>/dev/null || true

pass "=== Scenario 2 PASSED: agv-3 won transport (longest time), 3 legs completed ==="
pass "=== Phase 5 acceptance gate CONFIRMED: same topology, different constraint → different winner ==="
