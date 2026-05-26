#!/usr/bin/env bash
# gpxsolar_linux_build.sh — Build complet du launcher gpxsolar (Linux)
#
# Miroir bash de gpxsolar_win_build.ps1. Réutilise la spec gpxsolar_win.spec
# (PyInstaller produit un ELF sous Linux, le nom _win est trompeur).
#
# 3 étapes :
#   1. PyInstaller onedir       -> dist_onedir/gpxsolar/    (la vraie app)
#   2. zip                      -> build/gpxsolar_bundle.zip
#   3. PyInstaller launcher     -> dist/gpxsolar            (launcher léger)
#      + copie gpxsolar_bundle.zip à côté du binaire
#
# Mise à jour sans rebuild :
#   Ouvrir gpxsolar_bundle.zip -> _internal/ -> remplacer gpxsolar.py
#
# Usage :
#   bash gpxsolar_linux_build.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.gpxsolar/venv"
PYI="$VENV/bin/pyinstaller"

ONEDIR_OUT="$ROOT/dist_onedir"
ONEDIR_ROOT="$ONEDIR_OUT/gpxsolar"
BUNDLE_ZIP="$ROOT/build/gpxsolar_bundle.zip"
FINAL_OUT="$ROOT/dist"
FINAL_BIN="$FINAL_OUT/gpxsolar"
FINAL_ZIP="$FINAL_OUT/gpxsolar_bundle.zip"

C="\033[0;36m"; G="\033[0;32m"; Y="\033[0;33m"; N="\033[0m"

# ── Prérequis ─────────────────────────────────────────────────────────────────
if [[ ! -x "$PYI" ]]; then
    echo "PyInstaller introuvable : $PYI" >&2
    echo "Lance d'abord : bash setup_build_linux.sh" >&2
    exit 1
fi
if ! command -v zip &>/dev/null; then
    echo "zip absent. Installer : sudo apt install zip" >&2
    exit 1
fi

# ── 1. PyInstaller onedir ─────────────────────────────────────────────────────
echo -e "\n${C}[1/3] PyInstaller onedir (gpxsolar_win.spec)...${N}"
"$PYI" "$ROOT/gpxsolar_win.spec" \
    --noconfirm --clean \
    --distpath "$ONEDIR_OUT" \
    --workpath "$ROOT/build"

if [[ ! -x "$ONEDIR_ROOT/gpxsolar" ]]; then
    echo "$ONEDIR_ROOT/gpxsolar introuvable apres build" >&2
    exit 1
fi
onedir_size=$(du -sm "$ONEDIR_ROOT" | cut -f1)
echo "    Onedir : ${onedir_size} Mo"

# ── 2. Zip du onedir (contenu sans dossier parent — structure plate) ──────────
echo -e "\n${C}[2/3] Compression onedir -> bundle.zip...${N}"
mkdir -p "$(dirname "$BUNDLE_ZIP")"
rm -f "$BUNDLE_ZIP"
t0=$(date +%s)
( cd "$ONEDIR_ROOT" && zip -rq "$BUNDLE_ZIP" . )
t1=$(date +%s)
bundle_size=$(du -m "$BUNDLE_ZIP" | cut -f1)
echo "    Bundle : ${bundle_size} Mo en $((t1 - t0))s"

# ── 3. PyInstaller launcher (léger — sans bundle embarqué) ────────────────────
echo -e "\n${C}[3/3] PyInstaller launcher (gpxsolar_win_launcher.spec)...${N}"
"$PYI" "$ROOT/gpxsolar_win_launcher.spec" \
    --noconfirm --clean \
    --distpath "$FINAL_OUT" \
    --workpath "$ROOT/build"

if [[ ! -x "$FINAL_BIN" ]]; then
    echo "$FINAL_BIN introuvable apres build" >&2
    exit 1
fi

# Copier le bundle à côté du binaire (séparé -> remplaçable sans rebuilder)
cp -f "$BUNDLE_ZIP" "$FINAL_ZIP"
echo "    Bundle copie : $FINAL_ZIP"

final_size=$(du -m "$FINAL_BIN" | cut -f1)
final_zip_size=$(du -m "$FINAL_ZIP" | cut -f1)

echo ""
echo -e "${G}=== BUILD TERMINE ===${N}"
echo -e "${G}  Livrables :${N}"
echo "    $FINAL_BIN  (${final_size} Mo)"
echo "    $FINAL_ZIP  (${final_zip_size} Mo)"
echo ""
echo -e "${Y}  Mise a jour sans rebuild :${N}"
echo "    Ouvrir gpxsolar_bundle.zip -> _internal/gpxsolar.py  (ou python update_app.py)"
