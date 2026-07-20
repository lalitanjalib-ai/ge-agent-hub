# Complete WIF/OBO setup for employ_verification in Gemini Enterprise.
#
# Prerequisites (run once in an interactive terminal):
#   gcloud auth login
#   gcloud auth application-default login
#   Set OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET in .env
#
# Usage (from employ_verification/):
#   .\scripts\complete_wif_setup.ps1

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

# Pre-flight: auth + secret check
Write-Host "=== Pre-flight: gcloud ADC ===" -ForegroundColor Cyan
gcloud auth application-default print-access-token *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Run: gcloud auth application-default login" -ForegroundColor Red
    exit 1
}

$envContent = Get-Content ".env" -Raw
if ($envContent -match 'OAUTH_CLIENT_SECRET=CHANGE_ME') {
    Write-Host "Set OAUTH_CLIENT_SECRET in .env before running setup." -ForegroundColor Red
    exit 1
}

Write-Host "=== Step 0: validate configuration ===" -ForegroundColor Cyan
python scripts/validate_wif_config.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Step 1: grant IAM for workforce principals ===" -ForegroundColor Cyan
python scripts/grant_permissions.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Step 2: create GE authorization resource ===" -ForegroundColor Cyan
python scripts/setup_agent_auth.py --id auth-employee-verification
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Step 3: deploy agent + register in Gemini Enterprise ===" -ForegroundColor Cyan
python scripts/deploy.py employee_verification
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Done ===" -ForegroundColor Green
Write-Host "Sign into Gemini Enterprise as an Entra user and run an employee lookup."
Write-Host "Optional: validate STS exchange in isolation:"
Write-Host "  python scripts/verify_wif.py <ENTRA_ACCESS_TOKEN>"
