#!/usr/bin/env bash
# gpxsolar_mac_build.sh : build de GPXSOLAR.app (PyInstaller onedir + .app)
#
# 3 étapes (miroir de lidar2map_mac_build.sh) :
#   1. PyInstaller                -> dist/GPXSOLAR.app   (le programme livré)
#   2. signature ad hoc du .app complet, vérifiée
#   3. archive ditto              -> dist/gpxsolar-macos-<arch>.zip
#      (arm64 sur Apple Silicon, x86_64 sur Intel)
#
# Jusqu'à la 1.4, GPXSOLAR.app était un lanceur qui contenait le programme
# zippé et l'extrayait dans ~/Library/Application Support/gpxsolar au premier
# lancement. Depuis la 1.5, le .app est le programme lui-même, comme ceux de
# lidar2map.
#
# Usage :
#   bash gpxsolar_mac_build.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.gpxsolar/venv"
PYI="$VENV/bin/pyinstaller"

if [ ! -x "$PYI" ]; then
    echo "ERREUR : $PYI introuvable."
    echo "  Lance d'abord :  bash setup_build_mac.sh"
    exit 1
fi

DIST_OUT="$ROOT/dist"
BUILD_DIR="$ROOT/build"
FINAL_APP="$DIST_OUT/GPXSOLAR.app"

# Archi du livrable : celle du Python du venv, pas celle du shell. PyInstaller
# produit un binaire pour l'interpreteur qu'il utilise ; sous Rosetta, `uname -m`
# mentirait (x86_64 alors que le venv peut etre arm64, ou l'inverse).
ARCH="$("$VENV/bin/python" -c 'import platform; print(platform.machine())')"
echo "Architecture cible : $ARCH"

# ── 1. PyInstaller : dossier onedir, puis .app (BUNDLE de gpxsolar_mac.spec) ─
echo ""
echo "[1/3] PyInstaller (gpxsolar_mac.spec)..."
"$PYI" "$ROOT/gpxsolar_mac.spec" \
    --noconfirm --clean \
    --distpath "$DIST_OUT" \
    --workpath "$BUILD_DIR"

if [ ! -x "$FINAL_APP/Contents/MacOS/gpxsolar" ]; then
    echo "ERREUR : $FINAL_APP/Contents/MacOS/gpxsolar introuvable apres build"
    exit 1
fi
# Le dossier onedir intermédiaire, déjà recopié dans le .app.
rm -rf "$DIST_OUT/gpxsolar"

# ── 2. Signature du .app complet ─────────────────────────────────────────────
# PyInstaller signe déjà le .app à la fin de BUNDLE. Le signer de nouveau en
# profondeur, puis le vérifier, garantit un sceau valide sur tout son contenu,
# comme pour lidar2map. Jusqu'à la 1.4, le bundle zippé copié
# après coup dans Contents/Resources rompait ce sceau, et macOS déclarait
# alors l'application endommagée une fois l'archive téléchargée.
echo ""
echo "[2/3] Signature ad hoc du bundle complet..."
codesign --force --deep --all-architectures --sign - "$FINAL_APP"
codesign --verify --deep --strict --verbose=2 "$FINAL_APP"
FINAL_SIZE=$(du -sm "$FINAL_APP" | cut -f1)

# ── 3. Archive zip distribuable (ditto preserve perms + symlinks + xattrs) ───
RELEASE_ZIP="$DIST_OUT/gpxsolar-macos-$ARCH.zip"
echo ""
echo "[3/3] Archive distribution (ditto)..."
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
echo "  Note : signature ad hoc valide, mais application non notarisee :"
echo "  macOS affichera une alerte Gatekeeper au premier lancement. Pour"
echo "  contourner :"
echo "    xattr -dr com.apple.quarantine \"$FINAL_APP\""
echo "  Ou clic droit -> Ouvrir -> Ouvrir quand meme."
