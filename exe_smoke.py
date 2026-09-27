#!/usr/bin/env python3
"""Épreuve de l'exécutable livré, avant publication (release.yml).

Toutes les autres suites tournent sur les sources. Ce script lance le
binaire tel qu'il sera publié, hors réseau, jumeau simplifié de
tests/exe_smoke.py de lidar2map :

  1. démarrage : « gpxsolar --version » répond, avec la version du code.
     C'est le vrai démarrage du programme figé (Python embarqué, _loader.py,
     gpxsolar.py et ses imports de tête), sans la fenêtre : Qt et
     QtWebEngine ne se chargent qu'avec l'interface, hors de portée d'un
     runner sans écran ;
  2. ménage de ce qu'un lanceur <= 1.4 laissait : son extraction, marquée
     .bundle_sha, et le gpxsolar_bundle.zip voisin du programme, posés avant
     le démarrage, ont disparu (_installation.nettoyer_ancienne_extraction) ;
  3. rien n'est écrit à côté du binaire.

Appelé par release.yml après « Package » sur chaque runner, et utilisable
en local :

    python exe_smoke.py dist/gpxsolar-windows-x86_64.zip

Stdlib uniquement. Le dossier personnel est remplacé par un dossier
temporaire (HOME, USERPROFILE, LOCALAPPDATA, APPDATA, GPXSOLAR_HOME) : un
lancement local ne touche ni l'installation ni les réglages réels.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

# Premier lancement d'un programme tout juste décompressé : l'antivirus
# (Windows) ou l'évaluation du .app (macOS) examinent ses fichiers.
DELAI_DEMARRAGE_S = 300
RACINE_DEPOT = Path(__file__).resolve().parent


class Echec(Exception):
    """Contrôle en échec : message affiché, code de sortie 1."""


def version_du_code() -> str:
    texte = (RACINE_DEPOT / "gpxsolar.py").read_text(encoding="utf-8")
    trouve = re.search(r'^VERSION\s*=\s*"([^"]+)"', texte, re.M)
    if not trouve:
        raise Echec("constante VERSION introuvable dans gpxsolar.py")
    return trouve.group(1)


def extraire(archive: Path, dest: Path) -> None:
    if archive.name.endswith(".tar.gz"):
        with tarfile.open(archive) as tar:
            tar.extractall(dest, filter="data")
    elif sys.platform == "darwin":
        # ditto conserve permissions, liens symboliques et attributs du .app.
        subprocess.run(["ditto", "-x", "-k", str(archive), str(dest)], check=True)
    else:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)


def trouver_programme(dest: Path) -> Path:
    app = dest / "GPXSOLAR.app"
    if app.is_dir():
        programme = app / "Contents" / "MacOS" / "gpxsolar"
        ressources = app / "Contents" / "Frameworks"
    else:
        programme = next((candidat for motif in ("*/gpxsolar.exe", "*/gpxsolar")
                          for candidat in sorted(dest.glob(motif))
                          if candidat.is_file()), None)
        if programme is None:
            raise Echec(f"aucun programme gpxsolar dans {dest}")
        ressources = programme.parent / "_internal"
    if not programme.is_file():
        raise Echec(f"programme introuvable : {programme}")
    if not ressources.is_dir():
        # Archive d'un lanceur <= 1.4 : un binaire et un bundle zippé.
        raise Echec(f"ressources embarquées introuvables : {ressources}")
    return programme


def dossier_extraction(home: Path) -> Path:
    """Même chemin que _installation.dossier_extraction()."""
    if sys.platform == "win32":
        return home / "AppData" / "Local" / "gpxsolar"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "gpxsolar"
    return home / ".local" / "share" / "gpxsolar"


def environnement(racine: Path) -> tuple[dict, Path]:
    home = racine / "home"
    for sous_dossier in ("AppData/Local", "AppData/Roaming"):
        (home / sous_dossier).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(HOME=str(home), USERPROFILE=str(home),
               LOCALAPPDATA=str(home / "AppData" / "Local"),
               APPDATA=str(home / "AppData" / "Roaming"),
               GPXSOLAR_HOME=str(racine / "donnees"))
    env.pop("GPXSOLAR_CHILD", None)
    return env, home


def poser_restes_du_lanceur(home: Path, programme: Path) -> list[Path]:
    extraction = dossier_extraction(home)
    (extraction / "_internal").mkdir(parents=True)
    (extraction / ".bundle_sha").write_text("0" * 64 + "\n0\n", encoding="utf-8")
    restes = [extraction]
    # Sous macOS, le bundle vivait dans le .app, remplacé d'un bloc ; écrire
    # dans le nouveau casserait d'ailleurs sa signature.
    if programme.parent.name != "MacOS":
        bundle = programme.parent / "gpxsolar_bundle.zip"
        bundle.write_bytes(b"PK\x05\x06" + bytes(18))     # zip vide
        restes.append(bundle)
    return restes


def smoke(archive: Path, racine: Path) -> None:
    dest = racine / "archive"
    print(f"== extraction de {archive.name}", flush=True)
    extraire(archive, dest)
    programme = trouver_programme(dest)
    avant = {p.name for p in programme.parent.iterdir()}
    env, home = environnement(racine)
    restes = poser_restes_du_lanceur(home, programme)
    print(f"   programme : {programme}", flush=True)

    print("\n== 1. démarrage (--version)", flush=True)
    attendue = version_du_code()
    try:
        resultat = subprocess.run(
            [str(programme), "--version"], env=env, cwd=racine,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            errors="replace", timeout=DELAI_DEMARRAGE_S,
        )
    except subprocess.TimeoutExpired:
        raise Echec(f"--version sans réponse après {DELAI_DEMARRAGE_S} s") from None
    sortie = (resultat.stdout or "") + (resultat.stderr or "")
    if resultat.returncode != 0 or f"gpxsolar {attendue}" not in sortie:
        raise Echec(f"--version : code {resultat.returncode}, attendu "
                    f"« gpxsolar {attendue} »\n{sortie[-3000:]}")
    print(f"   OK : gpxsolar {attendue}", flush=True)

    print("\n== 2. ménage de ce que laissait un lanceur <= 1.4", flush=True)
    encore = [str(chemin) for chemin in restes if chemin.exists()]
    if encore:
        raise Echec(f"restes du lanceur toujours présents : {', '.join(encore)}"
                    f"\n{sortie[-3000:]}")
    print(f"   OK : {len(restes)} reste(s) retiré(s) au démarrage", flush=True)

    print("\n== 3. rien d'écrit à côté du binaire", flush=True)
    apres = {p.name for p in programme.parent.iterdir()}
    nouveaux = sorted(apres - avant)
    if nouveaux:
        raise Echec(f"écrits à côté du binaire : {', '.join(nouveaux)}")
    print("   OK", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("archive", type=Path,
                        help="archive publiée (.zip, ou .tar.gz sous Linux)")
    parser.add_argument("--garder", action="store_true",
                        help="conserver le dossier temporaire (diagnostic)")
    args = parser.parse_args(argv)
    if not args.archive.is_file():
        print(f"archive introuvable : {args.archive}", file=sys.stderr)
        return 2
    racine = Path(tempfile.mkdtemp(prefix="gpxsolar_exe_smoke_"))
    debut = time.monotonic()
    try:
        smoke(args.archive.resolve(), racine)
    except Echec as exc:
        print(f"\nÉCHEC : {exc}", flush=True)
        return 1
    finally:
        if args.garder:
            print(f"\nDossier conservé : {racine}")
        else:
            shutil.rmtree(racine, ignore_errors=True)
    print(f"\nÉpreuve du binaire : OK en {time.monotonic() - debut:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
