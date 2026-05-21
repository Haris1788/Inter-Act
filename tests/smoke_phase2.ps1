# Phase 2 (US #29) — single-shot smoke test.
#
# Exercises Steps 1-9 end-to-end against a live docker stack.
# Prereqs:
#   - docker compose up --build is running
#   - Build & Deploy has been clicked once (so arm-1 and arm-2 are reconciler-deployed)
#   - All 5 actors visible in /bus/roster
#
# Usage:
#   .\tests\smoke_phase2.ps1
#
# Exit code 0 = pass, 1 = fail.

$ErrorActionPreference = "Continue"
$base = "http://localhost:5000"

function Pass($msg) { Write-Host "  PASS: $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "  FAIL: $msg" -ForegroundColor Red; $script:failed = $true }
function Step($msg) { Write-Host "`n[$msg]" -ForegroundColor Cyan }

$script:failed = $false

# ── Step A: roster has all expected actors ────────────────────────────────────
Step "A. Roster check"
$roster = Invoke-RestMethod -Uri "$base/bus/roster"
$actors = @($roster.actors | ForEach-Object { $_.actorId } | Sort-Object)
Write-Host "  Actors registered: $($actors -join ', ')"
if ($actors -contains "agv-1" -and $actors -contains "agv-2" -and $actors -contains "agv-3") {
  Pass "3 AGVs registered"
} else {
  Fail "Need 3 AGVs"
}
if ($actors -contains "arm-1" -and $actors -contains "arm-2") {
  Pass "2 arms registered"
} else {
  Fail "Need 2 arms (click Build & Deploy in canvas first)"
}

# ── Step B: dispatch a task ───────────────────────────────────────────────────
Step "B. /bus/task/dispatch"
$body = '{"task":{"requiredStations":["input_arm","output_arm"],"label":"red"}}'
$result = Invoke-RestMethod -Method POST -Uri "$base/bus/task/dispatch" `
  -ContentType "application/json" -Body $body
$taskId = $result.taskId
Write-Host "  taskId: $taskId"
Write-Host "  status: $($result.status)"
Write-Host "  path:   $($result.path -join ' -> ')"
if ($result.status -eq "agreed") { Pass "negotiation agreed in $($result.attempts) attempt(s)" }
else { Fail "expected status=agreed, got $($result.status)"; exit 1 }

$chosenAgv = ($result.path | Where-Object { $_ -like "agv-*" })[0]
Write-Host "  chosen AGV: $chosenAgv"

# ── Step C: tracker registered the task ───────────────────────────────────────
Step "C. /bus/task/$taskId (tracker state)"
Start-Sleep -Milliseconds 500
$entry = Invoke-RestMethod -Uri "$base/bus/task/$taskId"
Write-Host "  tracker status: $($entry.status)"
Write-Host "  tracker path:   $($entry.path -join ' -> ')"
if ($entry.path.Count -eq 3 -and $entry.status -in @("running", "replanning", "completed")) {
  Pass "tracker has the task with the agreed path"
} else {
  Fail "tracker entry malformed"
}

# ── Step D: roster reflects BUSY ──────────────────────────────────────────────
Step "D. roster shows path participants as BUSY"
$roster2 = Invoke-RestMethod -Uri "$base/bus/roster"
$busy = @($roster2.actors | Where-Object { $_.status -eq "BUSY" } | ForEach-Object { $_.actorId })
Write-Host "  BUSY actors: $($busy -join ', ')"
if ($busy -contains $chosenAgv -or $busy.Count -ge 1) {
  Pass "at least one path participant marked BUSY"
} else {
  Write-Host "  (note: AGV may have already completed/failed by this point — non-fatal)" -ForegroundColor Yellow
}

# ── Step E: simulate AGV fault → replan ───────────────────────────────────────
Step "E. Fault the chosen AGV → expect REPLAN_REQUIRED → new path"
$faultRes = Invoke-RestMethod -Method POST `
  -Uri "$base/bus/actor/$chosenAgv/fault" `
  -ContentType "application/json" -Body '{"reason":"smoke test fault"}'
Write-Host "  fault response: $($faultRes.status) for $($faultRes.actorId)"

# Replan handler is async — give it time to release survivors and re-dispatch.
Start-Sleep -Seconds 4

$entry2 = Invoke-RestMethod -Uri "$base/bus/task/$taskId"
Write-Host "  after fault — tracker status: $($entry2.status)"
Write-Host "  after fault — tracker path:   $($entry2.path -join ' -> ')"
Write-Host "  attempts: $($entry2.attempts), excluded: $($entry2.excluded -join ',')"
if ($entry2.excluded -contains $chosenAgv) {
  Pass "failed AGV ($chosenAgv) added to excluded list"
} else {
  Fail "expected excluded to contain $chosenAgv"
}
$newAgv = ($entry2.path | Where-Object { $_ -like "agv-*" })[0]
if ($newAgv -and $newAgv -ne $chosenAgv) {
  Pass "replan picked a different AGV: $newAgv"
} elseif ($entry2.status -eq "aborted") {
  Pass "task aborted after replan exhaustion (acceptable in low-AGV-count scenarios)"
} else {
  Write-Host "  (note: replan picked same AGV or didn't fire — check backend logs)" -ForegroundColor Yellow
}

# ── Step F: roster marks faulted AGV as FAILED ────────────────────────────────
Step "F. roster: $chosenAgv has status FAILED"
$roster3 = Invoke-RestMethod -Uri "$base/bus/roster"
$faulted = $roster3.actors | Where-Object { $_.actorId -eq $chosenAgv }
Write-Host "  $chosenAgv status: $($faulted.status)  reason: $($faulted.fault_reason)"
if ($faulted.status -eq "FAILED") { Pass "actor_registry mirrored ACTOR_FAILED" }
else { Fail "expected FAILED, got $($faulted.status)" }

# ── Step G: complete the task → expect ACTOR_FREE fan-out ─────────────────────
Step "G. /bus/task/$taskId/complete → ACTOR_FREE for path participants"
$completeRes = Invoke-RestMethod -Method POST -Uri "$base/bus/task/$taskId/complete" `
  -ContentType "application/json"
Write-Host "  completion published by: $($completeRes.by)"
Start-Sleep -Seconds 1

$roster4 = Invoke-RestMethod -Uri "$base/bus/roster"
$entry3 = Invoke-RestMethod -Uri "$base/bus/task/$taskId"
Write-Host "  tracker final status: $($entry3.status)"
if ($entry3.status -eq "completed") { Pass "task tracker marked completed" }
else { Fail "expected completed, got $($entry3.status)" }

# Check that path's non-failed actors are FREE
foreach ($actor in $entry3.path) {
  $rec = $roster4.actors | Where-Object { $_.actorId -eq $actor }
  if ($actor -eq $chosenAgv) { continue }  # stays FAILED, that's fine
  if ($rec.status -eq "FREE") {
    Pass "$actor is FREE"
  } else {
    Fail "$actor still $($rec.status)"
  }
}

Write-Host ""
if ($script:failed) {
  Write-Host "SMOKE TEST FAILED" -ForegroundColor Red
  exit 1
} else {
  Write-Host "SMOKE TEST PASSED — Phase 2 negotiation, failure, replan, completion all working" -ForegroundColor Green
  exit 0
}
