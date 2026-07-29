#!/usr/bin/env bash
# gpxsolar_mac_build.sh — Build complet du launcher GPXSOLAR.app
#
# 4 étapes (miroir de gpxsolar_win_build.ps1) :
#   1. PyInstaller onedir         -> dist_onedir/gpxsolar/  (la vraie app)
#   2. zip                        -> build/gpxsolar_bundle.zip
#   3. PyInstaller launcher .app  -> dist/GPXSOLAR.app       (launcher léger)
#      + copie gpxsolar_bundle.zip dans Contents/Resources/
#   4. Archive zip distribuable (ditto) -> dist/gpxsolar-macos-<arch>.zip
#      (arm64 sur Apple Silicon, x86_64 sur Intel)
#
# Usage :
#   bash gpxsolar_mac_build.sh
#
# Comportement du livrable :
#   - Premier lancement : extraction dans ~/Library/Application Support/gpxsolar/ (~5-10 s)
#   - Lancements suivants : skip extract si SHA bundle inchangé (~1 s)
#   - Mise à jour (nouveau .app livré) : SHA différent -> ré-extraction propre

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.gpxsolar/venv"
PYI="$VENV/bin/pyinstaller"

if [ ! -x "$PYI" ]; then
    echo "ERREUR : $PYI introuvable."
    echo "  Lance d'abord :  bash setup_build_mac.sh"
    exit 1
fi

ONEDIR_OUT="$ROOT/dist_onedir"
ONEDIR_ROOT="$ONEDIR_OUT/gpxsolar"
BUILD_DIR="$ROOT/build"
BUNDLE_ZIP="$BUILD_DIR/gpxsolar_bundle.zip"
FINAL_OUT="$ROOT/dist"
FINAL_APP="$FINAL_OUT/GPXSOLAR.app"

# Archi du livrable : celle du Python du venv, pas celle du shell. PyInstaller
# produit un binaire pour l'interpreteur qu'il utilise ; sous Rosetta, `uname -m`
# mentirait (x86_64 alors que le venv peut etre arm64, ou l'inverse).
ARCH="$("$VENV/bin/python" -c 'import platform; print(platform.machine())')"
echo "Architecture cible : $ARCH"

# ── 1. PyInstaller onedir ─────────────────────────────────────────────────────
echo ""
echo "[1/4] PyInstaller onedir (gpxsolar_mac.spec)..."
"$PYI" "$ROOT/gpxsolar_mac.spec" \
    --noconfirm --clean \
    --distpath "$ONEDIR_OUT" \
    --workpath "$BUILD_DIR"

if [ ! -f "$ONEDIR_ROOT/gpxsolar" ]; then
    echo "ERREUR : $ONEDIR_ROOT/gpxsolar introuvable apres build"
    exit 1
fi
ONEDIR_SIZE=$(du -sm "$ONEDIR_ROOT" | cut -f1)
echo "    Onedir : ${ONEDIR_SIZE} Mo"

# ── 2. Zip du onedir ──────────────────────────────────────────────────────────
echo ""
echo "[2/4] Compression onedir -> bundle.zip..."
mkdir -p "$BUILD_DIR"
rm -f "$BUNDLE_ZIP"
START_TS=$(date +%s)
# ditto sans --keepParent : zippe le CONTENU de gpxsolar/ (pas le dossier).
cd "$ONEDIR_ROOT"
ditto -c -k . "$BUNDLE_ZIP"
cd "$ROOT"
END_TS=$(date +%s)
BUNDLE_SIZE=$(du -sm "$BUNDLE_ZIP" | cut -f1)
echo "    Bundle : ${BUNDLE_SIZE} Mo en $((END_TS - START_TS))s"

# ── 3. PyInstaller launcher .app ─────────────────────────────────────────────
echo ""
echo "[3/4] PyInstaller launcher .app (gpxsolar_mac_launcher.spec)..."
"$PYI" "$ROOT/gpxsolar_mac_launcher.spec" \
    --noconfirm --clean \
    --distpath "$FINAL_OUT" \
    --workpath "$BUILD_DIR"

if [ ! -d "$FINAL_APP" ]; then
    echo "ERREUR : $FINAL_APP introuvable apres build launcher"
    exit 1
fi

# Copier le bundle dans Contents/Resources/ (séparé -> remplaçable sans rebuild)
echo "  Copie du bundle dans Contents/Resources/..."
mkdir -p "$FINAL_APP/Contents/Resources"
cp "$BUNDLE_ZIP" "$FINAL_APP/Contents/Resources/gpxsolar_bundle.zip"
echo "  -> $FINAL_APP/Contents/Resources/gpxsolar_bundle.zip"

FINAL_SIZE=$(du -sm "$FINAL_APP" | cut -f1)

# Supprimer l'exe brut intermédiaire (déjà dans GPXSOLAR.app/Contents/MacOS/)
rm -f "$FINAL_OUT/gpxsolar"

# ── 4. Archive zip distribuable (ditto preserve perms + symlinks + xattrs) ───
RELEASE_ZIP="$FINAL_OUT/gpxsolar-macos-$ARCH.zip"
echo ""
echo "[4/4] Archive distribution (ditto)..."
rm -f "$RELEASE_ZIP"
ditto -c -k --keepParent "$FINAL_APP" "$RELEASE_ZIP"
ZIP_SIZE=$(du -sm "$RELEASE_ZIP" | cut -f1)
ZIP_SHA=$(shasum -a 256 "$RELEASE_ZIP" | awk '{print $1}')

echo ""
echo "=== BUILD TERMINE ==="
echo "  Livrables :"
echo "    $FINAL_APP   (${FINAL_SIZE} Mo)"
echo "    $RELEASE_ZIP (${ZIP_SIZE} Mo)"
echo "    sha256       $ZIP_SHA"
echo ""
echo "  Note : .app non signe -> macOS affichera une alerte Gatekeeper au"
echo "  premier lancement. Pour contourner :"
echo "    xattr -dr com.apple.quarantine \"$FINAL_APP\""
echo "  Ou clic droit -> Ouvrir -> Ouvrir quand meme."
