#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPX Solar Shadow Analyzer
=========================

Analyse l'ensoleillement d'une randonnée GPX avec détection d'ombres
de relief (MNT/MNH) et de végétation (ESA WorldCover) — sortie KML/KMZ
pour Google Earth et CSV agrégé.

Sources DEM supportées : SRTM1, Copernicus DEM, IGN BDALTI 25 m, IGN
RGEALTI 5 m, IGN LiDAR HD 0.5 m. Coordonnées : WGS84 (entrée) / Lambert93
(EPSG:2154) pour les sorties IGN.

Usage :
    python gpxsolar.py                # GUI pywebview
    python gpxsolar.py --help         # options CLI

Bootstrap des dépendances (style lidar2map) :
    --bootstrap=auto   (défaut) : venv automatique dans ~/.gpxsolar
    --bootstrap=pip            : install dans l'env Python courant
    --bootstrap=none           : pas d'install, vérifie seulement
    --help-bootstrap           : affiche l'aide du bootstrap
"""

# ====================================================================
#  BOOTSTRAP DES DÉPENDANCES (modes auto | pip | none — cf. lidar2map)
# ====================================================================
import subprocess
import sys
import os
import platform
import importlib.util
import logging
from pathlib import Path

# Forcer UTF-8 sur stdout/stderr : la console Windows est en cp1252 par défaut
# et le script affiche des caractères Unicode (✓, ╔, …) -> UnicodeEncodeError
# sinon. À faire AVANT tout print (bootstrap, launcher, --installer-deps).
for _std in ("stdout", "stderr"):
    _s = getattr(sys, _std, None)
    if _s is not None and (getattr(_s, "encoding", "") or "").lower() != "utf-8":
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

# ─────────────────────────────────────────────────────────────────────────────
# MODE LAUNCHER (build onefile)
# ─────────────────────────────────────────────────────────────────────────────
# Le même gpxsolar.py est buildé en DEUX versions :
#   1) onedir (gpxsolar.spec)        : la vraie app, lourde (numpy, scipy,
#      rasterio, pywebview, etc.). C'est ce qui tourne au final.
#   2) onefile (gpxsolar_launcher.spec) : un petit launcher qui contient le
#      onedir zippé en ressource. À l'exécution il extrait dans
#      %LOCALAPPDATA%\gpxsolar (avec contrôle SHA pour détecter les mises à
#      jour), puis spawn le vrai exe avec une sentinelle pour qu'il saute ce
#      bloc.
#
# cwd : le launcher passe son propre dossier comme cwd au subprocess, de sorte
# que les fichiers cwd-relatifs (gpx_analyzer_history.json, GPX_Ombres/, …)
# soient créés à côté de l'exe lanceur, pas dans %LOCALAPPDATA%\gpxsolar.
_INNER_FLAG = "--__gpxsolar_inner__"
if getattr(sys, "frozen", False):
    if _INNER_FLAG in sys.argv:
        # On est l'exe interne : retirer la sentinelle puis continuer normalement
        sys.argv.remove(_INNER_FLAG)
    else:
        # On est peut-être le launcher : chercher le bundle.
        import hashlib, zipfile
        _exe = Path(sys.executable).resolve()
        _sys = platform.system()

        # Ordre de recherche du bundle (archi lidar2map) :
        #   1. À côté de l'exe / dans Contents/Resources/ (bundle fichier séparé,
        #      remplaçable sans rebuild via update_app.py)
        #   2. Dans sys._MEIPASS (bundle embarqué — fallback ancienne archi)
        if _sys == "Darwin" and ".app" in str(_exe):
            _bundle = _exe.parent.parent / "Resources" / "gpxsolar_bundle.zip"
        else:
            _bundle = _exe.parent / "gpxsolar_bundle.zip"
        if not _bundle.exists():
            _meipass_str = getattr(sys, "_MEIPASS", None)
            if _meipass_str:
                _bundle = Path(_meipass_str) / "gpxsolar_bundle.zip"

        if _bundle.exists():
            # Dossier d'extraction : chemins système standard par OS.
            if _sys == "Windows":
                _app_dir   = Path(os.environ.get("LOCALAPPDATA",
                                str(Path.home() / "AppData" / "Local"))) / "gpxsolar"
                _inner_exe = _app_dir / "gpxsolar.exe"
            elif _sys == "Darwin":
                _app_dir   = Path.home() / "Library" / "Application Support" / "gpxsolar"
                _inner_exe = _app_dir / "gpxsolar"
            else:
                _app_dir   = Path.home() / ".local" / "share" / "gpxsolar"
                _inner_exe = _app_dir / "gpxsolar"
            _sha_file = _app_dir / ".bundle_sha"
            _lock     = _app_dir.parent / ".gpxsolar_extracting"

            # ── --desinstaller intercepté dans le launcher ────────────────────
            # Traité ici AVANT tout calcul de SHA. Le launcher supprime tout
            # directement (venv, bundle extrait) sans re-spawner.
            if "--desinstaller" in sys.argv:
                import shutil as _sh_u
                _gpx_home = Path.home() / ".gpxsolar"
                _cibles_u = [
                    (_app_dir,            "bundle extrait"),
                    (_gpx_home / "venv",  "venv Python"),
                ]
                print()
                print("  ── Désinstallation gpxsolar ───────────────────────────────────")
                print()
                _total_u = 0
                for _c_u, _label_u in _cibles_u:
                    if _c_u.exists():
                        _taille_u = sum(
                            f.stat().st_size for f in _c_u.rglob("*") if f.is_file())
                        _total_u += _taille_u
                        print(f"  Suppression {_label_u} ({_taille_u / 1e6:.0f} Mo)")
                        print(f"    {_c_u}")
                        _sh_u.rmtree(_c_u, ignore_errors=True)
                        print(f"    {'✓ supprimé' if not _c_u.exists() else '⚠ partiel'}")
                    else:
                        print(f"  {_label_u} : absent ({_c_u})")
                print()
                print(f"  {_total_u / 1e6:.0f} Mo libérés.")
                print()
                print("  Note : gpxsolar.py, l'exe/.app et le zip ne sont pas supprimés.")
                print("  Supprimez-les manuellement si nécessaire.")
                sys.exit(0)

            def _bundle_sha():
                h = hashlib.sha256()
                with open(_bundle, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                return h.hexdigest()

            # ── Détection de mise à jour avec cache mtime ─────────────────────
            # Le SHA256 d'un zip de plusieurs centaines de Mo prend ~0.5-1 s à
            # chaque lancement. On mémorise le mtime du bundle dans _sha_file
            # ("sha256hex\nmtime_float") pour éviter ce calcul quand rien n'a
            # changé.
            _need_extract = True
            if _sha_file.exists() and _inner_exe.exists() and not _inner_exe.is_dir():
                try:
                    _sha_lines     = _sha_file.read_text(encoding="utf-8").strip().split("\n")
                    _saved_sha     = _sha_lines[0]
                    _saved_mtime   = float(_sha_lines[1]) if len(_sha_lines) > 1 else 0.0
                    _current_mtime = _bundle.stat().st_mtime
                    if abs(_current_mtime - _saved_mtime) < 0.01:
                        _need_extract = False
                    else:
                        _expected_sha = _bundle_sha()
                        _need_extract = (_expected_sha != _saved_sha)
                except Exception:
                    _need_extract = True   # _sha_file corrompu → ré-extraire
            if _need_extract:
                _expected_sha = _bundle_sha()   # calcul SHA si pas encore fait

            # Si le zip a été créé avec --keepParent, l'extraction crée un
            # sous-dossier gpxsolar/ → l'exe est un niveau plus bas. On corrige.
            def _resolve_exe(exe):
                if exe.exists() and exe.is_dir():
                    deeper = exe / exe.name
                    if deeper.exists() and not deeper.is_dir():
                        return deeper
                return exe

            if _need_extract:
                import time as _time
                # Lockfile contre les extractions simultanées (double-clic).
                # Durci contre les locks ORPHELINS : si le lock est plus vieux
                # que _LOCK_STALE_S (instance tuée/plantée pendant l'extraction),
                # on le considère périmé et on le retire au lieu d'attendre 60 s
                # puis d'échouer. L'extraction du bundle prend ~30 s -> 300 s est
                # une borne haute sûre (pas de faux positif en cas de double-clic).
                _LOCK_STALE_S = 300
                _lock_actif = _lock.exists()
                if _lock_actif:
                    try:
                        _lock_actif = (_time.time() - _lock.stat().st_mtime) < _LOCK_STALE_S
                    except Exception:
                        _lock_actif = False
                    if not _lock_actif:
                        print("  Lockfile périmé détecté — nettoyage et reprise.", flush=True)
                        _lock.unlink(missing_ok=True)
                if _lock_actif:
                    print("Installation en cours dans une autre instance — attente...",
                          flush=True)
                    for _ in range(60):
                        _time.sleep(1)
                        if not _lock.exists():
                            break
                    _inner_check = _resolve_exe(_inner_exe)
                    if _inner_check.exists() and _sha_file.exists():
                        _need_extract = False
                    else:
                        print("  ⚠ Installation concurrente incomplète ou échouée.",
                              flush=True)
                        print(f"  Supprimez le lockfile et relancez : {_lock}", flush=True)
                        sys.exit(1)
                else:
                    _app_dir.parent.mkdir(parents=True, exist_ok=True)
                    _lock.touch()
                    try:
                        if _app_dir.exists():
                            import shutil as _sh
                            _sh.rmtree(_app_dir, ignore_errors=True)
                        _app_dir.mkdir(parents=True, exist_ok=True)
                        _bundle_size = _bundle.stat().st_size
                        print(f"Premier lancement — installation ({_bundle_size // 1_000_000} Mo)...",
                              flush=True)
                        # ditto (Mac) préserve le bit +x ; zipfile (Linux/fallback)
                        # le perd → on réapplique les permissions POSIX après coup.
                        _used_zipfile = False
                        if _sys == "Darwin":
                            import subprocess as _sp_d
                            _r = _sp_d.run(["ditto", "-x", "-k",
                                            str(_bundle), str(_app_dir)],
                                           capture_output=True)
                            if _r.returncode != 0:
                                with zipfile.ZipFile(_bundle) as _z:
                                    _t = Path(_app_dir).resolve()
                                    for _mem in _z.infolist():
                                        if _mem.filename.startswith(("/", "\\")) \
                                                or ":" in _mem.filename[:3]:
                                            raise ValueError(
                                                f"Bundle suspect : {_mem.filename!r}")
                                        _d = (_t / _mem.filename).resolve()
                                        if _d != _t and _t not in _d.parents:
                                            raise ValueError(
                                                f"Bundle suspect : {_mem.filename!r}")
                                    _z.extractall(_app_dir)
                                _used_zipfile = True
                            _sp_d.run(["xattr", "-dr", "com.apple.quarantine",
                                       str(_app_dir)], capture_output=True)
                        else:
                            with zipfile.ZipFile(_bundle) as _z:
                                _members = _z.infolist()
                                _n = len(_members)
                                _t = Path(_app_dir).resolve()
                                for _mem in _members:
                                    if _mem.filename.startswith(("/", "\\")) \
                                            or ":" in _mem.filename[:3]:
                                        raise ValueError(
                                            f"Bundle suspect : {_mem.filename!r}")
                                    _d = (_t / _mem.filename).resolve()
                                    if _d != _t and _t not in _d.parents:
                                        raise ValueError(
                                            f"Bundle suspect : {_mem.filename!r}")
                                for _i, _m in enumerate(_members, 1):
                                    _z.extract(_m, _app_dir)
                                    _mode = (_m.external_attr >> 16) & 0xFFFF
                                    if _mode and _sys != "Windows":
                                        try:
                                            (Path(_app_dir) / _m.filename).chmod(_mode & 0o777)
                                        except Exception:
                                            pass
                                    if _i % max(1, _n // 20) == 0:
                                        print(f"  {_i * 100 // _n}%",
                                              end="\r", flush=True)
                            print("  100%", flush=True)
                            _used_zipfile = True

                        # Filet de sécurité : zip sans permissions POSIX
                        # (external_attr == 0, ex: créé sous Windows) → forcer +x
                        # sur l'exe interne pour qu'il puisse être spawné.
                        if _used_zipfile and _sys != "Windows":
                            import stat as _stat
                            _inner_exe_resolved = _resolve_exe(_inner_exe)
                            if _inner_exe_resolved.exists():
                                _inner_exe_resolved.chmod(
                                    _inner_exe_resolved.stat().st_mode
                                    | _stat.S_IXUSR | _stat.S_IXGRP | _stat.S_IXOTH)

                        _inner_resolved = _resolve_exe(_inner_exe)
                        if not _inner_resolved.exists():
                            raise RuntimeError(
                                f"Extraction incomplète : {_inner_exe} introuvable")
                        _sha_file.write_text(
                            f"{_expected_sha}\n{_bundle.stat().st_mtime}",
                            encoding="utf-8")
                        print("Installation terminée.", flush=True)
                    except Exception as _e_extract:
                        print(f"\n  ⚠ Erreur d'extraction : {_e_extract}", flush=True)
                        print("  Relancez l'application pour réessayer.", flush=True)
                        sys.exit(1)
                    finally:
                        _lock.unlink(missing_ok=True)

            _inner_exe = _resolve_exe(_inner_exe)

            # cwd = dossier contenant le launcher (ou parent du .app sur Mac) :
            # les fichiers cwd-relatifs (gpx_analyzer_history.json, GPX_Ombres/,
            # …) sont créés là où l'utilisateur a posé l'exe, pas dans _app_dir.
            if _sys == "Darwin" and ".app" in str(_exe):
                _work_dir = _exe.parent.parent.parent.parent
            else:
                _work_dir = _exe.parent

            _rc = subprocess.call(
                [str(_inner_exe), _INNER_FLAG] + sys.argv[1:],
                cwd=str(_work_dir),
            )
            sys.exit(_rc)
        # Pas de bundle.zip → exe onedir lancé directement → continuer.


_DEPS_CRITIQUES = [
    # (module à importer, paquet pip)
    ("pytz",            "pytz"),
    ("srtm",            "srtm.py"),
    ("pysolar",         "pysolar"),
    ("gpxpy",           "gpxpy"),
    ("pandas",          "pandas"),
    ("requests",        "requests"),
    ("numpy",           "numpy"),
    ("timezonefinder",  "timezonefinder"),
    ("webview",         "pywebview"),
    ("simplekml",       "simplekml"),
    ("shapely",         "shapely"),
    ("PIL",             "Pillow"),
    ("pyproj",          "pyproj"),
    ("rasterio",        "rasterio"),
]
_DEPS_OPTIONNELLES = [
    # numba : accélération ×3-10 du ray-tracing et de l'interpolation
    # temporelle. Si l'install échoue (Python trop récent par ex.), on
    # garde le fallback NumPy pur.
    ("numba",           "numba"),
    # py7zr : extraction des archives IGN (.7z). Sans, BDALTI/RGEALTI
    # restent téléchargeables mais l'extraction échouera.
    ("py7zr",           "py7zr"),
]


def _gui_deps_plateforme():
    """Backend GUI de pywebview selon l'OS — (module, paquet pip).

    On force le backend **Qt** (PyQt6 + QtWebEngine) sur les TROIS OS — cf.
    _forcer_backend_qt(). C'est le même moteur Chromium partout -> pile uniforme.

      Windows  Qt au lieu de WinForms/WebView2+pythonnet : ce dernier souffre
               d'une régression pythonnet 3.1.0 (sérialisation .NET en récursion
               infinie -> bridge cassé -> GUI gelée) et de freezes récurrents.
               Qt supprime toute la couche .NET.
      Linux    Pas de backend natif → Qt est le seul installable via pip.
      macOS    Cocoa/WebKit via pyobjc (natif) + PyQt6 en filet (Mac headless).
    """
    _s = platform.system()
    if _s == "Darwin":
        return [
            ("WebKit",                    "pyobjc-framework-WebKit"),
            ("Cocoa",                     "pyobjc-framework-Cocoa"),
            ("PyQt6",                     "PyQt6"),
            ("PyQt6.QtWebEngineWidgets",  "PyQt6-WebEngine"),
            ("qtpy",                      "qtpy"),
        ]
    if _s in ("Linux", "Windows"):
        return [
            ("PyQt6",                     "PyQt6"),
            ("PyQt6.QtWebEngineWidgets",  "PyQt6-WebEngine"),
            ("qtpy",                      "qtpy"),
        ]
    return []


# Les deps GUI plateforme sont critiques : sans backend, la GUI ne se lance pas.
_DEPS_CRITIQUES += _gui_deps_plateforme()


def _resoudre_mode_bootstrap():
    """Détermine le mode de bootstrap (auto|pip|none|force) et nettoie sys.argv.

    Modes :
      auto  : crée un venv ~/.gpxsolar/venv si une dépendance critique manque.
              Si toutes les dépendances sont déjà importables, ne touche à rien.
      force : crée TOUJOURS le venv (utile pour isoler une install ou debug).
      pip   : install directe dans le Python courant (sans venv).
      none  : pas d'install ; vérifie les imports et plante si manquants.

    Priorité (du plus faible au plus fort) :
      1. Défaut          : "auto"
      2. Variable d'env  : GPXSOLAR_BOOTSTRAP={auto|pip|none|force}
      3. Argument CLI    : --bootstrap={auto|pip|none|force}
    """
    mode = "auto"
    valid = ("auto", "pip", "none", "force")

    env_mode = os.environ.get("GPXSOLAR_BOOTSTRAP", "").lower().strip()
    if env_mode in valid:
        mode = env_mode

    to_remove = []
    for i, arg in enumerate(sys.argv):
        if arg.startswith("--bootstrap="):
            v = arg.split("=", 1)[1].lower().strip()
            if v in valid:
                mode = v
            to_remove.append(i)
        elif arg == "--bootstrap" and i + 1 < len(sys.argv):
            v = sys.argv[i + 1].lower().strip()
            if v in valid:
                mode = v
            to_remove.append(i); to_remove.append(i + 1)

    if "--help-bootstrap" in sys.argv:
        print(__doc__)
        sys.exit(0)

    for i in sorted(to_remove, reverse=True):
        if i < len(sys.argv):
            del sys.argv[i]
    return mode


def _imports_manquants(deps):
    def _absent(mod):
        try:
            return importlib.util.find_spec(mod) is None
        except (ImportError, ValueError):
            # ValueError : module parent absent (PyQt6.X quand PyQt6 manque)
            return True
    return [pkg for mod, pkg in deps if _absent(mod)]


def _afficher_erreur_deps(manquantes, hint=""):
    print()
    print("  ╔══════════════════════════════════════════════════════════════╗")
    print("  ║  ERREUR : modules Python critiques manquants                 ║")
    print("  ╚══════════════════════════════════════════════════════════════╝")
    print(f"  Manquants : {', '.join(manquantes)}")
    if hint:
        print(f"  {hint}")
    print()
    print("  Solutions :")
    print(f"    pip install {' '.join(manquantes)}")
    print("    # ou créez un venv :")
    print("    python -m venv ~/.gpxsolar/venv")
    if platform.system() == "Windows":
        print("    %USERPROFILE%\\.gpxsolar\\venv\\Scripts\\pip install " + " ".join(manquantes))
    else:
        print(f"    ~/.gpxsolar/venv/bin/pip install {' '.join(manquantes)}")
    print()


def _pip_install(python_exe, packages, label):
    """Tente un pip install dans l'env donné. Retourne (ok, stderr_tail)."""
    cmd = [str(python_exe), "-m", "pip", "install", "-q",
           "--disable-pip-version-check"] + list(packages)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"{label}: {e}"
    if r.returncode == 0:
        return True, ""
    stderr = (r.stderr or r.stdout or "").strip()
    if stderr:
        stderr = "\n  ".join(stderr.split("\n")[-3:])
    return False, stderr


def _bootstrap_venv_auto(force: bool = False):
    """Crée (si nécessaire) un venv ~/.gpxsolar/venv et y relance le script.

    Args:
        force : si True, crée TOUJOURS le venv même si les déps sont déjà
                importables dans le Python courant. Sinon (mode 'auto'),
                on saute la création quand tout est déjà en place.
    """
    is_windows = platform.system() == "Windows"
    home_dir   = Path.home() / ".gpxsolar"
    venv_path  = home_dir / "venv"

    # Déjà dans CE venv ? (ré-entrée après exec)
    try:
        if Path(sys.prefix).resolve() == venv_path.resolve():
            print(f"  [bootstrap] dans le venv {venv_path}")
            return
    except OSError:
        pass

    manquantes = _imports_manquants(_DEPS_CRITIQUES)
    if not manquantes and not force:
        # Toutes les déps déjà importables : pas besoin de venv. Mais on l'annonce
        # pour que l'utilisateur ne s'attende pas à voir un venv apparaître.
        print(f"  [bootstrap] dépendances déjà disponibles dans {sys.executable}")
        print(f"             venv non créé — utilisez --bootstrap=force pour forcer la création")
        return

    venv_bin = venv_path / ("Scripts" if is_windows else "bin")
    venv_py  = venv_bin / ("python.exe" if is_windows else "python")
    venv_pip = venv_bin / ("pip.exe"    if is_windows else "pip")

    # Venv existant + déjà équipé : relancer dedans
    if venv_py.exists():
        check = subprocess.run(
            [str(venv_py), "-c", "import " + ",".join(m for m, _ in _DEPS_CRITIQUES)],
            capture_output=True)
        if check.returncode == 0:
            print(f"  Relance dans le venv : {venv_path}")
            _relancer(venv_py, is_windows)

    if not venv_py.exists():
        suppr = ("rmdir /s /q %USERPROFILE%\\.gpxsolar" if is_windows
                 else "rm -rf ~/.gpxsolar")
        print()
        print("  ╔══════════════════════════════════════════════════════════════╗")
        print("  ║  Premier lancement — création d'un venv isolé pour gpxsolar ║")
        print("  ║  (~80 Mo une fois installé). Aucun impact sur Python système.║")
        print(f"  ║  Pour le supprimer : {suppr}".ljust(63) + " ║")
        print("  ║  Pour passer en install directe (sans venv) :                ║")
        print("  ║    python gpxsolar.py --bootstrap=pip                        ║")
        print("  ╚══════════════════════════════════════════════════════════════╝")
        print(f"  Création du venv {venv_path}...")
        try:
            subprocess.run([sys.executable, "-m", "venv", str(venv_path)],
                           check=True)
        except subprocess.CalledProcessError as e:
            print(f"  ERREUR création venv : {e}")
            print("  Installez Python 3.9+ avec le module venv (apt install python3-venv).")
            sys.exit(1)

    # Installation groupée des déps critiques + optionnelles
    pip_args_crit = [pkg for _, pkg in _DEPS_CRITIQUES]
    pip_args_opt  = [pkg for _, pkg in _DEPS_OPTIONNELLES]
    print(f"  Installation des dépendances dans le venv (3-5 min)...")
    ok, err = _pip_install(venv_py, pip_args_crit + pip_args_opt, "venv-groupé")
    if not ok:
        print(f"  Install groupée échouée, retry sans les optionnelles ({', '.join(pip_args_opt)})...")
        ok, err = _pip_install(venv_py, pip_args_crit, "venv-critique")
        if ok:
            for opt in pip_args_opt:
                ok_one, _ = _pip_install(venv_py, [opt], f"venv-{opt}")
                print(f"    {'✓' if ok_one else '⚠'} {opt} : {'OK' if ok_one else 'échec — fonctionnalité réduite'}")
        else:
            print(f"  ERREUR install déps critiques :\n  {err}")
            print(f"  Retry manuel : {venv_pip} install {' '.join(pip_args_crit)}")
            sys.exit(1)
    print("  ✓ Dépendances installées.")
    print("  Relance dans le venv...")
    _relancer(venv_py, is_windows)


def _relancer(venv_python, is_windows):
    """Relance le script avec le Python du venv (exec sur Unix, run sur Windows)."""
    if is_windows:
        try:
            sys.stdout.flush(); sys.stderr.flush()
            r = subprocess.run([str(venv_python)] + sys.argv,
                               stdout=sys.stdout, stderr=sys.stderr,
                               stdin=sys.stdin)
            sys.exit(r.returncode)
        except KeyboardInterrupt:
            sys.exit(130)
    else:
        os.execv(str(venv_python), [str(venv_python)] + sys.argv)


def _bootstrap_pip_courant():
    """Mode --bootstrap=pip : install dans le Python courant (sans venv).
    Stratégie 3 niveaux : standard → --break-system-packages → --user.
    """
    manquantes = _imports_manquants(_DEPS_CRITIQUES + _DEPS_OPTIONNELLES)
    if not manquantes:
        return

    crit_pkgs = [pkg for mod, pkg in _DEPS_CRITIQUES
                 if importlib.util.find_spec(mod) is None]
    opt_pkgs  = [pkg for mod, pkg in _DEPS_OPTIONNELLES
                 if importlib.util.find_spec(mod) is None]
    all_pkgs = crit_pkgs + opt_pkgs

    in_venv = (hasattr(sys, "real_prefix") or
               (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix))

    base_cmd = [sys.executable, "-m", "pip", "install", "-q",
                "--disable-pip-version-check"]
    if in_venv:
        strategies = [(base_cmd + all_pkgs, "standard (venv)")]
    else:
        strategies = [
            (base_cmd + all_pkgs,                              "standard"),
            (base_cmd + all_pkgs + ["--break-system-packages"], "--break-system-packages (PEP 668)"),
            (base_cmd + all_pkgs + ["--user"],                  "--user"),
        ]

    print(f"  Installation : {', '.join(all_pkgs)}...")
    last_err = ""
    for cmd, label in strategies:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as e:
            last_err = f"{label}: {e}"; continue
        if r.returncode == 0:
            importlib.invalidate_caches()
            still = [pkg for mod, pkg in _DEPS_CRITIQUES
                     if importlib.util.find_spec(mod) is None]
            if not still:
                print(f"  ✓ Installation OK ({label})")
                return
            last_err = f"pip OK mais imports manquants : {still}"
        else:
            last_err = (r.stderr or r.stdout or "").strip()
            if last_err:
                last_err = "\n  ".join(last_err.split("\n")[-3:])

    # Retry sans optionnelles
    if opt_pkgs and crit_pkgs:
        print(f"  Retry sans optionnelles ({', '.join(opt_pkgs)})...")
        ok, err = _pip_install(sys.executable, crit_pkgs, "courant-crit")
        if ok:
            importlib.invalidate_caches()
            still = [pkg for mod, pkg in _DEPS_CRITIQUES
                     if importlib.util.find_spec(mod) is None]
            if not still:
                print("  ✓ Critiques installées (optionnelles indisponibles).")
                return

    _afficher_erreur_deps(crit_pkgs,
                          hint=f"Dernier message pip : {last_err}" if last_err else "")
    sys.exit(1)


def _installer_deps_et_quitter():
    """--installer-deps : crée ~/.gpxsolar/venv, y installe TOUTES les deps
    (critiques + optionnelles + GUI plateforme) puis quitte SANS lancer la GUI.

    Appelé par les scripts setup_build_* (équivalent du --installer-deps de
    lidar2map). Le venv ainsi équipé sert ensuite à PyInstaller pour le build.
    """
    is_windows = platform.system() == "Windows"
    venv_path  = Path.home() / ".gpxsolar" / "venv"
    venv_bin   = venv_path / ("Scripts" if is_windows else "bin")
    venv_py    = venv_bin / ("python.exe" if is_windows else "python")

    if not venv_py.exists():
        print(f"  Création du venv {venv_path}...")
        try:
            subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)
        except subprocess.CalledProcessError as e:
            print(f"  ERREUR création venv : {e}")
            print("  Installez Python 3.9+ avec le module venv (apt install python3-venv).")
            sys.exit(1)

    crit = [pkg for _, pkg in _DEPS_CRITIQUES]
    opt  = [pkg for _, pkg in _DEPS_OPTIONNELLES]
    print(f"  Installation des dépendances dans {venv_path} (3-5 min)...")
    ok, err = _pip_install(venv_py, crit + opt, "installer-deps")
    if not ok:
        print(f"  Install groupée échouée, retry critiques seules...")
        ok, err = _pip_install(venv_py, crit, "installer-deps-crit")
        if ok:
            for o in opt:
                ok_one, _ = _pip_install(venv_py, [o], f"opt-{o}")
                print(f"    {'✓' if ok_one else '⚠'} {o} : {'OK' if ok_one else 'échec — fonctionnalité réduite'}")
        else:
            print(f"  ERREUR install déps critiques :\n  {err}")
            sys.exit(1)
    print(f"  ✓ Dépendances installées dans {venv_path}")
    sys.exit(0)


def _bootstrap_environnement():
    """Orchestrateur unique du démarrage : choisit le mode et applique."""
    # En mode binaire PyInstaller, les dépendances sont déjà bundlées dans
    # l'exécutable — toute tentative d'install via pip échouerait (sys.executable
    # pointe vers le binaire, pas vers un python utilisable). On consomme quand
    # même les flags --bootstrap=* dans sys.argv pour qu'argparse ne plante pas.
    if getattr(sys, "frozen", False):
        _resoudre_mode_bootstrap()  # uniquement pour nettoyer sys.argv
        if "--installer-deps" in sys.argv:
            sys.argv.remove("--installer-deps")  # no-op en frozen (deps bundlées)
        print("  [bootstrap] mode=frozen (PyInstaller binary) — bootstrap ignoré")
        return

    # --installer-deps : install dédiée pour les scripts de build, puis exit.
    if "--installer-deps" in sys.argv:
        _resoudre_mode_bootstrap()  # nettoie d'éventuels --bootstrap=*
        _installer_deps_et_quitter()

    mode = _resoudre_mode_bootstrap()
    print(f"  [bootstrap] mode={mode} python={sys.executable}")
    if mode == "none":
        manquantes = _imports_manquants(_DEPS_CRITIQUES)
        if manquantes:
            _afficher_erreur_deps(manquantes, hint="Mode --bootstrap=none actif.")
            sys.exit(1)
        print("  [bootstrap] toutes les dépendances critiques sont importables")
        return
    if mode == "pip":
        _bootstrap_pip_courant()
        return
    if mode == "force":
        _bootstrap_venv_auto(force=True)
        return
    # mode == "auto"
    _bootstrap_venv_auto(force=False)


_bootstrap_environnement()

# Configuration logging par défaut (peut être surchargée par main()).
# Sans cette config initiale, les logs émis pendant l'import (monkeypatch
# pysolar, etc.) seraient perdus.
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%H:%M:%S",
    )

# ====================================================================
#  IMPORTS STANDARDS ET TIERS
# ====================================================================
import argparse
import traceback
import math
import json
import queue
import threading
import urllib.parse
import tempfile
import shutil
import re
import concurrent.futures
from datetime import datetime, timedelta
from collections import OrderedDict

# Les imports tiers (maintenant disponibles)
import pytz
# NOTE — imports différés pour accélérer le démarrage GUI :
#   srtm           (~0.9 s)  : importé à la demande dans HGTDataManager._init_srtm
#   pandas         (~0.75 s) : importé juste avant la sauvegarde CSV
#   timezonefinder (~1.1 s)  : importé dans _LazyTimezoneFinder._get()
# Total économisé : ~2.7 s sur le warm start. Ces modules sont chargés au
# 1er calcul (clic "Lancer"), pas à l'ouverture de la GUI.

import gpxpy
import gpxpy.gpx
import requests
import numpy as np

# Essai d'import de py7zr
try:
    import py7zr
    PY7ZR_AVAILABLE = True
except ImportError:
    PY7ZR_AVAILABLE = False

# Essai d'import de pyproj
try:
    from pyproj import Transformer
    PYPROJ_AVAILABLE = True
except ImportError:
    PYPROJ_AVAILABLE = False

# Essai d'import de rasterio
try:
    import rasterio
    import rasterio.features
    from rasterio.windows import Window
    
    from rasterio.transform import rowcol, from_origin
    RASTERIO_AVAILABLE = True
except ImportError:
    RASTERIO_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import simplekml
    SIMPLEKML_AVAILABLE = True
except ImportError:
    SIMPLEKML_AVAILABLE = False

try:
    from shapely.geometry import Polygon, MultiPolygon, shape, Point, LineString
    from shapely.strtree import STRtree
    from shapely.ops import transform
    
    SHAPELY_AVAILABLE = True
except ImportError:
    SHAPELY_AVAILABLE = False

# Numba en lazy load : son import (~3-4 s, charge llvmlite.dll 76 Mo) est
# différé jusqu'au 1er calcul. La GUI s'ouvre instantanément, le coût est
# payé au clic "Lancer le calcul". Avec @cache=True les runs suivants
# rechargent le binaire compilé depuis le cache.
NUMBA_AVAILABLE = None   # tri-état : None=non tenté, True=loaded, False=indispo
jit = None
prange = range  # fallback Python (boucles séquentielles)


def _try_load_numba():
    """Importe numba à la demande. Idempotent.

    Au premier appel : tente l'import, applique @jit aux 3 kernels
    chauds (wc_to_height, compute_ray_intersections_detailed,
    _nearest_seg_with_param) et rebind les globales.
    Aux appels suivants : no-op.
    """
    global NUMBA_AVAILABLE, jit, prange
    global wc_to_height, compute_ray_intersections_detailed, _nearest_seg_with_param

    if NUMBA_AVAILABLE is not None:
        return NUMBA_AVAILABLE

    try:
        from numba import jit as _jit, prange as _prange
    except ImportError:
        NUMBA_AVAILABLE = False
        return False

    jit = _jit
    prange = _prange
    NUMBA_AVAILABLE = True
    logging.info("Numba chargé — JIT des kernels (cache disque réutilisé si présent)")

    # Re-compile les 3 kernels chauds avec @jit, en remplaçant les versions
    # pure-Python définies plus bas.
    @_jit(nopython=True)
    def _wc_jit(wc_values):
        heights = np.empty(len(wc_values), dtype=np.float64)
        for i in range(len(wc_values)):
            val = int(wc_values[i])
            if val == 10:   heights[i] = 15.0
            elif val == 20: heights[i] = 3.0
            elif val == 30: heights[i] = 0.5
            elif val == 40: heights[i] = 0.5
            elif val == 50: heights[i] = 0.0
            elif val == 60: heights[i] = 0.0
            elif val == 70: heights[i] = 0.0
            elif val == 80: heights[i] = 0.0
            elif val == 90: heights[i] = 2.0
            elif val == 95: heights[i] = 10.0
            elif val == 100: heights[i] = 0.2
            else: heights[i] = 0.0
        return heights

    @_jit(nopython=True, parallel=True, cache=True, fastmath=True)
    def _ray_jit(obstacle_profile, ground_profile, object_heights, ray_altitudes, tolerance):
        n_rays, n_steps = obstacle_profile.shape
        relief_is_blocking = np.zeros(n_rays, dtype=np.bool_)
        veg_is_blocking = np.zeros(n_rays, dtype=np.bool_)
        for i in _prange(n_rays):
            for j in range(n_steps):
                if not relief_is_blocking[i] and ground_profile[i, j] > (ray_altitudes[i, j] + tolerance):
                    relief_is_blocking[i] = True
                if not veg_is_blocking[i] and (obstacle_profile[i, j] > (ray_altitudes[i, j] + tolerance)) and (object_heights[i, j] > 0):
                    veg_is_blocking[i] = True
        return relief_is_blocking, veg_is_blocking

    @_jit(nopython=True, parallel=True, cache=True, fastmath=True)
    def _seg_jit(xs, ys, x1, y1, x2, y2):
        n = xs.size
        m = x1.size
        best_idx = np.empty(n, dtype=np.int64)
        best_t   = np.empty(n, dtype=np.float64)
        for i in _prange(n):
            px = xs[i]; py = ys[i]
            min_d2 = 1.0e300
            bi = 0; bt = 0.0
            for j in range(m):
                ax = x1[j]; ay = y1[j]
                bx = x2[j]; by = y2[j]
                dx = bx - ax; dy = by - ay
                L2 = dx*dx + dy*dy
                if L2 < 1.0e-12:
                    qx = ax; qy = ay; t = 0.0
                else:
                    t = ((px - ax)*dx + (py - ay)*dy) / L2
                    if t < 0.0:   t = 0.0
                    elif t > 1.0: t = 1.0
                    qx = ax + t*dx; qy = ay + t*dy
                ex = px - qx; ey = py - qy
                d2 = ex*ex + ey*ey
                if d2 < min_d2:
                    min_d2 = d2; bi = j; bt = t
            best_idx[i] = bi; best_t[i] = bt
        return best_idx, best_t

    wc_to_height = _wc_jit
    compute_ray_intersections_detailed = _ray_jit
    _nearest_seg_with_param = _seg_jit
    return True


import inspect
import pysolar.solar as pysolar_solar
import pysolar.solartime as pysolartime # AJOUT

def _unwrap_inplace(mod, names):
    for n in names:
        if hasattr(mod, n):
            f = getattr(mod, n)
            try:
                setattr(mod, n, inspect.unwrap(f))
            except Exception:
                pass

# Unwrap des fonctions vues dans TON profil (celles qui coûtent cher)
_unwrap_inplace(pysolar_solar, [
    "get_altitude",
    "get_azimuth",
    "get_position",          # alt + az en UN seul calcul topocentrique
    "get_topocentric_position",
    "get_nutation",
    "get_coeff",
    "get_geocentric_longitude",
    "get_heliocentric_longitude",
])

_unwrap_inplace(pysolartime, [

    "get_julian_solar_day",

])



# Monkeypatch radical pour désactiver le décorateur tzinfo_check

try:

    import pysolar.tzinfo_check as tz_check # Renommer pour éviter confusion

    import functools # Nécessaire pour functools.wraps

    def no_op_func_with_check(f):

        # Cette fonction va remplacer le décorateur. Elle prend la fonction "f"

        # et retourne un wrapper qui appelle simplement "f"

        # sans effectuer les vérifications de tzinfo.

        @functools.wraps(f) # Conserver les métadonnées de la fonction originale

        def wrapper(*args, **kwargs):

            return f(*args, **kwargs)

        return wrapper

    

    # Remplacer le décorateur original par notre version no-op

    tz_check.func_with_check = no_op_func_with_check

    logging.debug("Monkeypatch pysolar.tzinfo_check.func_with_check appliqué.")

except Exception as e:

    logging.warning(f"Échec monkeypatch pysolar.tzinfo_check: {e}")



# Remplacement dur: ne plus utiliser les fonctions décorées exposées par pysolar.solar



# (on appelle directement le "noyau" via les fonctions internes déjà importées par le module) 




def fast_altitude(lat, lon, dt_utc):



    # dt_utc doit être timezone-aware UTC (tu le garantis déjà)



    # Correction: utiliser '__wrapped__' pour le test hasattr et l'appel



    if hasattr(pysolar_solar.get_altitude, "__wrapped__"):



        return pysolar_solar.get_altitude.__wrapped__(lat, lon, dt_utc)



    else:



        return pysolar_solar.get_altitude(lat, lon, dt_utc)







def fast_azimuth(lat, lon, dt_utc):



    if hasattr(pysolar_solar.get_azimuth, "__wrapped__"):



        return pysolar_solar.get_azimuth.__wrapped__(lat, lon, dt_utc)



    else:



        return pysolar_solar.get_azimuth(lat, lon, dt_utc)



def fast_position(lat, lon, dt_utc):
    """Retourne (azimuth, altitude) en calculant la position topocentrique
    UNE seule fois. pysolar.get_position partage get_topocentric_position
    entre alt et az → résultats strictement identiques à un appel séparé de
    get_altitude + get_azimuth, mais ~2× moins de calcul VSOP."""
    f = pysolar_solar.get_position
    if hasattr(f, "__wrapped__"):
        return f.__wrapped__(lat, lon, dt_utc)
    return f(lat, lon, dt_utc)


GET_ALTITUDE = fast_altitude



GET_AZIMUTH  = fast_azimuth

GET_POSITION = fast_position

class TransformerPool:
    """Cache de Transformer pyproj, UN par thread.

    pyproj.Transformer n'est pas thread-safe : deux threads qui appellent
    .transform() sur la MÊME instance peuvent corrompre l'état interne PROJ
    (objet de contexte partagé). compute_shadow_geotiff projette depuis
    plusieurs workers (process_block : Lambert93→WGS84 ; get_values_vec_multi :
    WGS84→Lambert93) → on isole une instance par thread via threading.local().
    Construire un Transformer coûte ~1 ms, payé une fois par thread (4-8).
    C'est le pattern recommandé par la doc pyproj plutôt qu'un lock global."""
    _local = threading.local()

    @classmethod
    def wgs84_to_lambert(cls):
        t = getattr(cls._local, "w2l", None)
        if t is None:
            t = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)
            cls._local.w2l = t
        return t

    @classmethod
    def lambert_to_wgs84(cls):
        t = getattr(cls._local, "l2w", None)
        if t is None:
            t = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
            cls._local.l2w = t
        return t







class LRUTileCache:
    def __init__(self, max_size=20):
        self.cache = OrderedDict()
        self.max_size = max_size
        self.eviction_callback = None
    
    def __contains__(self, key):
        return key in self.cache

    def get(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None
    
    def put(self, key, value):
        if len(self.cache) >= self.max_size:
            evicted_key, evicted_value = self.cache.popitem(last=False)
            if self.eviction_callback:
                self.eviction_callback(evicted_key, evicted_value)
        
        self.cache[key] = value
        self.cache.move_to_end(key)

    def __len__(self):
        return len(self.cache)



# *************************************************************
# CONSTANTES ET CONFIGURATION
# *************************************************************
OBSERVER_EYE_HEIGHT = 1.7
SHADOW_GPX_DIR = 'GPX_Ombres'
CONFIG_FILE = 'gpx_analyzer_config.json'
HISTORY_FILE = 'gpx_analyzer_history.json'
HISTORY_MAX_ENTRIES = 30
# Workers de la carte d'ombre : adaptatif au lieu d'un 4 codé en dur. Le gain
# threads plafonne car pysolar (Python pur) ne relâche pas le GIL et numba
# (parallel=True) sature déjà les cœurs → au-delà de cpu_count on sur-souscrit
# (mesuré : sur 4 cœurs, w=4 bat w=8). cpu_count est donc un défaut sain.
DEFAULT_NUM_WORKERS = os.cpu_count() or 4
WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
WGS84_E2 = 2 * WGS84_F - WGS84_F**2
EARTH_RADIUS = 6371000.0

def _bilinear_sample_raster(data_arr, rows_f, cols_f, nodata=-9999, fallback=0.0):
    """Échantillonnage bilinéaire vectorisé d'un raster 2D.

    Args:
        data_arr : np.ndarray 2D (H, W)
        rows_f, cols_f : np.ndarray float64 — indices de pixel (peuvent être
            fractionnaires). Les points hors-bornes reçoivent `fallback`.
        nodata : valeur sentinelle ; si l'un des 4 voisins est nodata, on
            retombe sur le voisin entier (NN) ou `fallback` si lui aussi.
        fallback : valeur par défaut (typiquement 0.0).
    Returns:
        np.ndarray float64 de même longueur que rows_f.
    """
    n = rows_f.size
    out = np.full(n, fallback, dtype=np.float64)
    if n == 0:
        return out

    h, w = data_arr.shape
    r0 = np.floor(rows_f).astype(np.int32)
    c0 = np.floor(cols_f).astype(np.int32)
    r1 = r0 + 1
    c1 = c0 + 1
    fy = (rows_f - r0).astype(np.float64)
    fx = (cols_f - c0).astype(np.float64)

    valid = (r0 >= 0) & (r1 < h) & (c0 >= 0) & (c1 < w)
    if not np.any(valid):
        return out

    r0v = r0[valid]; c0v = c0[valid]
    r1v = r1[valid]; c1v = c1[valid]
    fxv = fx[valid]; fyv = fy[valid]

    q00 = data_arr[r0v, c0v].astype(np.float64)
    q01 = data_arr[r0v, c1v].astype(np.float64)
    q10 = data_arr[r1v, c0v].astype(np.float64)
    q11 = data_arr[r1v, c1v].astype(np.float64)

    nd_mask = (q00 == nodata) | (q01 == nodata) | (q10 == nodata) | (q11 == nodata)

    vals = (q00 * (1.0 - fxv) * (1.0 - fyv) +
            q01 * fxv         * (1.0 - fyv) +
            q10 * (1.0 - fxv) * fyv         +
            q11 * fxv         * fyv)

    if np.any(nd_mask):
        # Fallback NN sur le voisin entier (r0, c0)
        nn = np.where(q00 == nodata, fallback, q00)
        vals = np.where(nd_mask, nn, vals)

    out[valid] = vals
    return out


# Approximation equirectangulaire : excellente pour petits segments (ex. ~10 m)
def equirect_m_vec(lat1, lon1, lat2, lon2, radius=EARTH_RADIUS):
    lat1r = np.deg2rad(lat1)
    lat2r = np.deg2rad(lat2)
    dlat = lat2r - lat1r
    dlon = np.deg2rad(lon2 - lon1)
    x = dlon * np.cos((lat1r + lat2r) * 0.5)
    y = dlat
    return radius * np.sqrt(x * x + y * y)

# Ajout pour le cache solaire
SOLAR_ROUND_SEC = 600 # Par défaut 300 secondes (5 min) pour la quantification du temps
SOLAR_ROUND_DEG = 2e-3 # Par défaut 1e-3 degrés pour la quantification des coordonnées (environ 111m)


class _BoundedDictCache:
    """Cache dict borné style LRU — interface compatible dict pour SOLAR_CACHE.
    Évite la croissance illimitée du cache solaire (auparavant un `dict` global
    qui pouvait monter à des centaines de MB sur de longues sessions / grosses
    cartes d'ombre).
    """
    # _lock : SOLAR_CACHE est lu/écrit par les workers de compute_shadow_geotiff
    # (ThreadPoolExecutor). get()+move_to_end() et popitem()+setitem() ne sont
    # pas atomiques → sans lock, un thread peut move_to_end une clé qu'un autre
    # vient de popitem → KeyError intermittent. Le GIL protège la mémoire, pas
    # cette séquence. Lock léger, contention négligeable vs le coût pysolar.
    __slots__ = ('_cache', 'max_size', '_lock')

    def __init__(self, max_size=500_000):
        self._cache = OrderedDict()
        self.max_size = max_size
        self._lock = threading.Lock()

    def get(self, key, default=None):
        with self._lock:
            v = self._cache.get(key)
            if v is None:
                return default
            self._cache.move_to_end(key)
            return v

    def __getitem__(self, key):
        with self._lock:
            v = self._cache[key]
            self._cache.move_to_end(key)
            return v

    def __setitem__(self, key, value):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            elif len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            self._cache[key] = value

    def __contains__(self, key):
        with self._lock:
            return key in self._cache

    def __len__(self):
        return len(self._cache)

    def clear(self):
        with self._lock:
            self._cache.clear()


SOLAR_CACHE = _BoundedDictCache(max_size=500_000)

class MissingDataError(Exception):
    pass

TZ_CACHE = {}
TZ_CACHE_LOCK = threading.Lock() 

# *************************************************************
# GESTION DE LA CONFIGURATION
# *************************************************************
def save_config(data: dict) -> None:
    """Sauvegarde la configuration utilisateur dans un fichier JSON local."""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except OSError as e:
        logging.warning(f"Sauvegarde de la configuration impossible : {e}")


def save_history(cfg: dict, duration_s: float, output_path: str = "") -> None:
    """Ajoute une entrée à l'historique (en tête). Conserve HISTORY_MAX_ENTRIES."""
    try:
        history = load_history()
        entry = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "duree": f"{int(duration_s)//60}m {int(duration_s)%60}s" if duration_s else "—",
            "duree_s": int(duration_s or 0),
            "output": output_path or "",
            "gpx_name": os.path.basename(cfg.get('gpx_file', '')) if cfg.get('gpx_file') else '',
            "dem_source": cfg.get('dem_source', ''),
            "date_rando": cfg.get('date', ''),
            "time_rando": cfg.get('time', ''),
            "analysis_type": cfg.get('analysis_type', ''),
            "params": cfg,
        }
        history.insert(0, entry)
        history = history[:HISTORY_MAX_ENTRIES]
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as e:
        logging.warning(f"Sauvegarde de l'historique impossible : {e}")


def load_history() -> list:
    """Charge la liste des entrées d'historique (liste vide si absent/corrompu)."""
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
    except (OSError, ValueError) as e:
        logging.warning(f"Lecture historique impossible : {e}")
    return []


def clear_history() -> bool:
    """Réinitialise complètement l'historique. Retourne True si OK."""
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f)
        return True
    except OSError as e:
        logging.warning(f"Suppression historique impossible : {e}")
        return False


def load_config() -> dict:
    """Charge la configuration depuis un fichier JSON. Retourne {} si absent."""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except (OSError, ValueError) as e:
        logging.warning(f"Chargement de la configuration impossible : {e}")
    return {}

# *************************************************************
# GESTIONNAIRE DE LOGS POUR LINTERFACE GRAPHIQUE
# *************************************************************
class QueueHandler(logging.Handler):
    """Classe pour envoyer les enregistrements de log à une file d'attente."""
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))

# *************************************************************
# FONCTIONS UTILITAIRES
# *************************************************************

def generate_time_options():
    """Génère une liste d'heures au format HH:MM par incréments de 30 minutes."""
    times = []
    for h in range(24):
        for m in ['00', '30']:
            times.append(f"{h:02d}:{m}")
    return times

def get_meters_per_degree_wgs84(lat):
    lat_rad = math.radians(lat)
    sin_lat = math.sin(lat_rad)
    M = WGS84_A * (1 - WGS84_E2) / (1 - WGS84_E2 * sin_lat**2)**1.5
    N = WGS84_A / (1 - WGS84_E2 * sin_lat**2)**0.5
    m_per_deg_lat = M * math.pi / 180.0
    m_per_deg_lon = N * math.cos(lat_rad) * math.pi / 180.0
    return m_per_deg_lat, m_per_deg_lon

def get_meters_per_degree_wgs84_vec(lats):
    """Vectorized version of get_meters_per_degree_wgs84."""
    lat_rad = np.radians(lats)
    sin_lat = np.sin(lat_rad)
    
    # Calculate M (radius of curvature in the meridian plane)
    m_num = WGS84_A * (1 - WGS84_E2)
    m_den = (1 - WGS84_E2 * sin_lat**2)**1.5
    M = m_num / m_den
    
    # Calculate N (radius of curvature in the prime vertical)
    n_den = (1 - WGS84_E2 * sin_lat**2)**0.5
    N = WGS84_A / n_den
    
    # Calculate meters per degree
    m_per_deg_lat = M * np.pi / 180.0
    m_per_deg_lon = N * np.cos(lat_rad) * np.pi / 180.0
    
    return m_per_deg_lat, m_per_deg_lon



# Fonctions pour le cache solaire
def _q_time(dtutc, step_s=SOLAR_ROUND_SEC):
    ts = int(dtutc.timestamp())
    return ts - (ts % step_s)

def _q_coord(x, step=SOLAR_ROUND_DEG):
    return round(x / step) * step

def _q_coord_int(x, precision_factor=1e5):
    # Quantifie une coordonnée flottante en un entier pour la clé de cache
    return int(x * precision_factor + 0.5)

def solar_altaz_cached(lat, lon, dtutc, step_s=SOLAR_ROUND_SEC):
    """Cache solaire quantifié temporellement et spatialement.
    Clé alignée avec solar_altaz_cached_vec pour un cache PARTAGÉ
    (auparavant chaque variante utilisait un schéma de quantification
    différent — round(lat,4)+datetime vs _q_coord+int — donc le cache
    était fragmenté et aucune entrée n'était jamais réutilisée entre
    les deux fonctions)."""
    lat_q = _q_coord(lat)
    lon_q = _q_coord(lon)
    ts_q  = _q_time(dtutc, step_s=step_s)
    key = (float(lat_q), float(lon_q), int(ts_q))

    v = SOLAR_CACHE.get(key)
    if v is not None:
        return v

    az, alt = GET_POSITION(lat, lon, dtutc)

    SOLAR_CACHE[key] = (alt, az)
    return alt, az

def solar_altaz_cached_vec(lats, lons, ts, step_s=SOLAR_ROUND_SEC):
    """Version vectorisée + cache. `ts` = timestamps UTC (float, secondes
    depuis l'epoch). La quantification (temps + coord) est faite en NumPy
    pour la clé de cache ; pour les misses, pysolar est appelé avec l'heure
    ORIGINALE reconstruite depuis ts[i] (résultat identique au calcul d'origine).

    On prend des timestamps plutôt que des datetime pour éviter le double
    aller-retour datetime↔timestamp : l'appelant (process_block, simulatehike)
    possède déjà les timestamps, et on ne matérialise un objet datetime que
    pour les misses de cache (rare quand la quantification 60 s mord)."""
    n = len(lats)
    alts = np.empty(n, dtype=np.float64)
    azs  = np.empty(n, dtype=np.float64)
    if n == 0:
        return alts, azs

    lats_arr = np.asarray(lats, dtype=np.float64)
    lons_arr = np.asarray(lons, dtype=np.float64)
    ts_arr   = np.asarray(ts,   dtype=np.float64)

    # Quantification spatiale vectorisée (cohérent avec _q_coord)
    inv = 1.0 / SOLAR_ROUND_DEG
    lat_q = np.round(lats_arr * inv) * SOLAR_ROUND_DEG
    lon_q = np.round(lons_arr * inv) * SOLAR_ROUND_DEG

    # Quantification temporelle vectorisée (cohérent avec _q_time)
    step_i = int(step_s)
    ts_q = (ts_arr.astype(np.int64) // step_i) * step_i  # entiers (timestamp UTC)

    # Lookup cache + résolution des misses (datetime construit uniquement au miss)
    for i in range(n):
        key = (float(lat_q[i]), float(lon_q[i]), int(ts_q[i]))
        v = SOLAR_CACHE.get(key)
        if v is not None:
            alts[i], azs[i] = v
        else:
            dt_i = datetime.fromtimestamp(float(ts_arr[i]), tz=pytz.utc)
            z, a = GET_POSITION(lats_arr[i], lons_arr[i], dt_i)
            alts[i] = a; azs[i] = z
            SOLAR_CACHE[key] = (a, z)

    return alts, azs

DEPARTMENT_CACHE = {} # Cache pour les résultats de get_department_from_coords

def get_department_from_coords(lat, lon):
    cache_key = (_q_coord_int(lat), _q_coord_int(lon)) # Utiliser la quantification entière pour la clé de cache
    with TZ_CACHE_LOCK: # Using the existing TZ_CACHE_LOCK for thread safety, assuming it's appropriate here.
        if cache_key in DEPARTMENT_CACHE:
            return DEPARTMENT_CACHE[cache_key]

        try:
            url = f"https://geo.api.gouv.fr/communes?lat={lat}&lon={lon}"
            response = requests.get(url)
            response.raise_for_status()  # Lève une exception pour les codes d'état HTTP d'erreur (4xx ou 5xx) 
            data = response.json()

            if data:
                # L'API peut retourner plusieurs communes si le point est à une intersection,
                # nous prenons la première qui est généralement la plus pertinente.
                commune_info = data[0]
                department_name = commune_info.get('departement', {}).get('nom')
                dept_code = commune_info.get('departement', {}).get('code')
                
                # Si les informations de département ne sont pas directement dans la commune,
                # il faut parfois faire un appel supplémentaire.
                if not department_name and 'codeDepartement' in commune_info:
                    department_code_from_commune = commune_info['codeDepartement']
                    dept_url = f"https://geo.api.gouv.fr/departements/{department_code_from_commune}"
                    dept_response = requests.get(dept_url)
                    dept_response.raise_for_status()
                    dept_data = dept_response.json()
                    if dept_data:
                        department_name = dept_data.get('nom')
                        dept_code = dept_data.get('code')

                if department_name and dept_code:
                    DEPARTMENT_CACHE[cache_key] = dept_code # Mettre en cache
                    return dept_code
                else:
                    logging.warning(f"Impossible de trouver les informations de département pour les coordonnées {lat}, {lon}.")
                    DEPARTMENT_CACHE[cache_key] = None # Mettre en cache un résultat None pour éviter de refaire l'appel
                    return None
            else:
                logging.warning(f"Aucune commune trouvée pour les coordonnées {lat}, {lon}.")
                DEPARTMENT_CACHE[cache_key] = None # Mettre en cache un résultat None pour éviter de refaire l'appel
                return None
        except requests.exceptions.RequestException as e:
            logging.warning(f"Impossible de contacter l'API Géo pour le département: {e}")
            DEPARTMENT_CACHE[cache_key] = None # Mettre en cache un résultat None pour éviter de refaire l'appel
            return None
        except (IndexError, KeyError, ValueError) as e: # Add ValueError for JSON decoding errors
            logging.warning(f"Réponse inattendue de l'API Géo pour {lat}, {lon}: {e}")
            DEPARTMENT_CACHE[cache_key] = None # Mettre en cache un résultat None pour éviter de refaire l'appel
            return None




def wc_to_height(wc_values):
    """Convertit codes WorldCover → hauteurs. Version pure-NumPy (par défaut).
    Remplacée par une version Numba jit au 1er calcul si numba est disponible
    (cf. _try_load_numba)."""
    wc = np.asarray(wc_values, dtype=np.int32)
    heights = np.zeros(wc.shape, dtype=np.float64)
    heights[wc == 10]  = 15.0
    heights[wc == 20]  = 3.0
    heights[wc == 30]  = 0.5
    heights[wc == 40]  = 0.5
    heights[wc == 90]  = 2.0
    heights[wc == 95]  = 10.0
    heights[wc == 100] = 0.2
    # 50/60/70/80/autres → 0.0 (déjà initialisé)
    return heights

class VegetationManager:
    VEGETATION_HEIGHTS = {10: 15.0, 20: 3.0, 30: 0.5, 40: 0.5, 50: 0.0, 60: 0.0, 70: 0.0, 80: 0.0, 90: 2.0, 95: 10.0, 100: 0.2}
    def __init__(self, worldcover_dir="WorldCover", auto_download=True, progress_callback=None):
        self.worldcover_dir, self.auto_download = worldcover_dir, auto_download
        self.datasets, self.height_cache, self.missing_tiles = {}, {}, set()
        self.enabled = False
        self.progress_callback = progress_callback 
        if RASTERIO_AVAILABLE:
            if not os.path.exists(worldcover_dir): os.makedirs(worldcover_dir, exist_ok=True)
            self._load_tiles()
            if self.datasets or self.auto_download: self.enabled = True
            
    @staticmethod
    def tile_indices_to_name(lat_tile, lon_tile):
        """Conversion indices → string (appelée rarement)"""
        ns = 'N' if lat_tile >= 0 else 'S'
        ew = 'E' if lon_tile >= 0 else 'W'
        return f"{ns}{abs(lat_tile):02d}{ew}{abs(lon_tile):03d}"

    def _download_tile(self, tile_name):
        url = f"https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile_name}_Map.tif"
        output_path = os.path.join(self.worldcover_dir, os.path.basename(url))
        if os.path.exists(output_path):
             return self._load_single_tile(os.path.basename(url))

        logging.info(f"Téléchargement Végétation {tile_name} (~1GB)...")
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            block_size = 8192
            downloaded_size = 0

            with open(output_path, 'wb') as f: 
                for chunk in response.iter_content(chunk_size=block_size):
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    if self.progress_callback and total_size > 0:
                        progress = (downloaded_size / total_size) * 100
                        self.progress_callback(50 + progress * 0.05, f"DL WorldCover {tile_name}: {progress:.1f}%")

            return self._load_single_tile(os.path.basename(output_path))
        except Exception as e:
            logging.error(f"Erreur DL Végétation: {e}")
            if os.path.exists(output_path): os.remove(output_path)
            return False
            
    def _load_tiles(self):
        logging.info(f" > Scan du dossier Végétation ({self.worldcover_dir})...")
        count = sum(1 for f in os.listdir(self.worldcover_dir) if f.endswith('.tif') and self._load_single_tile(f))
        if count > 0: logging.info(f"   - {count} tuiles végétation chargées.")
        if self.datasets: self.enabled = True
        
    def _load_single_tile(self, filename):
        try:
            ds = rasterio.open(os.path.join(self.worldcover_dir, filename))
            tile_name = filename.split('_')[-2]
            lat_sign = 1 if tile_name[0] == 'N' else -1
            lon_sign = 1 if tile_name[3] == 'E' else -1
            lat_tile = lat_sign * int(tile_name[1:3])
            lon_tile = lon_sign * int(tile_name[4:7])
            tile_key = (lat_tile, lon_tile)

            self.datasets[tile_key] = {
                'dataset': ds, 
                'bounds': ds.bounds, 
                'transform': ds.transform, 
                'inv_transform': ~ds.transform
            }
            return True
        except Exception as e:
            logging.error(f"Erreur chargement tuile végétation {filename}: {e}")
            return False

    def get_vegetation_heights_vec(self, lats, lons):
        if not self.enabled: 
            return np.zeros_like(lats, dtype=np.float64)
        
        heights = np.zeros_like(lats, dtype=np.float64)
        
        lat_tiles = (lats // 3).astype(np.int32) * 3
        lon_tiles = (lons // 3).astype(np.int32) * 3
        
        tile_indices = np.column_stack((lat_tiles, lon_tiles))
        unique_indices, inverse = np.unique(tile_indices, axis=0, return_inverse=True)
        
        for idx, (lat_t, lon_t) in enumerate(unique_indices):
            tile_key = (int(lat_t), int(lon_t))
            
            if tile_key not in self.datasets:
                tile_name = self.tile_indices_to_name(lat_t, lon_t)
                if self.auto_download and tile_name not in self.missing_tiles:
                    if not self._download_tile(tile_name):
                        self.missing_tiles.add(tile_name)
                if tile_key not in self.datasets: # Re-check after download attempt
                    continue

            data = self.datasets.get(tile_key)
            if data is None: continue

            mask = (inverse == idx)
            lats_tile, lons_tile = lats[mask], lons[mask]
            if lats_tile.size == 0: continue

            rows, cols = rasterio.transform.rowcol(data['transform'], lons_tile, lats_tile)
            rows, cols = np.asarray(rows, dtype=np.int32), np.asarray(cols, dtype=np.int32)
            
            min_row, max_row = np.min(rows), np.max(rows)
            min_col, max_col = np.min(cols), np.max(cols)
            
            min_row_clamp = max(0, min_row)
            min_col_clamp = max(0, min_col)
            
            h_full, w_full = data['dataset'].shape
            max_row_clamp = min(h_full - 1, max_row)
            max_col_clamp = min(w_full - 1, max_col)
            
            window_from_bounds = Window.from_slices((min_row_clamp, max_row_clamp + 1), (min_col_clamp, max_col_clamp + 1))
            
            arr = data['dataset'].read(1, window=window_from_bounds)
            h_window, w_window = arr.shape

            rows_relative = rows - min_row_clamp
            cols_relative = cols - min_col_clamp

            valid_indices_in_window = (rows_relative >= 0) & (rows_relative < h_window) & \
                                      (cols_relative >= 0) & (cols_relative < w_window) & \
                                      (rows >= min_row_clamp) & (rows <= max_row_clamp) & \
                                      (cols >= min_col_clamp) & (cols <= max_col_clamp)
            
            if not np.any(valid_indices_in_window): continue
            
            wc_values = arr[rows_relative[valid_indices_in_window], cols_relative[valid_indices_in_window]]
            
            veg_heights_for_mask = wc_to_height(wc_values)
            
            output_indices = np.where(mask)[0][valid_indices_in_window]
            heights[output_indices] = veg_heights_for_mask
        
        return heights

def compute_lidar_tiles_from_solar_rays_batched(
    points,
    start_time,
    transformer,
    max_distance,
    step,
    tile_size=1000,
    batch_size=256,
    solar_step_s=SOLAR_ROUND_SEC # Ajout
):
    """
    Version vectorisée par paquets du calcul EXACT des tuiles LiDAR
    traversées par les rayons solaires.

    Args:
        points: liste de points GPX
        start_time: datetime
        transformer: pyproj Transformer WGS84 → Lambert93
        max_distance: distance max du rayon (m)
        step: pas du ray marching (m)
        tile_size: taille tuile LiDAR (1000 m)
        batch_size: nombre de points GPX traités simultanément

    Returns:
        Set[(tx, ty)]
    """

    needed_tiles = set()

    # Distances le long du rayon
    distances = np.arange(step, max_distance + step, step, dtype=np.float64)
    nd = distances.size

    # Temps UTC
    if start_time.tzinfo is None:
        dt_utc = start_time.replace(tzinfo=pytz.utc)
    else:
        dt_utc = start_time.astimezone(pytz.utc)
    ts_utc = dt_utc.timestamp()

    # Pré-extraction lat/lon
    lats = np.array([p.latitude for p in points], dtype=np.float64)
    lons = np.array([p.longitude for p in points], dtype=np.float64)
    npts = len(points)

    for i in range(0, npts, batch_size):
        sl = slice(i, min(i + batch_size, npts))

        lat_b = lats[sl]
        lon_b = lons[sl]
        nb = lat_b.size

        # Soleil (scalaire par point, mais hors boucle distance)
        # Utiliser le cache solaire pour les altitudes et azimuts
        sun_alts_batch, sun_azs_batch = solar_altaz_cached_vec(
            lat_b, lon_b, np.full(nb, ts_utc, dtype=np.float64), step_s=solar_step_s)

        sun_alt = sun_alts_batch # Utiliser l'altitude du cache
        valid = sun_alt > 0
        if not np.any(valid):
            continue

        sun_az = sun_azs_batch[valid] # Utiliser l'azimut du cache et filtrer avec valid

        rad_az = np.deg2rad(sun_az)

        sin_az = np.sin(rad_az)[valid]
        cos_az = np.cos(rad_az)[valid]

        # Projection Lambert93
        xs, ys = transformer.transform(lon_b[valid], lat_b[valid])

        xs = np.asarray(xs, dtype=np.float64)
        ys = np.asarray(ys, dtype=np.float64)

        # Broadcasting : (n_points, n_distances)
        X = xs[:, None] + distances[None, :] * sin_az[:, None]
        Y = ys[:, None] + distances[None, :] * cos_az[:, None]

        TX = np.floor_divide(X, tile_size).astype(np.int32)
        TY = np.floor_divide(Y, tile_size).astype(np.int32)

        # Aplatissement → set
        tiles = set(zip(TX.ravel(), TY.ravel()))
        needed_tiles.update(tiles)

    return needed_tiles


class LidarManager:
    """
    Gestionnaire LiDAR avec lazy loading intelligent
    
    Philosophie:
    - Télécharger le minimum upfront (points GPX seulement)
    - Télécharger à la demande pendant le raycasting
    - Cache LRU pour optimiser la RAM
    """
    
    LAYER_MAP = {
        'mnt': "IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93",
        'mnh': "IGNF_LIDAR-HD_MNH_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93",
    }
    
    MAX_CACHE_SIZE = 50  # Nombre max de tuiles en RAM par couche
    MAX_RETRIES = 3      # Nombre de tentatives de téléchargement
    
    def __init__(self, log_func=print, progress_callback=None):
        self.log = log_func
        self.progress_callback = progress_callback
        self.rasters = {'mnt': LRUTileCache(max_size=self.MAX_CACHE_SIZE), 'mnh': LRUTileCache(max_size=self.MAX_CACHE_SIZE)}
        self.transformer = TransformerPool.wgs84_to_lambert()
        self.cache_dir = "LIDAR_CACHE"
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
        self.enabled = False
        self.lock = threading.RLock() # RLock pour les appels imbriqués
        self.downloaded_tiles = set()  # Tuiles téléchargées
        self.used_tiles = set()  # Tuiles effectivement utilisées pour le ray-tracing


    def _compute_average_solar_azimuth(self, points, start_time):
        """
        Calcule l'azimut solaire moyen pour le tracé
        
        Args:
            points: Points GPX
            start_time: Heure de départ de la randonnée
            
        Returns:
            Azimut moyen en degrés (0°=Nord, 90°=Est)
        """
        try:
            # Échantillonner quelques points (pas besoin de tous)
            sample_size = min(20, len(points))
            step = max(1, len(points) // sample_size)
            sampled_points = points[::step]
            
            # Utiliser l'heure de départ fournie
            if start_time.tzinfo is None:
                # Si pas de timezone, utiliser UTC
                dt_utc = start_time.replace(tzinfo=pytz.utc)
            else:
                dt_utc = start_time.astimezone(pytz.utc)
            
            azimuths = []
            for p in sampled_points:
                try:
                    alt, az = solar_altaz_cached(p.latitude, p.longitude, dt_utc)
                    azimuths.append(az)
                except Exception as e:
                    logging.debug(f"Skip point pour azimut solaire moyen: {e}")
            
            if azimuths:
                # Moyenne circulaire pour les angles
                avg_az = np.degrees(np.arctan2(
                    np.mean(np.sin(np.radians(azimuths))),
                    np.mean(np.cos(np.radians(azimuths)))
                ))
                return (avg_az + 360) % 360
            
        except Exception as e:
            self.log(f"⚠️ Impossible de calculer l'azimut solaire: {e}")
        
        return None

    def _is_tile_in_solar_direction(self, point_x, point_y, tile_x, tile_y, 
                                   solar_azimuth, tile_size=1000, margin_degrees=100):
        """
        Vérifie si une tuile est dans la direction du soleil
        
        Args:
            point_x, point_y: Coordonnées Lambert93 du point GPX
            tile_x, tile_y: Indices de la tuile
            solar_azimuth: Azimut solaire en degrés (0°=Nord, 90°=Est)
            margin_degrees: Marge angulaire (100° = ±50° autour du soleil)
            
        Returns:
            True si la tuile peut être éclairée par le soleil
        """
        # Centre de la tuile
        tile_center_x = tile_x * tile_size + tile_size / 2
        tile_center_y = tile_y * tile_size + tile_size / 2
        
        # Vecteur point → tuile
        dx = tile_center_x - point_x
        dy = tile_center_y - point_y
        
        # Angle de la tuile (Lambert93: Nord = +Y)
        tile_angle = (math.degrees(math.atan2(dx, dy)) + 360) % 360
        
        # Différence avec l'azimut solaire
        delta = abs((tile_angle - solar_azimuth + 180) % 360 - 180)
        
        return delta < margin_degrees

    def _calculate_solar_filtered_bbox_tiles(self, points, start_time, use_solar_filter=True):
        """
        Calcule un ensemble de tuiles basé sur la Bbox du GPX et un filtre solaire.
        Retourne un ensemble de tuiles candidates.
        """
        if not points:
            return set()

        # Projection vectorisée WGS84 → Lambert93 (1 appel pyproj pour tous les points)
        n = len(points)
        lats = np.fromiter((p.latitude  for p in points), dtype=np.float64, count=n)
        lons = np.fromiter((p.longitude for p in points), dtype=np.float64, count=n)
        xs, ys = self.transformer.transform(lons, lats)
        xs = np.asarray(xs, dtype=np.float64)
        ys = np.asarray(ys, dtype=np.float64)

        x_min, x_max = float(xs.min()), float(xs.max())
        y_min, y_max = float(ys.min()), float(ys.max())

        tile_size = 1000
        tx_min = int(math.floor(x_min / tile_size))
        tx_max = int(math.floor(x_max / tile_size))
        ty_min = int(math.floor(y_min / tile_size))
        ty_max = int(math.floor(y_max / tile_size))

        solar_azimuth = None
        if use_solar_filter and start_time:
            solar_azimuth = self._compute_average_solar_azimuth(points, start_time)
            if solar_azimuth is not None:
                self.log(f"  ☀️ Azimut solaire moyen: {solar_azimuth:.1f}° (pour filtre grossier)")

        candidate_tiles = set()
        for tx in range(tx_min, tx_max + 1):
            for ty in range(ty_min, ty_max + 1):
                if solar_azimuth is not None:
                    ref_x = (x_min + x_max) / 2
                    ref_y = (y_min + y_max) / 2

                    if not self._is_tile_in_solar_direction(
                        ref_x, ref_y, tx, ty, solar_azimuth
                    ):
                        continue

                candidate_tiles.add((tx, ty))
        
        total_bbox_tiles = (tx_max - tx_min + 1) * (ty_max - ty_min + 1)
        if solar_azimuth and total_bbox_tiles > 0:
            filtered_percent = (1 - len(candidate_tiles) / total_bbox_tiles) * 100
            self.log(f"  - Filtre Bbox Solaire: {len(candidate_tiles)}/{total_bbox_tiles} tuiles candidates ({filtered_percent:.0f}% économisé)")
        else:
            self.log(f"  - Bbox GPX: {len(candidate_tiles)} tuiles candidates")
            
        return candidate_tiles

    # NOTE : download_layers supprimée (méthode publique jamais appelée —
    # remplacée par HGTDataManager.prepare_lidar_data qui utilise directement
    # _calculate_solar_filtered_bbox_tiles + compute_lidar_tiles_from_solar_rays_batched).

    def _ensure_tile_downloaded(self, key, tx, ty):
        """
        Télécharge une tuile si elle n'existe pas sur disque
        (avec gestion des retries pour la robustesse réseau)
        
        Returns:
            True si téléchargement réussi ou fichier déjà présent
            False en cas d'échec après MAX_RETRIES tentatives
        """
        layer_name = self.LAYER_MAP.get(key)
        if not layer_name:
            self.log(f"❌ Erreur: Couche {key} non reconnue")
            return False
        
        x0 = tx * 1000
        y0 = ty * 1000
        tile_bbox = f"{x0},{y0},{x0+1000},{y0+1000}"
        
        cache_filename = f"LIDAR_{key}_L93_1km_{tx}_{ty}.tif"
        cache_path = os.path.join(self.cache_dir, cache_filename)
        
        if os.path.exists(cache_path):
            self.downloaded_tiles.add((tx, ty)) # AJOUT: Ajouter la tuile à downloaded_tiles même si déjà présente

            return True
        
        with self.lock:
            # Double-check après verrouillage
            if os.path.exists(cache_path):
                self.downloaded_tiles.add((tx, ty)) # AJOUT: Ajouter la tuile à downloaded_tiles même si déjà présente
    
                return True
            
            # Téléchargement avec retries
            params = {
                'SERVICE': 'WMS',
                'VERSION': '1.3.0',
                'REQUEST': 'GetMap',
                'LAYERS': layer_name,
                'FORMAT': 'image/geotiff',
                'CRS': 'EPSG:2154',
                'BBOX': tile_bbox,
                'WIDTH': 2000,
                'HEIGHT': 2000,
                'STYLES': ''
            }
            
            for attempt in range(1, self.MAX_RETRIES + 1):
                try:

                    
                    response = requests.get(
                        "https://data.geopf.fr/wms-r",
                        params=params,
                        timeout=90
                    )
                    response.raise_for_status()
                    
                    with open(cache_path, 'wb') as f:
                        f.write(response.content)
                    
                    self.log(f"  ✓ Tuile {tx},{ty} téléchargée")

                    self.downloaded_tiles.add((tx, ty)) # <-- Ceci est self.lidar_manager.downloaded_tiles

                    return True
                    
                except requests.exceptions.Timeout:
                    self.log(f"  ⏱️ Timeout (tentative {attempt}/{self.MAX_RETRIES})")
                    if attempt == self.MAX_RETRIES:
                        self.log(f"  ❌ Abandon après {self.MAX_RETRIES} tentatives")
                        return False
                    
                except requests.exceptions.RequestException as e:
                    self.log(f"  ❌ Erreur réseau: {e}")
                    if attempt == self.MAX_RETRIES:
                        return False
                    
                except Exception as e:
                    self.log(f"  ❌ Erreur inattendue: {e}")
                    return False
        
        return False
    
    def _load_tile_to_ram(self, key, tx, ty):
        """
        ✅ LAZY LOADING: Charge une tuile en RAM (télécharge si nécessaire)
        """
        with self.lock:
            tile_id = (tx, ty)
            
            if self.rasters[key].get(tile_id) is not None:
                return True
            
            cache_filename = f"LIDAR_{key}_L93_1km_{tx}_{ty}.tif"
            cache_path = os.path.join(self.cache_dir, cache_filename)
            
            if not os.path.exists(cache_path):
                self.log(f"🔄 Lazy download: Tuile ({key.upper()}) {tx},{ty} nécessaire pour raycasting...")
                if not self._ensure_tile_downloaded(key, tx, ty):
                    self.log(f"  ⚠️ Échec lazy download pour {tx},{ty}. Altitude par défaut (0m).")
                    return False
                
                if self.progress_callback:
                    self.progress_callback(
                        0,
                        f"Lazy DL: {key.upper()} tuile {tx},{ty}..."
                    )
            
            try:
                with rasterio.open(cache_path) as ds:
                    data = ds.read(1)
                    trans = ds.transform
                    nodata = ds.nodata
                    
                    self.rasters[key].put(tile_id, {
                        'data': data,
                        'transform': trans,
                        'inv_transform': ~trans,
                        'nodata': nodata if nodata is not None else -9999
                    })
                    return True
            except Exception as e:
                self.log(f"  ❌ Erreur lecture {cache_path}: {e}")
                return False
    
    # NOTE : wrapper scalaire get_value supprimé (jamais appelé).

    def get_values_vec_multi(self, layer_keys, lats, lons):
        """Lecture vectorisée de PLUSIEURS couches (ex: ['mnt', 'mnh']) en
        partageant la projection pyproj + le groupement par tuile, qui sont
        identiques pour toutes les couches (mêmes coordonnées).

        Le lock ne couvre QUE la projection et le lazy-load des tuiles ;
        l'extraction NumPy (le gros du coût en carte d'ombre) tourne lock-free
        sur des références de tuiles capturées sous lock — leurs `data` sont
        immuables après chargement et restent vivantes via la référence même
        si le cache LRU les évince entre-temps. Les workers de
        compute_shadow_geotiff peuvent ainsi réellement paralléliser
        l'extraction au lieu de se sérialiser sur un lock global.

        Retourne {layer_key: np.ndarray(len(lats))}.
        """
        n = len(lats)
        out = {k: np.zeros(n, dtype=float) for k in layer_keys}
        if n == 0:
            return out

        tile_size = 1000
        present_layers = [k for k in layer_keys if k in self.rasters]
        if not present_layers:
            return out

        # Projection HORS lock : transformer thread-local (cf. TransformerPool),
        # donc pas de partage d'instance pyproj entre workers → pas besoin de
        # sérialiser. Le groupement par tuile (np.unique) est purement local.
        transformer = TransformerPool.wgs84_to_lambert()
        xs, ys = transformer.transform(lons, lats)
        xs = np.asarray(xs, dtype=np.float64)
        ys = np.asarray(ys, dtype=np.float64)

        # Indices de tuile (grille 1 km) + tuiles uniques via bit-pack 1D
        txs = np.floor_divide(xs, tile_size).astype(np.int32)
        tys = np.floor_divide(ys, tile_size).astype(np.int32)
        tile_keys = (txs.astype(np.int64) << 32) | (tys.astype(np.int64) & 0xFFFFFFFF)
        unique_keys, inverse_indices = np.unique(tile_keys, return_inverse=True)
        tx_unique = (unique_keys >> 32).astype(np.int32)
        ty_unique = (unique_keys & 0xFFFFFFFF).astype(np.int32)
        unique_tiles = [(int(tx_unique[k]), int(ty_unique[k]))
                        for k in range(len(unique_keys))]

        # Lazy-load + capture des références de tuiles : SEULE section sous lock
        # (mutation du cache LRU + chargement disque). Pour des tuiles déjà en
        # RAM, fenêtre minuscule (lookups dict).
        with self.lock:
            tiles_by_layer = {k: {} for k in present_layers}
            for tx, ty in unique_tiles:
                self.used_tiles.add((tx, ty))
                for k in present_layers:
                    if self.rasters[k].get((tx, ty)) is None:
                        self._load_tile_to_ram(k, tx, ty)
                    tile = self.rasters[k].get((tx, ty))
                    if tile is not None:
                        tiles_by_layer[k][(tx, ty)] = tile

        # --- Extraction lock-free (références capturées, data read-only) ---
        for k in present_layers:
            elevs = out[k]
            layer_tiles = tiles_by_layer[k]
            for unique_idx, (tx, ty) in enumerate(unique_tiles):
                tile = layer_tiles.get((tx, ty))
                if tile is None:
                    continue
                mask = (inverse_indices == unique_idx)
                xs_tile = xs[mask]
                ys_tile = ys[mask]
                if xs_tile.size == 0:
                    continue
                data = tile['data']
                nodata = tile.get('nodata', -9999)
                cols_f, rows_f = tile['inv_transform'] * (xs_tile, ys_tile)
                rows = rows_f.astype(np.int32)
                cols = cols_f.astype(np.int32)
                h, w = data.shape
                valid_mask = (rows >= 0) & (rows < h) & (cols >= 0) & (cols < w)
                if not np.any(valid_mask):
                    continue
                vals = data[rows[valid_mask], cols[valid_mask]].astype(float)
                vals[vals == nodata] = 0.0
                output_indices = np.where(mask)[0][valid_mask]
                elevs[output_indices] = vals
        return out

    def get_values_vec(self, layer_key, lats, lons):
        """Récupération vectorisée d'UNE couche (cf. get_values_vec_multi)."""
        return self.get_values_vec_multi([layer_key], lats, lons)[layer_key]



class HGTDataManager:
    SOURCES = {
        'srtm1': {'name': 'SRTM1', 'precision': 8.5, 'resolution': 30, 'coverage': 'Mondiale (60°N-56°S)'},
        'copernicus': {'name': 'Copernicus DEM', 'precision': 4.0, 'resolution': 30, 'coverage': 'Mondiale'},
        'ign_bdalti_25m': {'name': 'IGN BDALTI', 'precision': 1.0, 'resolution': 25, 'coverage': 'France'},
        'ign_rgealti_5m': {'name': 'IGN RGEALTI', 'precision': 0.5, 'resolution': 5, 'coverage': 'France'},
        'ign_lidar_hd': {'name': 'IGN LiDAR HD', 'precision': 0.5, 'resolution': 0.5, 'coverage': 'France'},
    }
    
    def __init__(self, hgt_dir, vegetation_manager=None, source='srtm1', interpolation='bilinear', analysis_resolution=5.0, 
                 max_shadow_distance=1000.0, log_func=print, progress_callback=None, solar_step_s=SOLAR_ROUND_SEC):
        self.hgt_dir = hgt_dir
        self.vegetation_manager = vegetation_manager
        self.source = source.lower()
        self.interpolation = interpolation
        self.log = log_func
        self.analysis_resolution = float(analysis_resolution)
        self.progress_callback = progress_callback
        self.solar_step_s = solar_step_s
        self._shadowmode = 'both'
        self._shadowmode_lock = threading.Lock()

        self.srtm_data = None

        self.ign_grid_tiles = {}
        self.lidar_manager = None
        self.elevation_cache = {}
        self.elevation_cache_lock = threading.Lock()
        
        self.used_tiles = set() # Pour tracer les tuiles utilisées (toutes sources)
        self.downloaded_tiles_info = set() # AJOUT: Pour tracer toutes les tuiles téléchargées (toutes sources)
        self.loaded_in_ram_tiles = set() # AJOUT: Pour tracer les tuiles dont les données sont en RAM

        # NOUVEAU: Gestion du cache LRU pour les tuiles HGT (Copernicus, SRTM, IGN non-LiDAR)
        self.MAX_CACHE_SIZE_HGT = 50 # Nombre max de tuiles HGT en RAM
        self.hgt_rasters = LRUTileCache(max_size=self.MAX_CACHE_SIZE_HGT) # Cache LRU pour les données raster des tuiles HGT (clé: tile_key, valeur: {data, transform, nodata})
        self.hgt_rasters.eviction_callback = self._handle_hgt_eviction
        self.hgt_tile_metadata = {} # Stocke les métadonnées (path, bounds) des tuiles HGT sur disque (clé: tile_key)

        source_info = self.SOURCES.get(self.source, {})
        self.resolution = source_info.get('resolution', 30)
        self.max_distance = max_shadow_distance
        self.step = min(self.analysis_resolution, self.resolution)


        if self.source == 'ign_lidar_hd':
            self.lidar_manager = LidarManager(self.log, self.progress_callback) 
        elif self.source.startswith('ign_'):
            if not PYPROJ_AVAILABLE: raise MissingDataError("pyproj non installé.")
            # Transformer obtenu à la demande via TransformerPool (thread-local),
            # plus d'instance partagée stockée sur self (cf. _get_ign_elevations_vec).

        else:
             if not os.path.exists(hgt_dir): os.makedirs(hgt_dir)

             if self.source == 'srtm1': self._init_srtm()
             elif self.source == 'copernicus': self._init_copernicus()

    @property
    def shadowmode(self):
        with self._shadowmode_lock:
            return self._shadowmode

    @shadowmode.setter
    def shadowmode(self, value):
        with self._shadowmode_lock:
            self._shadowmode = value

    # NOTE : ancienne version non-LRU de _get_ign_elevation supprimée
    # (était écrasée par la 2e définition ligne ~1980 → dead code).

    def prepare_lidar_data(self, points, start_time=None):
        if not self.lidar_manager: return
        
        # Étape 1: Calcul des tuiles candidates (filtre grossier)
        candidate_tiles = self.lidar_manager._calculate_solar_filtered_bbox_tiles(
            points, start_time, use_solar_filter=True
        )
        
        # Étape 2: Calcul des tuiles exactes (corridor vectorisé par paquets)
        exact_tiles = compute_lidar_tiles_from_solar_rays_batched(
            points=points,
            start_time=start_time,
            transformer=self.lidar_manager.transformer,
            max_distance=self.max_distance,
            step=self.step,
            batch_size=256,
            solar_step_s=self.solar_step_s # Ajout
        )

        # Étape 3 : tuiles à télécharger = celles précisément traversées par
        # les rayons solaires (exact_tiles). Le filtre bbox (candidate_tiles)
        # est trop strict pour de longues traces — sur les bords le cône
        # solaire ±50° depuis le centre peut exclure des tuiles correctement
        # détectées par le ray-tracing. On garde candidate_tiles uniquement
        # comme borne supérieure pour le log d'économie ; la vraie sélection
        # est exact_tiles. Bug latent : auparavant l'intersection pouvait
        # supprimer des tuiles légitimes pour des traces longues.
        final_tiles = exact_tiles
        if candidate_tiles:
            dropped = exact_tiles - candidate_tiles
            if dropped:
                logging.debug("LiDAR : %d tuile(s) hors bbox solaire conservées "
                              "(ray-tracing exact)", len(dropped))

        # --- Boucle de chargement (adaptée de l'original) ---
        overall_current_progress_start = 5.0
        total_download_progress_range = 45.0
        total_tiles_to_process = len(final_tiles) * 2 # MNT et MNH
        
        if self.progress_callback:
            self.progress_callback(
                overall_current_progress_start,
                f"Chargement Hybride LiDAR: {total_tiles_to_process} tuiles..."
            )
            
        processed_tiles = 0
        for key in ['mnt', 'mnh']:
            self.lidar_manager.rasters.setdefault(key, {})
            for tx, ty in final_tiles:
                self.lidar_manager._ensure_tile_downloaded(key, tx, ty)
                self.lidar_manager._load_tile_to_ram(key, tx, ty)
                
                processed_tiles += 1
                if self.progress_callback and total_tiles_to_process > 0:
                    progress_value = (overall_current_progress_start +
                                    (processed_tiles / total_tiles_to_process) *
                                    total_download_progress_range)
                    self.progress_callback(
                        progress_value,
                        f"Chargement: {processed_tiles}/{total_tiles_to_process} tuiles..."
                    )
        
        if self.progress_callback:
            self.progress_callback(
                overall_current_progress_start + total_download_progress_range,
                "Chargement LiDAR terminé. Lazy loading activé."
            )
            
        # AJOUT: Ajouter toutes les tuiles téléchargées par LidarManager à downloaded_tiles_info
        tile_size = 1000

        for tx, ty in self.lidar_manager.downloaded_tiles:
            x0 = tx * tile_size
            y0 = ty * tile_size
            self.downloaded_tiles_info.add(('lidar', (tx, ty), (x0, y0, x0 + tile_size, y0 + tile_size)))


        # AJOUT: Ajouter toutes les tuiles chargées en RAM par LidarManager à loaded_in_ram_tiles
        for key, lru_cache_obj in self.lidar_manager.rasters.items():
            for tile_id, _ in lru_cache_obj.cache.items():
                tx, ty = tile_id
                x0 = tx * tile_size
                y0 = ty * tile_size
                self.loaded_in_ram_tiles.add(('lidar', tile_id, (x0, y0, x0 + tile_size, y0 + tile_size)))



        self.lidar_manager.enabled = True
        total_loaded = sum(len(v.cache) for v in self.lidar_manager.rasters.values())
        self.log(f"✓ LiDAR initialisé:")
        self.log(f"  - Tuiles en RAM: {total_loaded} (~{total_loaded * 15:.0f} MB)")







    def get_ground_elevations_vec(self, lats, lons):
        """Méthode vectorielle pour l'altitude du sol."""
        if self.lidar_manager:
            return self.lidar_manager.get_values_vec('mnt', lats, lons)
        
        if self.source == 'srtm1' and self.srtm_data:
            elevs = [(self.srtm_data.get_elevation(lat, lon) or 0) for lat, lon in zip(lats, lons)]
            return np.array(elevs)
        
        if self.source == 'copernicus':
            # Implémenter _get_copernicus_elevations_vec
            return self._get_copernicus_elevations_vec(lats, lons)

        if self.source.startswith('ign_'):
            # Implémenter _get_ign_elevations_vec
            return self._get_ign_elevations_vec(lats, lons)
        
        # Fallback si aucune source vectorisée n'est disponible (devrait être rare)
        # Bug fix : self.get_ground_elevation n'existait pas → AttributeError.
        elevs = [self.get_elevation(lat, lon) for lat, lon in zip(lats, lons)]
        return np.array(elevs)

    def get_object_heights_vec(self, lats, lons):
        """Méthode vectorielle pour la hauteur des objets/végétation."""
        if self.lidar_manager:
            # MNH est déjà une hauteur relative (pas besoin de soustraire MNT)
            return self.lidar_manager.get_values_vec('mnh', lats, lons)

        if self.vegetation_manager:
            return self.vegetation_manager.get_vegetation_heights_vec(lats, lons)

        return np.zeros_like(lats, dtype=float)

    def get_ground_and_object_elevations_vec(self, lats, lons):
        """Renvoie (sol, hauteur_objets) en UNE passe quand c'est possible.

        Source LiDAR : MNT et MNH partagent projection + groupement de tuiles
        → un seul get_values_vec_multi au lieu de deux passes redondantes
        (projection pyproj + np.unique + lock x2). Autres sources : sol (DEM)
        et objets (végétation WorldCover) viennent de données distinctes, pas
        de partage possible → on retombe sur les deux appels séparés."""
        if self.lidar_manager:
            r = self.lidar_manager.get_values_vec_multi(['mnt', 'mnh'], lats, lons)
            return r['mnt'], r['mnh']
        return (self.get_ground_elevations_vec(lats, lons),
                self.get_object_heights_vec(lats, lons))

    def _get_bdalti_download_info(self, department_code):
        dept_id = department_code.zfill(3) if department_code.isdigit() else department_code
        base_url = "https://geoservices.ign.fr/telechargement-api/BDALTI"
        params = {"zone": f"D{dept_id}"}
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        logging.info(f" > Interrogation de l'API IGN (HTML): {base_url} pour BDALTI zone={params['zone']}")
        try:
            response = requests.get(base_url, params=params, headers=headers, timeout=20)
            response.raise_for_status()
            html_content = response.text
            if not html_content: return None, None
            matches = re.findall(r'<a\s+(?:[^>]*?\s+)?href="([^"]+\.7z)"', html_content, re.IGNORECASE)
            if matches:
                relative_url = matches[0]
                download_url = urllib.parse.urljoin(response.url, relative_url)
                archive_name = os.path.basename(download_url)
                logging.info(f"   - URL BDALTI trouvée (via Regex): {download_url}")
                return download_url, archive_name
            logging.warning("   - Erreur: Aucun lien de téléchargement .7z trouvé dans la réponse HTML.")
            return None, None
        except Exception as e:
            logging.error(f"   - Erreur inattendue lors de la récupération des informations BDALTI: {e}")
            return None, None
    def _get_rgealti_download_info(self, department_code):
        dept_id = department_code.zfill(3) if department_code.isdigit() else department_code
        base_url = "https://geoservices.ign.fr/telechargement-api/RGEALTI"
        params = {"zone": f"D{dept_id}"}
        headers = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' }
        logging.info(f" > Interrogation de l'API IGN (HTML): {base_url} pour RGEALTI zone={params['zone']}")
        try:
            response = requests.get(base_url, params=params, headers=headers, timeout=20)
            response.raise_for_status()
            html_content = response.text
            if not html_content: return None, None
            search_pattern_in_url = f"d{dept_id.lower()}"
            matches = re.findall(r'<a\s+(?:[^>]*?\s+)?href="([^"]+\.7z)"', html_content, re.IGNORECASE)
            for relative_url in matches:
                url_lower = relative_url.lower()
                if "rgealti" in url_lower and "5m" in url_lower and search_pattern_in_url in url_lower:
                    download_url = urllib.parse.urljoin(response.url, relative_url)
                    archive_name = os.path.basename(download_url)
                    logging.info(f"   - URL RGEALTI 5M trouvée (via Regex): {download_url}")
                    return download_url, archive_name
            logging.warning("   - Erreur: Aucun lien RGEALTI 5M .7z trouvé pour ce département.")
            return None, None
        except Exception as e:
            logging.error(f"   - Erreur inattendue lors de la récupération des informations RGEALTI: {e}")
            return None, None
    def prepare_bdalti_data(self, department_code):
        base_ign_dir = './IGN_BDALTI_25M'
        dept_dir = os.path.join(base_ign_dir, department_code)
        if os.path.exists(dept_dir) and os.listdir(dept_dir):
            self._load_ign_asc_tiles(dept_dir)
            return
        download_url, archive_name = self._get_bdalti_download_info(department_code)
        if not download_url or not archive_name: raise MissingDataError(f"Impossible de récupérer les informations de téléchargement BDALTI pour le département {department_code}.")
        archive_path = os.path.join('.', archive_name)
        if not os.path.exists(dept_dir): os.makedirs(dept_dir)
        if not os.path.exists(archive_path): self._download_ign_archive(download_url, archive_path)
        if os.path.exists(archive_path) and not self._decompress_ign_archive(archive_path, dept_dir):
            if os.path.exists(dept_dir) and not os.listdir(dept_dir): os.rmdir(dept_dir)
            raise MissingDataError(f"Echec de la préparation des données BDALTI pour le département {department_code}.")
        self._load_ign_asc_tiles(dept_dir)
    def prepare_rgealti_data(self, department_code):
        base_ign_dir = './IGN_RGEALTI_5M'
        dept_dir = os.path.join(base_ign_dir, department_code)
        if os.path.exists(dept_dir) and os.listdir(dept_dir):
            self._load_ign_asc_tiles(dept_dir)
            return
        download_url, archive_name = self._get_rgealti_download_info(department_code)
        if not download_url or not archive_name: raise MissingDataError(f"Impossible de récupérer les informations de téléchargement RGEALTI 5M pour le département {department_code}.")
        archive_path = os.path.join('.', archive_name)
        if not os.path.exists(dept_dir): os.makedirs(dept_dir)
        if not os.path.exists(archive_path): self._download_ign_archive(download_url, archive_path)
        if os.path.exists(archive_path) and not self._decompress_ign_archive(archive_path, dept_dir):
            if os.path.exists(dept_dir) and not os.listdir(dept_dir): os.rmdir(dept_dir)
            raise MissingDataError(f"Echec de la préparation des données RGEALTI 5M pour le département {department_code}.")
        self._load_ign_asc_tiles(dept_dir)
    def _init_srtm(self):
        os.environ["SRTM1_DIR"] = os.path.abspath(self.hgt_dir)
        try:
            import srtm  # import différé (charge ~0.9 s)
            self.srtm_data = srtm.get_data()
            # AJOUT: Ajouter les tuiles SRTM1 du répertoire à downloaded_tiles_info
            for filename in os.listdir(self.hgt_dir):
                if filename.lower().endswith('.hgt'):
                    match = re.search(r'([NS])(\d+)([EW])(\d+)', filename)
                    if match:
                        lat_sign = 1 if match.group(1) == 'N' else -1
                        lon_sign = 1 if match.group(3) == 'E' else -1
                        lat_int = lat_sign * int(match.group(2))
                        lon_int = lon_sign * int(match.group(4))
                        # Bornes d'une tuile SRTM1 (1 degré x 1 degré)
                        bbox_srtm = (float(lon_int), float(lat_int), float(lon_int + 1), float(lat_int + 1))

        except Exception as e: logging.error(f"Erreur init SRTM: {e}")
    def _init_copernicus(self):
        if not RASTERIO_AVAILABLE:
            logging.warning("rasterio non disponible, Copernicus DEM désactivé")
            self.source = 'srtm1'; self._init_srtm()

    def _handle_hgt_eviction(self, evicted_key, evicted_value):
        """Callback pour l'éviction des tuiles HGT du cache LRU."""
        # evicted_key est un tuple (source_type, identifier)
        source_type, identifier = evicted_key

        if source_type == 'copernicus':
            # identifier est le filename
            metadata = self.hgt_tile_metadata.get(evicted_key)
            if metadata:
                bbox = metadata['bounds']
                self.loaded_in_ram_tiles.discard(('copernicus', identifier, (bbox.left, bbox.bottom, bbox.right, bbox.top)))
        elif source_type == 'ign':
            # identifier est le filepath
            metadata = self.hgt_tile_metadata.get(evicted_key)
            if metadata:
                bbox = metadata['bounds']
                self.loaded_in_ram_tiles.discard(('ign', identifier, (bbox['xmin'], bbox['ymin'], bbox['xmax'], bbox['ymax'])))


    def _download_ign_archive(self, url, path):

        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            block_size = 8192 # 8KB
            downloaded_size = 0
            
            with open(path, 'wb') as f: 
                for chunk in response.iter_content(chunk_size=block_size):
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    if self.progress_callback and total_size > 0:
                        progress = (downloaded_size / total_size) * 100
                        self.progress_callback(50 + progress * 0.05, f"DL IGN {os.path.basename(path)}: {progress:.1f}%") 

        except Exception as e: logging.error(f"\n✗ Erreur DL archive IGN: {e}")
    def _decompress_ign_archive(self, archive_path, dest_dir):
        if not PY7ZR_AVAILABLE:
            logging.error("✗ Erreur: py7zr non installé (pip install py7zr).")
            return False

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                with py7zr.SevenZipFile(archive_path, mode='r') as z: z.extractall(path=tmpdir)
                asc_files_extracted = 0
                for root, _, files in os.walk(tmpdir):
                    for filename in files:
                        if filename.lower().endswith('.asc'):
                            src_path = os.path.join(root, filename)
                            dest_path = os.path.join(dest_dir, filename)
                            if not os.path.exists(dest_path):
                                shutil.move(src_path, dest_path)
                                asc_files_extracted += 1


            return True
        except Exception as e:
            logging.error(f"✗ Erreur décompression: {e}")
            return False
    def _load_ign_asc_tiles(self, ign_dir):

        new_tiles_found = 0
        for dirpath, _, filenames in os.walk(ign_dir):
            for filename in filenames:
                # if filename in self.ign_asc_tiles: continue # Plus besoin
                if filename.lower().endswith('.asc'):
                    filepath = os.path.join(dirpath, filename)
                    try:
                        header = {}
                        with open(filepath, 'r') as f:
                            for _ in range(6):
                                line = f.readline().strip().split()
                                header[line[0].lower()] = float(line[1])
                        x_min, y_min, cellsize = header['xllcorner'], header['yllcorner'], header['cellsize']
                        x_max, y_max = x_min + (header['ncols'] * cellsize), y_min + (header['nrows'] * cellsize)

                        # Calculer les indices de grille (cellules 1km x 1km) couverts par cette tuile
                        tile_size = 1000 # Par défaut pour IGN
                        tx_start = int(x_min // tile_size)
                        ty_start = int(y_min // tile_size)
                        tx_end = int(x_max // tile_size)
                        ty_end = int(y_max // tile_size)

                        tile_data = {'path': filepath, 'header': header, 'bounds': {'xmin': x_min, 'ymin': y_min, 'xmax': x_max, 'ymax': y_max}}

                        tile_key = ('ign', filepath)
                        self.hgt_tile_metadata[tile_key] = tile_data

                        # Ajouter la tuile à downloaded_tiles_info (pour la visualisation)
                        self.downloaded_tiles_info.add(('ign', filepath, (x_min, y_min, x_max, y_max)))
                        
                        # Stocker la référence de cette tuile dans toutes les cellules de grille qu'elle couvre
                        for tx in range(tx_start, tx_end + 1):
                            if tx not in self.ign_grid_tiles:
                                self.ign_grid_tiles[tx] = {}
                            for ty in range(ty_start, ty_end + 1):
                                if ty not in self.ign_grid_tiles[tx]:
                                    self.ign_grid_tiles[tx][ty] = [] # Chaque cellule peut contenir plusieurs tuiles
                                self.ign_grid_tiles[tx][ty].append(tile_key) # Stocke la clé de tuile, pas l'objet entier
                        new_tiles_found += 1
                    except Exception as e: logging.warning(f"Entête .asc illisible {filename}: {e}")

        if not self.ign_grid_tiles: raise MissingDataError(f"Aucune tuile .asc valide n'a pu être chargée depuis {os.path.abspath(ign_dir)}")




    def _load_copernicus_tile_to_ram(self, tile_key_tuple):
        # tile_key_tuple est de la forme ('copernicus', filename)
        with self.elevation_cache_lock:
            if self.hgt_rasters.get(tile_key_tuple) is not None:
                return True
            
            metadata = self.hgt_tile_metadata.get(tile_key_tuple)
            if metadata is None:
                # Cela signifie que la tuile n'a même pas été scannée (n'existe pas sur disque)
                return False 
            
            filepath = metadata['filepath']
            try:
                with rasterio.open(filepath) as ds:
                    data_arr = ds.read(1)
                
                # Gestion du cache LRU
                self.hgt_rasters.put(tile_key_tuple, {
                    'data': data_arr,
                    'transform': metadata['transform'],
                    'inv_transform': metadata['inv_transform'],
                    'nodata': metadata['nodata']
                })



                return True
            except Exception as e:
                self.log(f"Erreur chargement tuile Copernicus {filepath} en RAM: {e}")
                return False
                
    # NOTE : ancienne version cassée de _ensure_copernicus_tile_metadata_loaded
    # supprimée (utilisait une variable `tile_name` non définie → NameError si
    # appelée, mais elle était écrasée par la 2e définition plus bas → dead code).

    def _load_ign_tile_to_ram(self, tile_key):
                
        # tile_key est de la forme ('ign', filepath)
        with self.elevation_cache_lock:
            if self.hgt_rasters.get(tile_key) is not None:
                return True
            
            metadata = self.hgt_tile_metadata.get(tile_key)
            if metadata is None:
                return False 
            
            filepath = metadata['path'] # Pour IGN, le chemin est dans 'path'
            try:
                # Lecture des données (NumPy array)
                data_arr = np.loadtxt(filepath, skiprows=6)
                
                # Gestion du cache LRU
                self.hgt_rasters.put(tile_key, {
                    'data': data_arr,
                    'header': metadata['header'], # Stocker l'en-tête pour le calcul d'élévation
                    'bounds': metadata['bounds'] # On stocke les bounds aussi ici pour faciliter l'accès
                })

                # Mise à jour de loaded_in_ram_tiles pour la visualisation
                bbox = metadata['bounds']
                self.loaded_in_ram_tiles.add(('ign', tile_key[1], (bbox['xmin'], bbox['ymin'], bbox['xmax'], bbox['ymax'])))

                return True
            except Exception as e:
                self.log(f"Erreur chargement tuile IGN {filepath} en RAM: {e}")
                return False
    def _get_copernicus_elevation(self, lat, lon):
        found_tile_key = self._ensure_copernicus_tile_metadata_loaded(lat, lon)
        if found_tile_key is None:
            return 0.0 # Tuile non trouvée ou échec du téléchargement/chargement des métadonnées

        # S'assurer que la tuile est chargée en RAM (via le cache LRU)
        if not self._load_copernicus_tile_to_ram(found_tile_key):
            return 0.0 # Échec du chargement en RAM, retourner 0.0

        # Récupérer la tuile du cache LRU
        tile_data = self.hgt_rasters.get(found_tile_key)
        if tile_data is None: # Ne devrait pas arriver si _load_copernicus_tile_to_ram a retourné True
            return 0.0

        # Marquer la tuile comme utilisée pour la visualisation KML
        metadata = self.hgt_tile_metadata.get(found_tile_key)
        if metadata: 
            bbox = metadata['bounds']
            self.used_tiles.add((found_tile_key[0], found_tile_key[1], (bbox.left, bbox.bottom, bbox.right, bbox.top)))
        
        # Récupérer l'élévation (logique existante)
        inv_t = tile_data['inv_transform']
        col, row = inv_t * (lon, lat)
        col, row = int(col), int(row)
        arr = tile_data['data']
        nodata = tile_data['nodata']
        
        if 0 <= row < arr.shape[0] and 0 <= col < arr.shape[1]:
            val = arr[row, col]
            return float(val) if val != nodata else 0.0
        return 0.0
    def _get_ign_elevation(self, lat, lon):
        # Transformer thread-local (cf. TransformerPool) : ce chemin tourne dans
        # les workers de compute_shadow_geotiff, pas de partage d'instance pyproj.
        x, y = TransformerPool.wgs84_to_lambert().transform(lon, lat)
        tile_size = 1000
        tx = int(x // tile_size)
        ty = int(y // tile_size)
        
        # Trouver la clé de tuile IGN dans la grille
        candidate_tile_keys = self.ign_grid_tiles.get(tx, {}).get(ty, [])
        found_tile_key = None
        for tk in candidate_tile_keys:
            # metadata = self.hgt_tile_metadata.get(tk) # Already `tk` is the key for hgt_tile_metadata
            # Access metadata directly
            metadata_ign = self.hgt_tile_metadata.get(tk)
            if metadata_ign:
                b = metadata_ign['bounds']
                if b['xmin'] <= x < b['xmax'] and b['ymin'] <= y < b['ymax']:
                    found_tile_key = tk
                    break
        
        if found_tile_key is None:
            return 0.0 # Pas de tuile trouvée pour ces coordonnées

        # S'assurer que la tuile est chargée en RAM (via le cache LRU)
        if not self._load_ign_tile_to_ram(found_tile_key):
            return 0.0 # Échec du chargement en RAM, retourner 0.0

        # Récupérer la tuile du cache LRU
        tile_data = self.hgt_rasters.get(found_tile_key)
        if tile_data is None: # Ne devrait pas arriver si _load_ign_tile_to_ram a retourné True
            return 0.0

        # Marquer la tuile comme utilisée pour la visualisation KML
        # found_tile_key est ('ign', filepath)
        # We need the full bbox from metadata, not just the dictionary from tile_data
        metadata_full = self.hgt_tile_metadata.get(found_tile_key)
        if metadata_full:
            bbox = metadata_full['bounds']
            self.used_tiles.add((found_tile_key[0], found_tile_key[1], (bbox['xmin'], bbox['ymin'], bbox['xmax'], bbox['ymax'])))
        
        # Récupérer l'élévation (logique existante, adaptée au nouveau format de tile_data)
        h = tile_data['header']
        data_arr = tile_data['data']

        col_f = (x - h['xllcorner']) / h['cellsize']
        # Use bounds stored in tile_data, which came from metadata, for ymax
        row_f = (tile_data['bounds']['ymax'] - y) / h['cellsize']
        
        col, row = int(col_f), int(row_f)
        
        if not (0 <= row < h['nrows'] - 1 and 0 <= col < h['ncols'] - 1):
            return data_arr[row, col] if (0 <= row < h['nrows'] and 0 <= col < h['ncols']) else 0.0
        
        if self.interpolation == 'bilinear':
            x1, y1, x2, y2 = col, row, col + 1, row + 1
            q11, q12, q21, q22 = data_arr[y1, x1], data_arr[y2, x1], data_arr[y1, x2], data_arr[y2, x2]
            nodata = h.get('nodata_value', -99999)
            if any(val == nodata for val in [q11, q12, q21, q22]): return data_arr[row, col] if data_arr[row, col] != nodata else 0.0
            f_xy1 = (x2 - col_f) * q11 + (col_f - x1) * q21
            f_xy2 = (x2 - col_f) * q12 + (col_f - x1) * q22
            return (y2 - row_f) * f_xy1 + (row_f - y1) * f_xy2
        else: return data_arr[row, col]
    def _get_copernicus_elevations_vec(self, lats, lons):
        if not RASTERIO_AVAILABLE:
            return np.zeros_like(lats, dtype=np.float64)

        elevations = np.zeros_like(lats, dtype=np.float64)
        
        # 1. Calculer les coordonnées entières de la tuile pour chaque point
        lat_tiles = np.floor(lats).astype(int)
        lon_tiles = np.floor(lons).astype(int)

        # 2. Grouper les points par tuile unique
        tile_coords = np.vstack((lat_tiles, lon_tiles)).T
        unique_tile_coords, inverse_indices = np.unique(tile_coords, axis=0, return_inverse=True)

        # 3. Pour chaque tuile unique, s'assurer qu'it's ready (téléchargée + métadonnées chargées) et la charger en RAM
        for lat_t, lon_t in unique_tile_coords:
            # Cette fonction utilise maintenant le bon nom et gère le DL/chargement des métadonnées
            tile_key = self._ensure_copernicus_tile_metadata_loaded(lat_t, lon_t)
            if tile_key:
                # Charge les données de la tuile en RAM si elles ne le sont pas déjà
                self._load_copernicus_tile_to_ram(tile_key)

        # 4. Extraire les élévations, tuile par tuile
        for i, (lat_t, lon_t) in enumerate(unique_tile_coords):
            # Reconstruire la clé de la tuile pour la retrouver dans le cache hgt_rasters
            lat_prefix = 'N' if lat_t >= 0 else 'S'
            lon_prefix = 'E' if lon_t >= 0 else 'W'
            tile_filename = f"Copernicus_DSM_COG_10_{lat_prefix}{abs(lat_t):02d}_00_{lon_prefix}{abs(lon_t):03d}_00_DEM.tif"
            tile_key = ('copernicus', tile_filename)

            tile_data = self.hgt_rasters.get(tile_key)
            
            if tile_data is None:
                continue  # Tuile non chargée, on laisse l'élévation à 0

            # Marquer la tuile comme utilisée pour la visualisation KML
            metadata = self.hgt_tile_metadata.get(tile_key)
            if metadata: 
                bbox = metadata['bounds']
                self.used_tiles.add((tile_key[0], tile_key[1], (bbox.left, bbox.bottom, bbox.right, bbox.top)))

            # Masque pour sélectionner tous les points qui appartiennent à cette tuile unique
            mask = (inverse_indices == i)
            if not np.any(mask):
                continue

            # Coordonnées des points dans cette tuile
            lats_tile = lats[mask]
            lons_tile = lons[mask]

            # Conversion des coordonnées géo en indices de pixels
            inv_transform = tile_data['inv_transform']
            cols_f, rows_f = inv_transform * (lons_tile, lats_tile)
            cols_f = np.asarray(cols_f, dtype=np.float64)
            rows_f = np.asarray(rows_f, dtype=np.float64)

            data_arr = tile_data['data']
            nodata = tile_data['nodata']

            if self.interpolation == 'bilinear':
                vals = _bilinear_sample_raster(data_arr, rows_f, cols_f,
                                                nodata=nodata, fallback=0.0)
                elevations[np.where(mask)[0]] = vals
            else:
                # Voisin le plus proche (comportement original)
                rows_int = rows_f.astype(np.int32)
                cols_int = cols_f.astype(np.int32)
                h, w = data_arr.shape
                valid_mask_in_tile = (rows_int >= 0) & (rows_int < h) & \
                                     (cols_int >= 0) & (cols_int < w)
                if not np.any(valid_mask_in_tile):
                    continue
                valid_rows = rows_int[valid_mask_in_tile]
                valid_cols = cols_int[valid_mask_in_tile]
                vals = data_arr[valid_rows, valid_cols].astype(np.float64)
                vals[vals == nodata] = 0.0
                output_indices = np.where(mask)[0][valid_mask_in_tile]
                elevations[output_indices] = vals

        return elevations
    
    def _get_ign_elevations_vec(self, lats, lons):
        if not PYPROJ_AVAILABLE:
            return np.zeros_like(lats, dtype=np.float64)

        elevations = np.zeros_like(lats, dtype=np.float64)

        # Transformer thread-local (cf. TransformerPool) — appelé depuis les
        # workers de compute_shadow_geotiff sans sérialisation.
        xs, ys = TransformerPool.wgs84_to_lambert().transform(lons, lats)
        xs = np.array(xs, dtype=np.float64)
        ys = np.array(ys, dtype=np.float64)

        # 1. Calculer les indices de tuile pour chaque point
        tile_size = 1000 # Par défaut pour IGN
        txs = np.floor_divide(xs, tile_size).astype(np.int32)
        tys = np.floor_divide(ys, tile_size).astype(np.int32)

        # Créer des clés uniques pour les tuiles
        # Bit-pack tx et ty pour créer une clé 1D unique
        tile_keys_packed = (txs.astype(np.int64) << 32) | (tys.astype(np.int64) & 0xFFFFFFFF)
        unique_tile_keys_packed, inverse_indices = np.unique(tile_keys_packed, return_inverse=True)
        
        # Reconstruire tx, ty uniques à partir des clés uniques
        tx_unique = (unique_tile_keys_packed >> 32).astype(np.int32)
        ty_unique = (unique_tile_keys_packed & 0xFFFFFFFF).astype(np.int32)
        
        # Convertir les coordonnées uniques en tuples (tx, ty) pour la compatibilité avec la suite du code
        # Ces (tx,ty) sont les index de la grille 1km, pas les clés IGN (ign, filepath)
        unique_grid_tiles = [(tx_unique[k], ty_unique[k]) for k in range(len(unique_tile_keys_packed))]

        # 2. Pour chaque tuile unique de la grille 1km, traiter les tuiles IGN correspondantes
        for unique_grid_idx, (tx, ty) in enumerate(unique_grid_tiles):
            # Les ign_grid_tiles stockent les clés réelles des tuiles IGN (ign, filepath)
            ign_tile_keys_in_grid_cell = self.ign_grid_tiles.get(tx, {}).get(ty, [])
            
            mask_for_grid_cell = (inverse_indices == unique_grid_idx)
            
            xs_in_grid_cell = xs[mask_for_grid_cell]
            ys_in_grid_cell = ys[mask_for_grid_cell]
            
            if xs_in_grid_cell.size == 0:
                continue

            # Pour chaque point dans cette cellule de grille, trouver sa tuile IGN spécifique
            # Nous ne pouvons pas vectoriser cela directement car chaque point pourrait
            # se trouver dans une tuile IGN différente au sein de la même cellule de grille 1km.
            # On va donc regrouper par tuile IGN réelle.
            
            # Map pour stocker la clé IGN réelle et les indices des points qui lui appartiennent
            points_by_ign_tile = {} 

            for i, (x_p, y_p) in enumerate(zip(xs_in_grid_cell, ys_in_grid_cell)):
                found_ign_tile_key = None
                for tk_ign in ign_tile_keys_in_grid_cell:
                    metadata_ign = self.hgt_tile_metadata.get(tk_ign)
                    if metadata_ign:
                        b = metadata_ign['bounds']
                        if b['xmin'] <= x_p < b['xmax'] and b['ymin'] <= y_p < b['ymax']:
                            found_ign_tile_key = tk_ign
                            break
                if found_ign_tile_key:
                    points_by_ign_tile.setdefault(found_ign_tile_key, []).append(i)

            # Maintenant, pour chaque tuile IGN réelle trouvée, charger ses données et extraire les élévations
            for ign_tile_key, local_indices in points_by_ign_tile.items():
                # S'assurer que la tuile est chargée en RAM via le cache LRU
                if not self._load_ign_tile_to_ram(ign_tile_key):
                    continue # Passer à la tuile suivante si échec de chargement
                
                # Récupérer la tuile du cache LRU
                tile_data = self.hgt_rasters.get(ign_tile_key)
                if tile_data is None:
                    continue

                # Marquer la tuile comme utilisée
                metadata_full = self.hgt_tile_metadata.get(ign_tile_key)
                if metadata_full:
                    bbox = metadata_full['bounds']
                    self.used_tiles.add((ign_tile_key[0], ign_tile_key[1], (bbox['xmin'], bbox['ymin'], bbox['xmax'], bbox['ymax'])))
                
                # Extraire les élévations pour les points appartenant à cette tuile IGN
                h_header = tile_data['header']
                data_arr = tile_data['data']

                # Points (xs, ys) correspondant aux local_indices
                xs_local = xs_in_grid_cell[local_indices]
                ys_local = ys_in_grid_cell[local_indices]

                col_fs = (xs_local - h_header['xllcorner']) / h_header['cellsize']
                row_fs = (tile_data['bounds']['ymax'] - ys_local) / h_header['cellsize']
                col_fs = np.asarray(col_fs, dtype=np.float64)
                row_fs = np.asarray(row_fs, dtype=np.float64)

                nodata = h_header.get('nodata_value', -99999)

                if self.interpolation == 'bilinear':
                    vals = _bilinear_sample_raster(data_arr, row_fs, col_fs,
                                                    nodata=nodata, fallback=0.0)
                    original_indices_for_these_points = np.where(mask_for_grid_cell)[0][np.array(local_indices)]
                    elevations[original_indices_for_these_points] = vals
                else:
                    rows_int = row_fs.astype(np.int32)
                    cols_int = col_fs.astype(np.int32)
                    h_arr, w_arr = data_arr.shape
                    valid_indices_in_arr = (rows_int >= 0) & (rows_int < h_arr) & \
                                           (cols_int >= 0) & (cols_int < w_arr)
                    if not np.any(valid_indices_in_arr):
                        continue
                    vals = data_arr[rows_int[valid_indices_in_arr], cols_int[valid_indices_in_arr]].astype(np.float64)
                    vals[vals == nodata] = 0.0
                    original_indices_for_these_points = np.where(mask_for_grid_cell)[0][np.array(local_indices)[valid_indices_in_arr]]
                    elevations[original_indices_for_these_points] = vals
            
        return elevations
    def _download_copernicus_tile(self, lat, lon):
        if not RASTERIO_AVAILABLE: return None
        lat_prefix, lon_prefix = ('N', 'E') if lat >= 0 else ('S', 'W')
        lat_tile, lon_tile = int(lat), int(lon)
        tile_name = f"Copernicus_DSM_COG_10_{lat_prefix}{abs(lat_tile):02d}_00_{lon_prefix}{abs(lon_tile):03d}_00_DEM"
        filename = f"{tile_name}.tif"
        output_path = os.path.join(self.hgt_dir, filename)
        if os.path.exists(output_path):
            return True # Déjà sur disque
        
        base_url = "https://copernicus-dem-30m.s3.amazonaws.com"
        url = f"{base_url}/{tile_name}/{filename}"

        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            response = requests.get(url, timeout=30, headers=headers, stream=True)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            block_size = 8192 # 8KB
            downloaded_size = 0

            with open(output_path, 'wb') as f: 
                for chunk in response.iter_content(chunk_size=block_size):
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    if self.progress_callback and total_size > 0:
                        progress = (55 + (downloaded_size / total_size) * 0.05) # De 55% à 60%
                        self.progress_callback(progress, f"DL Copernicus {filename}: {progress:.1f}%")
            

            # Ajouter la tuile à downloaded_tiles_info ici, mais sans charger en RAM
            # Le _scan_copernicus_tiles au début du processus s'occupe de ça.
            # On pourrait le faire ici si on voulait une mise à jour immédiate
            # de downloaded_tiles_info, mais pour l'instant, c'est géré par le scan initial.
            return True
            
        except Exception as e:
            logging.error(f"Erreur téléchargement Copernicus: {e}")
            if os.path.exists(output_path):
                os.remove(output_path)
            return False

    def _ensure_copernicus_tile_metadata_loaded(self, lat, lon):
        lat_prefix, lon_prefix = ('N', 'E') if lat >= 0 else ('S', 'W')
        lat_tile, lon_tile = int(lat), int(lon)
        tile_filename = f"Copernicus_DSM_COG_10_{lat_prefix}{abs(lat_tile):02d}_00_{lon_prefix}{abs(lon_tile):03d}_00_DEM.tif"
        tile_key = ('copernicus', tile_filename)

        # Vérifier si les métadonnées sont déjà chargées
        if tile_key in self.hgt_tile_metadata:
            return tile_key
        
        # Si non, la tuile est-elle sur le disque?
        filepath = os.path.join(self.hgt_dir, tile_filename)
        if not os.path.exists(filepath):
            # Non, essayer de la télécharger
            if not self._download_copernicus_tile(lat, lon):
    
                return None
        
        # Maintenant, la tuile doit être sur le disque, charger ses métadonnées
        try:
            with rasterio.open(filepath) as ds:
                self.hgt_tile_metadata[tile_key] = {
                    'filepath': filepath,
                    'bounds': ds.bounds,
                    'transform': ds.transform,
                    'inv_transform': ~ds.transform,
                    'nodata': ds.nodata if ds.nodata is not None else -9999
                }
            bbox = self.hgt_tile_metadata[tile_key]['bounds']
            self.downloaded_tiles_info.add(('copernicus', tile_filename, (bbox.left, bbox.bottom, bbox.right, bbox.top)))

            return tile_key
        except Exception as e:
            self.log(f"Impossible de lire les métadonnées de la tuile Copernicus {filepath} après téléchargement: {e}")
            return None
    def get_elevation(self, lat, lon):
        # Utiliser une quantification entière pour la clé de cache afin d'éviter les appels coûteux à round()
        cache_key = (_q_coord_int(lat), _q_coord_int(lon), self.source, self.interpolation)
        with self.elevation_cache_lock:
            if cache_key in self.elevation_cache:
                return self.elevation_cache[cache_key]

        elev = 0.0
        if self.source == 'copernicus': elev = self._get_copernicus_elevation(lat, lon) or 0.0
        elif self.source.startswith('ign_'): elev = self._get_ign_elevation(lat, lon) or 0.0
        elif self.srtm_data:
            elev = self.srtm_data.get_elevation(lat, lon) or 0.0
            # AJOUT: Si la tuile SRTM1 a été utilisée, elle est considérée comme "chargée en RAM"
            lat_int, lon_int = int(lat), int(lon)
            tile_name = f"{ 'N' if lat_int >= 0 else 'S' }{abs(lat_int):02d}{ 'E' if lon_int >= 0 else 'W' }{abs(lon_int):03d}.hgt"
            bbox_srtm = (float(lon_int), float(lat_int), float(lon_int + 1), float(lat_int + 1))
            self.loaded_in_ram_tiles.add(('srtm1', tile_name, bbox_srtm))

        with self.elevation_cache_lock:
            self.elevation_cache[cache_key] = elev
        return elev

def adaptive_distances(max_dist, initial_step=5.0):
    near = np.arange(initial_step, 50, initial_step)      # Réduit de 100 à 50
    mid = np.arange(50, 300, initial_step * 3)            # Réduit de 500 à 300
    far = np.arange(300, max_dist, initial_step * 8)      # Pas encore plus large
    return np.concatenate([near, mid, far])

# Versions pure-NumPy (utilisées tant que _try_load_numba() n'a pas été
# appelée — typiquement avant le 1er calcul). _try_load_numba() les
# remplace par leurs équivalents jit-compilés.

def compute_ray_intersections_detailed(obstacle_profile, ground_profile,
                                       object_heights, ray_altitudes, tolerance):
    """Fallback NumPy. Plus lent que Numba mais sémantiquement identique."""
    relief_covers = ground_profile > (ray_altitudes + tolerance)
    relief_is_blocking = np.any(relief_covers, axis=1)
    veg_covers = (obstacle_profile > (ray_altitudes + tolerance)) & (object_heights > 0)
    veg_is_blocking = np.any(veg_covers, axis=1)
    return relief_is_blocking, veg_is_blocking


def _nearest_seg_with_param(xs, ys, x1, y1, x2, y2):
    """Fallback NumPy tilé pour limiter la mémoire."""
    n = xs.size; m = x1.size
    best_idx = np.empty(n, dtype=np.int64)
    best_t   = np.empty(n, dtype=np.float64)
    chunk = max(1024, min(8192, 65536 // max(1, m)))
    for k in range(0, n, chunk):
        px = xs[k:k+chunk, None]; py = ys[k:k+chunk, None]
        dx = (x2 - x1)[None, :]; dy = (y2 - y1)[None, :]
        L2 = dx*dx + dy*dy
        L2_safe = np.where(L2 < 1e-12, 1.0, L2)
        t = ((px - x1[None, :])*dx + (py - y1[None, :])*dy) / L2_safe
        t = np.clip(t, 0.0, 1.0)
        qx = x1[None, :] + t*dx
        qy = y1[None, :] + t*dy
        d2 = (px - qx)**2 + (py - qy)**2
        idx = np.argmin(d2, axis=1)
        best_idx[k:k+chunk] = idx
        best_t[k:k+chunk]   = t[np.arange(idx.size), idx]
    return best_idx, best_t

def get_sun_blocking_type_vec(lats, lons, ts, hgt_manager, solar_step_s=SOLAR_ROUND_SEC):
    """
    Calcule le type d'obstacle pour un LOT de points via Ray Tracing vectorisé.

    Args:
        lats, lons : arrays float64 des coordonnées WGS84.
        ts         : array float64 des timestamps UTC (secondes depuis l'epoch).
        hgt_manager, solar_step_s : cf. HGTDataManager.

    Retourne deux listes alignées:
      - listes de statuts ('RELIEF', 'VEGETATION', 'SUN', 'NIGHT', 'RELIEF_VEG')
      - liste de shadow_hit (lat, lon) ou None lorsque pas d'empreinte trouvée

    Prend des arrays (pas des objets GPXTrackPoint) : l'appelant carte d'ombre
    génère des dizaines de milliers de pixels par bloc — créer autant d'objets
    Python juste pour les re-décomposer en arrays était du gaspillage pur.
    """
    lats = np.asarray(lats, dtype=np.float64)
    lons = np.asarray(lons, dtype=np.float64)
    n_pts = lats.size
    if n_pts == 0:
        return [], []

    # Positions solaires (avec cache) — ts = timestamps UTC déjà en main
    sun_alts, sun_azs_full = solar_altaz_cached_vec(lats, lons, ts, step_s=solar_step_s)
    
    # STATUS_MAP: 0=SUN, 1=RELIEF, 2=VEGETATION, 3=RELIEF_VEG, 4=NIGHT
    STATUS_MAP_INV = {0: 'SUN', 1: 'RELIEF', 2: 'VEGETATION', 3: 'RELIEF_VEG', 4: 'NIGHT'}
    statuses = np.zeros(n_pts, dtype=np.uint8)
    night_mask = sun_alts <= 0
    statuses[night_mask] = 4 # NIGHT

    # Préparer les arrays pour les shadow hits (NaN par défaut)
    hit_lats_full = np.full(n_pts, np.nan, dtype=np.float64)
    hit_lons_full = np.full(n_pts, np.nan, dtype=np.float64)

    # Masque pour ne traiter que les points où le soleil est levé
    valid_mask = ~night_mask
    if not np.any(valid_mask):
        shadow_hits = [(lat, lon) if not np.isnan(lat) else None 
                       for lat, lon in zip(hit_lats_full, hit_lons_full)]
        return [STATUS_MAP_INV[s] for s in statuses], shadow_hits

    # 3. Préparer les données pour les points valides uniquement
    sun_azs = sun_azs_full[valid_mask]
    rad_alts = np.deg2rad(sun_alts[valid_mask])
    rad_azs = np.deg2rad(sun_azs)
    cos_azs = np.cos(rad_azs)
    sin_azs = np.sin(rad_azs)

    # 4. Préparer le Ray-Tracing vectorisé
    step = hgt_manager.step
    max_dist = hgt_manager.max_distance
    distances = adaptive_distances(max_dist, initial_step=step)

    observer_lats = lats[valid_mask]
    observer_lons = lons[valid_mask]
    observer_ground_elevs = hgt_manager.get_ground_elevations_vec(observer_lats, observer_lons)
    ray_start_altitudes = observer_ground_elevs + OBSERVER_EYE_HEIGHT

    # 5. Calculer les coordonnées 3D de tous les points de tous les rayons via broadcasting
    m_per_deg_lat_vec, m_per_deg_lon_vec = get_meters_per_degree_wgs84_vec(observer_lats)
    d_lat = (distances[None, :] * cos_azs[:, None]) / m_per_deg_lat_vec[:, None]
    d_lon = (distances[None, :] * sin_azs[:, None]) / m_per_deg_lon_vec[:, None]

    ray_lats = observer_lats[:, None] + d_lat
    ray_lons = observer_lons[:, None] + d_lon
    ray_altitudes = ray_start_altitudes[:, None] + distances[None, :] * np.tan(rad_alts)[:, None] + distances[None, :]**2 / (2 * EARTH_RADIUS)

    # 6. Obtenir le profil d'altitude du terrain et des obstacles
    #    (sol + objets en une passe : partage projection + tuiles en LiDAR)
    flat_lats = ray_lats.ravel()
    flat_lons = ray_lons.ravel()
    ground_flat, object_flat = hgt_manager.get_ground_and_object_elevations_vec(flat_lats, flat_lons)
    ground_profile = ground_flat.reshape(ray_lats.shape)
    object_heights = object_flat.reshape(ray_lats.shape)

    # Appliquer le shadow_mode à la SOURCE du ray-tracing
    shadow_mode = getattr(hgt_manager, 'shadowmode', 'both')  # Récupérer le mode depuis hgtmanager
    if shadow_mode == 'relief':
        object_heights = np.zeros_like(object_heights)  # Ignorer toute végétation
    elif shadow_mode == 'vegetation':
        # Aplatir le terrain à l'altitude de l'observateur pour ignorer le relief
        ground_profile = np.full_like(ground_profile, observer_ground_elevs[:, None])

    obstacle_profile = ground_profile + object_heights

    # 7. Comparaison vectorisée (pour inclure RELIEF_VEG)
    TOLERANCE = 0.1
    if NUMBA_AVAILABLE:
        relief_is_blocking, veg_is_blocking = compute_ray_intersections_detailed(
            obstacle_profile, ground_profile, object_heights, ray_altitudes, TOLERANCE
        )
    else:
        relief_covers_ray = ground_profile > (ray_altitudes + TOLERANCE)
        relief_is_blocking = np.any(relief_covers_ray, axis=1)
        veg_covers_ray = (obstacle_profile > (ray_altitudes + TOLERANCE)) & (object_heights > 0)
        veg_is_blocking = np.any(veg_covers_ray, axis=1)

    # 8. Mettre à jour les statuts (avec les codes uint8)
    temp_statuses = np.zeros(len(observer_lats), dtype=np.uint8) # 0 = SUN
    temp_statuses[veg_is_blocking] = 2 # VEGETATION
    temp_statuses[relief_is_blocking] = 1 # RELIEF
    temp_statuses[relief_is_blocking & veg_is_blocking] = 3 # RELIEF_VEG
    
    np.put(statuses, np.where(valid_mask)[0], temp_statuses)

    # 9. VECTORIZE shadow hit calculation
    all_rays_blocking_mask = obstacle_profile > (ray_altitudes + TOLERANCE)
    any_block_per_ray = np.any(all_rays_blocking_mask, axis=1)
    
    if np.any(any_block_per_ray):
        # Indices des rayons qui sont bloqués
        blocked_ray_indices = np.where(any_block_per_ray)[0] # Corrected variable name
        
        # Pour ces rayons, trouver le premier point de blocage
        first_block_indices = np.argmax(all_rays_blocking_mask[blocked_ray_indices], axis=1)
        
        # Coordonnées des points de "hit"
        hit_lats = ray_lats[blocked_ray_indices, first_block_indices]
        hit_lons = ray_lons[blocked_ray_indices, first_block_indices]
        
        # Indices originaux dans le batch (correspondent à la position des points dans `hit_lats_full` et `hit_lons_full`)
        original_indices = np.where(valid_mask)[0][blocked_ray_indices]
        
        # Directly place the results in the full-sized arrays
        hit_lats_full[original_indices] = hit_lats
        hit_lons_full[original_indices] = hit_lons

    # Create the final list of tuples from the arrays
    shadow_hits = [(lat, lon) if not np.isnan(lat) else None 
                   for lat, lon in zip(hit_lats_full, hit_lons_full)]
    
    # ====================================================================
    # CONVERSION FINALE des statuts numériques en strings
    # ====================================================================
    final_statuses_numeric = statuses
    if shadow_mode == 'relief':
        final_statuses_numeric[final_statuses_numeric == 2] = 0 # VEGETATION (2) -> SUN (0)
        final_statuses_numeric[final_statuses_numeric == 3] = 1 # RELIEF_VEG (3) -> RELIEF (1)
    elif shadow_mode == 'vegetation':
        final_statuses_numeric[final_statuses_numeric == 1] = 0 # RELIEF (1) -> SUN (0)
        final_statuses_numeric[final_statuses_numeric == 3] = 2 # RELIEF_VEG (3) -> VEGETATION (2)

    final_statuses_str = [STATUS_MAP_INV[s] for s in final_statuses_numeric]
    
    return final_statuses_str, shadow_hits

def simulatehike(gpxobj, startdt, hgtmanager, localtz, direction, shadowmode,
                 progresscallback=None, batch_size=256, solar_step_s=SOLAR_ROUND_SEC):

    # Charge numba à la demande (différé depuis l'import pour accélérer
    # l'ouverture de la GUI). Idempotent — no-op si déjà chargé.
    _try_load_numba()

    starttimesim = datetime.now()
    rawpoints = gpxobj.tracks[0].segments[0].points
    simpoints = rawpoints if direction == "CW" else rawpoints[::-1]
    totalpoints = len(simpoints)
    logfunc = hgtmanager.log

    # === (A) Pré-calcul elevations (déjà chez toi) ===

    alllats = np.array([p.latitude for p in simpoints], dtype=np.float64)
    alllons = np.array([p.longitude for p in simpoints], dtype=np.float64)
    allelevations = hgtmanager.get_ground_elevations_vec(alllats, alllons)

    # === (B) Pré-calcul distances entre points consécutifs (N-1 segments) ===
    # Pour ~10 m, equirect est parfait; sinon remplace equirect_m_vec par haversine_m_vec.
    if totalpoints >= 2:
        seg_dists_m = equirect_m_vec(alllats[:-1], alllons[:-1], alllats[1:], alllons[1:])
        seg_dists_m = np.append(seg_dists_m, 0.0)  # dernier point : pas de segment sortant
    else:
        seg_dists_m = np.zeros(totalpoints, dtype=np.float64)
    
    # === (C) PASS 1 : temps de parcours — entièrement vectorisé (NumPy) ===
    # Pente, vitesse Tobler et durée par segment calculées en une passe ;
    # cumul temporel via np.cumsum (séquentiel mais O(N) sans appel Python).
    slope_ratios = np.zeros(totalpoints, dtype=np.float64)
    if totalpoints >= 2:
        elev_diff = allelevations[1:] - allelevations[:-1]
        safe_dists = np.where(seg_dists_m[:-1] > 0.0, seg_dists_m[:-1], 1.0)
        slope_ratios[:-1] = np.where(seg_dists_m[:-1] > 0.0, elev_diff / safe_dists, 0.0)

    seg_slopes_percent = slope_ratios * 100.0

    # Tobler vectorisé : (6 * exp(-3.5 * |slope+0.05|)) / 3.6 [m/s]
    speeds_ms = (6.0 * np.exp(-3.5 * np.abs(slope_ratios + 0.05))) / 3.6
    valid_seg = (seg_dists_m > 0.0) & (speeds_ms > 0.1)
    seg_durs_s = np.where(valid_seg, seg_dists_m / np.maximum(speeds_ms, 0.1), 0.0)
    if totalpoints > 0:
        seg_durs_s[-1] = 0.0  # dernier point : pas de segment sortant

    # Décalage cumulatif : point i démarre à start + sum(durées[0..i-1])
    cum_offsets = np.concatenate(([0.0], np.cumsum(seg_durs_s[:-1]))) if totalpoints > 0 else np.zeros(0)
    start_aware = localtz.localize(startdt).astimezone(pytz.utc)
    start_ts = start_aware.timestamp()
    times_ts = start_ts + cum_offsets

    # Attribution finale (boucle Python inévitable pour assigner aux objets GPXTrackPoint)
    for i in range(totalpoints):
        p = simpoints[i]
        p.elevation = float(allelevations[i])
        p.time = datetime.fromtimestamp(float(times_ts[i]), tz=pytz.utc)

    # === (D) PASS 2 : calcul ombres (ton code existant) ===

    allstatuses = []
    all_shadow_hits = []
    # Mettre à jour le shadowmode dans hgtmanager avant le ray-tracing
    hgtmanager.shadowmode = shadowmode
    for i in range(0, totalpoints, batch_size):
        sl = slice(i, i + batch_size)
        batchstatuses, batch_shadow_hits = get_sun_blocking_type_vec(
            alllats[sl], alllons[sl], times_ts[sl], hgtmanager, solar_step_s)
        allstatuses.extend(batchstatuses)
        all_shadow_hits.extend(batch_shadow_hits)

    # === (E) PASS 3 : agrégation (on réutilise dist/durée pré-calculés) ===

    stats = {
        "totaldist": 0.0, "totaldur": 0.0,
        "distsun": 0.0, "distrelief": 0.0, "distveg": 0.0, "distrelief_veg": 0.0,
        "dursun": 0.0, "durrelief": 0.0, "durveg": 0.0, "durrelief_veg": 0.0,
    }
    processeddata = []

    categorymap = {"RELIEF": "relief", "VEGETATION": "veg", "SUN": "sun", "RELIEF_VEG": "relief_veg"}

    for i in range(totalpoints):
        p1 = simpoints[i]
        rawstatus = allstatuses[i]

        # Le filtrage est déjà fait à la source
        finalstatus = rawstatus

        dist = float(seg_dists_m[i])
        duration = float(seg_durs_s[i])

        if finalstatus != "NIGHT":
            cat = categorymap.get(finalstatus)
            if cat:
                stats[f"dist{cat}"] += dist
                stats[f"dur{cat}"] += duration

            stats["totaldist"] += dist
            stats["totaldur"] += duration

        processeddata.append({
            "point": p1, 
            "status": finalstatus, 
            "shadow_hit": all_shadow_hits[i] if i < len(all_shadow_hits) else None,
            "slope_percent": seg_slopes_percent[i] # Add slope percentage here
        })


    return processeddata, stats


def aligned_bbox_from_processeddata(processeddata, res, transformer_l93, margin_meters=500):
    """
    Calcule une Bbox Lambert93 alignée sur une grille à partir des points d'une trace.
    Ajoute une marge de sécurité pour capturer les ombres projetées au-delà de la trace.
    
    Args:
        processeddata: Données de trace traitées
        res: Résolution en mètres
        transformer_l93: Transformateur vers Lambert93
        margin_meters: Marge de sécurité en mètres (défaut: 500m)
    """
    lats = np.array([item["point"].latitude for item in processeddata], dtype=np.float64)
    lons = np.array([item["point"].longitude for item in processeddata], dtype=np.float64)
    
    xs, ys = transformer_l93.transform(lons, lats)
    
    xmin, xmax = float(np.min(xs)), float(np.max(xs))
    ymin, ymax = float(np.min(ys)), float(np.max(ys))

    # ✅ AJOUT DE MARGE : Étendre la bbox pour capturer les ombres lointaines
    xmin -= margin_meters
    ymin -= margin_meters
    xmax += margin_meters
    ymax += margin_meters

    # Alignement pixel (vers l'extérieur)
    xmin_a = math.floor(xmin / res) * res
    ymin_a = math.floor(ymin / res) * res
    xmax_a = math.ceil(xmax / res) * res
    ymax_a = math.ceil(ymax / res) * res
    
    # Dimensions raster entières
    width = int(round((xmax_a - xmin_a) / res))
    height = int(round((ymax_a - ymin_a) / res))
    
    # Transform affine (top-left = xmin_a, ymax_a)
    transform = from_origin(xmin_a, ymax_a, res, res)
    
    return xmin_a, ymin_a, xmax_a, ymax_a, width, height, transform


def build_time_function_segmented(trace_points_with_time, transformer_l93):
    """Construit l'interpolateur temporel vectorisé t_of_xy_vec(xs, ys).

    Pour chaque pixel, on cherche le segment de trace le plus proche puis on
    interpole le temps le long de ce segment (projection paramétrique). Le
    coût brut est O(n_pixels × n_segments). Pour une longue trace, c'est le
    poste dominant de la carte d'ombre → on PRÉFILTRE les segments par bloc
    (cf. _nearest dans t_of_xy_vec) : seuls les segments susceptibles d'être
    le plus proche d'un pixel du bloc sont conservés.

    (L'ancienne variante scalaire STRtree t_of_xy n'était jamais appelée par
    compute_shadow_geotiff — supprimée avec son arbre, construit pour rien.)"""
    n_seg = max(0, len(trace_points_with_time) - 1)

    # Pré-projection vectorisée des endpoints en Lambert93
    if n_seg > 0:
        all_lats = np.array([p.latitude  for p in trace_points_with_time], dtype=np.float64)
        all_lons = np.array([p.longitude for p in trace_points_with_time], dtype=np.float64)
        all_xs, all_ys = transformer_l93.transform(all_lons, all_lats)
        all_xs = np.asarray(all_xs, dtype=np.float64)
        all_ys = np.asarray(all_ys, dtype=np.float64)
        seg_x1 = all_xs[:-1].copy()
        seg_y1 = all_ys[:-1].copy()
        seg_x2 = all_xs[1:].copy()
        seg_y2 = all_ys[1:].copy()
        seg_t1 = np.array([p.time.timestamp() for p in trace_points_with_time[:-1]], dtype=np.float64)
        seg_t2 = np.array([p.time.timestamp() for p in trace_points_with_time[1:]],  dtype=np.float64)
    else:
        seg_x1 = seg_y1 = seg_x2 = seg_y2 = seg_t1 = seg_t2 = np.zeros(0, dtype=np.float64)

    logging.info("Interpolation temporelle : %d segments [%s]",
                 n_seg, "Numba" if NUMBA_AVAILABLE else "NumPy")
    if n_seg > 0:
        logging.info("   Début : %s, Fin : %s",
                     trace_points_with_time[0].time.strftime('%H:%M'),
                     trace_points_with_time[-1].time.strftime('%H:%M'))

    def _prefilter_segments(xs, ys):
        """Indices des segments pouvant être le plus proche d'UN pixel du bloc.

        Borne provablement sûre : soit C le centre du bloc et r son demi-
        diagonal (donc tout pixel p vérifie dist(C,p) ≤ r). Pour le segment
        S* le plus proche de C, tout pixel a un voisin à distance
        ≤ r + min(dC). Donc le vrai plus proche S d'un pixel vérifie
        dist(C,S) ≤ 2r + min(dC). On garde dC ≤ min(dC) + 2r — jamais le
        vrai plus proche n'est écarté (les ex æquo sont tous conservés, donc
        l'argmin — premier minimum — est identique au calcul sur tous les
        segments)."""
        xmin = xs.min(); xmax = xs.max()
        ymin = ys.min(); ymax = ys.max()
        cx = 0.5 * (xmin + xmax); cy = 0.5 * (ymin + ymax)
        r = 0.5 * math.hypot(xmax - xmin, ymax - ymin)
        # Distance² du centre C à chaque segment (point→segment, vectorisé)
        dx = seg_x2 - seg_x1; dy = seg_y2 - seg_y1
        L2 = dx * dx + dy * dy
        L2_safe = np.where(L2 < 1e-12, 1.0, L2)
        t = ((cx - seg_x1) * dx + (cy - seg_y1) * dy) / L2_safe
        t = np.clip(t, 0.0, 1.0)
        qx = seg_x1 + t * dx; qy = seg_y1 + t * dy
        dC = np.hypot(cx - qx, cy - qy)
        thr = dC.min() + 2.0 * r
        return np.where(dC <= thr)[0]

    def _nearest_numpy(xs, ys, sx1, sy1, sx2, sy2):
        """Fallback NumPy si Numba absent — tiled pour la mémoire."""
        n = xs.size; m = sx1.size
        best_idx = np.empty(n, dtype=np.int64)
        best_t   = np.empty(n, dtype=np.float64)
        chunk = max(1024, min(8192, 65536 // max(1, m)))
        for k in range(0, n, chunk):
            px = xs[k:k+chunk, None]; py = ys[k:k+chunk, None]
            dx = (sx2 - sx1)[None, :]
            dy = (sy2 - sy1)[None, :]
            L2 = dx*dx + dy*dy
            L2_safe = np.where(L2 < 1e-12, 1.0, L2)
            t = ((px - sx1[None, :])*dx + (py - sy1[None, :])*dy) / L2_safe
            t = np.clip(t, 0.0, 1.0)
            qx = sx1[None, :] + t*dx
            qy = sy1[None, :] + t*dy
            d2 = (px - qx)**2 + (py - qy)**2
            idx = np.argmin(d2, axis=1)
            best_idx[k:k+chunk] = idx
            best_t[k:k+chunk]   = t[np.arange(idx.size), idx]
        return best_idx, best_t

    def t_of_xy_vec(xs, ys):
        """Interpolation vectorisée — retourne np.ndarray[float64] de timestamps UTC."""
        xs = np.ascontiguousarray(xs, dtype=np.float64)
        ys = np.ascontiguousarray(ys, dtype=np.float64)
        if seg_x1.size == 0 or xs.size == 0:
            ts0 = (trace_points_with_time[0].time.timestamp()
                   if trace_points_with_time else 0.0)
            return np.full(xs.size, ts0, dtype=np.float64)

        # Préfiltrage spatial : restreint l'ensemble des segments candidats.
        keep = _prefilter_segments(xs, ys)
        sx1 = np.ascontiguousarray(seg_x1[keep]); sy1 = np.ascontiguousarray(seg_y1[keep])
        sx2 = np.ascontiguousarray(seg_x2[keep]); sy2 = np.ascontiguousarray(seg_y2[keep])

        if NUMBA_AVAILABLE:
            bi, bt = _nearest_seg_with_param(xs, ys, sx1, sy1, sx2, sy2)
        else:
            bi, bt = _nearest_numpy(xs, ys, sx1, sy1, sx2, sy2)
        gi = keep[bi]   # indices locaux (sous-ensemble) → indices globaux
        return seg_t1[gi] + bt * (seg_t2[gi] - seg_t1[gi])

    return t_of_xy_vec










_STATUS_STR_TO_CODE = {'SUN': 0, 'RELIEF': 1, 'VEGETATION': 2, 'RELIEF_VEG': 3, 'NIGHT': 4}


def process_block(args):
    i, j, block_width, block_height, xmin_a, ymax_a, res, t_of_xy_vec, shadow_mode, hgt_manager = args

    hgt_manager.shadowmode = shadow_mode

    # Grille pixel → Lambert93 (centre des pixels)
    x_grid = (np.arange(i, i + block_width) + 0.5) * res + xmin_a
    y_grid = ymax_a - (np.arange(j, j + block_height) + 0.5) * res
    x_coords, y_coords = np.meshgrid(x_grid, y_grid)

    x_flat = np.ascontiguousarray(x_coords.ravel())
    y_flat = np.ascontiguousarray(y_coords.ravel())

    # Interpolation temporelle vectorisée (1 appel pour tous les pixels) :
    # ts_arr (timestamps UTC) est passé tel quel — plus de matérialisation de
    # dizaines de milliers d'objets datetime puis re-conversion en timestamps.
    ts_arr = t_of_xy_vec(x_flat, y_flat)  # array float64 de timestamps UTC

    transformer_wgs84 = TransformerPool.lambert_to_wgs84()
    lons, lats = transformer_wgs84.transform(x_flat, y_flat)

    statuses, _ = get_sun_blocking_type_vec(
        lats, lons, ts_arr, hgt_manager, solar_step_s=hgt_manager.solar_step_s)

    # Conversion statuts → uint8 vectorisée via dict.get sur la liste (rapide)
    numeric_statuses = np.fromiter(
        (_STATUS_STR_TO_CODE.get(s, 255) for s in statuses),
        dtype=np.uint8, count=len(statuses)
    )
    result_block = numeric_statuses.reshape((block_height, block_width))

    return (i, j, result_block)

def compute_shadow_geotiff(processed_data, hgt_manager, shadow_mode,
                              analysis_resolution, out_tif,
                              progress_callback=None, num_workers=4, margin_meters=500):
    """Version avec updates GUI throttlés"""

    # Charge numba à la demande (idempotent — déjà chargé si simulatehike
    # est passé avant).
    _try_load_numba()

    log_func = hgt_manager.log
    transformer_l93 = TransformerPool.wgs84_to_lambert()
    res = float(analysis_resolution)
    
    xmin_a, ymin_a, xmax_a, ymax_a, width, height, transform = \
        aligned_bbox_from_processeddata(processed_data, res, transformer_l93, margin_meters=margin_meters)
    
    log_func(f"Grille: {width}x{height} pixels, résolution {res}m")
    
    
    # ✅ NOUVELLE LOGIQUE: Interpolation temporelle par segment (plus précise)
    trace_points_with_time = [item['point'] for item in processed_data]
    t_of_xy_vec = build_time_function_segmented(trace_points_with_time, transformer_l93)
    log_func("Fonction d'interpolation temporelle par segment créée.")
    
    profile = {
        'driver': 'GTiff', 'dtype': 'uint8', 'nodata': 255,
        'width': width, 'height': height, 'count': 1,
        'crs': 'EPSG:2154', 'transform': transform, 'compress': 'lzw'
    }
    
    block_size = 256
    total_blocks = math.ceil(width / block_size) * math.ceil(height / block_size)
    processed_blocks = 0
    
    GUI_UPDATE_INTERVAL = max(1, total_blocks // 20)
    
    log_func(f"Traitement {total_blocks} blocs avec {num_workers} workers...")
    
    hgt_manager.shadowmode = shadow_mode
    
    with rasterio.open(out_tif, 'w', **profile) as dst:
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            
            block_tasks = []
            for j in range(0, height, block_size):
                for i in range(0, width, block_size):
                    block_height = min(block_size, height - j)
                    block_width = min(block_size, width - i)
                    args = (i, j, block_width, block_height, xmin_a, ymax_a,
                           res, t_of_xy_vec,
                           shadow_mode, hgt_manager)
                    block_tasks.append(args)
            
            futures = {executor.submit(process_block, task): task for task in block_tasks}
            
            for future in concurrent.futures.as_completed(futures):
                i, j, result_block = future.result()
                dst.write(result_block, 
                         window=Window(i, j, result_block.shape[1], 
                                      result_block.shape[0]), indexes=1)
                
                processed_blocks += 1
                
                # 🔥 Update GUI uniquement tous les N blocs
                if progress_callback and (processed_blocks % GUI_UPDATE_INTERVAL == 0 
                                         or processed_blocks == total_blocks):
                    progress = 60 + (processed_blocks / total_blocks) * 30
                    progress_callback(progress, 
                                    f"Carte: {processed_blocks}/{total_blocks}")
    


# Palette carte d'ombre : code statut uint8 → RGBA. Source de vérité unique
# partagée par l'export KMZ ET l'export MBTiles.
#
# La semi-transparence est BAKÉE dans les pixels (alpha SHADOW_OVERLAY_ALPHA),
# et il FAUT laisser l'opacité du calque à 100 % côté app. Raison (contre-
# intuitive) : Locus/OsmAnd composent un calque semi-transparent tuile par
# tuile ; quand on baisse l'opacité DU CALQUE, les bords de tuiles ne fusionnent
# pas et des COUTURES apparaissent. Avec la transparence bakée + calque à 100 %,
# chaque tuile n'est dessinée qu'une fois (pas de re-composition d'opacité) →
# pas de couture, et le fond transparaît quand même. Le KMZ (image unique) n'a
# pas ce souci mais utilise la même teinte/alpha pour un rendu identique.
# nodata (255) → alpha 0 (hors emprise totalement transparent).
SHADOW_OVERLAY_ALPHA = 110   # ≈43 % — bakée dans le KMZ et les tuiles MBTiles
SHADOW_RGB = {
    0: (255, 255, 0),    # SUN → jaune
    1: (160, 160, 160),  # RELIEF → gris
    2: (0,   153, 0),    # VEGETATION → vert
    3: (165, 42,  42),   # RELIEF + VEGETATION → marron
    4: (0,   0,   0),    # NIGHT → noir
}
SHADOW_COLOR_MAP = {c: (r, g, b, SHADOW_OVERLAY_ALPHA)
                    for c, (r, g, b) in SHADOW_RGB.items()}
SHADOW_COLOR_MAP[255] = (0, 0, 0, 0)   # nodata → transparent


def geotiff_to_kml_groundoverlay(tif_path, kmz_output_path, log_func, existing_kml_obj=None, progress_callback=None):
    """
    Convertit un GeoTIFF de carte d'ombre en une image PNG colorisée
    et crée un fichier KML avec un GroundOverlay pour l'afficher.
    Utilise rasterio et Pillow pour éviter une dépendance à GDAL.
    """
    if not PIL_AVAILABLE:
        log_func("ERREUR: La librairie Pillow est requise pour la conversion PNG.")
        return None

    log_func("DEBUG: Démarrage de la conversion GeoTIFF -> KML GroundOverlay (avec Pillow).")
    png_path = tif_path.replace(".tif", ".png")

    color_map = SHADOW_COLOR_MAP

    try:
        with rasterio.open(tif_path) as src:
            # Lire les données du raster
            data = src.read(1)
            
            # Créer une image RGBA vide
            rgba = np.zeros((data.shape[0], data.shape[1], 4), dtype=np.uint8)
            
            # Appliquer la palette de couleurs
            for value, color in color_map.items():
                rgba[data == value] = color
            
            # Créer l'image avec Pillow et la sauvegarder
            img = Image.fromarray(rgba)
            img.save(png_path)
            log_func(f"DEBUG: Conversion GeoTIFF -> PNG terminée: {png_path}")

    except Exception as e:
        log_func(f"ERREUR: Impossible de convertir le GeoTIFF en PNG: {e}")
        traceback.print_exc()
        return None

    # Création du GroundOverlay KMZ
    try:
        kml = existing_kml_obj if existing_kml_obj else simplekml.Kml(name=os.path.basename(tif_path).replace(".tif", ""))
        go = kml.newgroundoverlay(name="Carte d'ombre")
        
        # Ajouter le PNG à l'archive et définir la référence
        go.icon.href = kml.addfile(png_path)
        
        # Placer l'image par ses 4 COINS RÉELS (gx:LatLonQuad), pas dans une
        # boîte lat/lon nord-haut. Une LatLonBox suppose nord-grille = nord
        # géographique : faux en Lambert93 (convergence des méridiens ~2° à
        # 5,9°E), ce qui décalait l'overlay de ~100 m aux coins (l'ombre ne
        # tombait plus sur la trace). Le quad épouse le rectangle Lambert93 réel
        # → overlay aligné sur la trace dans Google Earth.
        # NB : QGIS ne rend PAS les GroundOverlay gx:LatLonQuad (limite OGR/KML).
        # Pour le SIG, utiliser le GeoTIFF (EPSG:2154) ou le MBTiles (EPSG:3857),
        # tous deux exacts ; le KMZ est un livrable Google Earth.
        with rasterio.open(tif_path) as src:
            bl = src.bounds
            transformer_wgs84 = TransformerPool.lambert_to_wgs84()
            # Ordre KML gx:LatLonQuad : sens antihoraire depuis le bas-gauche
            # de l'image (SW, SE, NE, NW), en (lon, lat).
            sw = transformer_wgs84.transform(bl.left,  bl.bottom)
            se = transformer_wgs84.transform(bl.right, bl.bottom)
            ne = transformer_wgs84.transform(bl.right, bl.top)
            nw = transformer_wgs84.transform(bl.left,  bl.top)
            go.gxlatlonquad.coords = [sw, se, ne, nw]

        kml.savekmz(kmz_output_path) # kmz_output_path est maintenant le chemin du KMZ
        log_func(f"DEBUG: KMZ GroundOverlay créé: {kmz_output_path}")
        return kmz_output_path
    except Exception as e:
        log_func(f"ERREUR: Impossible de créer le KMZ GroundOverlay: {e}")
        traceback.print_exc()
        return None


def geotiff_to_mbtiles_overlay(tif_path, mbtiles_path, log_func,
                               zoom_min=None, zoom_max=None,
                               progress_callback=None):
    """
    Convertit le GeoTIFF de carte d'ombre (EPSG:2154, uint8 catégoriel) en un
    MBTiles overlay : tuiles PNG RGBA transparentes, en Web Mercator (EPSG:3857).

    But : charger la carte d'ombre comme CALQUE transparent dans les apps GPS
    smartphone qui lisent le MBTiles (Locus Map, OsmAnd, OruxMaps, Guru Maps…),
    par-dessus le fond de carte topo. MBTiles = format standard (SQLite avec un
    schéma défini), même pipeline que lidar2map mais avec deux spécialisations
    voulues par la nature du raster :
      - resampling NEAREST (pas bilinear) : raster CATÉGORIEL (codes 0=soleil …
        4=nuit, 255=nodata). Interpoler entre catégories produirait des codes
        intermédiaires absurdes (lidar2map est en hillshade continu → bilinear).
      - tuiles PNG RGBA (pas JPEG) : un overlay doit être transparent
        (nodata 255 → alpha 0 → le fond de carte transparaît).

    Réutilise SHADOW_COLOR_MAP : rendu identique à l'overlay KML.
    """
    if not PIL_AVAILABLE:
        log_func("ERREUR: Pillow requis pour l'export MBTiles.")
        return None

    import sqlite3, io

    from rasterio.warp import reproject, Resampling
    from rasterio.transform import from_origin

    EARTH_CIRC = 20037508.3427892   # demi-circonférence Mercator (m)
    EARTH_R    = 6378137.0           # rayon sphère Web Mercator (m)
    TILE_SIZE  = 256

    def merc_to_tile(mx, my, z):
        n = 2 ** z
        return (int((mx + EARTH_CIRC) / (2 * EARTH_CIRC) * n),
                int((EARTH_CIRC - my) / (2 * EARTH_CIRC) * n))

    def tile_bounds(tx, ty, z):
        n  = 2 ** z
        x0 = tx / n * 2 * EARTH_CIRC - EARTH_CIRC
        y1 = EARTH_CIRC - ty / n * 2 * EARTH_CIRC
        x1 = (tx + 1) / n * 2 * EARTH_CIRC - EARTH_CIRC
        y0 = EARTH_CIRC - (ty + 1) / n * 2 * EARTH_CIRC
        return x0, y0, x1, y1   # xmin ymin xmax ymax

    def lonlat_to_merc(lon, lat):
        mx = math.radians(lon) * EARTH_R
        my = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * EARTH_R
        return mx, my

    # LUT statut → RGBA (vectorise la colorisation : lut[codes] en un coup).
    # Semi-transparence BAKÉE (SHADOW_COLOR_MAP) — l'utilisateur laisse l'opacité
    # du calque à 100 % côté app, sinon Locus recompose les tuiles et fait
    # réapparaître les coutures (cf. note de SHADOW_COLOR_MAP).
    # nodata (code 255) → (0,0,0,0) : hors emprise totalement transparent.
    lut = np.zeros((256, 4), dtype=np.uint8)
    for code, rgba in SHADOW_COLOR_MAP.items():
        lut[code] = rgba

    try:
        with rasterio.open(tif_path) as src:
            transformer_wgs84 = TransformerPool.lambert_to_wgs84()
            b = src.bounds
            west,  south = transformer_wgs84.transform(b.left,  b.bottom)
            east,  north = transformer_wgs84.transform(b.right, b.top)
            lat_c = (south + north) / 2.0
            lon_c = (west + east) / 2.0
            xmin_merc, ymin_merc = lonlat_to_merc(west, south)
            xmax_merc, ymax_merc = lonlat_to_merc(east, north)

            # Zoom auto : caler la résolution native des tuiles sur celle du
            # raster source. CEIL (pas round) : on veut une résolution de tuile
            # au moins aussi fine que la source — round() pouvait choisir un
            # niveau plus GROSSIER (ex. 6,9 m/px à z14 alors que la source est
            # à 5 m), d'où une carte d'ombre moins précise que le KMZ natif.
            # En Mercator la résolution sol = res_equateur·cos(lat).
            src_res_m = abs(src.transform.a)  # m/px en Lambert93 ≈ m sol
            if zoom_max is None:
                merc_res_eq = 2 * EARTH_CIRC / TILE_SIZE  # res à z0 (équateur)
                z_native = math.log2(merc_res_eq * math.cos(math.radians(lat_c))
                                     / max(src_res_m, 1e-6))
                zoom_max = int(min(19, max(10, math.ceil(z_native))))
            if zoom_min is None:
                zoom_min = max(8, zoom_max - 4)

            if os.path.exists(mbtiles_path):
                os.remove(mbtiles_path)
            con = sqlite3.connect(mbtiles_path)
            cur = con.cursor()
            cur.executescript("""
                CREATE TABLE metadata (name TEXT, value TEXT);
                CREATE TABLE tiles   (zoom_level INTEGER, tile_column INTEGER,
                                      tile_row   INTEGER, tile_data   BLOB);
                CREATE UNIQUE INDEX idx_tiles ON tiles (zoom_level, tile_column, tile_row);
            """)
            bounds = f"{west:.6f},{south:.6f},{east:.6f},{north:.6f}"
            for k, v in [("name", os.path.splitext(os.path.basename(mbtiles_path))[0]),
                         ("type", "overlay"), ("version", "1.0"),
                         ("description", "Carte d'ombre portée solaire (gpxsolar)"),
                         ("format", "png"),
                         ("minzoom", str(zoom_min)), ("maxzoom", str(zoom_max)),
                         ("bounds", bounds),
                         ("center", f"{lon_c:.6f},{lat_c:.6f},{zoom_max}")]:
                cur.execute("INSERT INTO metadata VALUES (?,?)", (k, v))

            batch, BATCH = [], 500
            total = 0

            # Tuilage ALIGNÉ sur la grille de tuiles, niveau par niveau.
            # Pour chaque zoom : reprojection Lambert93 → Web Mercator sur une
            # grille dont l'origine est le coin d'une tuile et la résolution
            # exactement celle du niveau. Chaque tuile devient alors un slice
            # EXACT de 256 px (aucun arrondi par tuile) → pas de couture, et la
            # source est échantillonnée à la résolution propre de chaque zoom
            # (pas de pyramide de moyennage). C'est ce que fait gdal2tiles.
            # Coût : une reprojection par zoom, mais les cartes sont locales
            # (quelques milliers de px) → négligeable.
            for z in range(zoom_min, zoom_max + 1):
                res_z = 2 * EARTH_CIRC / (TILE_SIZE * 2 ** z)
                tx0, ty0 = merc_to_tile(xmin_merc, ymax_merc, z)  # coin haut-gauche
                tx1, ty1 = merc_to_tile(xmax_merc, ymin_merc, z)  # coin bas-droite
                gx0, _, _, gy1 = tile_bounds(tx0, ty0, z)         # origine grille alignée
                grid_w = (tx1 - tx0 + 1) * TILE_SIZE
                grid_h = (ty1 - ty0 + 1) * TILE_SIZE
                grid_transform = from_origin(gx0, gy1, res_z, res_z)
                grid = np.full((grid_h, grid_w), 255, dtype=np.uint8)
                reproject(
                    source        = rasterio.band(src, 1),
                    destination   = grid,
                    src_transform = src.transform, src_crs = src.crs,
                    src_nodata    = 255,
                    dst_transform = grid_transform, dst_crs = "EPSG:3857",
                    dst_nodata    = 255,
                    resampling    = Resampling.nearest,
                    num_threads   = 0)

                for ty in range(ty0, ty1 + 1):
                    ry = (ty - ty0) * TILE_SIZE
                    for tx in range(tx0, tx1 + 1):
                        rx = (tx - tx0) * TILE_SIZE
                        codes = grid[ry:ry + TILE_SIZE, rx:rx + TILE_SIZE]
                        if not (codes != 255).any():
                            continue   # tuile entièrement nodata → transparente → rien à écrire
                        rgba = lut[codes]   # (256, 256, 4)
                        buf = io.BytesIO()
                        Image.fromarray(rgba, "RGBA").save(
                            buf, "PNG", optimize=False, compress_level=6)
                        y_tms = (2 ** z - 1) - ty   # MBTiles = schéma TMS (Y inversé vs XYZ)
                        batch.append((z, tx, y_tms, buf.getvalue()))
                        total += 1
                    if len(batch) >= BATCH:
                        cur.executemany("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)", batch)
                        con.commit(); batch.clear()
            if batch:
                cur.executemany("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)", batch)
                con.commit()
            con.close()
        log_func(f"DEBUG: MBTiles overlay créé: {mbtiles_path} "
                 f"(z{zoom_min}-{zoom_max}, {total} tuiles)")
        return mbtiles_path
    except Exception as e:
        log_func(f"ERREUR: Impossible de créer le MBTiles: {e}")
        traceback.print_exc()
        return None


def make_ray_styles():
    def style(color, width=2):
        s = simplekml.Style()
        s.linestyle.color = color
        s.linestyle.width = width
        return s

    return {
        'SUN':         style('aa00ffff', 2),  # Jaune
        'VEGETATION':  style('aa00ff00', 2),  # Vert
        'RELIEF':      style('aa888888', 2),  # Gris
        'RELIEF_VEG':  style('aa003366', 2),  # Marron
    }

def extend_to_sun(lat, lon, alt, sun_az, sun_alt):
    if sun_alt < 0.5:
        return None

    m_lat, m_lon = get_meters_per_degree_wgs84(lat)

    alt_rad = np.deg2rad(max(sun_alt, 0.5))
    az_rad  = np.deg2rad(sun_az + 180.0)  # ← INVERSION CRUCIALE

    dist = min(20000, max(4000, 2000 / np.tan(alt_rad)))

    dlat = (dist * np.cos(az_rad)) / m_lat
    dlon = (dist * np.sin(az_rad)) / m_lon

    lat2 = lat + dlat
    lon2 = lon + dlon

    alt2 = (
        alt
        + dist * np.tan(alt_rad)
        - dist**2 / (2 * EARTH_RADIUS)
    )

    return lon2, lat2, alt2

def create_kml_file(original_path, processed_data, passage_interval_min=0, local_tz=None, hgt_manager=None, visualize_tiles=False, visualize_sun_rays=False, sun_ray_interval=20, analysis_type='ombre_soleil'):
    if not SIMPLEKML_AVAILABLE:
        logging.error("La librairie 'simplekml' est requise pour créer des fichiers KML.")
        return None

    kml = simplekml.Kml(name=f"Analyse {os.path.basename(original_path)}")

    # Définition des styles partagés pour la trace
    style_map_def = {
        'sun': ('ff00ffff', 4), 'relief': ('ffa0a0a0', 4),
        'vegetation': ('ff009900', 4), 'relief_veg': ('ff152aa5', 4),
        'night': ('ff000000', 4), 'inconnu': ('ff888888', 2)
    }
    
    shared_styles = {}
    for name, (color, width) in style_map_def.items():
        style = simplekml.Style()
        style.linestyle.color = color
        style.linestyle.width = width
        shared_styles[name] = style

    def calculate_destination(lat, lon, bearing, distance):
        R = 6371000  # Rayon de la Terre en mètres
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        bearing_rad = math.radians(bearing)
        
        lat2_rad = math.asin(math.sin(lat_rad) * math.cos(distance / R) +
                             math.cos(lat_rad) * math.sin(distance / R) * math.cos(bearing_rad))
        
        lon2_rad = lon_rad + math.atan2(math.sin(bearing_rad) * math.sin(distance / R) * math.cos(lat_rad),
                                       math.cos(distance / R) - math.sin(lat_rad) * math.sin(lat2_rad))
        
        return math.degrees(lat2_rad), math.degrees(lon2_rad)

    if not processed_data: return None

    if analysis_type == 'ombre_soleil':
        # Regrouper les segments par statut/couleur
        segments_by_status = {}
        last_status = processed_data[0]['status']
        current_segment_points = []
        for item in processed_data:
            p, status = item['point'], item['status']
            if not current_segment_points:
                current_segment_points.append(p)
                last_status = status
                continue
            if status != last_status:
                current_segment_points.append(p)
                if len(current_segment_points) > 1:
                    if last_status not in segments_by_status:
                        segments_by_status[last_status] = []
                    segments_by_status[last_status].append([(pt.longitude, pt.latitude, pt.elevation) for pt in current_segment_points])
                current_segment_points = [p]
            else:
                current_segment_points.append(p)
            last_status = status
        if len(current_segment_points) > 1:
            if last_status not in segments_by_status:
                segments_by_status[last_status] = []
            segments_by_status[last_status].append([(pt.longitude, pt.latitude, pt.elevation) for pt in current_segment_points])

        # Pour chaque statut/couleur, créer une MultiGeometry à la racine et y ajouter les segments
        for status, segments in segments_by_status.items():
            multigeo = kml.newmultigeometry(name=f"TOTAL_{status.upper()}")
            multigeo.style = shared_styles.get(status.lower(), shared_styles['inconnu'])
            for coords in segments:
                multigeo.newlinestring(coords=coords)
    elif analysis_type == 'pente':
        # Définition des styles pour la pente (schéma séquentiel basé sur la valeur absolue)
        slope_style_map = {
            'extreme': ('ff00008b', 5), # Rouge Foncé (> 30%)
            'vsteep': ('ff0000ff', 4),  # Rouge (20-30%)
            'steep': ('ff0080ff', 4),   # Orange (10-20%)
            'moderate': ('ff00ffff', 4),# Jaune (5-10%)
            'low': ('ff00ff00', 4)      # Vert (0-5%)
        }
        
        shared_slope_styles = {}
        for name, (color, width) in slope_style_map.items():
            style = simplekml.Style()
            style.linestyle.color = color
            style.linestyle.width = width
            shared_slope_styles[name] = style

        def get_slope_category(slope_percent):
            abs_slope = abs(slope_percent)
            if abs_slope > 30: return 'extreme'
            if abs_slope > 20: return 'vsteep'
            if abs_slope > 10: return 'steep'
            if abs_slope > 5: return 'moderate'
            return 'low'

        segments_by_slope_category = {}
        # Regrouper les segments par catégorie de pente
        last_slope_category = None
        current_segment_points = []
        for i, item in enumerate(processed_data):
            p = item['point']
            # Pour le dernier point, la pente est celle du segment précédent
            slope_percent = item['slope_percent'] if i < len(processed_data) - 1 else processed_data[i-1]['slope_percent'] if i > 0 else 0
            current_category = get_slope_category(slope_percent)
            
            if not current_segment_points:
                current_segment_points.append(p)
                last_slope_category = current_category
                continue
            
            if current_category != last_slope_category:
                current_segment_points.append(p) # Ajouter le point actuel pour fermer le segment précédent
                if len(current_segment_points) > 1:
                    if last_slope_category not in segments_by_slope_category:
                        segments_by_slope_category[last_slope_category] = []
                    segments_by_slope_category[last_slope_category].append([(pt.longitude, pt.latitude, pt.elevation) for pt in current_segment_points])
                current_segment_points = [p] # Démarrer un nouveau segment
            else:
                current_segment_points.append(p)
            last_slope_category = current_category
        
        # Ajouter le dernier segment
        if len(current_segment_points) > 1:
            if last_slope_category not in segments_by_slope_category:
                segments_by_slope_category[last_slope_category] = []
            segments_by_slope_category[last_slope_category].append([(pt.longitude, pt.latitude, pt.elevation) for pt in current_segment_points])

        # Pour chaque catégorie de pente, créer une MultiGeometry
        for category, segments in segments_by_slope_category.items():
            multigeo = kml.newmultigeometry(name=f"Pente {category.upper()}")
            style = shared_slope_styles.get(category, shared_styles['inconnu'])
            multigeo.style = style

            for coords in segments:
                linestring = multigeo.newlinestring(coords=coords)
                linestring.altitudemode = simplekml.AltitudeMode.clamptoground

                # --- Nouvelle logique pour les flèches ---
                # Placer une flèche au milieu de chaque segment de couleur continue
                if len(coords) > 2:
                    mid_index = len(coords) // 2
                    p1_coords = coords[mid_index -1]
                    p2_coords = coords[mid_index]

                    lon1, lat1, _ = p1_coords
                    lon2, lat2, _ = p2_coords
                    
                    # Calcul du bearing
                    lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
                    lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)
                    dLon = lon2_rad - lon1_rad
                    y = math.sin(dLon) * math.cos(lat2_rad)
                    x = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(dLon)
                    bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
                    
                    # Point central pour la pointe de la flèche
                    mid_lon = (lon1 + lon2) / 2
                    mid_lat = (lat1 + lat2) / 2
                    
                    ARROW_LENGTH = 12
                    ARROW_ANGLE = 30
                    
                    bearing1 = (bearing + 180 - ARROW_ANGLE + 360) % 360
                    bearing2 = (bearing + 180 + ARROW_ANGLE + 360) % 360
                    
                    end_lat1, end_lon1 = calculate_destination(mid_lat, mid_lon, bearing1, ARROW_LENGTH)
                    end_lat2, end_lon2 = calculate_destination(mid_lat, mid_lon, bearing2, ARROW_LENGTH)

                    # Obtenir l'altitude du terrain pour les extrémités des flèches
                    arrow_lats = np.array([mid_lat, end_lat1, end_lat2]) # Inclure mid_lat pour le point de départ de la flèche
                    arrow_lons = np.array([mid_lon, end_lon1, end_lon2]) # Inclure mid_lon
                    
                    # Utiliser get_ground_elevations_vec pour obtenir toutes les altitudes d'un coup
                    # S'assurer que hgt_manager est disponible et non None
                    if hgt_manager:
                        all_arrow_elevs = hgt_manager.get_ground_elevations_vec(arrow_lats, arrow_lons)
                        mid_elev_terrain = all_arrow_elevs[0] # Altitude du terrain au centre du segment
                        end_elev1_terrain = all_arrow_elevs[1]
                        end_elev2_terrain = all_arrow_elevs[2]
                    else: # Fallback si hgt_manager n'est pas fourni (ne devrait pas arriver en pratique ici)
                        mid_elev_terrain = (p1_coords[2] + p2_coords[2]) / 2 # Fallback to GPX elevation if no hgt_manager
                        end_elev1_terrain = (p1_coords[2] + p2_coords[2]) / 2
                        end_elev2_terrain = (p1_coords[2] + p2_coords[2]) / 2

                    # Créer les flèches à la racine du KML avec les altitudes corrigées et clampToGround
                    ls1 = kml.newlinestring(coords=[(mid_lon, mid_lat, mid_elev_terrain), (end_lon1, end_lat1, end_elev1_terrain)])
                    ls2 = kml.newlinestring(coords=[(mid_lon, mid_lat, mid_elev_terrain), (end_lon2, end_lat2, end_elev2_terrain)])
                    
                    ls1.altitudemode = simplekml.AltitudeMode.clamptoground
                    ls2.altitudemode = simplekml.AltitudeMode.clamptoground
                    ls1.style = style # Utiliser le style du segment
                    ls2.style = style # Utiliser le style du segment







    if visualize_tiles and hgt_manager:
            # Définition des styles pour les tuiles
            tile_style_used_green = simplekml.Style()
            tile_style_used_green.linestyle.color = simplekml.Color.green
            tile_style_used_green.linestyle.width = 3
            tile_style_used_green.polystyle.color = simplekml.Color.changealphaint(50, simplekml.Color.green) # Vert avec transparence
    
            tile_style_loaded_blue = simplekml.Style() # Nouveau style pour "chargées en RAM"
            tile_style_loaded_blue.linestyle.color = simplekml.Color.blue
            tile_style_loaded_blue.linestyle.width = 2
            tile_style_loaded_blue.polystyle.color = simplekml.Color.changealphaint(100, simplekml.Color.blue) # Bleu plus opaque (avant 40)
    
            tile_style_downloaded_light_blue = simplekml.Style() # Nouveau style pour "présentes sur disque"
            tile_style_downloaded_light_blue.linestyle.color = simplekml.Color.yellow
            tile_style_downloaded_light_blue.linestyle.width = 1
            tile_style_downloaded_light_blue.polystyle.color = simplekml.Color.changealphaint(50, simplekml.Color.yellow) # Jaune avec transparence
    
    
            # Début du bloc de visualisation des tuiles
    
            
            # Transformation inverse pour les tuiles Lambert93 vers WGS84
            inv_transformer_ign = None # Initialiser ici pour le scope
            # Vérifier si des tuiles IGN ou LiDAR sont présentes pour initialiser le transformer si nécessaire
            # OU s'il y a des tuiles LiDAR/IGN dans downloaded_tiles_info et qu'on les visualise.
            # Assurer que inv_transformer_ign est toujours initialisé si LiDAR ou IGN est actif comme source principale,
            # OU s'il y a des tuiles LiDAR/IGN dans downloaded_tiles_info et qu'on les visualise.
            if hgt_manager.source == 'ign_lidar_hd' or hgt_manager.source.startswith('ign_'):
                inv_transformer_ign = TransformerPool.lambert_to_wgs84()
            elif any(item[0] == 'ign' or item[0] == 'lidar' for item in hgt_manager.downloaded_tiles_info):
                inv_transformer_ign = TransformerPool.lambert_to_wgs84()
            # else: inv_transformer_ign reste None, c'est ce qu'on veut s'il n'y a pas de tuiles IGN/LiDAR
    
            for item in sorted(hgt_manager.downloaded_tiles_info, key=lambda x: x[0] + str(x[1])):
                source_type, tile_identifier, bbox_info = item
                
                lon_min, lat_min, lon_max, lat_max = None, None, None, None
                name = ""
    
                if source_type in ['srtm1', 'copernicus']:
                    name = os.path.basename(tile_identifier)
                    lon_min, lat_min, lon_max, lat_max = bbox_info
                elif source_type == 'lidar':
                    # bbox_info est déjà en Lambert93 (x0, y0, xmax, ymax)
                    name = f"LIDAR_{item[1][0]}_{item[1][1]}"
                    x_min, y_min, x_max, y_max = bbox_info
                    if inv_transformer_ign: # Utiliser le même transformer que pour IGN car les deux sont en Lambert93
                        lon_min, lat_min = inv_transformer_ign.transform(x_min, y_min)
                        lon_max, lat_max = inv_transformer_ign.transform(x_max, y_max)
    
                    else:
                        logging.warning(f"Transformer IGN/LiDAR non initialisé pour la tuile LiDAR {name}. Tuile non affichée.")
                        continue # Passer à la tuile suivante si la transformation ne peut pas être faite
                elif source_type == 'ign':
                    name = os.path.basename(tile_identifier)
                    # bbox_info est un tuple (xmin, ymin, xmax, ymax) en Lambert93
                    x_min, y_min, x_max, y_max = bbox_info
                    
                    # Transformation Lambert93 vers WGS84
                    if inv_transformer_ign:
                        lon_min, lat_min = inv_transformer_ign.transform(x_min, y_min)
                        lon_max, lat_max = inv_transformer_ign.transform(x_max, y_max)
                    else:
                        logging.warning("Transformer IGN non initialisé pour les tuiles IGN.")
                
                if lon_min is not None and lat_min is not None and lon_max is not None and lat_max is not None:
                    pol = kml.newpolygon(name=name)
                    # Fermer le polygone en répétant le premier point à la fin
                    pol.outerboundaryis = [(lon_min, lat_min), (lon_max, lat_min), (lon_max, lat_max), (lon_min, lat_max), (lon_min, lat_min)]
                    
                    # Choisir le style en fonction de l'utilisation
                    if item in hgt_manager.used_tiles:
                        pol.style = tile_style_used_green
                    elif item in hgt_manager.loaded_in_ram_tiles:
                        pol.style = tile_style_loaded_blue
                    else:
                        pol.style = tile_style_downloaded_light_blue

    if visualize_sun_rays and processed_data:
        try:
            logging.info(f"🌞 Création de {len(range(0, len(processed_data), sun_ray_interval))} rayons solaires (à la racine du KML)...")
            ray_styles = make_ray_styles()
            rays_created = 0
            
            for idx in range(0, len(processed_data), sun_ray_interval):
                item = processed_data[idx]
                pt = item['point']
                stat = item['status']
                hit = item.get('shadow_hit')
                
                if stat == 'NIGHT':
                    continue
                
                lat0, lon0, elev0 = pt.latitude, pt.longitude, (pt.elevation if pt.elevation else 0)
                alt0 = elev0 + OBSERVER_EYE_HEIGHT
                
                try:
                    sun_alt, sun_az = solar_altaz_cached(lat0, lon0, pt.time)
                    
                    if sun_alt <= 0: continue

                    if hit and stat in ('VEGETATION', 'RELIEF', 'RELIEF_VEG'):
                        lat_start, lon_start = hit
                        if hgt_manager:
                            try:
                                # Bug fix : get_ground_elevation n'existait pas — masqué par except.
                                # On utilise les variantes vectorisées (présentes) avec un tableau d'un point.
                                lat_arr = np.array([lat_start]); lon_arr = np.array([lon_start])
                                elev = float(hgt_manager.get_ground_elevations_vec(lat_arr, lon_arr)[0])
                                obj_h = float(hgt_manager.get_object_heights_vec(lat_arr, lon_arr)[0])
                                alt_start = elev + obj_h
                            except Exception:
                                alt_start = alt0
                        else: alt_start = alt0
                    else:
                        lat_start, lon_start, alt_start = lat0, lon0, alt0
                    
                    pt_sun = extend_to_sun(lat_start, lon_start, alt_start, sun_az, sun_alt)
                    if pt_sun is None: continue

                    ray = kml.newlinestring(name=f"Rayon {rays_created} - {stat}", coords=[(lon_start, lat_start, alt_start), pt_sun])
                    ray.altitudemode = simplekml.AltitudeMode.absolute
                    ray.style = ray_styles.get(stat, ray_styles['SUN'])
                    ray.extrude = 0
                    ray.tessellate = 0
                    
                    # Impact points removed per user request

                    rays_created += 1
                        
                except Exception as e:
                    logging.warning(f"Erreur rayon point {idx}: {e}")
                    continue
            
            logging.info(f"✓ {rays_created} rayons solaires créés (à la racine du KML)")
            
        except Exception as e:
            logging.error(f"ERREUR création rayons solaires: {e}")
    
    if passage_interval_min and passage_interval_min > 0 and processed_data:
        try:
            # Les times sont des datetime timezone-aware
            start_time = processed_data[0]['point'].time
            end_time = processed_data[-1]['point'].time
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=pytz.utc)
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=pytz.utc)

            # Calculer distances cumulées le long de la trace pour affichage
            try:
                lats = np.array([item['point'].latitude for item in processed_data], dtype=np.float64)
                lons = np.array([item['point'].longitude for item in processed_data], dtype=np.float64)
                if lats.size >= 2:
                    seg_dists = equirect_m_vec(lats[:-1], lons[:-1], lats[1:], lons[1:])
                    seg_dists = np.append(seg_dists, 0.0)
                else:
                    seg_dists = np.array([0.0])
                cumdist = np.concatenate(([0.0], np.cumsum(seg_dists[:-1])))
            except Exception:
                cumdist = np.zeros(len(processed_data), dtype=np.float64)

            # Choisir le fuseau d'affichage (local si fourni, sinon UTC)
            display_tz = local_tz if local_tz is not None else pytz.utc

            # Créer un seul placemark combiné pour Départ & Arrivée afin d'éviter le chevauchement
            p_start = processed_data[0]['point']
            p_end = processed_data[-1]['point']
            km0 = cumdist[0] / 1000.0
            km_end = (cumdist[-1] / 1000.0) if len(cumdist) == len(processed_data) else 0.0

            start_local_str = start_time.astimezone(display_tz).strftime('%Y-%m-%d %H:%M %Z')
            end_local_str = end_time.astimezone(display_tz).strftime('%Y-%m-%d %H:%M %Z')
            start_utc_str = start_time.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M UTC')
            end_utc_str = end_time.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M UTC')

            combined_name = f"Départ {start_time.astimezone(display_tz).strftime('%H:%M')} / Arrivée {end_time.astimezone(display_tz).strftime('%H:%M')} - {km_end:.2f} km"
            pm_comb = kml.newpoint(name=combined_name, coords=[(p_start.longitude, p_start.latitude, p_start.elevation)])
            pm_comb.style.iconstyle.scale = 1.0
            # Description détaillée lisible au clic
            pm_comb.description = (
                f"Départ (local): {start_local_str}\n"
                f"Départ (UTC): {start_utc_str}\n"
                f"Arrivée (local): {end_local_str}\n"
                f"Arrivée (UTC): {end_utc_str}\n"
                f"Distance totale: {km_end:.2f} km"
            )

            last_added_time = start_time

            next_time = start_time + timedelta(minutes=passage_interval_min)
            idx = 0
            while next_time <= end_time:
                # Avancer jusqu'au premier point dont l'heure >= next_time
                while idx < len(processed_data) and processed_data[idx]['point'].time < next_time:
                    idx += 1
                if idx >= len(processed_data):
                    break
                p = processed_data[idx]['point']
                iso = next_time.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                km = (cumdist[idx] / 1000.0) if idx < len(cumdist) else 0.0
                pm = kml.newpoint(name=f"{next_time.astimezone(display_tz).strftime('%H:%M')} - {km:.2f} km", coords=[(p.longitude, p.latitude, p.elevation)])
                # Horodatage utilisable par Google Earth / GPXSee (UTC)
                try:
                    pm.timestamp = simplekml.TimeStamp(when=iso)
                except Exception:
                    pm.description = f"Heure: {next_time.astimezone(display_tz).strftime('%Y-%m-%d %H:%M %Z')}\nDistance: {km:.2f} km"
                pm.description = f"Distance depuis départ: {km:.2f} km"
                pm.style.iconstyle.scale = 0.8
                last_added_time = next_time
                idx += 1
                next_time = next_time + timedelta(minutes=passage_interval_min)

            # Ne pas créer de placemark séparé pour l'arrivée: l'information est incluse dans le placemark combiné
        except Exception as e:
            logging.warning(f"Impossible de créer les points de passage: {e}")

    return kml


import cProfile, pstats
# import io # Non nécessaire pour cette approche

def profile_run_gui_process(*args_for_run_gui_process, **kwargs_for_run_gui_process):
    """Wrapper pour profiler la fonction run_gui_process. Enregistre le rapport dans un fichier."""
    log_output = kwargs_for_run_gui_process.get('log_func', print)
    
    # Le 14ème argument positionnel (index 13) est l'objet 'args' (argparse.Namespace)
    parsed_args_namespace = args_for_run_gui_process[13] 
    temp_dir = parsed_args_namespace.temp_dir
    
    log_output(f"DEBUG: Entering profile_run_gui_process wrapper. temp_dir set to: {temp_dir}") # Debug log
    
    profiler = cProfile.Profile()
    profiler.enable()
    
    try:
        result = run_gui_process(*args_for_run_gui_process, **kwargs_for_run_gui_process)
    except Exception as e:
        log_output(f"ERREUR LORS DE L'EXÉCUTION PROFILÉE: {e}")
        raise
    
    profiler.disable()
    
    report_filename = os.path.join(temp_dir, "profiling_full_click_to_open.txt")
    
    try:
        with open(report_filename, "w") as f:
            sortby = 'cumtime'
            ps = pstats.Stats(profiler, stream=f).sort_stats(sortby)
            f.write("--- RAPPORT DE PROFILAGE (run_gui_process) ---\
")
            ps.print_stats(50)
            f.write("--- FIN DU RAPPORT ---\
")
        log_output(f"--- RAPPORT DE PROFILAGE complet enregistré dans : {report_filename} ---")
    except Exception as e:
        log_output(f"ERREUR FATALE: Impossible d'enregistrer le rapport de profilage dans {report_filename} : {e}")
        # Optionally re-raise or handle more gracefully
        # raise # No, just log
    
    return result

def run_gui_process(file_path, date_str, time_str, dem_source, analysis_resolution, max_distance, shadow_mode, direction, open_gpx_after_calc, tz_finder, output_default, log_func, progress_callback, args, batch_size, passage_interval_min, solar_step_s, visualize_tiles, generate_shadow_map=False, num_workers=4, margin_meters=500, visualize_sun_rays=False, sun_ray_interval=20, analysis_type='ombre_soleil'):

    try:

        progress_callback(0, "Démarrage du calcul...")



        start_dt_naive = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M")

        


        veg_manager = None
        # Le LiDAR HD inclut déjà la végétation (MNH), donc on n'active le
        # gestionnaire de végétation WorldCover que pour les autres sources.
        if dem_source != 'ign_lidar_hd' and not args.no_vegetation_shadow:

            veg_manager = VegetationManager(args.vegetation_dir, not args.no_download_vegetation, progress_callback=progress_callback)



        hgt_manager = HGTDataManager('HGT', veg_manager, dem_source, 

                                     analysis_resolution=analysis_resolution,

                                     max_shadow_distance=max_distance,  # ✅

                                     log_func=log_func, progress_callback=progress_callback,

                                     solar_step_s=solar_step_s) # Ajout

        with open(file_path, 'r', encoding='utf-8') as f:
            gpx_raw = gpxpy.parse(f)
        
        points = gpx_raw.tracks[0].segments[0].points
        
        # Déterminer la timezone une fois pour toute la trace
        # Utilise le premier point de la trace pour déterminer la TZ.
        # Ceci suppose que la trace reste dans la même TZ, ce qui est généralement vrai pour une randonnée.
        local_tz_for_trace = pytz.utc # Valeur par défaut
        if points:
            try:
                tz_str = tz_finder.timezone_at(lng=points[0].longitude, lat=points[0].latitude)
                if tz_str:
                    local_tz_for_trace = pytz.timezone(tz_str)
                else:
                    log_func(f"Avertissement: Impossible de déterminer la timezone pour le point {points[0].latitude},{points[0].longitude}. Utilisation de UTC.")
            except Exception as e:
                log_func(f"Avertissement: Erreur lors de la détermination de la timezone. Utilisation de UTC. Erreur: {e}")

        if hgt_manager.source == 'ign_lidar_hd':
            hgt_manager.prepare_lidar_data(points, start_dt_naive)
        elif hgt_manager.source.startswith('ign_'):
             # Logique département pour BD ALTI...
            department = get_department_from_coords(points[0].latitude, points[0].longitude)
            if department:
                if hgt_manager.source == 'ign_bdalti_25m': hgt_manager.prepare_bdalti_data(department)
                elif hgt_manager.source == 'ign_rgealti_5m': hgt_manager.prepare_rgealti_data(department)
            else:
                log_func("⚠ Département non trouvé. Impossible de déterminer le département pour les sources IGN Alti.")
                return

        results, first_output_path = [], None
        directions_to_run = [("Sens Horaire", "CW"), ("Sens Anti-Horaire", "CCW")] if direction == 'both' else [(direction, direction.upper())]

        for label, code in directions_to_run:

            # Cloner gpx_raw pour chaque simulation
            with open(file_path, 'r', encoding='utf-8') as f_clone:
                gpx_clone = gpxpy.parse(f_clone)

            # Appel direct de simulatehike, le profiler est au-dessus
            # Appel direct de simulatehike, le profiler est au-dessus
            data, stats = simulatehike(gpx_clone, start_dt_naive, hgt_manager, local_tz_for_trace, code, shadow_mode, progress_callback, batch_size=batch_size, solar_step_s=solar_step_s)
            
            if not data: continue
            
            # AJOUT: Synchroniser hgt_manager.used_tiles pour les tuiles LiDAR après le ray-tracing
            if hgt_manager.source == 'ign_lidar_hd':
                tile_size = 1000
                for tx, ty in hgt_manager.lidar_manager.used_tiles:
                    x0 = tx * tile_size
                    y0 = ty * tile_size
                    hgt_manager.used_tiles.add(('lidar', (tx, ty), (x0, y0, x0 + tile_size, y0 + tile_size)))



            formatted_date = datetime.strptime(date_str, "%d/%m/%Y").strftime("%Y-%m-%d")
            formatted_time = time_str.replace(":", "h")
            
            # Étape 1: Créer l'objet KML de la trace (sans le sauvegarder)
            trace_kml_obj = create_kml_file(
                file_path, data, passage_interval_min=passage_interval_min, 
                local_tz=local_tz_for_trace, hgt_manager=hgt_manager, 
                visualize_tiles=visualize_tiles, visualize_sun_rays=visualize_sun_rays, 
                sun_ray_interval=sun_ray_interval,
                analysis_type=analysis_type
            )
            
            shadow_map_tif_path = None # Initialiser ici
            # Étape 2: Décider de la sauvegarde (fusion ou KML seul)
            if generate_shadow_map:
                dem_name = "".join(filter(str.isalnum, dem_source.replace(" ", "")))
                timestamp = datetime.now().strftime("%H%M%S")
                shadow_map_name_base = f"{os.path.splitext(os.path.basename(file_path))[0]}_{formatted_date}_{formatted_time}_{dem_name}_{shadow_mode}_{code}_shadow_map"
                
                # Chemin pour le GeoTIFF
                shadow_map_tif_path = os.path.join(SHADOW_GPX_DIR, f"{shadow_map_name_base}_{timestamp}.tif")
                log_func(f"DEBUG: Chemin GeoTIFF de la carte d'ombre: {shadow_map_tif_path}")

                
                # Générer le GeoTIFF
                compute_shadow_geotiff(
                    data, hgt_manager, shadow_mode, float(analysis_resolution), shadow_map_tif_path, progress_callback, num_workers=num_workers
                )

                # Trace colorée exportée AUSSI en KML autonome (= un "track" Locus).
                # Dans Locus Map / OsmAnd un track GPX/KML coexiste avec un
                # overlay-carte (le .mbtiles d'ombre) : ce sont deux couches
                # distinctes, le track ne compte PAS dans la limite d'un seul
                # overlay. On contourne ainsi le « je ne peux superposer qu'une
                # carte ». Sauvé AVANT la fusion KMZ ci-dessous, qui ajoute le
                # GroundOverlay à l'objet (sinon la trace standalone l'inclurait).
                # La trace reste vectorielle (nette, cliquable) — pas rasterisée.
                if trace_kml_obj is not None:
                    trace_kml_path = os.path.join(
                        SHADOW_GPX_DIR, f"{shadow_map_name_base}_trace.kml")
                    try:
                        trace_kml_obj.save(trace_kml_path)
                        log_func(f"DEBUG: Trace KML autonome (track Locus/OsmAnd): {trace_kml_path}")
                    except Exception as e_tr:
                        log_func(f"AVERTISSEMENT: export trace KML ignoré: {e_tr}")

                # Chemin pour le KMZ final
                kmz_output_path = os.path.join(SHADOW_GPX_DIR, f"{shadow_map_name_base}.kmz")

                # Générer le KMZ en fusionnant la trace et la carte d'ombre (qui inclut maintenant la grille si générée)
                final_output_path = geotiff_to_kml_groundoverlay(
                    shadow_map_tif_path,
                    kmz_output_path,
                    log_func,
                    existing_kml_obj=trace_kml_obj,
                    progress_callback=progress_callback
                )

                out_name = os.path.basename(final_output_path) if final_output_path else None
                if first_output_path is None: first_output_path = final_output_path

                # Export MBTiles overlay (calque transparent pour Locus Map /
                # OsmAnd / OruxMaps…). Non bloquant : un échec ne doit pas
                # compromettre le KMZ déjà produit.
                try:
                    mbtiles_output_path = os.path.join(
                        SHADOW_GPX_DIR, f"{shadow_map_name_base}.mbtiles")
                    geotiff_to_mbtiles_overlay(
                        shadow_map_tif_path, mbtiles_output_path, log_func,
                        progress_callback=progress_callback)
                except Exception as e_mbt:
                    log_func(f"AVERTISSEMENT: export MBTiles ignoré: {e_mbt}")


            else:
                # Sauvegarder le KML de la trace uniquement
                dem_name_raw = HGTDataManager.SOURCES.get(dem_source, {}).get('name', dem_source)
                dem_name = "".join(filter(str.isalnum, dem_name_raw.replace(" ", "")))
                out_name = f"{os.path.splitext(os.path.basename(file_path))[0]}_{formatted_date}_{formatted_time}_{dem_name}_{analysis_type}_{code}.kml"
                out_path = os.path.join(SHADOW_GPX_DIR, out_name)
                
                if trace_kml_obj:
                    trace_kml_obj.save(out_path)
                    log_func(f"   ✓ Fichier KML de trace créé : {out_name}")
                
                if first_output_path is None: first_output_path = out_path

            tot_dur = stats['totaldur']
            row = {
                "Fichier": out_name, "Départ": start_dt_naive, 
                "Dist Totale (km)": round(stats['totaldist']/1000, 2), "Durée Totale": str(timedelta(seconds=int(tot_dur))),
                "% Ensoleillé": round(stats['dursun']/tot_dur*100, 1) if tot_dur else 0,
                "% Ombre Relief": round(stats['durrelief']/tot_dur*100, 1) if tot_dur else 0,
                "% Ombre Végét.": round(stats['durveg']/tot_dur*100, 1) if tot_dur else 0,
            }
            results.append(row)

        if results:
            import pandas as pd  # import différé (charge ~0.75 s)
            df = pd.DataFrame(results)
            output_is_empty = not os.path.exists(output_default) or os.stat(output_default).st_size == 0
            df.to_csv(output_default, mode='a', header=output_is_empty, index=False, encoding='utf-8')
            
            if open_gpx_after_calc and first_output_path and os.path.exists(first_output_path):
                log_func(f"✓ Traitement terminé. Ouverture de {first_output_path}")
                os.startfile(first_output_path)
            else:
                log_func(f"✓ Traitement terminé. Sortie: {output_default}")

    except Exception as e:
        traceback.print_exc()
        log_func(f"ERREUR: {e}")
        raise

def show_form(args, tz_finder, output_default, help_text):
    """
    Interface graphique PyWebView (HTML/CSS/JS) — style identique à lidar2map.py.
    Communication bidirectionnelle Python <-> JS via l'objet Api exposé.
    """
    # Forcer le backend Qt AVANT d'importer webview (pywebview peut lire
    # PYWEBVIEW_GUI dès l'import). Windows+Linux : Qt au lieu de WinForms/.NET ;
    # macOS : laissé au runtime hook du .app. En frozen, le runtime hook le pose
    # déjà encore plus tôt — ceci fiabilise le mode `python gpxsolar.py` (dev).
    if platform.system() in ("Windows", "Linux"):
        os.environ.setdefault("PYWEBVIEW_GUI", "qt")
    import webview
    import json

    APP_VERSION = "v28.5"
    config = load_config()

    # Supprimer les warnings internes pywebview (AccessibilityObject, COM, etc.)
    for _name in ("pywebview", "pywebview.window", "pywebview.util",
                  "pywebview.platforms", "pywebview.js"):
        _lg = logging.getLogger(_name)
        _lg.setLevel(logging.CRITICAL)
        _lg.handlers.clear()
        _lg.propagate = False

    # File de logs partagée entre le logger Python et JS (via polling).
    log_queue = queue.Queue()

    class GuiQueueHandler(logging.Handler):
        """Handler logging → JSON-friendly items dans log_queue."""
        LEVEL_TAG = {
            logging.DEBUG: "dim", logging.INFO: "ok",
            logging.WARNING: "warn", logging.ERROR: "err",
            logging.CRITICAL: "err",
        }
        def emit(self, record):
            try:
                log_queue.put({
                    "line": self.format(record) + "\n",
                    "tag": self.LEVEL_TAG.get(record.levelno, "ok"),
                })
            except Exception:
                pass

    gui_handler = GuiQueueHandler()
    gui_handler.setLevel(logging.INFO)
    gui_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S'))
    # Éviter doublon si show_form rappelée
    for h in list(logging.getLogger().handlers):
        if isinstance(h, GuiQueueHandler):
            logging.getLogger().removeHandler(h)
    logging.getLogger().addHandler(gui_handler)

    # Données statiques pour le formulaire HTML
    DEM_SOURCES = [
        {"key": k,
         "label": f"{info['name']} ({info['resolution']} m)",
         "coverage": info['coverage']}
        for k, info in HGTDataManager.SOURCES.items()
    ]

    KML_LEGEND = [
        {'name': 'Soleil',       'color': '#FFFF00', 'description': 'Ensoleillé'},
        {'name': 'Ombre Relief', 'color': '#A0A0A0', 'description': 'Terrain/Montagne'},
        {'name': 'Ombre Vég.',   'color': '#009900', 'description': 'Végétation haute'},
        {'name': 'Ombre R+V',    'color': '#A52A15', 'description': 'Relief + Vég.'},
    ]
    SLOPE_LEGEND = [
        {'name': '0-5%',   'color': '#00FF00', 'description': 'Plat ou quasi-plat'},
        {'name': '5-10%',  'color': '#FFFF00', 'description': 'Pente faible'},
        {'name': '10-20%', 'color': '#FF8000', 'description': 'Pente moyenne'},
        {'name': '20-30%', 'color': '#FF0000', 'description': 'Pente forte'},
        {'name': '> 30%',  'color': '#8B0000', 'description': 'Pente très forte'},
    ]
    TILE_LEGEND = [
        {'name': 'Vert',  'color': '#00FF00', 'description': 'Tuile utilisée (ray-tracing)'},
        {'name': 'Bleu',  'color': '#0000FF', 'description': 'Tuile en RAM (cache LRU)'},
        {'name': 'Jaune', 'color': '#FFFF00', 'description': 'Tuile sur disque (pas en RAM)'},
    ]

    INIT_DEFAULTS = {
        'gpx_file': config.get('gpx_file', ''),
        'date': config.get('date', datetime.now().strftime("%d/%m/%Y")),
        'time': config.get('time', '09:00'),
        'dem_source': config.get('dem_source', args.dem_source),
        'analysis_resolution': str(config.get('analysis_resolution', args.analysis_resolution)),
        'shadow_mode': config.get('shadow_mode', 'both'),
        'direction': config.get('direction', 'both'),
        'open_gpx': bool(config.get('open_gpx', True)),
        'max_distance': str(config.get('max_distance', '1000')),
        'margin_meters': str(config.get('margin_meters', '500')),
        'batch_size': str(config.get('batch_size', '256')),
        'passage_interval_min': str(config.get('passage_interval_min',
                                               getattr(args, 'passage_interval_min', 0))),
        'solar_step_s': str(config.get('solar_step_s', '60')),
        'visualize_tiles': bool(config.get('visualize_tiles', False)),
        'generate_shadow_map': bool(config.get('generate_shadow_map', False)),
        'analysis_type': config.get('analysis_type', 'ombre_soleil'),
        'num_workers': str(config.get('num_workers', DEFAULT_NUM_WORKERS)),
        'visualize_sun_rays': bool(config.get('visualize_sun_rays', False)),
        'sun_ray_interval': str(config.get('sun_ray_interval', '20')),
    }

    class Api:
        def __init__(self):
            self._thread = None
            self._done = False
            self._retcode = None
            self._last_error = ""
            self._progress = {"value": 0, "text": "En attente..."}
            self.window = None

        def _get_window(self):
            if self.window is None and webview.windows:
                self.window = webview.windows[0]
            return self.window

        def get_historique(self):
            return load_history()

        def clear_historique(self):
            ok = clear_history()
            return {"ok": ok}

        def pick_gpx(self):
            w = self._get_window()
            if not w:
                return ""
            try:
                r = w.create_file_dialog(
                    webview.OPEN_DIALOG,
                    file_types=("GPX files (*.gpx)", "All files (*.*)"))
                return r[0] if r else ""
            except Exception as e:
                logging.warning(f"pick_gpx erreur: {e}")
                return ""

        def poll_log(self):
            items = []
            try:
                while True:
                    items.append(log_queue.get_nowait())
            except queue.Empty:
                pass
            return {
                "items": items,
                "done": self._done,
                "code": self._retcode,
                "progress": self._progress,
            }

        def get_last_error(self):
            return {"msg": self._last_error or "", "retcode": self._retcode or 0}

        def stop(self):
            # Le calcul est synchrone dans un thread daemon ; on ne peut pas
            # le tuer proprement sans coopération. On marque seulement l'arrêt
            # demandé pour libérer l'UI ; le thread se terminera naturellement.
            log_queue.put({"line": "\n⚠ Arrêt demandé (le calcul en cours continue jusqu'à la prochaine étape)\n",
                           "tag": "err"})
            self._done = True

        def launch(self, cfg):
            if self._thread and self._thread.is_alive():
                return {"error": "Un calcul est déjà en cours."}

            f = (cfg.get("gpx_file") or "").strip()
            d = (cfg.get("date") or "").strip()
            t = (cfg.get("time") or "").strip()
            if not (f and d and t):
                return {"error": "Veuillez spécifier un fichier GPX, une date et une heure."}
            if not os.path.exists(f):
                return {"error": f"Fichier GPX introuvable : {f}"}

            # Conversion + sauvegarde config (forme stable, comme l'ancien tk)
            try:
                new_config = {
                    'gpx_file': f, 'date': d, 'time': t,
                    'dem_source': cfg.get('dem_source', args.dem_source),
                    'analysis_resolution': str(cfg.get('analysis_resolution', args.analysis_resolution)),
                    'shadow_mode': cfg.get('shadow_mode', 'both'),
                    'direction': cfg.get('direction', 'both'),
                    'open_gpx': bool(cfg.get('open_gpx', True)),
                    'max_distance': str(cfg.get('max_distance', '1000')),
                    'margin_meters': str(cfg.get('margin_meters', '500')),
                    'batch_size': str(cfg.get('batch_size', '256')),
                    'passage_interval_min': str(cfg.get('passage_interval_min', 0)),
                    'solar_step_s': str(cfg.get('solar_step_s', '60')),
                    'visualize_tiles': bool(cfg.get('visualize_tiles', False)),
                    'generate_shadow_map': bool(cfg.get('generate_shadow_map', False)),
                    'analysis_type': cfg.get('analysis_type', 'ombre_soleil'),
                    'num_workers': str(cfg.get('num_workers', DEFAULT_NUM_WORKERS)),
                    'visualize_sun_rays': bool(cfg.get('visualize_sun_rays', False)),
                    'sun_ray_interval': str(cfg.get('sun_ray_interval', '20')),
                }
                save_config(new_config)
            except Exception as e:
                return {"error": f"Erreur paramètres : {e}"}

            self._done = False
            self._retcode = None
            self._last_error = ""
            self._progress = {"value": 0, "text": "Démarrage..."}
            self._t_launch = datetime.now()
            self._cfg_launch = new_config
            while not log_queue.empty():
                try:
                    log_queue.get_nowait()
                except queue.Empty:
                    break

            def log_func(msg):
                logging.info(msg)

            def progress_cb(value, text=""):
                try:
                    self._progress = {"value": float(value), "text": str(text)}
                except Exception:
                    pass

            def run():
                try:
                    log_func("----------------------------------------------------------------------")
                    log_func("Lancement du calcul...")
                    runner = profile_run_gui_process if args.profile else run_gui_process
                    if args.profile:
                        log_func("--- PROFILAGE ACTIVÉ (Traitement complet) ---")
                    runner(
                        f, d, t,
                        new_config['dem_source'],
                        new_config['analysis_resolution'],
                        float(new_config['max_distance']),
                        new_config['shadow_mode'],
                        new_config['direction'],
                        new_config['open_gpx'],
                        tz_finder, args.output,
                        log_func, progress_cb, args,
                        int(new_config['batch_size']),
                        int(new_config['passage_interval_min']),
                        int(new_config['solar_step_s']),
                        new_config['visualize_tiles'],
                        new_config['generate_shadow_map'],
                        int(new_config['num_workers']),
                        int(new_config['margin_meters']),
                        new_config['visualize_sun_rays'],
                        int(new_config['sun_ray_interval']),
                        new_config['analysis_type'],
                    )
                    self._retcode = 0
                    self._progress = {"value": 100, "text": "Terminé"}
                    log_func("✓ Calcul terminé avec succès.")
                    try:
                        duree = (datetime.now() - self._t_launch).total_seconds()
                        save_history(self._cfg_launch, duree, args.output)
                        log_func(f"  Historique sauvegardé ({HISTORY_FILE}).")
                    except Exception as he:
                        log_func(f"  Historique non sauvegardé : {he}")
                except Exception as e:
                    self._last_error = f"{type(e).__name__}: {e}"
                    self._retcode = 1
                    log_func(f"ERREUR FATALE: {e}")
                    traceback.print_exc()
                finally:
                    self._done = True
                    log_func("----------------------------------------------------------------------")

            self._thread = threading.Thread(target=run, daemon=True)
            self._thread.start()
            return {"ok": True}

    api = Api()

    # Données initiales injectées directement dans la page (rendu synchrone,
    # pas de dépendance à un appel async pywebview.api avant l'affichage).
    init_data = {
        "defaults": INIT_DEFAULTS,
        "dem_sources": DEM_SOURCES,
        "kml_legend": KML_LEGEND,
        "slope_legend": SLOPE_LEGEND,
        "tile_legend": TILE_LEGEND,
        "time_options": generate_time_options(),
        "help_text": help_text,
        "version": APP_VERSION,
        "historique": load_history(),
    }
    init_json = json.dumps(init_data, ensure_ascii=False).replace("</", "<\\/")
    HTML = _build_gpxsolar_html().replace(
        "/*__INIT_DATA__*/",
        f"window.INIT_DATA = {init_json};"
    )

    # Muselle l'avertissement bénin de fermeture QtWebEngine
    # ("Release of profile requested but WebEnginePage still not deleted") :
    # ordre de destruction géré par pywebview, sans conséquence. Un handler de
    # messages Qt filtre uniquement ce message ; tout le reste passe.
    # (PYWEBVIEW_GUI=qt est déjà posé avant `import webview`, plus haut.)
    if platform.system() in ("Windows", "Linux"):
        try:
            from PyQt6 import QtCore as _QtCore
            _QT_NOISE = ("WebEnginePage still not deleted",
                         "Release of profile requested")

            def _qt_msg_filter(_mode, _ctx, _msg):
                if any(_n in _msg for _n in _QT_NOISE):
                    return
                try:
                    sys.stderr.write(str(_msg) + "\n")
                except Exception:
                    pass

            _QtCore.qInstallMessageHandler(_qt_msg_filter)
        except Exception:
            pass

    # Taille initiale bornée à l'écran : avec le backend Qt + mise à l'échelle
    # DPI, une hauteur fixe (860) peut dépasser un écran de portable (ex. 1080p
    # à 150 % = 720 px logiques) -> fenêtre hors écran. On clampe sur la zone de
    # travail (hors barre des tâches) sous Windows. Fenêtre redimensionnable.
    _w, _h = 1180, 860
    try:
        if platform.system() == "Windows":
            import ctypes
            from ctypes import wintypes
            _r = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(_r), 0)  # SPI_GETWORKAREA
            _wa_w, _wa_h = _r.right - _r.left, _r.bottom - _r.top
            if _wa_h > 0:
                _h = max(560, min(_h, _wa_h - 48))
                _w = max(900, min(_w, _wa_w - 48))
    except Exception:
        pass

    win = webview.create_window(
        f"Simu Rando Solaire {APP_VERSION}",
        html=HTML,
        js_api=api,
        width=_w, height=_h,
        min_size=(900, 560),
        zoomable=True,
    )
    api.window = win
    # debug=True -> DevTools accessibles (clic droit -> Inspecter / F12). Via --debug.
    webview.start(debug=bool(getattr(args, "debug", False)))


def _build_gpxsolar_html():
    """Retourne le HTML/CSS/JS complet du formulaire pywebview.
    Le style suit la même palette que lidar2map.py (thème sombre)."""
    return r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Simu Rando Solaire</title>
<style>
:root{
  --bg:#12121f; --bg2:#1a1a30; --bg3:#1f1f3a; --bd:#2a2a50;
  --ac:#7070cc; --ac2:#e07060; --fg:#ececec; --dim:#a0a0d0;
  --green:#60cc80; --red:#cc6060; --yellow:#d8b85a;
  --fnt:"Segoe UI",system-ui,sans-serif;
  /* Couleurs par section */
  --c-file:    #4a6080;  /* bleu nuit */
  --c-dem:    #3a7070;   /* teal */
  --c-params: #7a9abf;   /* bleu acier */
  --c-modes:  #b07840;   /* ocre */
  --c-out:    #5b8a6e;   /* vert forêt */
  --c-legend: #7a6fa0;   /* violet doux */
}
*{box-sizing:border-box;margin:0;padding:0}
.hidden{display:none!important}
html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);font:13px var(--fnt)}
#main{padding:10px 24px 20px}
#form-inner{max-width:980px;width:100%;margin:0 auto;
  display:flex;flex-direction:column;gap:8px}
#btn-bar{max-width:980px;margin:8px auto;padding:0;display:flex;gap:8px;align-items:center}
/* Sections */
.section{background:var(--bg2);border:1px solid var(--bd);border-radius:6px;overflow:hidden}
.section-hd{padding:5px 10px;font-size:11px;font-weight:600;
  color:rgba(255,255,255,.9);text-transform:uppercase;letter-spacing:.6px;
  display:flex;align-items:center;gap:6px}
.section-body{padding:8px 10px;display:flex;flex-direction:column;gap:6px}
.sec-file   .section-hd{background:var(--c-file)}
.sec-dem    .section-hd{background:var(--c-dem)}
.sec-params .section-hd{background:var(--c-params)}
.sec-modes  .section-hd{background:var(--c-modes)}
.sec-out    .section-hd{background:var(--c-out)}
.sec-legend .section-hd{background:var(--c-legend)}
.sec-file   {background:rgba(74,96,128,.18);border-color:rgba(74,96,128,.4)}
.sec-dem    {background:rgba(58,112,112,.18);border-color:rgba(58,112,112,.4)}
.sec-params {background:rgba(122,154,191,.14);border-color:rgba(122,154,191,.35)}
.sec-modes  {background:rgba(176,120,64,.14);border-color:rgba(176,120,64,.35)}
.sec-out    {background:rgba(91,138,110,.14);border-color:rgba(91,138,110,.35)}
.sec-legend {background:rgba(122,111,160,.14);border-color:rgba(122,111,160,.35)}

.row{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.row label.lbl{font-size:11px;color:var(--dim);white-space:nowrap;min-width:170px}
.row label.lbl.short{min-width:100px}
.row label.lbl.tiny{min-width:60px}
input[type=text],input[type=number],input[type=date],input[type=time],select{
  background:var(--bg3);border:1px solid var(--bd);border-radius:4px;
  color:var(--fg);padding:3px 7px;font:12px var(--fnt);outline:none;
  flex:1;min-width:0}
input[type=text]:focus,input[type=number]:focus,input[type=date]:focus,
input[type=time]:focus,select:focus{border-color:var(--ac)}
input[type=number]{width:80px;flex:none}
.inp-short{width:64px!important;flex:none!important}
.inp-mid{width:110px!important;flex:none!important}
select{cursor:pointer}
.cb-group{display:flex;flex-wrap:wrap;gap:4px 14px;max-width:100%}
.cb-group label,.row label.cb{display:flex;align-items:center;gap:4px;font-size:12px;
  cursor:pointer;color:var(--fg);white-space:nowrap;min-width:auto}
/* Segmented control (radio styled as buttons) */
.seg{display:flex;border:1px solid var(--bd);border-radius:4px;overflow:hidden}
.seg input{display:none}
.seg label{padding:4px 12px;font-size:12px;cursor:pointer;
  background:var(--bg3);color:var(--dim);white-space:nowrap;min-width:auto}
.seg input:checked+label{background:var(--ac);color:#fff}
/* Boutons */
.btn{padding:4px 14px;border:none;border-radius:4px;cursor:pointer;
  font:12px var(--fnt);font-weight:600;letter-spacing:.3px}
.btn-run{background:var(--green);color:#0a0a14;padding:7px 24px;font-size:13px}
.btn-stop{background:var(--bg3);color:var(--ac2);border:1px solid var(--ac2)}
.btn-help{background:var(--bg3);color:var(--fg);border:1px solid var(--ac)}
.btn-sm{background:var(--bg3);color:var(--dim);border:1px solid var(--bd);
  padding:2px 8px;font-size:11px}
.btn:disabled{opacity:.4;cursor:default}
.hint{font-size:10px;color:var(--dim)}
/* Légende — cartes empilées + entrées en grille auto-fit (2 ou 3 colonnes) */
.legends-row{display:flex;flex-direction:column;gap:6px}
.legend-card{background:var(--bg3);border:1px solid var(--bd);
  border-radius:4px;padding:6px 8px;display:flex;flex-direction:column;gap:3px}
.legend-card h4{font-size:11px;color:var(--dim);text-transform:uppercase;
  letter-spacing:.4px;margin-bottom:2px}
.legend-items{display:grid;grid-template-columns:repeat(auto-fit, minmax(220px, 1fr));
  gap:2px 16px}
.legend-row{display:flex;align-items:center;gap:6px;font-size:11px;line-height:1.3}
.legend-swatch{width:13px;height:13px;border-radius:3px;border:1px solid var(--bd);flex-shrink:0}
/* Panneau de log fixe en bas — caché par défaut, ouvert via le bouton Logs */
#panneau-log{position:fixed;left:0;right:0;bottom:0;
  height:200px;min-height:60px;max-height:85vh;
  background:var(--bg2);border-top:2px solid var(--bd);
  display:flex;flex-direction:column;z-index:50}
#panneau-log.hidden{display:none}
#panneau-log.animating{transition:height .15s ease}
#main.log-visible{padding-bottom:210px}
#log-resize-handle{position:absolute;left:0;right:0;top:-3px;
  height:6px;cursor:ns-resize;z-index:51;background:transparent}
#log-resize-handle:hover,#log-resize-handle.dragging{
  background:var(--ac);opacity:.6}
body.log-resizing,body.log-resizing *{user-select:none!important;cursor:ns-resize!important}
/* Boutons toggle de la barre supérieure */
.btn-toggle{background:var(--bg3);border:1px solid var(--ac);color:var(--fg);
  padding:5px 14px;border-radius:4px;cursor:pointer;font-size:12px}
.btn-toggle.active{background:var(--ac);color:#fff}
/* Panneau historique latéral droit */
#panneau-hist{position:fixed;top:0;right:0;width:420px;height:100%;
  background:var(--bg2);border-left:1px solid var(--bd);overflow-y:auto;
  z-index:100;padding:12px;box-sizing:border-box}
#panneau-hist.hidden{display:none}
.hist-header{display:flex;justify-content:space-between;align-items:center;
  margin-bottom:10px;color:var(--ac);font-weight:600}
.hist-entry{border:1px solid var(--bd);border-radius:4px;padding:8px;
  margin-bottom:6px;cursor:pointer;font-size:12px;background:var(--bg3);
  transition:border-color .12s}
.hist-entry:hover{border-color:var(--ac)}
.hist-entry .hist-top{display:flex;justify-content:space-between;gap:6px}
.hist-entry .hist-top strong{color:var(--fg)}
.hist-entry .hist-meta{color:var(--dim);margin-top:3px;font-size:11px}
.hist-empty{color:var(--dim);font-size:12px;padding:8px 0}
#log-header{display:flex;align-items:center;gap:8px;
  padding:6px 12px;background:var(--bg3);
  border-bottom:1px solid var(--bd);user-select:none;font-size:12px}
#log-header strong{color:var(--ac);font-weight:600}
#log-header .log-actions{margin-left:auto;display:flex;gap:6px}
#log-header button{background:transparent;border:1px solid var(--bd);color:var(--dim);
  padding:2px 8px;border-radius:3px;cursor:pointer;font-size:11px}
#log-header button:hover{background:var(--bd);color:var(--fg)}
#log-progress{height:3px;background:var(--bg3);position:relative}
#log-progress-bar{position:absolute;top:0;left:0;height:100%;
  background:var(--ac);transition:width .2s;width:0%}
#log-progress-bar.err{background:var(--red)}
#log-progress-bar.ok{background:var(--green)}
#log-content{flex:1;overflow-y:auto;overflow-x:auto;
  padding:6px 12px;
  font-family:Consolas,"Courier New",monospace;font-size:11px;line-height:1.4;
  color:var(--fg);background:#0a0a14;
  white-space:pre-wrap;word-wrap:break-word}
#log-content .log-ok  {color:#c8c8d4}
#log-content .log-err {color:#ff7060;font-weight:500}
#log-content .log-warn{color:var(--yellow)}
#log-content .log-dim {color:#7575a0;font-style:italic}
/* Aide modale */
#help-modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;
  z-index:200;align-items:center;justify-content:center}
#help-modal.show{display:flex}
#help-modal .inner{max-width:640px;background:var(--bg2);border:1px solid var(--bd);
  border-radius:8px;padding:18px;color:var(--fg);white-space:pre-wrap;font-size:13px;
  max-height:80vh;overflow-y:auto}
#help-modal .inner h3{margin-bottom:10px;color:var(--ac)}
#help-modal .close{margin-top:12px;display:flex;justify-content:flex-end}
</style>
</head>
<body>

<div id="main">
<div id="btn-bar">
  <button class="btn btn-run" id="btn-run" onclick="lancer()">▶ Lancer le calcul</button>
  <button class="btn btn-stop" id="btn-stop" onclick="arreter()" disabled>■ Arrêter</button>
  <button class="btn btn-help" onclick="afficherAide()">? Aide</button>
  <button class="btn-toggle" id="btn-hist" onclick="toggleHistorique()" style="margin-left:12px">⏱ Historique</button>
  <button class="btn-toggle" id="btn-log"  onclick="toggleLogPanel()">📋 Logs</button>
  <span id="footer-status" style="font-size:11px;color:var(--dim);margin-left:8px"></span>
</div>

<div id="form-inner">

  <!-- Fichier / Date / Heure -->
  <div class="section sec-file">
   <div class="section-hd">Fichier GPX &amp; date</div>
   <div class="section-body">
    <div class="row">
      <label class="lbl">Fichier GPX</label>
      <input type="text" id="f-gpx" placeholder="…/parcours.gpx" style="flex:1">
      <button class="btn btn-sm" onclick="pickGpx()">…</button>
    </div>
    <div class="row">
      <label class="lbl">Date de départ</label>
      <input type="date" id="f-date" class="inp-mid">
      <label class="lbl tiny" style="margin-left:14px">Heure</label>
      <select id="f-time" class="inp-mid"></select>
    </div>
   </div>
  </div>

  <!-- DEM source -->
  <div class="section sec-dem">
   <div class="section-hd">Modèle de données d'altitude (DEM)</div>
   <div class="section-body">
    <div class="row" id="dem-radios"></div>
   </div>
  </div>

  <!-- Options de simulation (numériques) -->
  <div class="section sec-params">
   <div class="section-hd">Options de simulation</div>
   <div class="section-body">
    <div class="row">
      <label class="lbl">Résolution analyse (m)</label>
      <input type="number" id="f-analysis-resolution" step="0.5" min="0.5" class="inp-short">
      <label class="lbl short" style="margin-left:14px">Distance max. ombre (m)</label>
      <input type="number" id="f-max-distance" step="100" min="100" class="inp-short">
      <label class="lbl short" style="margin-left:14px">Marge bbox (m)</label>
      <input type="number" id="f-margin-meters" step="100" min="0" class="inp-short">
    </div>
    <div class="row">
      <label class="lbl">Taille des lots (batch)</label>
      <input type="number" id="f-batch-size" step="1" min="1" class="inp-short">
      <label class="lbl short" style="margin-left:14px">Workers (parallèle)</label>
      <input type="number" id="f-num-workers" step="1" min="1" max="32" class="inp-short">
      <label class="lbl short" style="margin-left:14px">Intervalle pts (min)</label>
      <input type="number" id="f-passage-interval" step="1" min="0" class="inp-short">
      <span class="hint">0 = aucun</span>
    </div>
    <div class="row">
      <label class="lbl">Pas solaire (s) — cache</label>
      <select id="f-solar-step" class="inp-short">
        <option value="10">10</option>
        <option value="30">30</option>
        <option value="60">60</option>
        <option value="120">120</option>
        <option value="300">300</option>
      </select>
      <span class="hint">Ex : 60 (rapide), 10 (précis)</span>
    </div>
   </div>
  </div>

  <!-- Modes -->
  <div class="section sec-modes">
   <div class="section-hd">Modes</div>
   <div class="section-body">
    <div class="row">
      <label class="lbl">Calcul d'ombre</label>
      <div class="seg">
        <input type="radio" name="shadow" id="sh-relief" value="relief"><label for="sh-relief">Relief seul</label>
        <input type="radio" name="shadow" id="sh-veg"    value="vegetation"><label for="sh-veg">Végétation seule</label>
        <input type="radio" name="shadow" id="sh-both"   value="both"><label for="sh-both">Les deux</label>
      </div>
      <label class="lbl short" style="margin-left:14px">Sens du parcours</label>
      <div class="seg">
        <input type="radio" name="direction" id="di-cw"   value="CW"><label for="di-cw">Horaire</label>
        <input type="radio" name="direction" id="di-ccw"  value="CCW"><label for="di-ccw">Anti-horaire</label>
        <input type="radio" name="direction" id="di-both" value="both"><label for="di-both">Les deux</label>
      </div>
    </div>
    <div class="row">
      <label class="lbl">Type d'analyse KML</label>
      <div class="seg">
        <input type="radio" name="analysis" id="an-ombre" value="ombre_soleil"><label for="an-ombre">Ombre / Soleil</label>
        <input type="radio" name="analysis" id="an-pente" value="pente"><label for="an-pente">Pente (depuis MNT)</label>
      </div>
    </div>
   </div>
  </div>

  <!-- Sorties / options annexes -->
  <div class="section sec-out">
   <div class="section-hd">Sorties &amp; visualisations</div>
   <div class="section-body">
    <div class="cb-group">
      <label><input type="checkbox" id="f-open-gpx"> Ouvrir le KML résultat après calcul</label>
      <label><input type="checkbox" id="f-visualize-tiles"> Visualiser les tuiles (KML)</label>
      <label><input type="checkbox" id="f-generate-shadow-map"> Générer carte d'ombre (GeoTIFF)</label>
    </div>
    <div class="row">
      <label class="cb"><input type="checkbox" id="f-visualize-sun-rays"> Visualiser rayons solaires (KML)</label>
      <label class="lbl tiny" style="margin-left:14px">Intervalle rayons</label>
      <input type="number" id="f-sun-ray-interval" step="1" min="1" class="inp-short">
    </div>
   </div>
  </div>

  <!-- Légendes -->
  <div class="section sec-legend">
   <div class="section-hd">Légendes</div>
   <div class="section-body">
    <div class="legends-row">
      <div class="legend-card" id="legend-kml">
        <h4>Couleurs KML — Ombre/Soleil</h4>
        <div class="legend-items"></div>
      </div>
      <div class="legend-card hidden" id="legend-slope">
        <h4>Couleurs KML — Pentes (abs)</h4>
        <div class="legend-items"></div>
      </div>
      <div class="legend-card hidden" id="legend-tile">
        <h4>Tuiles (visualisation)</h4>
        <div class="legend-items"></div>
      </div>
    </div>
   </div>
  </div>

</div>
</div>

<!-- Panneau historique (latéral droit, caché par défaut) -->
<div id="panneau-hist" class="hidden">
  <div class="hist-header">
    <strong>⏱ Historique des calculs</strong>
    <div style="display:flex;gap:6px;align-items:center">
      <button class="btn-sm" onclick="viderHistorique()"
              style="background:transparent;border:1px solid var(--red);color:var(--red)">🗑 Vider</button>
      <button class="btn-sm" onclick="toggleHistorique()"
              style="background:transparent;border:none;font-size:16px">✕</button>
    </div>
  </div>
  <div id="hist-list"></div>
</div>

<!-- Panneau de log (caché par défaut) -->
<div id="panneau-log" class="hidden">
  <div id="log-resize-handle"></div>
  <div id="log-header">
    <strong>📋 Logs</strong>
    <span id="log-status" style="color:var(--dim)">Prêt</span>
    <div class="log-actions">
      <button onclick="viderLog()">🗑 Vider</button>
      <button onclick="copierLog()">⎘ Copier</button>
      <button onclick="toggleLogPanel()" title="Masquer (ré-affichable via le bouton Logs)">✕</button>
    </div>
  </div>
  <div id="log-progress"><div id="log-progress-bar"></div></div>
  <div id="log-content"></div>
</div>

<!-- Modale d'aide -->
<div id="help-modal" onclick="if(event.target===this)fermerAide()">
  <div class="inner">
    <h3>Aide — Simu Rando Solaire</h3>
    <div id="help-body"></div>
    <div class="close"><button class="btn btn-sm" onclick="fermerAide()">Fermer</button></div>
  </div>
</div>

<script>
/*__INIT_DATA__*/
let _polling = null;
let _initialized = false;

// ── Init ─────────────────────────────────────────────────────────────────────
// Les données sont déjà injectées dans window.INIT_DATA (rendu synchrone).
// On attend juste DOMContentLoaded puis on rend tout immédiatement.
document.addEventListener('DOMContentLoaded', () => {
  installerResize();
  try {
    initFromData(window.INIT_DATA || {});
  } catch(e) {
    document.getElementById('footer-status').textContent = 'Erreur init : ' + e;
    console.error('init error:', e);
  }
  // Démarrer le polling pour les logs/progress dès qu'un calcul tournera.
  // On retente l'init du polling tant que pywebview.api n'est pas prêt.
  startPollingWhenReady();
});

function initFromData(d) {
  if (_initialized) return;
  _initialized = true;
  buildTimeOptions(d.time_options || []);
  buildDemSources(d.dem_sources || []);
  buildLegend('legend-kml',   d.kml_legend || []);
  buildLegend('legend-slope', d.slope_legend || []);
  buildLegend('legend-tile',  d.tile_legend || []);
  document.getElementById('help-body').textContent = d.help_text || '';
  loadDefaults(d.defaults || {});
  buildHistorique(d.historique || []);
  ajouterLigneLog('Interface graphique initialisée.\n', 'dim');
  ajouterLigneLog('Prêt à lancer une simulation.\n', 'dim');
}

function startPollingWhenReady(tries=0) {
  if (window.pywebview && window.pywebview.api &&
      typeof window.pywebview.api.poll_log === 'function') {
    if (!_polling) _polling = setInterval(pollOnce, 250);
    return;
  }
  if (tries < 400) setTimeout(() => startPollingWhenReady(tries+1), 50);
}

function buildTimeOptions(opts) {
  const sel = document.getElementById('f-time');
  sel.innerHTML = '';
  opts.forEach(t => {
    const o = document.createElement('option');
    o.value = t; o.textContent = t;
    sel.appendChild(o);
  });
}

function buildDemSources(sources) {
  const c = document.getElementById('dem-radios');
  c.innerHTML = '';
  const seg = document.createElement('div');
  seg.className = 'seg';
  sources.forEach(s => {
    const id = 'dem-' + s.key;
    const inp = document.createElement('input');
    inp.type = 'radio'; inp.name = 'dem'; inp.id = id; inp.value = s.key;
    const lab = document.createElement('label');
    lab.setAttribute('for', id);
    lab.textContent = s.label;
    lab.title = s.coverage || '';
    seg.appendChild(inp); seg.appendChild(lab);
  });
  c.appendChild(seg);
}

function buildLegend(containerId, entries) {
  const root = document.getElementById(containerId);
  if (!root) return;
  const items = root.querySelector('.legend-items');
  if (!items) return;
  items.innerHTML = '';
  entries.forEach(e => {
    const row = document.createElement('div');
    row.className = 'legend-row';
    const sw = document.createElement('span');
    sw.className = 'legend-swatch';
    sw.style.background = e.color;
    const txt = document.createElement('span');
    txt.textContent = `${e.name} — ${e.description}`;
    row.appendChild(sw); row.appendChild(txt);
    items.appendChild(row);
  });
}

// ── Chargement valeurs par défaut ────────────────────────────────────────────
function loadDefaults(d) {
  document.getElementById('f-gpx').value = d.gpx_file || '';
  document.getElementById('f-date').value = ddmmyyyy_to_iso(d.date);
  if (d.time && document.getElementById('f-time').querySelector(`option[value="${d.time}"]`)) {
    document.getElementById('f-time').value = d.time;
  }
  setRadio('dem', d.dem_source);
  document.getElementById('f-analysis-resolution').value = d.analysis_resolution || '5';
  document.getElementById('f-max-distance').value       = d.max_distance || '1000';
  document.getElementById('f-margin-meters').value      = d.margin_meters || '500';
  document.getElementById('f-batch-size').value         = d.batch_size || '256';
  document.getElementById('f-num-workers').value        = d.num_workers || '4';
  document.getElementById('f-passage-interval').value   = d.passage_interval_min || '0';
  if (d.solar_step_s) document.getElementById('f-solar-step').value = d.solar_step_s;
  setRadio('shadow',    d.shadow_mode    || 'both');
  setRadio('direction', d.direction      || 'both');
  setRadio('analysis',  d.analysis_type  || 'ombre_soleil');
  document.getElementById('f-open-gpx').checked            = !!d.open_gpx;
  document.getElementById('f-visualize-tiles').checked     = !!d.visualize_tiles;
  document.getElementById('f-generate-shadow-map').checked = !!d.generate_shadow_map;
  document.getElementById('f-visualize-sun-rays').checked  = !!d.visualize_sun_rays;
  document.getElementById('f-sun-ray-interval').value      = d.sun_ray_interval || '20';
  appliquerLegendes();
  attacherListeners();
}

function attacherListeners() {
  document.querySelectorAll('input[name=analysis]').forEach(r =>
    r.addEventListener('change', appliquerLegendes));
  document.getElementById('f-visualize-tiles')
    .addEventListener('change', appliquerLegendes);
}

function appliquerLegendes() {
  const at = (document.querySelector('input[name=analysis]:checked') || {}).value || 'ombre_soleil';
  document.getElementById('legend-kml').classList.toggle('hidden',  at !== 'ombre_soleil');
  document.getElementById('legend-slope').classList.toggle('hidden', at !== 'pente');
  const vt = document.getElementById('f-visualize-tiles').checked;
  document.getElementById('legend-tile').classList.toggle('hidden', !vt);
}

function setRadio(name, value) {
  const el = document.querySelector(`input[name=${name}][value="${value}"]`);
  if (el) el.checked = true;
}
function getRadio(name) {
  const el = document.querySelector(`input[name=${name}]:checked`);
  return el ? el.value : '';
}

// ── Conversion date DD/MM/YYYY <-> YYYY-MM-DD ────────────────────────────────
function ddmmyyyy_to_iso(s) {
  if (!s) return '';
  const m = String(s).match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (m) return `${m[3]}-${m[2]}-${m[1]}`;
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  return '';
}
function iso_to_ddmmyyyy(s) {
  if (!s) return '';
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : s;
}

// ── Dialogs ──────────────────────────────────────────────────────────────────
async function pickGpx() {
  try {
    const p = await pywebview.api.pick_gpx();
    if (p) document.getElementById('f-gpx').value = p;
  } catch(e) { console.error(e); }
}

// ── Aide ─────────────────────────────────────────────────────────────────────
function afficherAide() {
  document.getElementById('help-modal').classList.add('show');
}
function fermerAide() {
  document.getElementById('help-modal').classList.remove('show');
}

// ── Panneau Logs (show/hide) ─────────────────────────────────────────────────
function toggleLogPanel() {
  const p = document.getElementById('panneau-log');
  const m = document.getElementById('main');
  const b = document.getElementById('btn-log');
  if (!p) return;
  p.classList.add('animating');
  setTimeout(() => p.classList.remove('animating'), 200);
  p.classList.toggle('hidden');
  const visible = !p.classList.contains('hidden');
  if (m) m.classList.toggle('log-visible', visible);
  if (b) b.classList.toggle('active', visible);
}

// ── Historique (panneau latéral) ─────────────────────────────────────────────
let _historique = [];

function toggleHistorique() {
  const p = document.getElementById('panneau-hist');
  const b = document.getElementById('btn-hist');
  if (!p) return;
  p.classList.toggle('hidden');
  if (b) b.classList.toggle('active', !p.classList.contains('hidden'));
}

function buildHistorique(hist) {
  _historique = hist || [];
  const list = document.getElementById('hist-list');
  if (!list) return;
  if (!_historique.length) {
    list.innerHTML = '<div class="hist-empty">Aucun calcul enregistré.</div>';
    return;
  }
  list.innerHTML = _historique.map((e, i) => {
    const gpx  = e.gpx_name || '(sans nom)';
    const dem  = e.dem_source || '';
    const day  = e.date_rando ? `${e.date_rando} ${e.time_rando || ''}`.trim() : '';
    const typ  = e.analysis_type === 'pente' ? 'Pente' : 'Ombre/Soleil';
    return `<div class="hist-entry" onclick="rappelHistorique(${i})">
      <div class="hist-top">
        <strong>${escapeHtml(gpx)}</strong>
        <span style="color:var(--dim);font-size:11px">${escapeHtml(e.date || '')}</span>
      </div>
      <div class="hist-meta">${escapeHtml(typ)} · ${escapeHtml(dem)} · ${escapeHtml(day)} · ${escapeHtml(e.duree || '')}</div>
    </div>`;
  }).join('');
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

function rappelHistorique(i) {
  const e = _historique[i];
  if (!e || !e.params) return;
  loadDefaults(e.params);
  toggleHistorique();
  document.getElementById('footer-status').textContent =
    `Paramètres rappelés depuis l'historique (${e.date || ''})`;
}

async function viderHistorique() {
  if (!_historique.length) { alert('L\'historique est déjà vide.'); return; }
  if (!confirm(`Supprimer ${_historique.length} entrée(s) de l'historique ?`)) return;
  try {
    const r = await pywebview.api.clear_historique();
    if (r && r.ok) {
      buildHistorique([]);
      document.getElementById('footer-status').textContent = '✓ Historique vidé';
    } else {
      alert('Erreur lors de la suppression.');
    }
  } catch(e) { alert('Erreur : ' + e); }
}

async function rafraichirHistorique() {
  try {
    const hist = await pywebview.api.get_historique();
    if (Array.isArray(hist)) buildHistorique(hist);
  } catch(e) { /* silencieux */ }
}

// ── Log panel ────────────────────────────────────────────────────────────────
function ajouterLigneLog(line, tag) {
  const c = document.getElementById('log-content');
  const span = document.createElement('span');
  span.className = 'log-' + (tag || 'ok');
  span.textContent = line;
  c.appendChild(span);
  c.scrollTop = c.scrollHeight;
}
function viderLog() {
  document.getElementById('log-content').innerHTML = '';
}
function copierLog() {
  const txt = document.getElementById('log-content').innerText;
  // navigator.clipboard.writeText ne fonctionne pas dans WebView2/pywebview en file://
  const ta = document.createElement('textarea');
  ta.value = txt; document.body.appendChild(ta);
  ta.select(); try { document.execCommand('copy'); } catch(e) {}
  document.body.removeChild(ta);
}
function setLogProgress(pct, cls) {
  const bar = document.getElementById('log-progress-bar');
  if (!bar) return;
  bar.style.width = (pct >= 0 && pct <= 100 ? pct : 0) + '%';
  bar.className = '';
  if (cls) bar.classList.add(cls);
}

// ── Resize handle du panneau de log ──────────────────────────────────────────
function installerResize() {
  const handle = document.getElementById('log-resize-handle');
  const panel  = document.getElementById('panneau-log');
  if (!handle || !panel) return;
  let dragging = false; let startY = 0; let startH = 0;
  handle.addEventListener('mousedown', e => {
    dragging = true; startY = e.clientY;
    startH = panel.getBoundingClientRect().height;
    handle.classList.add('dragging');
    document.body.classList.add('log-resizing');
    e.preventDefault();
  });
  window.addEventListener('mousemove', e => {
    if (!dragging) return;
    const dy = startY - e.clientY;
    const newH = Math.max(60, Math.min(window.innerHeight * 0.85, startH + dy));
    panel.style.height = newH + 'px';
    // ajuster le padding-bottom du main pour ne pas masquer la fin
    document.getElementById('main').style.paddingBottom = (newH + 20) + 'px';
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('dragging');
    document.body.classList.remove('log-resizing');
  });
}

// ── Polling logs/progress ────────────────────────────────────────────────────
async function pollOnce() {
  try {
    const r = await pywebview.api.poll_log();
    if (r && r.items) {
      r.items.forEach(it => {
        if (it.line !== undefined) ajouterLigneLog(it.line, it.tag || 'ok');
      });
    }
    if (r && r.progress) {
      const p = r.progress;
      const v = Math.max(0, Math.min(100, p.value || 0));
      setLogProgress(v, '');
      const fs = document.getElementById('footer-status');
      if (fs && _running) fs.textContent = `${Math.round(v)}% — ${(p.text||'').substring(0,80)}`;
    }
    if (_running && r && r.done) {
      _running = false;
      const code = r.code;
      document.getElementById('log-status').textContent =
        code === 0 ? '✓ Terminé' : `✗ Erreur (code ${code})`;
      document.getElementById('footer-status').textContent =
        code === 0 ? '✓ Terminé' : `✗ Erreur (code ${code})`;
      setLogProgress(100, code === 0 ? 'ok' : 'err');
      if (code === 0) {
        rafraichirHistorique();
      } else {
        // Forcer l'affichage du log en cas d'erreur
        const panLog = document.getElementById('panneau-log');
        if (panLog && panLog.classList.contains('hidden')) toggleLogPanel();
        try {
          const err = await pywebview.api.get_last_error();
          if (err && err.msg) {
            alert(`Le traitement a échoué (code ${err.retcode}).\n\n${err.msg}\n\n` +
                  `Voir le panneau de log pour les détails.`);
          }
        } catch(e) { /* silencieux */ }
      }
      btnReset();
    }
  } catch(e) { /* polling peut échouer brièvement à l'init */ }
}

// ── Lancement ────────────────────────────────────────────────────────────────
let _running = false;

function getConfig() {
  return {
    gpx_file:             document.getElementById('f-gpx').value.trim(),
    date:                 iso_to_ddmmyyyy(document.getElementById('f-date').value),
    time:                 document.getElementById('f-time').value,
    dem_source:           getRadio('dem'),
    analysis_resolution:  document.getElementById('f-analysis-resolution').value,
    max_distance:         document.getElementById('f-max-distance').value,
    margin_meters:        document.getElementById('f-margin-meters').value,
    batch_size:           document.getElementById('f-batch-size').value,
    num_workers:          document.getElementById('f-num-workers').value,
    passage_interval_min: document.getElementById('f-passage-interval').value,
    solar_step_s:         document.getElementById('f-solar-step').value,
    shadow_mode:          getRadio('shadow'),
    direction:            getRadio('direction'),
    analysis_type:        getRadio('analysis'),
    open_gpx:             document.getElementById('f-open-gpx').checked,
    visualize_tiles:      document.getElementById('f-visualize-tiles').checked,
    generate_shadow_map:  document.getElementById('f-generate-shadow-map').checked,
    visualize_sun_rays:   document.getElementById('f-visualize-sun-rays').checked,
    sun_ray_interval:     document.getElementById('f-sun-ray-interval').value,
  };
}

function setFormLocked(locked) {
  document.querySelectorAll('#form-inner input,#form-inner select,#form-inner button')
    .forEach(el => el.disabled = locked);
}

async function lancer() {
  const cfg = getConfig();
  if (!cfg.gpx_file) { alert('Veuillez sélectionner un fichier GPX.'); return; }
  if (!cfg.date)     { alert('Veuillez sélectionner une date.'); return; }
  if (!cfg.time)     { alert('Veuillez sélectionner une heure.'); return; }
  if (!cfg.dem_source) { alert('Veuillez sélectionner un modèle d\'altitude.'); return; }

  document.getElementById('btn-run').disabled  = true;
  document.getElementById('btn-stop').disabled = false;
  setFormLocked(true);
  _running = true;
  document.getElementById('log-status').textContent = 'En cours…';
  document.getElementById('footer-status').textContent = 'En cours…';
  setLogProgress(0, '');
  // Ouvrir automatiquement le panneau de log
  const panLog = document.getElementById('panneau-log');
  if (panLog && panLog.classList.contains('hidden')) toggleLogPanel();

  try {
    const res = await pywebview.api.launch(cfg);
    if (res && res.error) {
      alert(res.error);
      btnReset();
      _running = false;
    }
  } catch(e) {
    alert('Erreur de lancement : ' + e);
    btnReset(); _running = false;
  }
}

async function arreter() {
  try { await pywebview.api.stop(); } catch(e) {}
  _running = false;
  document.getElementById('footer-status').textContent = '⚠ Arrêté';
  btnReset();
}

function btnReset() {
  document.getElementById('btn-run').disabled  = false;
  document.getElementById('btn-stop').disabled = true;
  setFormLocked(false);
}
</script>
</body>
</html>"""


def run_headless(args, tz_finder):
    """Calcul en ligne de commande (sans GUI), même méthode que lidar2map :
    déclenché dès qu'un argument est passé. Requiert --gpx, --date, --time.
    Appelle directement run_gui_process (le moteur de calcul, indépendant de
    pywebview) puis retourne un code de sortie (0 = succès)."""
    if not args.gpx:
        logging.error("Mode ligne de commande : --gpx est requis (avec --date "
                      "JJ/MM/AAAA et --time HH:MM). Lancez sans argument pour "
                      "ouvrir l'interface graphique.")
        return 2
    if not (args.date and args.time):
        logging.error("--gpx nécessite aussi --date (JJ/MM/AAAA) et --time (HH:MM).")
        return 2
    if not os.path.exists(args.gpx):
        logging.error(f"Fichier GPX introuvable : {args.gpx}")
        return 2

    def log_func(msg):
        logging.info(msg)

    _last = {"pct": -1}
    def progress_cb(value, text=""):
        try:
            pct = int(float(value))
        except Exception:
            return
        if pct != _last["pct"]:
            _last["pct"] = pct
            logging.info(f"[{pct:3d}%] {text}".rstrip())

    runner = profile_run_gui_process if args.profile else run_gui_process
    t0 = datetime.now()
    try:
        # Même ordre d'arguments que l'appel depuis la GUI (Api.launch -> run()).
        runner(
            args.gpx, args.date, args.time,
            args.dem_source,
            str(args.analysis_resolution),
            float(args.max_shadow_distance),
            args.shadow_mode,
            args.direction,
            bool(args.open),
            tz_finder, args.output,
            log_func, progress_cb, args,
            int(args.batch_size),
            int(args.passage_interval_min),
            int(args.solar_step_s),
            bool(args.visualize_tiles),
            bool(args.generate_shadow_map),
            int(args.num_workers),
            int(args.margin_meters),
            bool(args.visualize_sun_rays),
            int(args.sun_ray_interval),
            args.analysis_type,
        )
    except Exception as e:
        logging.error(f"ERREUR FATALE: {e}")
        return 1
    logging.info(f"✓ Calcul termine en {(datetime.now() - t0).total_seconds():.0f}s. "
                 f"Sorties dans {SHADOW_GPX_DIR} ; CSV : {args.output}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="GPX Solar Shadow Analyzer (LiDAR integrated)", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--output', default='analyse_solaire.csv', help='Fichier CSV de sortie')
    parser.add_argument('--hgt-dir', default='HGT', help='Répertoire des fichiers HGT (pour SRTM/Copernicus)')
    parser.add_argument('--dem-source', default='srtm1', choices=list(HGTDataManager.SOURCES.keys()), help='Source DEM par défaut')
    parser.add_argument('--interpolation', default='bilinear', choices=['nearest', 'bilinear', 'cubic'], help='Méthode d\'interpolation')
    parser.add_argument('--analysis-resolution', type=float, default=5.0, help='Résolution d\'analyse pour le calcul d\'ombre (en mètres)') # Nouvelle ligne
    parser.add_argument('--vegetation-dir', default='WorldCover', help='Répertoire WorldCover')
    parser.add_argument('--passage-interval-min', type=int, default=0, help='Intervalle en minutes pour créer des points de passage dans le KML (0=aucun)')
    parser.add_argument('--no-download-vegetation', action='store_true', help='Désactiver téléchargement auto végétation')
    parser.add_argument('--no-vegetation-shadow', action='store_true', help='Désactiver complètement la détection d\'ombre de la végétation')
    parser.add_argument('--max-shadow-distance', type=float, default=1000.0,
                       help='Distance maximale de détection d\'ombre (en mètres, défaut: 1000)')
    parser.add_argument('--profile', action='store_true', help='Activer le profilage de performance.')
    parser.add_argument('--temp-dir', type=str, default=tempfile.gettempdir(), help='Répertoire temporaire pour les rapports de profilage.')
    parser.add_argument('--debug', action='store_true', help='Ouvre les DevTools pywebview (clic droit -> Inspecter / F12) pour voir la console JS et le bridge.')

    # --- Mode ligne de commande (headless), même méthode que lidar2map ---------
    # Sans argument -> GUI. Dès qu'un argument est passé, on bascule en mode CLI
    # (calcul direct sans fenêtre). Le calcul a besoin de --gpx + --date + --time.
    grp_cli = parser.add_argument_group(
        'Mode ligne de commande (headless)',
        "Passer --gpx (avec --date et --time) lance le calcul sans interface "
        "graphique. Pratique pour scripter / serveur / reproduire un rendu.")
    grp_cli.add_argument('--gpx', metavar='CHEMIN', default=None,
                         help='Fichier GPX à analyser. Sa présence déclenche le mode CLI.')
    grp_cli.add_argument('--date', metavar='JJ/MM/AAAA', default=None,
                         help='Date de départ (ex: 21/06/2024).')
    grp_cli.add_argument('--time', metavar='HH:MM', default=None,
                         help='Heure de départ (ex: 09:00).')
    grp_cli.add_argument('--shadow-mode', choices=['relief', 'vegetation', 'both'],
                         default='both', help='Type d\'ombre simulé (défaut: both).')
    grp_cli.add_argument('--direction', choices=['CW', 'CCW', 'both'], default='both',
                         help='Sens de parcours simulé (défaut: both).')
    grp_cli.add_argument('--analysis-type', choices=['ombre_soleil', 'pente'],
                         default='ombre_soleil', help='Type d\'analyse (défaut: ombre_soleil).')
    grp_cli.add_argument('--visualize-tiles', action='store_true',
                         help='Dessiner les tuiles/dalles DEM utilisées dans le KML.')
    grp_cli.add_argument('--generate-shadow-map', action='store_true',
                         help='Générer la carte d\'ombre raster (fond de carte) en KMZ.')
    grp_cli.add_argument('--visualize-sun-rays', action='store_true',
                         help='Dessiner les rayons solaires simulés dans le KML.')
    grp_cli.add_argument('--sun-ray-interval', type=int, default=20,
                         help='Intervalle entre rayons solaires (défaut: 20).')
    grp_cli.add_argument('--batch-size', type=int, default=256,
                         help='Taille de lot du calcul (défaut: 256).')
    grp_cli.add_argument('--solar-step-s', type=int, default=60,
                         help='Pas temporel du soleil en secondes (défaut: 60).')
    grp_cli.add_argument('--num-workers', type=int, default=DEFAULT_NUM_WORKERS,
                         help=f'Workers parallèles pour la carte d\'ombre (défaut: {DEFAULT_NUM_WORKERS} = nb de cœurs).')
    grp_cli.add_argument('--margin-meters', type=int, default=500,
                         help='Marge autour de la trace en mètres (défaut: 500).')
    grp_cli.add_argument('--open', action='store_true',
                         help='Ouvrir le résultat à la fin (Windows uniquement).')

    help_text = "Ce script analyse l\'ensoleillement d\'une trace GPX.\n\n"
    help_text += "1. Choisissez un fichier GPX.\n"
    help_text += "2. Sélectionnez une date et une heure de départ.\n"
    help_text += "3. Choisissez un modèle de données d\'altitude:\n"
    help_text += "   - SRTM/Copernicus: Mondiaux, basse résolution.\n"
    help_text += "   - IGN ALTI: France, moyenne résolution.\n"
    help_text += "   - IGN LiDAR HD: France, très haute résolution.\n"
    help_text += "4. Choisissez les options de simulation (type d\'ombre et sens).\n"
    help_text += "   - En mode LiDAR, les options contrôlent les couches (MNT, MNS, MNH).\n"
    help_text += "5. Lancez le calcul.\n\n"
    help_text += "Les résultats sont un fichier KML à ouvrir dans Google Earth et un rapport Excel."
    args = parser.parse_args()
    
    # --- Début de la modification du logging ---
    # Supprimer tous les handlers existants pour éviter les duplications
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
        handler.close()

    # Configuration du logger principal
    logging.getLogger().setLevel(logging.INFO)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')
    # --- Fin de la modification du logging ---

    if not os.path.exists(SHADOW_GPX_DIR): os.makedirs(SHADOW_GPX_DIR)

    # TimezoneFinder en lazy load : son init charge ~52 Mo de polygones
    # timezone en RAM (1.5-2 s). Pas besoin de payer ce coût au démarrage
    # de la GUI — on l'instancie à la première utilisation (à l'intérieur
    # de run_gui_process, quand on connaît les coordonnées du tracé).
    class _LazyTimezoneFinder:
        __slots__ = ("_real",)
        def __init__(self):
            self._real = None
        def _get(self):
            if self._real is None:
                logging.info("Chargement de TimezoneFinder (~2.5 s : module + données)...")
                from timezonefinder import TimezoneFinder  # import différé
                self._real = TimezoneFinder()
            return self._real
        def timezone_at(self, **kwargs):
            return self._get().timezone_at(**kwargs)

    tz_finder = _LazyTimezoneFinder()

    # Mode (même méthode que lidar2map) : sans argument (ou --debug seul, qui est
    # un flag GUI/DevTools) -> interface graphique ; sinon -> calcul headless.
    _is_only_debug = (len(sys.argv) == 2 and sys.argv[1] == "--debug")
    if len(sys.argv) == 1 or _is_only_debug:
        show_form(args, tz_finder, args.output, help_text)
    else:
        sys.exit(run_headless(args, tz_finder))




if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERREUR FATALE INATTENDUE: {e}")
        traceback.print_exc()
        input("\nAppuyez sur Entrée pour fermer...")