$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$Python = Join-Path $Root '.venv/Scripts/python.exe'
$Assets = Join-Path $Root 'build-assets'
$BackendStage = Join-Path $Assets 'backend'
$PyInstallerDist = Join-Path $Assets 'pyinstaller-dist'
$PyInstallerWork = Join-Path $Assets 'pyinstaller-work'
$FrontendDist = Join-Path $Root 'frontend/dist'
$ElectronBuilderCache = Join-Path $env:TEMP 'MeetingAssistant-electron-builder-cache'

if (-not (Test-Path -LiteralPath $Python)) { throw 'Run install.ps1 first to create the development Python environment.' }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Install Node.js 20+ before building the desktop application.' }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Install Node.js 20+ before building the desktop application.' }
New-Item -ItemType Directory -Path $ElectronBuilderCache -Force | Out-Null
$env:ELECTRON_BUILDER_CACHE = $ElectronBuilderCache
$SevenZip = Join-Path $env:ProgramFiles '7-Zip/7z.exe'
if (Test-Path -LiteralPath $SevenZip) { $env:ELECTRON_BUILDER_7ZIP_PATH = $SevenZip }
$NsisCache = Join-Path $env:LOCALAPPDATA 'electron-builder/Cache/nsis'
$CachedNsis = Join-Path $NsisCache 'nsis-3.0.4.1-nsis-3.0.4.1'
$CachedNsisResources = Join-Path $NsisCache 'nsis-resources-3.4.1-nsis-resources-3.4.1'
if (Test-Path -LiteralPath $CachedNsis) { $env:ELECTRON_BUILDER_NSIS_DIR = $CachedNsis }
if (Test-Path -LiteralPath $CachedNsisResources) { $env:ELECTRON_BUILDER_NSIS_RESOURCES_DIR = $CachedNsisResources }
Push-Location (Join-Path $Root 'frontend')
try {
    $ElectronBuilderCli = Join-Path $Root 'frontend/node_modules/electron-builder/cli.js'
    if (-not (Test-Path -LiteralPath $ElectronBuilderCli)) { throw 'Run install.ps1 first to install frontend dependencies.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }

    $ElectronExe = Join-Path $Root 'frontend/node_modules/electron/dist/electron.exe'
    if (-not (Test-Path -LiteralPath $ElectronExe)) {
        $ElectronCache = Join-Path $env:TEMP 'MeetingAssistant-electron-cache'
        New-Item -ItemType Directory -Path $ElectronCache -Force | Out-Null
        $env:electron_config_cache = $ElectronCache
        & node (Join-Path $Root 'frontend/node_modules/electron/install.js')
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $ElectronExe)) {
            throw 'Electron runtime download failed. Retry the desktop build after checking the network connection.'
        }
    }
} finally { Pop-Location }

$PyInstaller = Join-Path (Split-Path -Parent $Python) 'pyinstaller.exe'
if (-not (Test-Path -LiteralPath $PyInstaller)) {
    & $Python -m pip install '.[desktop-build]'
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build dependencies could not be installed.' }
}

& $Python -m PyInstaller --noconfirm --clean --onedir --name MeetingAssistantBackend `
    --distpath $PyInstallerDist --workpath $PyInstallerWork --specpath $Assets --paths $Root `
    --collect-all faster_whisper --collect-all ctranslate2 --collect-all av `
    --collect-all soundcard --collect-all sounddevice `
    --add-data "$FrontendDist;frontend/dist" scripts/backend_entry.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed while packaging the local backend.' }

Copy-Item -Path (Join-Path $PyInstallerDist 'MeetingAssistantBackend/*') -Destination $BackendStage -Recurse -Force
Push-Location (Join-Path $Root 'frontend')
try {
    $Version = (Get-Content -LiteralPath 'package.json' -Raw | ConvertFrom-Json).version
    $InstallerName = "MeetingAssistant Setup $Version.exe"
    $BuildId = Get-Date -Format 'yyyyMMdd-HHmmss'
    $PackageOutputName = "desktop-package-$BuildId"
    $PackageOutputRelative = "../build-assets/$PackageOutputName"
    & npm.cmd run desktop:package:win -- "--config.directories.output=$PackageOutputRelative"
    if ($LASTEXITCODE -ne 0) { throw 'Electron NSIS installer build failed.' }

    $PackageOutput = Join-Path $Assets $PackageOutputName
    $BuiltInstaller = Join-Path $PackageOutput $InstallerName
    if (-not (Test-Path -LiteralPath $BuiltInstaller)) { throw "Expected installer was not produced: $BuiltInstaller" }
    $Release = Join-Path $Root 'release'
    New-Item -ItemType Directory -Path $Release -Force | Out-Null
    Copy-Item -LiteralPath $BuiltInstaller -Destination (Join-Path $Release $InstallerName) -Force

    $ResolvedAssets = (Resolve-Path -LiteralPath $Assets).Path.TrimEnd('\') + '\'
    $ResolvedPackageOutput = (Resolve-Path -LiteralPath $PackageOutput).Path
    if (-not $ResolvedPackageOutput.StartsWith($ResolvedAssets, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($ResolvedPackageOutput) -notlike 'desktop-package-*') {
        throw "Refusing to remove an unexpected packaging directory: $ResolvedPackageOutput"
    }
    try {
        Remove-Item -LiteralPath $ResolvedPackageOutput -Recurse -Force
    } catch {
        Write-Warning "Packaging succeeded, but Windows is using temporary output files, so they were kept at $ResolvedPackageOutput"
    }
} finally { Pop-Location }

Write-Host "Desktop installer created under $(Join-Path $Root 'release')." -ForegroundColor Green
