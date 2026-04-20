# Docker setup script for Inter-Act project
# Run this in PowerShell to add Docker to PATH for the current session

$env:Path += ";D:\Docker\bin"
Write-Host "Docker added to PATH. You can now run 'docker compose up --build'" -ForegroundColor Green
