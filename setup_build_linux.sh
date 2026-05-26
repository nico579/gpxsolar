#!/usr/bin/env bash
# setup_build_linux.sh — Prépare un Ubuntu/Debian pour builder gpxsolar
#
# 3 étapes (miroir de setup_build_windows.ps1 / setup_build_mac.sh) :
#   1. python3.12 + python3.12-venv via apt
#      (python3.12-venv est un paquet séparé sur Debian/Ubuntu — sans lui,
#       la création de venv est impossible)
#   2. --installer-deps → toutes les dépendances Python dans ~/.gpxsolar/venv
#      (y compris PyQt6 + WebEngine pour le backend GUI Linux)
#   3. PyInstaller dans ce venv
#
# (Contrairement à lidar2map, gpxsolar n'a PAS besoin de JRE ni d'osmosis.)
#
# Usage : bash setup_build_linux.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.gpxsolar/venv"

G="\033[0;32m"; N="\033[0m"
ok()   { echo -e "${G}  ✓ $*${N}"; }
step() { echo -e "\n${G}[$1]${N} $2"; }

# ── 1. Python 3.12 + venv ─────────────────────────────────────────────────────
step "1/3" "Python 3.12 + python3.12-venv"

if ! command -v python3.12 &>/dev/null; then
    echo "  Python 3.12 absent — ajout du PPA deadsnakes..."
    sudo apt install -y software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt update -qq
fi

# python3.12-venv est OBLIGATOIRE sur Debian/Ubuntu (paquet séparé)
sudo apt install -y python3.12 python3.12-venv
ok "$(python3.12 --version)"

# ── 2. gpxsolar.py → bootstrap automatique de toutes les dépendances ──────────
step "2/3" "Bootstrap des dépendances via gpxsolar.py"
echo "  Lancement avec --installer-deps (crée ~/.gpxsolar/venv + deps, dont PyQt6)..."
python3.12 "$SCRIPT_DIR/gpxsolar.py" --installer-deps
ok "Dépendances installées dans $VENV"

# ── 3. PyInstaller ────────────────────────────────────────────────────────────
step "3/3" "PyInstaller"
"$VENV/bin/pip" install --quiet --disable-pip-version-check pyinstaller
ok "PyInstaller $("$VENV/bin/pyinstaller" --version)"

echo ""
ok "Setup terminé. Lance maintenant :"
echo "    bash gpxsolar_linux_build.sh"
echo "  → dist/gpxsolar + dist/gpxsolar_bundle.zip"
