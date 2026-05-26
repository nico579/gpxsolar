#!/usr/bin/env bash
# setup_build_mac.sh — Prepare un Mac ARM64 pour builder GPXSOLAR.app
#
# 1. Installe Python 3.12 si absent (depuis python.org)
# 2. Lance gpxsolar.py --installer-deps -> cree ~/.gpxsolar/venv + toutes les
#    deps (dont pyobjc + PyQt6/WebEngine pour le backend GUI)
# 3. Installe PyInstaller
#
# (Contrairement a lidar2map, gpxsolar n'a PAS besoin de JRE ni d'osmosis.)
#
# Usage : bash setup_build_mac.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.gpxsolar/venv"

G="\033[0;32m"; Y="\033[0;33m"; N="\033[0m"
ok()   { echo -e "${G}  OK $*${N}"; }
warn() { echo -e "${Y}  !! $*${N}"; }
step() { echo -e "\n${G}[$1]${N} $2"; }

# -- 1. Python 3.12 ------------------------------------------------------------
step "1/3" "Python 3.12"
_python=""
for p in python3.12 \
          /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 \
          /opt/homebrew/bin/python3.12; do
    command -v "$p" &>/dev/null && { _python="$p"; break; }
done

if [[ -n "$_python" ]]; then
    ok "$($_python --version) -> $_python"
else
    _pkg="python-3.12.10-macos11.pkg"
    echo "  Telechargement Python 3.12..."
    curl -L --progress-bar \
        "https://www.python.org/ftp/python/3.12.10/$_pkg" -o "/tmp/$_pkg"
    sudo installer -pkg "/tmp/$_pkg" -target /
    rm -f "/tmp/$_pkg"
    _python="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12"
    ok "$($_python --version)"
fi

# -- 2. Bootstrap dependances --------------------------------------------------
step "2/3" "Bootstrap des dependances via gpxsolar.py"
echo "  Lancement avec --installer-deps..."
"$_python" "$SCRIPT_DIR/gpxsolar.py" --installer-deps

if [[ ! -f "$VENV/bin/pip" ]]; then
    echo ""
    echo "  ERREUR : venv attendu introuvable a $VENV"
    echo "  --installer-deps aurait du le creer. Voir le log ci-dessus."
    exit 1
fi
ok "Dependances installees dans $VENV"

# -- 3. PyInstaller ------------------------------------------------------------
step "3/3" "PyInstaller"
"$VENV/bin/pip" install --quiet --disable-pip-version-check pyinstaller
ok "PyInstaller $("$VENV/bin/pyinstaller" --version)"

echo ""
ok "Setup termine. Pour builder :"
echo "    bash gpxsolar_mac_build.sh"
