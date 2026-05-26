# setup_build_windows.ps1 — Prepare un PC Windows pour builder gpxsolar.exe
#
# 1. Installe Python 3.12 si absent (via winget ou python.org)
# 2. Lance gpxsolar.py --installer-deps -> cree ~/.gpxsolar/venv + toutes les deps
# 3. Installe PyInstaller dans ce venv
#
# (Contrairement a lidar2map, gpxsolar n'a PAS besoin de JRE ni d'osmosis.)
#
# Usage (PowerShell) :
#   Unblock-File .\setup_build_windows.ps1
#   .\setup_build_windows.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VENV = "$env:USERPROFILE\.gpxsolar\venv"

function ok($msg)      { Write-Host "  [OK] $msg" -ForegroundColor Green }
function warn($msg)    { Write-Host "  [!!] $msg" -ForegroundColor Yellow }
function step($n,$msg) { Write-Host "" ; Write-Host "[$n] $msg" -ForegroundColor Cyan }

# -- 1. Python 3.12 ------------------------------------------------------------
step "1/3" "Python 3.12"

$pyOk = $false
try {
    $ver = & python --version 2>&1
    if ($ver -match "3\.1[12]") { $pyOk = $true; ok "Python trouve : $ver" }
} catch {}

if (-not $pyOk) {
    if (Get-Command "winget" -ErrorAction SilentlyContinue) {
        Write-Host "  Installation via winget..."
        winget install --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    } else {
        $installer = "$env:TEMP\python-3.12.10-amd64.exe"
        Write-Host "  Telechargement Python 3.12..."
        Invoke-WebRequest "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe" `
            -OutFile $installer -UseBasicParsing
        Start-Process $installer -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1" -Wait
        Remove-Item $installer
    }
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" `
              + [System.Environment]::GetEnvironmentVariable("Path","User")
    ok "Python 3.12 installe"
}

# -- 2. Bootstrap dependances --------------------------------------------------
step "2/3" "Bootstrap des dependances via gpxsolar.py"
Write-Host "  Lancement avec --installer-deps (cree ~/.gpxsolar/venv + deps)..."
& python "$ScriptDir\gpxsolar.py" --installer-deps

if (-not (Test-Path "$VENV\Scripts\pip.exe")) {
    Write-Host ""
    Write-Host "  ERREUR : venv attendu introuvable a $VENV" -ForegroundColor Red
    Write-Host "  --installer-deps aurait du le creer. Voir le log ci-dessus."
    exit 1
}
ok "Dependances installees dans $VENV"

# -- 3. PyInstaller ------------------------------------------------------------
step "3/3" "PyInstaller"
& "$VENV\Scripts\pip.exe" install --quiet pyinstaller
$pyiVer = & "$VENV\Scripts\pyinstaller.exe" --version
ok "PyInstaller $pyiVer"

Write-Host ""
ok "Setup termine. Pour builder :"
Write-Host "    .\gpxsolar_win_build.ps1"
