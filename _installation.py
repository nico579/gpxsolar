"""Installation de gpxsolar depuis la 1.5.0 : le programme livré tel quel,
ce qu'un lanceur <= 1.4 laissait derrière lui, ce que la 1.5.0 laisse dans
_internal sous la 1.6, l'environnement rendu aux programmes du système, et
la désinstallation.

Jusqu'à la 1.4, un petit lanceur contenait le programme zippé
(gpxsolar_bundle.zip), l'extrayait dans le dossier de données de l'OS
(%LOCALAPPDATA%\\gpxsolar, etc.), puis le lançait depuis son propre dossier.
Depuis la 1.5, l'archive livre directement le programme (dossier onedir, ou
GPXSOLAR.app sous macOS), comme celles de lidar2map, blink2video et
watch2notif. Jumeau des fonctions de lidar2map (_runtime_paths.
dossier_programme, _bootstrap_runtime.chemins_desinstallation,
desinstaller_lidar2map, nettoyer_ancienne_extraction et
retablir_environnement_systeme) ; le ménage des restes de Qt est propre à
gpxsolar, seul des quatre à avoir livré Qt tel quel.

Dépendances : déclarées une seule fois, dans requirements.in, et installées
depuis le verrou requirements.txt (versions exactes, empreintes SHA-256,
valable pour Windows, macOS et Linux). Les aides qui le lisent, plus bas,
sont les jumelles de celles de lidar2map (_bootstrap_runtime) : elles ne
peuvent pas vivre dans nico579-commons, puisque le bootstrap tourne avant
que la bibliothèque soit installée.

Bibliothèque standard seule : la désinstallation et le bootstrap des
dépendances passent avant l'installation de tout paquet.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

import _restes_qt

NOM_BUNDLE = "gpxsolar_bundle.zip"
MARQUE_EXTRACTION = ".bundle_sha"
# Écrit par PyInstaller dans _internal à chaque build, avec tout le reste :
# sa date est celle de la version en place (voir nettoyer_restes_de_qt).
REPERE_VERSION = "base_library.zip"
MARGE_RESTES_S = 3600

RACINE = Path(__file__).resolve().parent
# Dépendances directes (noms seuls), et leur verrou, à côté de gpxsolar.py.
DEPENDANCES = RACINE / "requirements.in"
VERROU = RACINE / "requirements.txt"
# Dans le venv du mode sources : empreinte du verrou qu'on y a installé. Un
# verrou changé (nouvelle version de gpxsolar) le fait réinstaller.
MARQUE_VERROU = "gpxsolar-verrou.sha256"


def dossier_programme(executable) -> Path:
    """Dossier du programme figé : celui de l'exécutable, ou celui qui
    contient GPXSOLAR.app sous macOS. Jusqu'à la 1.3, gpxsolar y rangeait son
    état (le lanceur y lançait le programme) : c'est là que la 1.4 et les
    suivantes le reprennent (voir _dossiers.preparer_etat)."""
    dossier = Path(executable).resolve().parent
    if (dossier.name == "MacOS" and dossier.parent.name == "Contents"
            and dossier.parent.parent.suffix == ".app"):
        return dossier.parent.parent.parent
    return dossier


def dossier_extraction(*, systeme, home, localappdata=None) -> Path:
    """Dossier où le lanceur d'une version <= 1.4 extrayait le programme."""
    home = Path(home)
    if systeme == "Windows":
        base = Path(localappdata) if localappdata else home / "AppData" / "Local"
        return base / "gpxsolar"
    if systeme == "Darwin":
        return home / "Library" / "Application Support" / "gpxsolar"
    return home / ".local" / "share" / "gpxsolar"


def chemins_desinstallation(*, systeme, home, localappdata=None):
    """Cibles de --desinstaller, sans accéder au disque."""
    return (
        (dossier_extraction(systeme=systeme, home=home, localappdata=localappdata),
         "ancienne extraction du lanceur (<= 1.4)"),
        (Path(home) / ".gpxsolar" / "venv", "venv Python"),
    )


def _taille(chemin: Path) -> int:
    total = 0
    for racine, _, fichiers in os.walk(chemin, followlinks=False):
        for nom in fichiers:
            try:
                total += (Path(racine) / nom).lstat().st_size
            except OSError:
                pass
    return total


def desinstaller(*, systeme, home, localappdata=None, executable=None,
                 supprimer_arbre=shutil.rmtree, ecrire=print) -> bool:
    """Supprime les cibles de chemins_desinstallation() ; True si complet.

    ``executable`` est le programme figé en cours : installé, par exemple,
    dans l'ancien dossier d'extraction, il n'est jamais supprimé par
    lui-même. L'état (gpxsolar-data) et les sorties (Documents/gpxsolar)
    restent en place."""
    programme = Path(executable).resolve() if executable else None
    complet = True
    total = 0
    ecrire("")
    ecrire("  ── gpxsolar uninstall ───────────────────────────────────")
    ecrire("")
    for chemin, label in chemins_desinstallation(
            systeme=systeme, home=home, localappdata=localappdata):
        if not chemin.exists():
            ecrire(f"  {label} : absent ({chemin})")
            continue
        if programme is not None and chemin.resolve() in programme.parents:
            ecrire(f"  {label} : kept, the running program lives there ({chemin})")
            continue
        taille = _taille(chemin)
        total += taille
        ecrire(f"  Removing {label} ({taille / 1e6:.0f} MB)")
        ecrire(f"    {chemin}")
        try:
            supprimer_arbre(chemin)
        except OSError as exc:
            complet = False
            ecrire(f"    ⚠ partial ({exc})")
            continue
        if chemin.exists():
            complet = False
            ecrire("    ⚠ partial")
        else:
            ecrire("    ✓ removed")
    ecrire("")
    ecrire(f"  {total / 1e6:.0f} MB freed.")
    ecrire("")
    ecrire("  Note: gpxsolar.py, the program folder (.exe/.app), settings and")
    ecrire("  outputs are not removed. Remove them manually if needed.")
    ecrire("")
    return complet


def nettoyer_ancienne_extraction(*, systeme, home, localappdata=None, executable):
    """Retire ce qu'un lanceur <= 1.4 a laissé, et rend la liste de ce qui a
    été retiré : le programme qu'il extrayait, et son bundle zippé resté à
    côté du programme quand la nouvelle archive a été décompressée
    par-dessus l'ancienne.

    Le dossier n'est retiré que s'il porte la marque du lanceur
    (.bundle_sha) et que le programme ne tourne pas depuis lui. Il est
    d'abord renommé : sous Windows, le renommage échoue tant qu'une ancienne
    instance y tourne encore, et le nettoyage attend alors le lancement
    suivant."""
    retires = []
    programme = Path(executable).resolve()
    zip_voisin = programme.parent / NOM_BUNDLE
    if zip_voisin.is_file():
        try:
            zip_voisin.unlink()
            retires.append(zip_voisin)
        except OSError:
            pass

    dossier = dossier_extraction(systeme=systeme, home=home, localappdata=localappdata)
    corbeille = dossier.with_name(dossier.name + ".ancienne-extraction")
    if corbeille.exists():   # reste d'un nettoyage interrompu
        shutil.rmtree(corbeille, ignore_errors=True)
    if not (dossier / MARQUE_EXTRACTION).is_file():
        return retires
    if dossier.resolve() in programme.parents:
        return retires
    try:
        os.rename(dossier, corbeille)
    except OSError:
        return retires
    shutil.rmtree(corbeille, ignore_errors=True)
    retires.append(dossier)
    return retires


def _plus_recente_modification(chemin: Path) -> float:
    """Date de modification la plus récente d'un fichier, d'un lien, ou des
    fichiers et liens d'un dossier ; celle des dossiers eux-mêmes ne compte
    pas, car elle suit le jour de la décompression plutôt que l'archive."""
    if not chemin.is_dir() or chemin.is_symlink():
        return chemin.lstat().st_mtime
    plus_recente = 0.0
    for racine, _, fichiers in os.walk(chemin, followlinks=False):
        for nom in fichiers:
            try:
                plus_recente = max(plus_recente, (Path(racine) / nom).lstat().st_mtime)
            except OSError:
                pass
    return plus_recente


def nettoyer_restes_de_qt(*, systeme, interne, marge_s=MARGE_RESTES_S):
    """Retire de _internal ce que la 1.5.0 y a laissé quand la 1.6 a été
    décompressée par-dessus, et rend la liste de ce qui a été retiré.

    Deux conditions, pour ne jamais toucher à ce que livre la version en
    place. Le nom doit figurer dans les listes de _restes_qt : rien d'autre
    ne peut partir. Et tous ses fichiers doivent dater d'au moins marge_s
    avant REPERE_VERSION : la décompression donne à chaque fichier la date
    de son archive (Explorateur, 7-Zip, tar) ou celle du jour, si bien
    qu'un nom qu'une version future livrerait de nouveau porte la date du
    reste et demeure. La date de création ne vaut rien ici, 7-Zip garde
    celle du fichier qu'il écrase ; et le repère est dans _internal plutôt
    que l'exécutable, qu'une signature pourrait dater après le reste.

    Une entrée occupée ou protégée reste en place pour le lancement
    suivant."""
    noms = _restes_qt.PAR_SYSTEME.get(systeme)
    if not noms:
        return []
    interne = Path(interne)
    try:
        limite = (interne / REPERE_VERSION).stat().st_mtime - marge_s
    except OSError:
        return []
    retires = []
    for nom in sorted(noms):
        chemin = interne / nom
        if not os.path.lexists(chemin):
            continue
        try:
            if _plus_recente_modification(chemin) >= limite:
                continue
            if chemin.is_dir() and not chemin.is_symlink():
                shutil.rmtree(chemin)
            else:
                chemin.unlink()
        except OSError:
            continue
        retires.append(chemin)
    return retires


def retablir_environnement_systeme(*, fige=None, plateforme=None, environ=None):
    """Rend aux programmes du système le LD_LIBRARY_PATH d'origine.

    Sous Linux, le bootloader de PyInstaller préfixe cette variable du dossier
    de ses bibliothèques (_internal) et garde l'ancienne valeur dans
    LD_LIBRARY_PATH_ORIG. Tout enfant en hérite : xdg-open et le navigateur
    qu'ouvre webbrowser depuis la 1.6.0 chargeaient alors les bibliothèques
    du binaire au lieu des leurs, et le systemd de Debian Trixie refuse une
    libcrypto plus ancienne que la sienne (constaté sur blink2video, issue
    #23, d'où ce rétablissement dans lidar2map et blink2video). C'est celui
    que recommande PyInstaller pour les programmes externes :
    https://pyinstaller.org/en/stable/runtime-information.html#ld-library-path-libpath-considerations

    Appelée par gpxsolar.py dès son démarrage, avant tout lancement de
    processus. Le chargeur d'un processus ne lit la variable qu'à son
    démarrage : la rétablir ne change rien pour celui-ci, ni pour une
    relance du binaire, que son bootloader préfixe de nouveau.
    """
    fige = getattr(sys, "frozen", False) if fige is None else fige
    plateforme = sys.platform if plateforme is None else plateforme
    environ = os.environ if environ is None else environ
    if not fige or plateforme in ("win32", "darwin"):
        return
    origine = environ.get("LD_LIBRARY_PATH_ORIG")
    if origine is not None:
        environ["LD_LIBRARY_PATH"] = origine
    else:
        # Variable absente avant le bootloader : il n'a rien gardé à rétablir.
        environ.pop("LD_LIBRARY_PATH", None)


def nom_normalise(nom: str) -> str:
    """Nom de distribution comparable (PEP 503) : « Pillow », « pillow » et
    « srtm.py » / « srtm-py » se valent."""
    return re.sub(r"[-_.]+", "-", nom).lower()


def dependances_directes(fichier: Path = DEPENDANCES, *, conditionnelles=False) -> list:
    """Noms des paquets de requirements.in, sans version.

    Un paquet qui porte un marqueur d'environnement (« ; sys_platform ... »)
    est conditionnel : absent à bon droit sur certains systèmes (numba sur
    les Mac Intel, faute de roue), il n'est rendu que sur demande. Le
    contrôle au démarrage ne l'exige donc pas, comme les dépendances dites
    optionnelles d'avant le verrou."""
    noms = []
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        ligne = ligne.split("#", 1)[0].strip()
        if not ligne or ligne.startswith("-"):
            continue
        if ";" in ligne and not conditionnelles:
            continue
        noms.append(re.split(r"[\s<>=!~;\[]", ligne, maxsplit=1)[0])
    return noms


def dependances_absentes(noms, distributions=None) -> list:
    """Ceux de ``noms`` qu'aucune distribution installée ne fournit.

    Lit les métadonnées des paquets installés, sans rien importer : pas de
    table paquet-module à tenir (Pillow s'importe PIL, srtm.py srtm), et
    aucun module lourd chargé au démarrage."""
    if distributions is None:
        import importlib.metadata
        distributions = importlib.metadata.distributions()
    installes = {nom_normalise(d.metadata["Name"] or "") for d in distributions}
    return [nom for nom in noms if nom_normalise(nom) not in installes]


def empreinte_verrou(verrou: Path = VERROU) -> str:
    return hashlib.sha256(verrou.read_bytes()).hexdigest()


def commande_installation(python, *options, verrou: Path = VERROU) -> list:
    """pip install du verrou, empreintes vérifiées."""
    return [str(python), "-m", "pip", "install", "-q", "--disable-pip-version-check",
            "--require-hashes", "-r", str(verrou), *options]
