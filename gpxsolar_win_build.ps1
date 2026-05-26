# gpxsolar_win_build.ps1 — Build complet du launcher gpxsolar.exe
#
# 3 étapes :
#   1. PyInstaller onedir         -> dist_onedir/gpxsolar/    (la vraie app)
#   2. Compress-Archive (zip)     -> build/gpxsolar_bundle.zip
#   3. PyInstaller launcher       -> dist/gpxsolar.exe        (launcher léger)
#      + copie gpxsolar_bundle.zip à côté du .exe
#
# Mise à jour sans rebuild :
#   Ouvrir gpxsolar_bundle.zip -> _internal/ -> remplacer gpxsolar.py
#   (ou : python update_app.py)
#
# Usage :
#   PowerShell -ExecutionPolicy Bypass -File gpxsolar_win_build.ps1

$root  = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv  = Join-Path $env:USERPROFILE ".gpxsolar\venv"
$pyi   = Join-Path $venv "Scripts\pyinstaller.exe"

$onedirOut   = "$root\dist_onedir"
$bundleZip   = "$root\build\gpxsolar_bundle.zip"
$finalOut    = "$root\dist"
$finalExe    = "$finalOut\gpxsolar.exe"
$finalZip    = "$finalOut\gpxsolar_bundle.zip"   # zip à côté du .exe

if (-not (Test-Path $pyi)) {
    throw "PyInstaller introuvable : $pyi`n  Lance d'abord : .\setup_build_windows.ps1"
}

# ─────────────────────────────────────────────────────────────────────────────
# 1. PyInstaller onedir (la vraie app)
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[1/3] PyInstaller onedir (gpxsolar_win.spec)..." -ForegroundColor Cyan
$out = & $pyi "$root\gpxsolar_win.spec" `
    --noconfirm --clean `
    --distpath $onedirOut `
    --workpath "$root\build" 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { Write-Host $out; throw "PyInstaller onedir a echoue" }
($out -split "`n")[-4..-1] | ForEach-Object { "    $_" }

$onedirRoot = "$onedirOut\gpxsolar"
if (-not (Test-Path "$onedirRoot\gpxsolar.exe")) {
    throw "$onedirRoot\gpxsolar.exe introuvable apres build"
}
$onedirSize = (Get-ChildItem $onedirRoot -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("    Onedir : {0:N1} Mo" -f $onedirSize)

# ─────────────────────────────────────────────────────────────────────────────
# 2. Zip du onedir (contenu sans dossier parent — structure plate)
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[2/3] Compression onedir -> bundle.zip..." -ForegroundColor Cyan
if (Test-Path $bundleZip) { Remove-Item $bundleZip }
New-Item -ItemType Directory -Force -Path (Split-Path $bundleZip) | Out-Null
$sw = [System.Diagnostics.Stopwatch]::StartNew()
Compress-Archive -Path "$onedirRoot\*" -DestinationPath $bundleZip -CompressionLevel Optimal -Force
$sw.Stop()
$bundleSize = (Get-Item $bundleZip).Length / 1MB
Write-Host ("    Bundle : {0:N1} Mo en {1:N1}s" -f $bundleSize, $sw.Elapsed.TotalSeconds)

# ─────────────────────────────────────────────────────────────────────────────
# 3. PyInstaller launcher (léger — sans bundle embarqué)
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[3/3] PyInstaller launcher (gpxsolar_win_launcher.spec)..." -ForegroundColor Cyan
$out = & $pyi "$root\gpxsolar_win_launcher.spec" `
    --noconfirm --clean `
    --distpath $finalOut `
    --workpath "$root\build" 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { Write-Host $out; throw "PyInstaller launcher a echoue" }
($out -split "`n")[-4..-1] | ForEach-Object { "    $_" }

if (-not (Test-Path $finalExe)) { throw "$finalExe introuvable apres build" }

# Copier le zip à côté du .exe → remplaçable sans rebuilder
Copy-Item $bundleZip $finalZip -Force
Write-Host "    Bundle copie : $finalZip"

$finalSize    = (Get-Item $finalExe).Length / 1MB
$finalZipSize = (Get-Item $finalZip).Length / 1MB

Write-Host ""
Write-Host "=== BUILD TERMINE ===" -ForegroundColor Green
Write-Host "  Livrables :" -ForegroundColor Green
Write-Host ("    $finalExe  ({0:N1} Mo)" -f $finalSize)
Write-Host ("    $finalZip  ({0:N1} Mo)" -f $finalZipSize)
Write-Host ""
Write-Host "  Mise a jour sans rebuild :" -ForegroundColor Yellow
Write-Host "    Ouvrir gpxsolar_bundle.zip -> _internal\gpxsolar.py  (ou python update_app.py)"
