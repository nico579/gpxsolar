# _loader.py — Entry point du binaire PyInstaller (macOS/Linux/Windows)
#
# Ce fichier est compilé dans le binaire PyInstaller et NE CHANGE JAMAIS.
# Il se contente de trouver gpxsolar.py dans _internal/ et de l'exécuter.
#
# gpxsolar.py est ainsi livré comme fichier texte dans _internal/. C'était le
# support du patch sans reconstruction (update_app.py), retiré le 26 septembre
# 2026 comme sur lidar2map : toute livraison passe désormais par une release
# reconstruite. Le mécanisme reste en place, sans autre usage.

import sys
import runpy
from pathlib import Path

# En mode PyInstaller onedir, sys._MEIPASS pointe vers _internal/
# En mode développement (python gpxsolar.py direct), _MEIPASS absent.
_base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
_script = _base / "gpxsolar.py"

# Fallback : chercher à côté du binaire (structure pré-PyInstaller-6)
if not _script.exists():
    _script = Path(sys.executable).parent / "_internal" / "gpxsolar.py"

if not _script.exists():
    print(f"ERREUR : gpxsolar.py introuvable dans {_base}")
    print("  Vérifiez que _internal/gpxsolar.py est présent dans le bundle.")
    sys.exit(1)

# sys.argv[0] doit pointer vers le script pour que __file__ soit cohérent
sys.argv[0] = str(_script)

# runpy.run_path exécute le script en lui donnant __file__ et
# __name__ == '__main__', comme si on avait fait `python gpxsolar.py`.
runpy.run_path(str(_script), run_name="__main__")
