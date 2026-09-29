#!/usr/bin/env python3
"""deploy.py — Déploiement unifié gpxsolar (cross-platform Windows/macOS/Linux).

Ce dossier de travail EST le dépôt git depuis le 26 septembre 2026 : deploy.py
commit et pousse directement ici, sur le modèle de lidar2map. Plus de clone
temporaire ni de table de correspondance de noms : README.md, BUILD.md et
.github/workflows/ portent ici leur vrai nom. Plus de patch des bundles sans
reconstruction non plus (update_app.py et update.yml retirés le 26 septembre
2026, comme lidar2map en 1.53) : toute livraison passe par une release
reconstruite par release.yml, que déclenche un tag.

Seuls les fichiers déjà suivis partent (git add -u) : un fichier nouveau non
ignoré bloque le déploiement tant qu'il n'a pas été ajouté (git add) ou
ignoré (.gitignore pour tous, .git/info/exclude pour soi seul), ce dossier
accumulant traces, caches et sauvegardes personnels qu'un git add -A
publierait.

Usage :
  python deploy.py -m "mon correctif"        # tests + push, pas de release
  python deploy.py -m "..." --new-tag        # tests + push + tag v<VERSION> + suivi du build
  python deploy.py -m "..." --new-tag v1.5.0 # accepté seulement si ça égale v<VERSION>
  python deploy.py -m "..." --dry-run        # affiche le diff, ne commit ni ne pousse
  python deploy.py -m "..." --skip-tests     # saute tests et ruff (déconseillé)

Prérequis : git, gh (authentifié : gh auth status), ruff pour le contrôle de
style (averti s'il manque).
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import NoReturn

# Force UTF-8 sur stdout/stderr : sous Windows, le défaut cp1252 fait planter
# print() dès qu'on écrit un caractère non-Latin1 (flèches →, accents corrompus
# dans certaines variantes, etc.). reconfigure() est dispo dès Python 3.7.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# === CONFIG ===================================================================

REPO = "nico579/gpxsolar"
VERSION_FILE = "gpxsolar.py"
# Les tests hors réseau que lance aussi la CI (ci.yml), dans le même ordre.
# gpxsolar n'a pas de lanceur de suites comme le tests/run_tests.py de
# lidar2map : trois scripts suffisent.
TESTS = ("test_gpxsolar.py", "test_dossiers.py", "test_installation.py", "test_bootstrap.py",
         "test_serve_web.py")
SRC = Path(__file__).resolve().parent
BRANCHE_RELEASE = "main"
SHA_GIT_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_RELEASE_RE = re.compile(r"^v\d+\.\d+\.\d+$")

# === COLOR / IO HELPERS =======================================================

_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
if os.name == "nt" and _USE_COLOR:
    # Active la séquence ANSI sur les terminaux Windows récents.
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        h = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(h, ctypes.byref(mode))
        kernel32.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        _USE_COLOR = False

_COLORS = {"cyan": "\033[36m", "yellow": "\033[33m",
           "red": "\033[31m", "green": "\033[32m"}


def cprint(msg: str, color: str = "") -> None:
    if _USE_COLOR and color in _COLORS:
        print(f"{_COLORS[color]}{msg}\033[0m")
    else:
        print(msg)


def fail(msg: str) -> NoReturn:
    cprint(f"\nERREUR : {msg}", "red")
    sys.exit(1)


# === SHELL HELPERS ============================================================

def run(cmd, check=True, capture=False, env=None, timeout=120):
    try:
        # UTF-8 explicite : les tests écrivent en UTF-8 (PYTHONUTF8=1, voir
        # preflight), et le décodage par défaut en cp1252 plantait le fil
        # lecteur sur certains octets, en noyant la vraie erreur.
        result = subprocess.run(
            cmd, cwd=str(SRC), check=False, text=True,
            encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        fail(f"{' '.join(cmd)} a dépassé le timeout ({timeout}s).")
    if check and result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        fail(f"{' '.join(cmd)} a échoué (code {result.returncode})" + (f"\n{err}" if err else ""))
    return result


def git(*args, check=True, capture=False):
    """git <args>, dans le dossier de travail (qui est le dépôt)."""
    return run(["git", *args], check=check, capture=capture)


def gh_json(*args):
    res = run(["gh", *args], capture=True)
    return json.loads(res.stdout)


def read_code_version() -> str:
    """Lit la constante VERSION de gpxsolar.py : SOURCE UNIQUE de la version.

    Le tag de release en est dérivé (v<VERSION>), au lieu d'être saisi une 2e
    fois : sans ça, tag et constante peuvent diverger (le titre de fenêtre
    annoncerait une version, la release une autre).
    """
    txt = (SRC / VERSION_FILE).read_text(encoding="utf-8")
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', txt, re.M)
    if not m:
        fail(f"constante VERSION introuvable dans {VERSION_FILE}")
    return m.group(1)


def _sortie_git(*args) -> str:
    return git(*args, capture=True).stdout.strip()


def _remote_officiel(url: str) -> bool:
    """Reconnaît uniquement le dépôt GitHub attendu, sans alias ni userinfo.

    Le suffixe .git est facultatif en https : actions/checkout pose l'URL sans
    lui (workflow « deploy.py cross-platform »)."""
    url = str(url or "").strip()
    if url == f"git@github.com:{REPO}.git":
        return True
    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    propre = (parsed.password is None and not parsed.params
              and not parsed.query and not parsed.fragment)
    if parsed.scheme == "https":
        return (propre and parsed.hostname == "github.com"
                and parsed.username is None and port is None
                and parsed.path in (f"/{REPO}", f"/{REPO}.git"))
    if parsed.scheme == "ssh":
        return (propre and parsed.hostname == "github.com"
                and parsed.username == "git" and port in (None, 22)
                and parsed.path == f"/{REPO}.git")
    return False


def _sha_git(valeur: str, contexte: str) -> str:
    valeur = str(valeur or "").strip().lower()
    if not SHA_GIT_RE.fullmatch(valeur):
        fail(f"SHA Git invalide pour {contexte} : {valeur!r}")
    return valeur


def _sha_remote(ref: str, obligatoire: bool = True) -> str:
    """Lit une référence distante sans modifier le dépôt ni ses refs locales."""
    resultat = git("ls-remote", "--exit-code", "origin", ref,
                   check=False, capture=True)
    if resultat.returncode == 2 and not obligatoire:
        return ""
    if resultat.returncode != 0:
        detail = (resultat.stderr or resultat.stdout or "").strip()
        fail(f"impossible de lire {ref} sur origin" + (f"\n{detail}" if detail else ""))
    lignes = [ligne.split() for ligne in resultat.stdout.splitlines() if ligne.strip()]
    if len(lignes) != 1 or len(lignes[0]) != 2 or lignes[0][1] != ref:
        fail(f"réponse ambiguë de origin pour {ref}")
    return _sha_git(lignes[0][0], ref)


def verifier_depot(new_tag: str = "") -> str:
    """Refuse de déployer depuis une branche, un remote ou un HEAD inattendu.

    HEAD doit égaler origin/main : un commit poussé ailleurs (session cloud,
    PR fusionnée, modification faite sur GitHub) se récupère par git pull
    avant de déployer. L'ancien déploiement par copie vers un clone
    temporaire l'écrasait sans le voir."""
    branche = _sortie_git("branch", "--show-current")
    if branche != BRANCHE_RELEASE:
        fail(f"branche courante {branche or '(HEAD détaché)'} ; "
             f"le déploiement exige {BRANCHE_RELEASE}.")

    fetch_url = _sortie_git("remote", "get-url", "origin")
    push_url = _sortie_git("remote", "get-url", "--push", "origin")
    if not _remote_officiel(fetch_url) or not _remote_officiel(push_url):
        fail(f"origin doit pointer en lecture et écriture vers le dépôt officiel "
             f"github.com/{REPO}.\nfetch={fetch_url!r}\npush={push_url!r}")

    local = _sha_git(_sortie_git("rev-parse", "HEAD"), "HEAD local")
    distant = _sha_remote(f"refs/heads/{BRANCHE_RELEASE}")
    if local != distant:
        fail(f"HEAD local ({local}) ne correspond pas exactement à "
             f"origin/{BRANCHE_RELEASE} ({distant}). Récupère les commits "
             "distants (git pull) avant de déployer.")

    if new_tag:
        existe_localement = git("show-ref", "--verify", "--quiet",
                                f"refs/tags/{new_tag}", check=False)
        if existe_localement.returncode == 0:
            fail(f"le tag {new_tag} existe déjà localement")
        if existe_localement.returncode not in (0, 1):
            fail(f"impossible de vérifier le tag local {new_tag}")
        if _sha_remote(f"refs/tags/{new_tag}", obligatoire=False):
            fail(f"le tag {new_tag} existe déjà sur origin")
    return local


def verifier_fichiers_nouveaux() -> None:
    """Bloque sur tout fichier nouveau ni suivi ni ignoré (voir le docstring
    du module) : l'ajouter ou l'ignorer est un choix explicite."""
    nouveaux = _sortie_git("ls-files", "--others", "--exclude-standard").splitlines()
    if nouveaux:
        fail("fichiers nouveaux ni suivis ni ignorés :\n  "
             + "\n  ".join(nouveaux)
             + "\nAjoute-les (git add) ou ignore-les (.gitignore pour tous, "
               ".git/info/exclude pour toi seul), puis relance.")


# === PRE-FLIGHT ===============================================================

def preflight() -> None:
    """Les tests hors réseau de la CI, puis ruff, avant de pousser : le
    jumeau lidar2map fait de même avec ses suites. BOOTSTRAP=none, comme en
    CI : un test ne doit jamais créer le venv ~/.gpxsolar/venv."""
    env = dict(os.environ, PYTHONUTF8="1", GPXSOLAR_BOOTSTRAP="none")
    for script in TESTS:
        cprint(f"==> python {script}", "cyan")
        res = run([sys.executable, script], check=False, capture=True,
                  env=env, timeout=900)
        if res.returncode != 0:
            print((res.stdout or "")[-6000:] + (res.stderr or ""))
            fail(f"{script} en échec - corrige avant de pousser.")
        cprint("    OK", "green")

    if shutil.which("ruff") is None:
        cprint("==> ruff introuvable : contrôle de style sauté (pip install ruff)", "yellow")
        return
    cprint("==> ruff check .", "cyan")
    res = run(["ruff", "check", "."], check=False, capture=True)
    if res.returncode != 0:
        print((res.stdout or "") + (res.stderr or ""))
        fail("ruff signale des erreurs - corrige avant de pousser.")
    cprint("    OK", "green")


# === PUSH + TAG ===============================================================

def compute_diff(dry_run: bool = False) -> list:
    cprint("\n==> Modifications :", "cyan")
    # Un dry-run doit être parfaitement observateur : même ``git add`` est une
    # mutation de l'index et peut écraser la sélection de l'utilisateur.
    if not dry_run:
        git("add", "-u")
    status = git("status", "--short", "--untracked-files=no", capture=True).stdout.strip()
    if not status:
        cprint("    Aucun changement. Rien à pousser.", "yellow")
        return []
    for line in status.splitlines():
        print(f"    {line}")
    print()
    if dry_run:
        git("diff", "--stat")
        git("diff", "--cached", "--stat")
        return status.splitlines()
    git("diff", "--cached", "--stat")
    changed = _sortie_git("diff", "--cached", "--name-only").splitlines()
    return [c.strip() for c in changed if c.strip()]


def _publier_tag(tag: str, sha: str) -> None:
    cprint(f"\n==> Tag {tag}", "cyan")
    git("tag", "-a", tag, "-m", f"gpxsolar {tag}", sha)
    git("push", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    distant = _sha_remote(f"refs/tags/{tag}^{{}}")
    if distant != sha:
        fail(f"le tag distant {tag} pointe vers {distant}, attendu {sha}")


def commit_and_push(message: str, new_tag: str) -> str:
    cprint("\n==> Commit", "cyan")
    git("commit", "-m", message)
    sha = _sha_git(_sortie_git("rev-parse", "HEAD"), "commit créé")
    cprint("\n==> Push origin main", "cyan")
    git("push", "origin", f"HEAD:refs/heads/{BRANCHE_RELEASE}")
    distant = _sha_remote(f"refs/heads/{BRANCHE_RELEASE}")
    if distant != sha:
        fail(f"origin/{BRANCHE_RELEASE} pointe vers {distant}, attendu {sha}")
    if new_tag:
        _publier_tag(new_tag, sha)
    return sha


def watch_release(tag: str, sha: str) -> None:
    """Le tag poussé déclenche release.yml tout seul (on: push: tags: v*) :
    il ne reste qu'à retrouver le run et attendre la fin."""
    cprint(f"\n==> {tag} poussé -> release.yml se déclenche (build 4 runners, ~30 min)", "cyan")
    run_id = None
    for _ in range(12):
        time.sleep(5)
        runs = gh_json("run", "list", "--repo", REPO, "--workflow", "release.yml",
                       "--event", "push", "--commit", sha,
                       "--limit", "1", "--json", "databaseId,headSha")
        if runs:
            candidat = runs[0]
            if str(candidat.get("headSha") or "").lower() == sha:
                run_id = candidat["databaseId"]
                break
    if not run_id:
        cprint("    Run introuvable automatiquement - vérifie l'onglet Actions.", "yellow")
        return
    print(f"    Run : https://github.com/{REPO}/actions/runs/{run_id}")

    cprint("==> Surveillance du run", "cyan")
    res = run(["gh", "run", "watch", str(run_id), "--repo", REPO,
               "--exit-status", "--interval", "30"], check=False, timeout=3600)
    if res.returncode != 0:
        fail(f"le run release.yml a échoué : gh run view {run_id} --repo {REPO} --log-failed")

    cprint(f"\n==> OK. {tag} publiée.", "green")
    print(f"    Release : https://github.com/{REPO}/releases/tag/{tag}")


# === MAIN =====================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="deploy.py",
        description="Déploiement unifié gpxsolar - tests + push + tag + suivi du build.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Voir le docstring en tête du fichier pour les exemples.",
    )
    parser.add_argument("-m", "--message", required=True, help="message de commit")
    parser.add_argument("--new-tag", nargs="?", const="AUTO", default="",
                        help="pousse aussi un tag -> déclenche release.yml. Sans "
                             "valeur : dérivé de VERSION (v<VERSION>). Avec une "
                             "valeur vX.Y.Z : acceptée seulement si elle égale "
                             "v<VERSION>, sinon refusée.")
    parser.add_argument("--dry-run", action="store_true",
                        help="affiche le diff sans commit ni push")
    parser.add_argument("--skip-tests", action="store_true",
                        help="saute les tests et ruff (déconseillé)")
    args = parser.parse_args()

    # Version : source unique (constante VERSION), le tag en est dérivé.
    # Impossible de tagguer v1.5.0 avec VERSION restée à 1.4.x.
    if args.new_tag:
        want = f"v{read_code_version()}"
        if args.new_tag == "AUTO":
            args.new_tag = want
        elif args.new_tag != want:
            fail(f"--new-tag {args.new_tag} != {want} (constante VERSION dans "
                 f"{VERSION_FILE}). Bumpe VERSION, puis repasse --new-tag sans "
                 f"valeur (le tag est dérivé).")
        if not TAG_RELEASE_RE.fullmatch(args.new_tag):
            fail(f"tag de release invalide : {args.new_tag!r} (format attendu vX.Y.Z)")

    sha_initial = verifier_depot(args.new_tag)
    verifier_fichiers_nouveaux()

    if not args.skip_tests and not args.dry_run:
        preflight()

    changed = compute_diff(args.dry_run)
    # Une simulation ne doit pas non plus publier de tag sur un dépôt propre.
    if args.dry_run:
        cprint("\n==> --dry-run : pas de commit ni de push.", "yellow")
        return 0

    if not changed:
        if args.new_tag:
            cprint(f"\n==> Aucun changement à pousser ; tag {args.new_tag} sur le HEAD courant.", "cyan")
            _publier_tag(args.new_tag, sha_initial)
            watch_release(args.new_tag, sha_initial)
        return 0

    sha_publie = commit_and_push(args.message, args.new_tag)

    if args.new_tag:
        watch_release(args.new_tag, sha_publie)
    else:
        cprint("\n==> Poussé sur main (pas de tag -> pas de release).", "green")

    return 0


if __name__ == "__main__":
    sys.exit(main())
