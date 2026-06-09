# FDAS demo (PowerShell) — "plan once, the PEERS choose the winner (master-gone)"
#
# Demonstrates FDAS Step 2: the backend stops deciding who wins. The 32B plans
# once, packages are announced, AGVs bid, and a PEER resolver (the lowest-id
# bidder) publishes the claim. Visible in the bus log / frontend bus panel as:
#     task_announce -> bid -> fdas-resolve -> claim -> won
#
# HONEST SCOPE NOTE
#   This does NOT yet show "kill the backend and the fleet keeps working".
#   The message BUS is still hosted on the backend (HTTP ring buffer), so the
#   transport is still centralized even though the DECISIONS are now peer-made.
#   The kill-the-backend proof needs the peer-to-peer transport step (each actor
#   its own bus / direct peer broadcast) — that is still pending. Do not claim
#   the kill-backend headline from this script.
#
# PREREQUISITES
#   1. In docker-compose.yml, add  FDAS_PEER_RESOLVE=on  to the environment of
#      the backend service AND every agv-* service. Keep COORDINATION_MODE=smart.
#      (Default off => unchanged US 32/34 backend-resolver behaviour.)
#   2. TUC GPU reachable (LLM_BASE_URL = host.docker.internal:11500).

$ErrorActionPreference = "Stop"
$backend = "http://localhost:5000"

Write-Host "== FDAS demo (master-gone / peer resolution) ==" -ForegroundColor Cyan

Write-Host "1) Bringing the stack up (build)..."
docker compose up -d --build

Write-Host "2) Waiting for backend health..."
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-RestMethod "$backend/health" -TimeoutSec 3 | Out-Null; $ok = $true; break }
    catch { Start-Sleep 2 }
}
if (-not $ok) { Write-Host "backend never became healthy — check 'docker compose logs backend'" -ForegroundColor Red; exit 1 }

Write-Host "3) Confirm FDAS is ON in the AGV containers (capabilities echo):"
foreach ($p in 8101, 8102, 8103) {
    try { (Invoke-RestMethod "http://localhost:$p/capabilities" -TimeoutSec 3) | Out-String | Write-Host }
    catch { Write-Host "  agv on :$p not reachable yet" }
}

Write-Host "4) Sending ONE plan prompt (injects packages)..."
$body = @{ prompt = "send 3 packages: one blue, one green, one yellow" } | ConvertTo-Json
Invoke-RestMethod "$backend/plan" -Method Post -ContentType "application/json" -Body $body | Out-String | Write-Host
Write-Host "   plan announced. Now WATCH the frontend bus panel (http://localhost:8080)"
Write-Host "   or: docker logs -f interact-agv-1"
Write-Host "   You want to see:  task_announce -> bid -> fdas-resolve -> claim -> won"
Write-Host "   The 'fdas-resolve' line proves a PEER (not the backend) chose the winner."

Write-Host "== demo running; observe the logs ==" -ForegroundColor Cyan
