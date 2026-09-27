#!/usr/bin/env python3
"""Épreuve de l'exécutable livré, avant publication (release.yml).

Toutes les autres suites tournent sur les sources. Ce script lance le
binaire tel qu'il sera publié, hors réseau, jumeau simplifié de
tests/exe_smoke.py de lidar2map :

  1. démarrage : « gpxsolar --version » répond, avec la version du code.
     C'est le vrai démarrage du programme figé (Python embarqué, _loader.py,
     gpxsolar.py et ses imports de tête) ;
  2. ménage de ce qu'un lanceur <= 1.4 laissait : son extraction, marquée
     .bundle_sha, et le gpxsolar_bundle.zip voisin du programme, posés avant
     le démarrage, ont disparu (_installation.nettoyer_ancienne_extraction) ;
  3. l'interface : « --serve-gui --no-browser --no-tray » démarre le serveur
     web, /api/init répond "app": "gpxsolar" avec la version du code, la
     page, ses fichiers et le journal sont servis. Depuis la 1.6.0, qui a
     remplacé la fenêtre Qt par ce serveur, l'interface est enfin à portée
     d'un runner sans écran ;
  4. rien n'est écrit à côté du binaire, ni par l'un ni par l'autre.

Appelé par release.yml après « Package » sur chaque runner, et utilisable
en local :

    python exe_smoke.py dist/gpxsolar-windows-x86_64.zip

Stdlib uniquement. Le dossier personnel est remplacé par un dossier
temporaire (HOME, USERPROFILE, LOCALAPPDATA, APPDATA, GPXSOLAR_HOME) : un
lancement local ne touche ni l'installation ni les réglages réels.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
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


# ── Serveur de l'interface (repris de tests/exe_smoke.py de lidar2map) ─────

def port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def lancer(commande: list[str], env: dict, cwd: Path, journal: Path) -> subprocess.Popen:
    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
               if os.name == "nt" else {"start_new_session": True})
    with open(journal, "wb") as sortie:
        # stdin fermé : une question interactive ne doit jamais bloquer.
        return subprocess.Popen(commande, env=env, cwd=cwd,
                                stdin=subprocess.DEVNULL, stdout=sortie,
                                stderr=subprocess.STDOUT, **options)


def tuer_arbre(processus: subprocess.Popen) -> None:
    """Le serveur lance le calcul dans un processus enfant : tuer tout
    l'arbre, pas le parent seul."""
    if processus.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(processus.pid)],
                           capture_output=True, check=False)
        else:
            try:
                os.killpg(processus.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        processus.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


def lire(url: str, delai_s: float = 10) -> bytes:
    with urllib.request.urlopen(url, timeout=delai_s) as reponse:
        return reponse.read()


def attendre_serveur(port: int, processus: subprocess.Popen, delai_s: float) -> dict:
    fin = time.monotonic() + delai_s
    derniere = None
    while time.monotonic() < fin:
        if processus.poll() is not None:
            raise Echec(f"arrêté (code {processus.returncode}) avant de répondre"
                        f" sur le port {port}")
        try:
            init = json.loads(lire(f"http://127.0.0.1:{port}/api/init", 5))
            if init.get("app") == "gpxsolar":
                return init
            derniere = "réponse sans \"app\": \"gpxsolar\""
        except (OSError, ValueError) as exc:
            derniere = exc
        time.sleep(2)
    raise Echec(f"/api/init muet après {delai_s:.0f} s sur le port {port} ({derniere})")


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

    print("\n== 3. interface (--serve-gui)", flush=True)
    port = port_libre()
    journal = racine / "3_serveur.log"
    processus = lancer([str(programme), "--serve-gui", "--no-browser", "--no-tray",
                        "--port", str(port)], env, racine, journal)
    try:
        init = attendre_serveur(port, processus, DELAI_DEMARRAGE_S)
        if init.get("version") != f"v{attendue}":
            raise Echec(f"/api/init annonce {init.get('version')!r}, attendu v{attendue}")
        base = f"http://127.0.0.1:{port}"
        if b"Simu Rando Solaire" not in lire(base + "/"):
            raise Echec("la page servie n'est pas celle de gpxsolar")
        for fichier in ("/app.js", "/style.css", "/web_bridge.js"):
            if not lire(base + fichier):
                raise Echec(f"{fichier} servi vide")
        if "items" not in json.loads(lire(base + "/api/poll-log")):
            raise Echec("/api/poll-log sans « items »")
    except (OSError, ValueError, Echec) as exc:
        tuer_arbre(processus)
        sortie_serveur = journal.read_text(encoding="utf-8", errors="replace")
        raise Echec(f"{exc}\n{sortie_serveur[-3000:]}") from None
    tuer_arbre(processus)
    print(f"   OK : /api/init (v{attendue}), page, fichiers et journal sur le port {port}",
          flush=True)

    print("\n== 4. rien d'écrit à côté du binaire", flush=True)
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
