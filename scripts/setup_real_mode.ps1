param(
    [switch]$StatusOnly,
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot
$env:PYTHONIOENCODING = 'utf-8'

function Run-Step {
    param([string]$Title, [string[]]$Command)
    Write-Host "`n$Title" -ForegroundColor Cyan
    & $Command[0] @($Command[1..($Command.Length - 1)])
    if ($LASTEXITCODE -ne 0) { throw "Step failed with exit code ${LASTEXITCODE}: $($Command -join ' ')" }
}

Write-Host "== MRKT Scout Bot: real mode setup ==" -ForegroundColor Cyan
Write-Host "Project: $ProjectRoot"

if (-not $StatusOnly -and -not $SkipInstall) {
    Run-Step '[1/5] Installing requirements...' @('python', '-m', 'pip', 'install', '-r', 'requirements.txt')
}

Run-Step '[2/5] TON payout wallet status...' @('python', 'scripts\bind_ton_wallet.py', '--status')

if (-not $StatusOnly) {
    Write-Host "`n[3/5] Bind TON payout wallet." -ForegroundColor Cyan
    Write-Host 'Paste the 24 words only into this local hidden prompt. They are not printed to chat/logs.'
    & python scripts\bind_ton_wallet.py
    if ($LASTEXITCODE -ne 0) { throw "TON wallet binding failed with exit code $LASTEXITCODE" }
}

Run-Step '[4/5] MRKT/Telegram session status...' @('python', 'scripts\bind_mrkt_session.py', '--status')

if (-not $StatusOnly) {
    Write-Host "`n[5/5] Bind MRKT/Telegram session." -ForegroundColor Cyan
    Write-Host 'Telegram will ask for the login code/password in this local console.'
    & python scripts\bind_mrkt_session.py
    if ($LASTEXITCODE -ne 0) { throw "MRKT session binding failed with exit code $LASTEXITCODE" }
}

Write-Host "`nFinal status:" -ForegroundColor Green
& python scripts\bind_ton_wallet.py --status
if ($LASTEXITCODE -ne 0) { throw "TON status failed with exit code $LASTEXITCODE" }
& python scripts\bind_mrkt_session.py --status
if ($LASTEXITCODE -ne 0) { throw "MRKT status failed with exit code $LASTEXITCODE" }
Write-Host "`nReady. Start bot with:" -ForegroundColor Green
Write-Host "powershell -NoProfile -ExecutionPolicy Bypass -File `"$ProjectRoot\scripts\run_bot_real_mode.ps1`""

