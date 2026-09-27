# gpxsolar_win_build.ps1 : build de gpxsolar.exe (PyInstaller onedir)
#
# Une seule passe PyInstaller -> dist/gpxsolar/ (gpxsolar.exe + _internal/),
# le programme tel qu'il est livré. release.yml archive ensuite ce dossier.
#
# Jusqu'à la 1.4, deux passes de plus zippaient ce dossier et construisaient
# un lanceur onefile qui l'extrayait dans %LOCALAPPDATA% au premier
# lancement : deux exemplaires sur disque, et une extraction à attendre après
# chaque mise à jour. Depuis la 1.5, le dossier est livré tel quel, comme
# ceux de lidar2map, blink2video et watch2notif.
#
# Usage :
#   PowerShell -ExecutionPolicy Bypass -File gpxsolar_win_build.ps1

$root  = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv  = Join-Path $env:USERPROFILE ".gpxsolar\venv"
$pyi   = Join-Path $venv "Scripts\pyinstaller.exe"

$distOut = "$root\dist"
$appRoot = "$distOut\gpxsolar"

if (-not (Test-Path $pyi)) {
    throw "PyInstaller introuvable : $pyi`n  Lance d'abord : .\setup_build_windows.ps1"
}

Write-Host ""
Write-Host "PyInstaller onedir (gpxsolar_win.spec)..." -ForegroundColor Cyan
$out = & $pyi "$root\gpxsolar_win.spec" `
    --noconfirm --clean `
    --distpath $distOut `
    --workpath "$root\build" 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { Write-Host $out; throw "PyInstaller onedir a echoue" }
($out -split "`n")[-4..-1] | ForEach-Object { "    $_" }

if (-not (Test-Path "$appRoot\gpxsolar.exe")) {
    throw "$appRoot\gpxsolar.exe introuvable apres build"
}
$appSize = (Get-ChildItem $appRoot -Recurse -File | Measure-Object Length -Sum).Sum / 1MB

Write-Host ""
Write-Host "=== BUILD TERMINE ===" -ForegroundColor Green
Write-Host ("  $appRoot  ({0:N1} Mo)" -f $appSize)
