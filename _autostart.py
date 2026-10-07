"""Lancement automatique de gpxsolar (mode serveur web, --serve-gui) à l'ouverture de
la session de l'utilisateur, selon le système. Le mécanisme est celui des trois autres
applications, et le même code : nico579_commons.demarrage (raccourci .lnk dans le
dossier Démarrage sous Windows, service systemd utilisateur sous Linux, agent launchd
sous macOS). Ne reste ici que ce qui est propre à gpxsolar : la commande à lancer et
son dossier. La case « Démarrer automatiquement avec le système » du panneau Réglages
(⚙) est celle du commun (demarrage.routes), branchée sur entree().

Un lancement sans argument sert déjà l'interface ; --no-browser empêche seulement le
navigateur de s'ouvrir tout seul à l'ouverture de session (le serveur, lui, démarre, et
l'icône de la zone de notification permet de l'ouvrir).
"""
import platform
import sys
from pathlib import Path

from nico579_commons import demarrage

LINUX_SERVICE_NAME = "gpxsolar.service"
MAC_LABEL = "com.nico.gpxsolar"


def frozen() -> bool:
    """Vrai lorsque le programme tourne depuis un bundle PyInstaller."""
    return bool(getattr(sys, "frozen", False))


# __file__ pointe vers le dossier d'extraction temporaire de PyInstaller une fois figé,
# pas vers le dossier de l'exécutable.
PROJECT_DIR = Path(sys.executable if frozen() else __file__).resolve().parent


def commande() -> list:
    """Commande lancée à l'ouverture de session : le serveur web sans navigateur. Figé,
    le programme en cours est celui à relancer (l'archive le livre tel quel)."""
    if frozen():
        return [str(Path(sys.executable)), "--serve-gui", "--no-browser"]
    if platform.system() == "Windows":
        return [str(Path(sys.executable).with_name("pythonw.exe")),
                str(PROJECT_DIR / "gpxsolar.py"), "--serve-gui", "--no-browser"]
    return [sys.executable, str(PROJECT_DIR / "gpxsolar.py"), "--serve-gui", "--no-browser"]


def dossier_lancement() -> Path:
    """Dossier courant du lancement : celui du programme une fois figé, celui des
    sources sinon (pythonw.exe vit ailleurs)."""
    return Path(commande()[0]).parent if frozen() else PROJECT_DIR


def entree() -> demarrage.Entree:
    """L'entrée de démarrage de gpxsolar."""
    return demarrage.Entree(
        "gpxsolar", tuple(commande()), dossier_lancement(),
        "gpxsolar (interface web locale, --serve-gui)", label_macos=MAC_LABEL,
        apres_session_graphique=True, attente_relance_s=10)
