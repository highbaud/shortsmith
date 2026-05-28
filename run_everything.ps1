# Wrapper: finish the current batch then re-process everything outdated.
# Safe to run multiple times - both scripts are idempotent (they skip work
# that's already done).
#
# Usage: open PowerShell, then run:
#   cd "F:\Claude Code\shortsmith"
#   .\run_everything.ps1

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "===== PHASE 1: scripts/batch_pipeline.py =====" -ForegroundColor Cyan
Write-Host "Processes every work dir that has clips.json but no cut_manifests.json yet."
Write-Host "Skips anything already done. Resumes wherever the background batch left off."
Write-Host ""

uv run python scripts/batch_pipeline.py

Write-Host ""
Write-Host "===== PHASE 2: scripts/redo_outdated.py =====" -ForegroundColor Cyan
Write-Host "Re-processes every work dir whose cut_manifests.json predates the cut/clean fix."
Write-Host "Archives the stale manifest, re-runs --from-step 3, re-renders."
Write-Host ""

uv run python scripts/redo_outdated.py

Write-Host ""
Write-Host "===== ALL DONE =====" -ForegroundColor Green
Write-Host "Final outputs: <kit>/video-projects/auto-shorts/"
Write-Host "Press any key to close..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
