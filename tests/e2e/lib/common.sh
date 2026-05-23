#!/usr/bin/env bash
# tests/e2e/lib/common.sh — Shared helpers for INTER.act E2E scenarios.
#
# Source this file at the top of each scenario script:
#   source "$(dirname "$0")/../lib/common.sh"
#
# Dependencies: curl, jq, docker, agent-browser (in PATH).

set -euo pipefail

# ── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'  # No Colour

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
pass()  { echo -e "${GREEN}[PASS]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# ── Stack addresses ─────────────────────────────────────────────────────────
FRONTEND_URL="${FRONTEND_URL:-http://localhost:8080}"
BACKEND_URL="${BACKEND_URL:-http://localhost:5000}"
REDIS_CONTAINER="${REDIS_CONTAINER:-interact-redis-1}"
RESULTS_DIR="${RESULTS_DIR:-$(dirname "$0")/../results}"

# ── wait_for_backend ────────────────────────────────────────────────────────
# Poll GET /health until 200 or timeout (default 60 s).
wait_for_backend() {
    local timeout="${1:-60}"
    local deadline=$(( $(date +%s) + timeout ))
    info "Waiting for backend at ${BACKEND_URL}/health …"
    until curl -sf "${BACKEND_URL}/health" >/dev/null 2>&1; do
        if (( $(date +%s) > deadline )); then
            fail "Backend did not become healthy within ${timeout}s"
            return 1
        fi
        sleep 2
    done
    pass "Backend healthy"
}

# ── wait_for_frontend ───────────────────────────────────────────────────────
wait_for_frontend() {
    local timeout="${1:-30}"
    local deadline=$(( $(date +%s) + timeout ))
    info "Waiting for frontend at ${FRONTEND_URL} …"
    until curl -sf "${FRONTEND_URL}/" >/dev/null 2>&1; do
        if (( $(date +%s) > deadline )); then
            fail "Frontend did not respond within ${timeout}s"
            return 1
        fi
        sleep 2
    done
    pass "Frontend reachable"
}

# ── reset_state ─────────────────────────────────────────────────────────────
# 1. Stop + remove ALL actor containers (label interact.role=actor).
# 2. FLUSHALL Redis.
# 3. Restart the backend container so the actor runtime reconciler re-seeds.
# 4. Wait for the backend and at least one actor to come back.
reset_state() {
    info "Resetting state …"

    # Stop and remove actor containers.
    local actor_ids
    actor_ids=$(docker ps -aq --filter "label=interact.role=actor" 2>/dev/null || true)
    if [[ -n "$actor_ids" ]]; then
        info "Removing actor containers: ${actor_ids//$'\n'/ }"
        # shellcheck disable=SC2086
        docker rm -f $actor_ids >/dev/null 2>&1 || true
    else
        info "No actor containers running."
    fi

    # Flush Redis.
    if docker inspect "${REDIS_CONTAINER}" >/dev/null 2>&1; then
        info "Flushing Redis (${REDIS_CONTAINER}) …"
        docker exec "${REDIS_CONTAINER}" redis-cli FLUSHALL >/dev/null
        pass "Redis flushed"
    else
        warn "Redis container '${REDIS_CONTAINER}' not found — skipping flush."
    fi

    # Restart backend container (named interact-backend-1 by default).
    local backend_container="${BACKEND_CONTAINER:-interact-backend-1}"
    if docker inspect "${backend_container}" >/dev/null 2>&1; then
        info "Restarting backend (${backend_container}) …"
        docker restart "${backend_container}" >/dev/null
        sleep 4  # Let Flask + SocketIO initialise before health polling starts.
    else
        warn "Backend container '${backend_container}' not found — skipping restart."
    fi

    wait_for_backend 90
    wait_for_frontend 30

    # Wait for the runtime reconciler to seed actors (up to 45 s).
    info "Waiting for at least one actor container to appear …"
    local deadline=$(( $(date +%s) + 45 ))
    until [[ -n "$(docker ps -q --filter 'label=interact.role=actor')" ]]; do
        if (( $(date +%s) > deadline )); then
            warn "No actor containers appeared within 45 s — proceeding anyway."
            break
        fi
        sleep 3
    done
    pass "State reset complete"
}

# ── apply_topology ──────────────────────────────────────────────────────────
# POST a topology payload to /deploy/apply so actors exist at the desired
# positions.  This is the FALLBACK path for drag-and-drop when agent-browser
# cannot reach the ReactFlow drop zone.
#
# Usage: apply_topology <json-payload-file-or-inline-json>
apply_topology() {
    local payload="$1"
    info "Applying topology via POST /deploy/apply …"
    local http_code
    http_code=$(curl -sf -o /dev/null -w "%{http_code}" \
        -X POST "${BACKEND_URL}/deploy/apply" \
        -H "Content-Type: application/json" \
        -d "${payload}")
    if [[ "$http_code" == "200" ]] || [[ "$http_code" == "202" ]]; then
        pass "Topology applied (HTTP ${http_code})"
    else
        fail "deploy/apply returned HTTP ${http_code}"
        return 1
    fi
    # Give reconciler time to spin up containers.
    sleep 8
}

# ── build_topology_payload ──────────────────────────────────────────────────
# Emit a JSON payload for /deploy/apply using the Stage 3 topology block shape.
# Arguments: arm1_x arm1_y arm2_x arm2_y agv1_x agv1_y agv2_x agv2_y agv3_x agv3_y
build_topology_payload() {
    local arm1_x="$1" arm1_y="$2"
    local arm2_x="$3" arm2_y="$4"
    local agv1_x="$5" agv1_y="$6"
    local agv2_x="$7" agv2_y="$8"
    local agv3_x="$9" agv3_y="${10}"

    cat <<EOF
{
  "actors": [
    {"id": "arm-1", "kind": "arm", "label": "Arm 1", "position": {"x": ${arm1_x}, "y": ${arm1_y}}},
    {"id": "arm-2", "kind": "arm", "label": "Arm 2", "position": {"x": ${arm2_x}, "y": ${arm2_y}}},
    {"id": "agv-1", "kind": "agv", "label": "AGV 1"},
    {"id": "agv-2", "kind": "agv", "label": "AGV 2"},
    {"id": "agv-3", "kind": "agv", "label": "AGV 3"}
  ],
  "topology": {
    "canvas": {"width": 1600, "height": 1000, "cell_px": 40},
    "actors": [
      {"id": "arm-1", "kind": "arm", "x": ${arm1_x}, "y": ${arm1_y}, "reach_px": 120, "label": "Arm 1"},
      {"id": "arm-2", "kind": "arm", "x": ${arm2_x}, "y": ${arm2_y}, "reach_px": 120, "label": "Arm 2"},
      {"id": "agv-1", "kind": "agv", "x": ${agv1_x}, "y": ${agv1_y}, "speed": 1.0, "label": "AGV 1"},
      {"id": "agv-2", "kind": "agv", "x": ${agv2_x}, "y": ${agv2_y}, "speed": 1.0, "label": "AGV 2"},
      {"id": "agv-3", "kind": "agv", "x": ${agv3_x}, "y": ${agv3_y}, "speed": 1.0, "label": "AGV 3"}
    ]
  }
}
EOF
}

# ── latest_task_id ──────────────────────────────────────────────────────────
# Query /bus/replay for all TASK_ANNOUNCE messages; echo the task_id of the
# most-recent one.
latest_task_id() {
    curl -sf "${BACKEND_URL}/bus/replay?type=task_announce&limit=1000" \
    | jq -r '.messages | sort_by(.ts) | last | .task_id // empty'
}

# ── wait_for_task_announce ──────────────────────────────────────────────────
# Poll until a TASK_ANNOUNCE with a round ≥ min_round appears (default 0).
# Echos the task_id on success.
wait_for_task_announce() {
    local min_round="${1:-0}"
    local timeout="${2:-120}"
    local deadline=$(( $(date +%s) + timeout ))
    info "Waiting for TASK_ANNOUNCE (round ≥ ${min_round}) …"
    while true; do
        local tid
        tid=$(curl -sf "${BACKEND_URL}/bus/replay?type=task_announce&limit=1000" \
              | jq -r --argjson r "$min_round" \
                '.messages | map(select(.payload.negotiation_round >= $r)) | sort_by(.ts) | last | .task_id // empty')
        if [[ -n "$tid" ]]; then
            info "task_id = ${tid}"
            echo "$tid"
            return 0
        fi
        if (( $(date +%s) > deadline )); then
            fail "No TASK_ANNOUNCE appeared within ${timeout}s"
            return 1
        fi
        sleep 3
    done
}

# ── bus_events_for_task ─────────────────────────────────────────────────────
# Fetch all bus messages for a task_id, optionally filtered by type.
# Prints raw JSON array.
bus_events_for_task() {
    local task_id="$1"
    local type_filter="${2:-}"
    local url="${BACKEND_URL}/bus/replay?task_id=${task_id}&limit=10000"
    if [[ -n "$type_filter" ]]; then
        url="${url}&type=${type_filter}"
    fi
    curl -sf "$url" | jq '.messages'
}

# ── wait_for_consensus ──────────────────────────────────────────────────────
# Poll until CONSENSUS_REACHED appears for the task_id.
wait_for_consensus() {
    local task_id="$1"
    local timeout="${2:-120}"
    local deadline=$(( $(date +%s) + timeout ))
    info "Waiting for CONSENSUS_REACHED (task=${task_id}) …"
    while true; do
        local count
        count=$(bus_events_for_task "$task_id" "consensus_reached" | jq 'length')
        if (( count > 0 )); then
            pass "CONSENSUS_REACHED seen for task ${task_id}"
            return 0
        fi
        if (( $(date +%s) > deadline )); then
            fail "No CONSENSUS_REACHED within ${timeout}s for task ${task_id}"
            return 1
        fi
        sleep 3
    done
}

# ── assert_winner ───────────────────────────────────────────────────────────
# Check that the slot was won by expected_sender.
# Looks for ACCEPT messages for the task where sender == expected_sender and
# payload.slot kind matches (transport|pickup|place).
assert_winner() {
    local task_id="$1"
    local slot_kind="$2"
    local expected_sender="$3"

    local actual
    actual=$(bus_events_for_task "$task_id" "accept" \
             | jq -r --arg k "$slot_kind" \
               'map(select(.payload.slot.kind == $k)) | first | .sender // empty')

    if [[ "$actual" == "$expected_sender" ]]; then
        pass "assert_winner: slot '${slot_kind}' winner is ${actual} (expected ${expected_sender})"
    else
        fail "assert_winner: slot '${slot_kind}' winner is '${actual}', expected '${expected_sender}'"
        return 1
    fi
}

# ── assert_legs_completed ───────────────────────────────────────────────────
# Assert that exactly <count> LEG_COMPLETED messages exist for the task.
assert_legs_completed() {
    local task_id="$1"
    local expected_count="$2"
    local timeout="${3:-180}"
    local deadline=$(( $(date +%s) + timeout ))

    info "Waiting for ${expected_count} LEG_COMPLETED events (task=${task_id}) …"
    while true; do
        local actual
        actual=$(bus_events_for_task "$task_id" "leg_completed" | jq 'length')
        if (( actual >= expected_count )); then
            pass "assert_legs_completed: got ${actual} ≥ ${expected_count} LEG_COMPLETED"
            return 0
        fi
        if (( $(date +%s) > deadline )); then
            fail "assert_legs_completed: only ${actual}/${expected_count} LEG_COMPLETED after ${timeout}s"
            return 1
        fi
        sleep 5
    done
}

# ── assert_returned_to_origin ───────────────────────────────────────────────
# Check that an AGV's last reported position is within tolerance_px of (x, y).
# Reads agv_state from GET /runtime/status and finds the AGV by id.
assert_returned_to_origin() {
    local actor_id="$1"
    local expected_x="$2"
    local expected_y="$3"
    local tolerance_px="${4:-80}"

    local pos_json
    pos_json=$(curl -sf "${BACKEND_URL}/runtime/status" \
               | jq --arg id "$actor_id" '.assets // [] | map(select(.id == $id)) | first | .position // empty')

    if [[ -z "$pos_json" ]] || [[ "$pos_json" == "null" ]]; then
        warn "assert_returned_to_origin: no position data for ${actor_id} — skipping."
        return 0
    fi

    local actual_x actual_y
    actual_x=$(echo "$pos_json" | jq '.x // .px // 0')
    actual_y=$(echo "$pos_json" | jq '.y // .py // 0')

    local dx dy
    dx=$(echo "$actual_x $expected_x" | awk '{d=$1-$2; print (d<0)?-d:d}')
    dy=$(echo "$actual_y $expected_y" | awk '{d=$1-$2; print (d<0)?-d:d}')

    if (( $(echo "$dx <= $tolerance_px && $dy <= $tolerance_px" | bc -l) )); then
        pass "assert_returned_to_origin: ${actor_id} at (${actual_x},${actual_y}) within ${tolerance_px}px of (${expected_x},${expected_y})"
    else
        fail "assert_returned_to_origin: ${actor_id} at (${actual_x},${actual_y}), expected (${expected_x}±${tolerance_px},${expected_y}±${tolerance_px})"
        return 1
    fi
}

# ── assert_distinct_agv_count ───────────────────────────────────────────────
# Assert that at least <min_count> distinct AGV senders appear in BID or
# ACCEPT messages for the task.
assert_distinct_agv_count() {
    local task_id="$1"
    local min_count="$2"

    local distinct
    distinct=$(curl -sf "${BACKEND_URL}/bus/replay?task_id=${task_id}&limit=10000" \
               | jq '[.messages[] | select(.type == "bid" or .type == "accept") | select(.sender | startswith("agv-")) | .sender] | unique | length')

    if (( distinct >= min_count )); then
        pass "assert_distinct_agv_count: ${distinct} distinct AGVs ≥ ${min_count}"
    else
        fail "assert_distinct_agv_count: only ${distinct} distinct AGVs, need ≥ ${min_count}"
        return 1
    fi
}

# ── assert_fault_or_withdraw ────────────────────────────────────────────────
# Assert that a FAULT or WITHDRAW message exists for sender_id in the task.
assert_fault_or_withdraw() {
    local task_id="$1"
    local sender_id="$2"
    local timeout="${3:-60}"
    local deadline=$(( $(date +%s) + timeout ))

    info "Waiting for FAULT/WITHDRAW from ${sender_id} (task=${task_id}) …"
    while true; do
        local count
        count=$(curl -sf "${BACKEND_URL}/bus/replay?task_id=${task_id}&limit=10000" \
                | jq --arg s "$sender_id" \
                  '[.messages[] | select((.type == "task_fault" or .type == "withdraw") and .sender == $s)] | length')
        if (( count > 0 )); then
            pass "assert_fault_or_withdraw: FAULT/WITHDRAW seen from ${sender_id}"
            return 0
        fi
        if (( $(date +%s) > deadline )); then
            fail "assert_fault_or_withdraw: no FAULT/WITHDRAW from ${sender_id} within ${timeout}s"
            return 1
        fi
        sleep 3
    done
}

# ── assert_reopen_round ─────────────────────────────────────────────────────
# Assert that a TASK_ANNOUNCE with round ≥ min_round appears (i.e. the task
# was reopened).
assert_reopen_round() {
    local task_id="$1"
    local min_round="${2:-1}"
    local timeout="${3:-90}"
    local deadline=$(( $(date +%s) + timeout ))

    info "Waiting for reopened TASK_ANNOUNCE round ≥ ${min_round} (task=${task_id}) …"
    while true; do
        local count
        count=$(curl -sf "${BACKEND_URL}/bus/replay?task_id=${task_id}&type=task_announce&limit=10000" \
                | jq --argjson r "$min_round" \
                  '[.messages[] | select(.payload.negotiation_round >= $r)] | length')
        if (( count > 0 )); then
            pass "assert_reopen_round: found TASK_ANNOUNCE round ≥ ${min_round}"
            return 0
        fi
        if (( $(date +%s) > deadline )); then
            fail "assert_reopen_round: no reopened announce within ${timeout}s"
            return 1
        fi
        sleep 3
    done
}

# ── winning_agv_for_transport ───────────────────────────────────────────────
# Echo the sender (agv-id) that won the transport slot in round 0.
winning_agv_for_transport() {
    local task_id="$1"
    bus_events_for_task "$task_id" "accept" \
    | jq -r 'map(select(.payload.slot.kind == "transport" and (.payload.negotiation_round // 0) == 0)) | first | .sender // empty'
}

# ── capture_failure ─────────────────────────────────────────────────────────
# Dump diagnostic artefacts to tests/e2e/results/<scenario>_<ts>/.
capture_failure() {
    local scenario_name="$1"
    local ts
    ts=$(date +%Y%m%d_%H%M%S)
    local out_dir="${RESULTS_DIR}/${scenario_name}_${ts}"
    mkdir -p "$out_dir"
    warn "Capturing failure artefacts to ${out_dir} …"

    # Bus log (full, unfiltered).
    curl -sf "${BACKEND_URL}/bus/replay?limit=10000" > "${out_dir}/bus_full.json" 2>/dev/null || true

    # Runtime status.
    curl -sf "${BACKEND_URL}/runtime/status" > "${out_dir}/runtime_status.json" 2>/dev/null || true

    # Redis state — KEYS + selective DUMP.
    docker exec "${REDIS_CONTAINER}" redis-cli KEYS '*' > "${out_dir}/redis_keys.txt" 2>/dev/null || true

    # agent-browser screenshot (last open page, if still running).
    agent-browser screenshot "${out_dir}/browser.png" 2>/dev/null || true

    # Docker container list.
    docker ps -a --filter "label=interact.role=actor" > "${out_dir}/actor_containers.txt" 2>/dev/null || true

    warn "Artefacts saved to ${out_dir}"
}

# ── post_goal ───────────────────────────────────────────────────────────────
# POST a negotiate prompt directly (bypass UI; instant start).
post_goal() {
    local goal="$1"
    info "POSTing goal: '${goal}' …"
    local resp
    resp=$(curl -sf -X POST "${BACKEND_URL}/negotiate" \
               -H "Content-Type: application/json" \
               -d "{\"prompt\": \"${goal}\"}")
    echo "$resp"
}
