<#
.SYNOPSIS
  Switch the FDAS fleet between capability scenarios, live.

.DESCRIPTION
  Copies a scenario profile over pipeline.yaml, regenerates docker-compose.yml,
  and restarts the stack. Use between runs during a demo / professor Q&A.

  Scenarios:
    colour  -> pipeline.colour.yaml  (Scenario A: each AGV one colour + weight)
    free    -> pipeline.free.yaml    (Scenario B: any AGV, weight-only gate)

.EXAMPLE
  .\scripts\switch-scenario.ps1 free
  .\scripts\switch-scenario.ps1 colour
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('colour', 'color', 'free')]
    [string]$Scenario
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo

# colour/color are aliases for the same profile
if ($Scenario -eq 'free') {
    $profileFile = 'pipeline.free.yaml'
    $label = 'B - Weight only / colour-free'
} else {
    $profileFile = 'pipeline.colour.yaml'
    $label = 'A - Colour + Weight (strict routing)'
}

if (-not (Test-Path $profileFile)) {
    Write-Host "Profile not found: $profileFile" -ForegroundColor Red
    exit 1
}

Write-Host "== Switching to Scenario $label ==" -ForegroundColor Cyan

# 1. Make the chosen profile the active pipeline.yaml
Copy-Item $profileFile 'pipeline.yaml' -Force
Write-Host "1) $profileFile -> pipeline.yaml"

# 2. Regenerate the compose (now preserves FDAS_PEER_RESOLVE via pipeline.yaml)
Write-Host "2) regenerating docker-compose.yml ..."
python scripts/generate_compose.py

# 3. Sanity: confirm the FDAS flag survived regeneration
$fdasCount = (Select-String -Path 'docker-compose.yml' -Pattern 'FDAS_PEER_RESOLVE=on').Count
if ($fdasCount -lt 1) {
    Write-Host "   WARNING: FDAS_PEER_RESOLVE=on not found in compose - peer resolution may be OFF." -ForegroundColor Yellow
} else {
    Write-Host "   FDAS_PEER_RESOLVE=on present on $fdasCount services (backend + AGVs)." -ForegroundColor Green
}

# 4. Restart the stack with the new capabilities
Write-Host "3) restarting stack (docker compose up -d --build) ..."
docker compose up -d --build

# 5. Echo the active AGV capabilities so you can see the switch took effect
Write-Host "Active fleet capabilities:" -ForegroundColor Cyan
Select-String -Path 'pipeline.yaml' -Pattern 'allowed_labels' | ForEach-Object { "  $($_.Line.Trim())" }

Write-Host "== Scenario $label is live. Send a plan from the frontend and watch the bus. ==" -ForegroundColor Cyan

# NOTE: arms are NOT compose services - they keep running across this restart.
# After ANY arm code change, rebuild their image by hand:
#   docker build -f docker/Dockerfile.arm -t interact-shared-arm:latest .
