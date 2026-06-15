# Install WC 2026 pre-kickoff scheduled tasks (Windows)
# Runs live predictions + FotMob lineups 60 minutes before each match.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found on PATH. Install Python 3.10+ first."
}

$env:PYTHONIOENCODING = "utf-8"
Write-Host "Refreshing FotMob fixture schedule …"
python wc2026_scheduler.py refresh

Write-Host ""
Write-Host "Installing Windows Task Scheduler jobs (T-60 min before kickoff) …"
python wc2026_scheduler.py install --force

Write-Host ""
Write-Host "Done. To remove later: python wc2026_scheduler.py uninstall"
Write-Host "Alternative (no Task Scheduler): python wc2026_scheduler.py daemon"
