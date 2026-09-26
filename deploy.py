#!/usr/bin/env python3
"""deploy.py — Déploiement unifié gpxsolar (cross-platform Windows/macOS/Linux).

Ce dossier de travail EST le dépôt git depuis le 26 septembre 2026 : deploy.py
commit et pousse directement ici, sur le modèle de lidar2map. Plus de clone
temporaire ni de table de correspondance de noms : README.md, BUILD.md et
.github/workflows/ portent ici leur vrai nom. Seuls les fichiers déjà suivis
partent (git add -u) : un fichier nouveau non ignoré bloque le déploiement
tant qu'il n'a pas été ajouté (git add) ou ignoré (.gitignore pour tous,
.git/info/exclude pour soi seul), ce dossier accumulant traces, caches et
sauvegardes personnels qu'un git add -A publierait.

Un seul script pour tout :
  • push des sources vers le repo GitHub
  • détection automatique de ce qui a changé
  • action :
        - docs / meta seulement     -> push seul
        - gpxsolar.py seul          -> push + patch des 3 bundles (sans rebuild)
        - .spec / _loader / build.* -> push puis STOP : rebuild via release.yml
                                       (via --new-tag, choix de version humain)

Deux voies pour le patch :
  --mode cloud (défaut)  déclenche update.yml sur le runner GitHub
                         -> les ~1,5 Go transitent sur le réseau GitHub
  --mode local           lance update_app.py --release ici
                         -> les ~1,5 Go transitent par TA connexion

Usage :
  python deploy.py -m "mon correctif"                         # cloud, dernière release
  python deploy.py -m "..." --mode local                      # patch local
  python deploy.py -m "..." --patch-tag v1.0.2                # cibler un tag existant
  python deploy.py -m "..." --new-tag v1.0.3                  # créer nouveau tag → rebuild
  python deploy.py -m "..." --skip-push                       # patch direct (pas de push)
  python deploy.py -m "..." --dry-run                         # voir le diff sans push

Prérequis :
  python (>=3.8), git, gh (authentifié : gh auth status).
  --mode local : GH_TOKEN/GITHUB_TOKEN dans l'env (ou gh auth token disponible).
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

# === CONFIG (à adapter par projet) ===========================================

PROJECT = "gpxsolar"
APP_PY = "gpxsolar.py"
REPO_DEFAULT = "nico579/gpxsolar"

SRC = Path(__file__).resolve().parent
BRANCHE_RELEASE = "main"
SHA_GIT_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_RELEASE_RE = re.compile(r"^v\d+\.\d+\.\d+$")

# Patterns "rebuild requis" : si l'un de ces fichiers a changé, le patch ne
# suffit pas (l'archive launcher PyInstaller ou la spec ne sont pas patchables).
def is_rebuild_file(name: str) -> bool:
    return (
        name == "_loader.py"
        or name.endswith(".spec")
        or name.endswith("_build.ps1")
        or name.endswith("_build.sh")
        or name.startswith("setup_build_")
    )

# === COLOR / IO HELPERS ======================================================

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

# === SHELL HELPERS ===========================================================

def run(cmd, cwd=None, check=True, capture=False, env=None, timeout=120):
    """Wrapper subprocess.run. capture=True -> renvoie stdout (texte).

    timeout : secondes avant abandon. 120s suffit pour la majorité des git/gh
    opérations. Pour les commandes longues par nature (git clone d'un repo
    avec gros assets, gh run watch sur update.yml, update_app.py --release
    qui upload ~1,5 Go), passer un timeout explicite plus large au call site."""
    try:
        result = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None,
            check=False, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        fail(f"{' '.join(cmd)} a dépassé le timeout ({timeout}s) — réseau bloqué ou commande hangée ?")
    if check and result.returncode != 0:
        cmd_str = " ".join(cmd)
        err = (result.stderr or result.stdout or "").strip()
        fail(f"{cmd_str} a échoué (code {result.returncode})" + (f"\n{err}" if err else ""))
    return result

def git(*args, check=True, capture=False):
    """git <args>, dans le dossier de travail (qui est le dépôt)."""
    return run(["git", *args], cwd=SRC, check=check, capture=capture)

def gh_json(*args):
    """gh ... --json X (renvoie le JSON parsé)."""
    res = run(["gh", *args], capture=True)
    return json.loads(res.stdout)

def get_latest_tag(repo: str) -> str:
    res = run(["gh", "release", "view", "--repo", repo, "--json", "tagName"],
              capture=True, check=False)
    if res.returncode != 0:
        fail(f"aucune release sur {repo} (crée-en une via release.yml, ou passe --patch-tag)")
    data = json.loads(res.stdout)
    tag = data.get("tagName", "")
    if not tag:
        fail(f"aucune release sur {repo}")
    return tag

def read_code_version() -> str:
    """Lit la constante VERSION de gpxsolar.py : SOURCE UNIQUE de la version.

    Le tag de release en est dérivé (v<VERSION>), au lieu d'être saisi une 2e
    fois : sans ça, tag et constante peuvent diverger (le titre de fenêtre
    annoncerait une version, la release une autre).
    """
    txt = (SRC / APP_PY).read_text(encoding="utf-8")
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', txt, re.M)
    if not m:
        fail(f"constante VERSION introuvable dans {APP_PY}")
    return m.group(1)

def find_python() -> str:
    for name in ("python", "python3", "py"):
        if shutil.which(name):
            return name
    fail("python introuvable dans le PATH (requis pour --mode local)")

# === PUSH PHASE ==============================================================

def _sortie_git(*args) -> str:
    return git(*args, capture=True).stdout.strip()

def _remote_officiel(url: str) -> bool:
    """Reconnaît uniquement le dépôt GitHub attendu, sans alias ni userinfo.

    Le suffixe .git est facultatif en https : actions/checkout pose l'URL sans
    lui (workflow « deploy.py cross-platform »)."""
    url = str(url or "").strip()
    if url == f"git@github.com:{REPO_DEFAULT}.git":
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
                and parsed.path in (f"/{REPO_DEFAULT}", f"/{REPO_DEFAULT}.git"))
    if parsed.scheme == "ssh":
        return (propre and parsed.hostname == "github.com"
                and parsed.username == "git" and port in (None, 22)
                and parsed.path == f"/{REPO_DEFAULT}.git")
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
             f"github.com/{REPO_DEFAULT}.\nfetch={fetch_url!r}\npush={push_url!r}")

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
    git("tag", "-a", tag, "-m", f"{PROJECT} {tag}", sha)
    git("push", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    distant = _sha_remote(f"refs/tags/{tag}^{{}}")
    if distant != sha:
        fail(f"le tag distant {tag} pointe vers {distant}, attendu {sha}")

def commit_and_push(message: str, new_tag: str = "") -> str:
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

# === RELEASE PHASE (patch d'une release existante) ===========================

def invoke_cloud(repo: str, target_tag: str):
    cprint(f"\n==> Déclenchement de update.yml sur {target_tag} (voie cloud)", "cyan")
    run(["gh", "workflow", "run", "update.yml", "--repo", repo, "-f", f"tag={target_tag}"])

    run_id = None
    for _ in range(6):
        time.sleep(5)
        runs = gh_json("run", "list", "--repo", repo, "--workflow", "update.yml",
                       "--limit", "1", "--json", "databaseId")
        if runs:
            run_id = runs[0]["databaseId"]
            break
    if not run_id:
        fail("run introuvable (voir l'onglet Actions du repo)")
    print(f"    Run : https://github.com/{repo}/actions/runs/{run_id}")

    cprint("==> Surveillance du run (~6-7 min)", "cyan")
    # update.yml dure typiquement 5-7 min ; on tolère jusqu'à 20 min pour rester
    # robuste si le runner GitHub est lent ce jour-là.
    res = run(["gh", "run", "watch", str(run_id), "--repo", repo,
               "--exit-status", "--interval", "20"], check=False, timeout=1200)
    if res.returncode != 0:
        fail(f"le run update.yml a échoué : gh run view {run_id} --repo {repo} --log-failed")

    cprint(f"\n==> OK. Bundles de {target_tag} patchés sans rebuild (cloud).", "green")
    print(f"    Release : https://github.com/{repo}/releases/tag/{target_tag}")

def invoke_local(target_tag: str):
    cprint(f"\n==> Patch local via update_app.py --release sur {target_tag} (voie locale)", "cyan")
    cprint("    Les ~1,5 Go d'assets transitent par TA connexion (upload vers GitHub).", "yellow")

    py = find_python()

    env = os.environ.copy()
    if "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env:
        # Récupère le token de gh CLI si dispo, pour éviter à update_app.py
        # le détour par git credential (qui peut prompter).
        tok = run(["gh", "auth", "token"], capture=True, check=False)
        if tok.returncode == 0 and tok.stdout.strip():
            env["GH_TOKEN"] = tok.stdout.strip()
        else:
            cprint("    Note : aucun token dans l'env ni via gh ; update_app.py tentera git credential.", "yellow")

    update_app = SRC / "update_app.py"
    if not update_app.exists():
        fail(f"update_app.py introuvable à côté ({update_app})")

    # update_app.py --release : download 3 assets + patch + upload ~1,5 Go
    # depuis la connexion locale. Tolère jusqu'à 40 min pour les connexions lentes.
    run([py, str(update_app), "--release", "--tag", target_tag], env=env, timeout=2400)
    cprint(f"\n==> OK. Bundles de {target_tag} patchés sans rebuild (local).", "green")
    print(f"    Release : https://github.com/{REPO_DEFAULT}/releases/tag/{target_tag}")

def invoke_patch(mode: str, repo: str, target_tag: str):
    if mode == "local":
        invoke_local(target_tag)
    else:
        invoke_cloud(repo, target_tag)

# === MAIN ====================================================================

def main():
    parser = argparse.ArgumentParser(
        prog="deploy.py",
        description=f"Déploiement unifié {PROJECT} — push + patch cloud/local + tag.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Voir le docstring en tête du fichier pour les exemples.",
    )
    parser.add_argument("-m", "--message", required=True,
                        help="message de commit")
    parser.add_argument("--mode", choices=["cloud", "local"], default="cloud",
                        help="voie de patch (défaut: cloud = update.yml sur GitHub)")
    parser.add_argument("--patch-tag", default="",
                        help="tag existant à patcher (défaut: dernière release)")
    parser.add_argument("--new-tag", nargs="?", const="AUTO", default="",
                        help="rebuild complet via un nouveau tag git → déclenche "
                             "release.yml. Sans valeur : tag dérivé de VERSION "
                             "(v<VERSION>, source unique). Avec valeur vX.Y.Z : "
                             "acceptée mais doit égaler v<VERSION>, sinon refus.")
    parser.add_argument("--skip-push", action="store_true",
                        help="sauter push+détection, patcher directement la release")
    parser.add_argument("--push-only", action="store_true",
                        help="pousser les sources sur main SANS patcher les bundles "
                             "(itération debug/mesure : récup via git pull + run direct)")
    parser.add_argument("--dry-run", action="store_true",
                        help="afficher le diff sans commit ni push")
    parser.add_argument("--repo", default=REPO_DEFAULT,
                        help=f"repo GitHub cible (défaut: {REPO_DEFAULT})")
    args = parser.parse_args()

    # --- Version : source unique (constante VERSION) -> tag dérivé ---
    # Le tag n'est jamais une 2e saisie de la version : soit on le dérive de
    # VERSION (--new-tag sans valeur), soit on vérifie que la valeur explicite
    # colle. Impossible de tagguer v1.4.0 avec VERSION restée à 1.3.x.
    if args.new_tag:
        want = f"v{read_code_version()}"
        if args.new_tag == "AUTO":
            args.new_tag = want
        elif args.new_tag != want:
            fail(f"--new-tag {args.new_tag} != {want} (constante VERSION dans "
                 f"{APP_PY}). La version a UNE source : bumpe VERSION, puis "
                 f"passe --new-tag sans valeur (le tag est dérivé).")
        if not TAG_RELEASE_RE.fullmatch(args.new_tag):
            fail(f"tag de release invalide : {args.new_tag!r} (format attendu vX.Y.Z)")

    # --- Validation ---
    if args.new_tag and args.skip_push:
        fail("--new-tag et --skip-push sont contradictoires")
    if args.new_tag and args.patch_tag:
        fail("--new-tag et --patch-tag sont contradictoires (rebuild vs patch existant)")
    if args.dry_run and args.skip_push:
        fail("--dry-run et --skip-push sont contradictoires")
    if args.push_only and args.skip_push:
        fail("--push-only et --skip-push sont contradictoires (pousser sans patcher vs patcher sans pousser)")
    if args.push_only and args.new_tag:
        fail("--push-only et --new-tag sont contradictoires (push seul vs rebuild via tag)")

    # --- Mode --skip-push : patch direct, pas de push ni de détection ---
    if args.skip_push:
        cprint(f"==> --skip-push : pas de push ni de détection, déclenchement direct ({args.mode}).", "yellow")
        tag = args.patch_tag or get_latest_tag(args.repo)
        invoke_patch(args.mode, args.repo, tag)
        return 0

    # --- Phase 1 : push + détection ---
    cprint("==> [1/2] Push des sources sur main + détection du diff", "cyan")
    sha_initial = verifier_depot(args.new_tag)
    verifier_fichiers_nouveaux()
    changed = compute_diff(args.dry_run)

    # Une simulation ne doit rien publier, pas même un tag sur un dépôt sans
    # changement (l'ancien ordre poussait celui d'un --dry-run --new-tag).
    if args.dry_run:
        cprint("\n==> --dry-run : pas de commit ni de push.", "yellow")
        return 0

    if not changed:
        # --new-tag sans diff : cas légitime (sources déjà poussées lors d'un
        # patch précédent, on veut ensuite un vrai rebuild taggé).
        if args.new_tag:
            cprint(f"\n==> Aucun changement à pousser ; tag {args.new_tag} sur le HEAD courant.", "cyan")
            _publier_tag(args.new_tag, sha_initial)
            cprint(f"\n==> Tag {args.new_tag} poussé → release.yml va se déclencher (rebuild ~30 min sur 3 OS).", "green")
            print(f"    Suivi : https://github.com/{args.repo}/actions/workflows/release.yml")
        return 0  # message déjà affiché par compute_diff

    commit_and_push(args.message, args.new_tag)

    if args.new_tag:
        cprint(f"\n==> Nouveau tag {args.new_tag} poussé → release.yml va se déclencher (rebuild ~30 min sur 3 OS).", "green")
        print(f"    Suivi : https://github.com/{args.repo}/actions/workflows/release.yml")
        return 0

    cprint("\n==> Fichiers modifiés et poussés :", "cyan")
    for c in changed:
        print(f"    {c}")

    # --- Mode --push-only : sources poussées, on saute le patch des bundles ---
    if args.push_only:
        cprint("\n==> --push-only : sources sur main, patch des bundles sauté.", "green")
        cprint("    Récup : git pull && python gpxsolar.py ...", "cyan")
        return 0

    # --- Phase 2 : catégorisation -> action ---
    rebuild = [c for c in changed if is_rebuild_file(c)]
    # gpxsolar.py OU un fichier du front gui/ : les deux vivent dans _internal/
    # et sont patchables sans rebuild (update_app.py les remplace tous). Sans le
    # gui/, une modif d'UI seule aurait juste poussé les sources sans mettre à
    # jour les bundles (même correctif que le jumeau lidar2map).
    code_changed = APP_PY in changed or any(c.startswith("gui/") for c in changed)

    if rebuild:
        cprint("\n==> [2/2] REBUILD requis (fichiers impactant le binaire) :", "yellow")
        for f in rebuild:
            cprint(f"      {f}", "yellow")
        print()
        print(f"    Le patch (cloud ou local) ne change que _internal/{APP_PY} :")
        print("    il ne peut PAS livrer ces changements. Il faut un vrai rebuild")
        print("    via release.yml, déclenché par un NOUVEAU tag (choix de version à toi) :")
        cur = get_latest_tag(args.repo)
        print()
        cprint(f"      python deploy.py -m \"{args.message}\" --new-tag <vX.Y.Z>    # dernière release : {cur}", "cyan")
        print()
        cprint("    Sources déjà poussées ; il ne reste qu'à tagger pour lancer release.yml.", "yellow")
        return 0

    if code_changed:
        tag = args.patch_tag or get_latest_tag(args.repo)
        cprint(f"\n==> [2/2] {APP_PY} modifié → patch via --mode {args.mode} sur {tag}", "cyan")
        cprint("    Avertissement : si tu as touché au BLOC LAUNCHER ou aux DEPS dans", "yellow")
        cprint(f"    {APP_PY}, le patch ne suffit pas → rebuild via release.yml.", "yellow")
        invoke_patch(args.mode, args.repo, tag)
        return 0

    cprint("\n==> [2/2] Docs / meta seulement → aucun binaire à patcher, push suffit. Terminé.", "green")
    return 0

if __name__ == "__main__":
    sys.exit(main())
