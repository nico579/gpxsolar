#!/usr/bin/env bash
# gpxsolar_linux_build.sh : build de gpxsolar (Linux, PyInstaller onedir)
#
# Miroir bash de gpxsolar_win_build.ps1. Réutilise la spec gpxsolar_win.spec
# (PyInstaller produit un ELF sous Linux, le nom _win est trompeur).
#
# Une seule passe PyInstaller -> dist/gpxsolar/ (gpxsolar + _internal/), le
# programme tel qu'il est livré. release.yml archive ensuite ce dossier.
# Jusqu'à la 1.4, un lanceur l'extrayait dans ~/.local/share au premier
# lancement ; depuis la 1.5, le dossier est livré tel quel.
#
# Usage :
#   bash gpxsolar_linux_build.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.gpxsolar/venv"
PYI="$VENV/bin/pyinstaller"

DIST_OUT="$ROOT/dist"
APP_ROOT="$DIST_OUT/gpxsolar"

C="\033[0;36m"; G="\033[0;32m"; N="\033[0m"

# ── Prérequis ─────────────────────────────────────────────────────────────────
if [[ ! -x "$PYI" ]]; then
    echo "PyInstaller introuvable : $PYI" >&2
    echo "Lance d'abord : bash setup_build_linux.sh" >&2
    exit 1
fi

# ── PyInstaller onedir ────────────────────────────────────────────────────────
echo -e "\n${C}PyInstaller onedir (gpxsolar_win.spec)...${N}"
"$PYI" "$ROOT/gpxsolar_win.spec" \
    --noconfirm --clean \
    --distpath "$DIST_OUT" \
    --workpath "$ROOT/build"

if [[ ! -x "$APP_ROOT/gpxsolar" ]]; then
    echo "$APP_ROOT/gpxsolar introuvable apres build" >&2
    exit 1
fi
app_size=$(du -sm "$APP_ROOT" | cut -f1)

echo ""
echo -e "${G}=== BUILD TERMINE ===${N}"
echo "    $APP_ROOT  (${app_size} Mo)"
