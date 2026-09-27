"""Installation de gpxsolar depuis la 1.5.0 : le programme livré tel quel,
ce qu'un lanceur <= 1.4 laissait derrière lui, et la désinstallation.

Jusqu'à la 1.4, un petit lanceur contenait le programme zippé
(gpxsolar_bundle.zip), l'extrayait dans le dossier de données de l'OS
(%LOCALAPPDATA%\\gpxsolar, etc.), puis le lançait depuis son propre dossier.
Depuis la 1.5, l'archive livre directement le programme (dossier onedir, ou
GPXSOLAR.app sous macOS), comme celles de lidar2map, blink2video et
watch2notif. Jumeau des fonctions de lidar2map (_runtime_paths.
dossier_programme, _bootstrap_runtime.chemins_desinstallation,
desinstaller_lidar2map et nettoyer_ancienne_extraction).

Bibliothèque standard seule : la désinstallation passe avant le bootstrap
des dépendances.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

NOM_BUNDLE = "gpxsolar_bundle.zip"
MARQUE_EXTRACTION = ".bundle_sha"


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
