param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { throw 'Run .\install.ps1 first.' }
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) { throw 'Run .\install.ps1 to build the frontend first.' }
$env:PYTHONUTF8 = '1'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
Write-Host 'MeetingAssistant: http://127.0.0.1:8766 (Ctrl+C to stop)' -ForegroundColor Green
if ($NoBrowser) { & ./.venv/Scripts/python.exe -m meeting_assistant.launch --no-browser }
else { & ./.venv/Scripts/python.exe -m meeting_assistant.launch }
