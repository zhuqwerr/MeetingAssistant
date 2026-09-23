$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Install Node.js 20+ first.' }
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Install Python 3.12 (with Python launcher) first.' }
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required. Install it and retry.' }
}
& ./.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
# Upgrade environments created by the earlier experimental FunASR/GPU branch.
$installedPackages = & ./.venv/Scripts/python.exe -m pip list --format=json | ConvertFrom-Json
$obsoletePackages = @($installedPackages | Where-Object { $_.name -match '^(funasr|modelscope|modelscope-hub|torch|torchvision|torchaudio|kaldi-native-fbank|transformers|sentencepiece|nvidia-)' } | ForEach-Object { $_.name })
if ($obsoletePackages.Count -gt 0) {
    & ./.venv/Scripts/python.exe -m pip uninstall -y $obsoletePackages
    if ($LASTEXITCODE -ne 0) { throw 'Could not remove obsolete FunASR/GPU runtime packages.' }
}
& ./.venv/Scripts/python.exe -m pip install -e . --no-deps
if ($LASTEXITCODE -ne 0) { throw 'Backend installation failed.' }
Push-Location frontend
try {
    & npm.cmd ci --cache (Join-Path $PSScriptRoot '.npm-cache')
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
Write-Host 'Installed. Run .\start.ps1 to open MeetingAssistant.' -ForegroundColor Green
