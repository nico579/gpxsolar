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
import signal
import sys
import os
import time
import contextlib
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
                print("  ── gpxsolar uninstall ───────────────────────────────────")
                print()
                _total_u = 0
                for _c_u, _label_u in _cibles_u:
                    if _c_u.exists():
                        _taille_u = sum(
                            f.stat().st_size for f in _c_u.rglob("*") if f.is_file())
                        _total_u += _taille_u
                        print(f"  Suppression {_label_u} ({_taille_u / 1e6:.0f} MB)")
                        print(f"    {_c_u}")
                        _sh_u.rmtree(_c_u, ignore_errors=True)
                        print(f"    {'✓ removed' if not _c_u.exists() else '⚠ partial'}")
                    else:
                        print(f"  {_label_u} : absent ({_c_u})")
                print()
                print(f"  {_total_u / 1e6:.0f} MB freed.")
                print()
                print("  Note: gpxsolar.py, the exe/.app and the zip are not removed.")
                print("  Remove them manually if needed.")
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
                        print("  Stale lockfile detected - cleaning up and resuming.", flush=True)
                        _lock.unlink(missing_ok=True)
                if _lock_actif:
                    print("Installation in progress in another instance - waiting...",
                          flush=True)
                    for _ in range(60):
                        _time.sleep(1)
                        if not _lock.exists():
                            break
                    _inner_check = _resolve_exe(_inner_exe)
                    if _inner_check.exists() and _sha_file.exists():
                        _need_extract = False
                    else:
                        print("  ⚠ Concurrent install incomplete or failed.",
                              flush=True)
                        print(f"  Remove the lockfile and relaunch: {_lock}", flush=True)
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
                        print(f"First launch - installation ({_bundle_size // 1_000_000} MB)...",
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
                        print("Installation complete.", flush=True)
                    except Exception as _e_extract:
                        print(f"\n  ⚠ Extraction error: {_e_extract}", flush=True)
                        print("  Restart the application to try again.", flush=True)
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
    print("  ║  ERROR: missing critical Python modules".ljust(63) + " ║")
    print("  ╚══════════════════════════════════════════════════════════════╝")
    print(f"  Missing: {', '.join(manquantes)}")
    if hint:
        print(f"  {hint}")
    print()
    print("  Solutions:")
    print(f"    pip install {' '.join(manquantes)}")
    print("    # or create a venv:")
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
            print(f"  [bootstrap] inside venv {venv_path}")
            return
    except OSError:
        pass

    manquantes = _imports_manquants(_DEPS_CRITIQUES)
    if not manquantes and not force:
        # Toutes les déps déjà importables : pas besoin de venv. Mais on l'annonce
        # pour que l'utilisateur ne s'attende pas à voir un venv apparaître.
        print(f"  [bootstrap] dependencies already available in {sys.executable}")
        print(f"             venv not created - use --bootstrap=force to force creation")
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
            print(f"  Relaunching in venv: {venv_path}")
            _relancer(venv_py, is_windows)

    if not venv_py.exists():
        suppr = ("rmdir /s /q %USERPROFILE%\\.gpxsolar" if is_windows
                 else "rm -rf ~/.gpxsolar")
        print()
        print("  ╔══════════════════════════════════════════════════════════════╗")
        print("  ║  First launch - creating an isolated venv for gpxsolar".ljust(63) + " ║")
        print("  ║  (~80 MB once installed). No impact on system Python.".ljust(63) + " ║")
        print(f"  ║  To remove it: {suppr}".ljust(63) + " ║")
        print("  ║  To use a direct install (no venv):".ljust(63) + " ║")
        print("  ║    python gpxsolar.py --bootstrap=pip                        ║")
        print("  ╚══════════════════════════════════════════════════════════════╝")
        print(f"  Creating venv {venv_path}...")
        try:
            subprocess.run([sys.executable, "-m", "venv", str(venv_path)],
                           check=True)
        except subprocess.CalledProcessError as e:
            print(f"  ERROR creating venv: {e}")
            print("  Install Python 3.9+ with the venv module (apt install python3-venv).")
            sys.exit(1)

    # Installation groupée des déps critiques + optionnelles
    pip_args_crit = [pkg for _, pkg in _DEPS_CRITIQUES]
    pip_args_opt  = [pkg for _, pkg in _DEPS_OPTIONNELLES]
    print(f"  Installing dependencies in the venv (3-5 min)...")
    ok, err = _pip_install(venv_py, pip_args_crit + pip_args_opt, "venv-groupé")
    if not ok:
        print(f"  Bulk install failed, retrying without optional deps ({', '.join(pip_args_opt)})...")
        ok, err = _pip_install(venv_py, pip_args_crit, "venv-critique")
        if ok:
            for opt in pip_args_opt:
                ok_one, _ = _pip_install(venv_py, [opt], f"venv-{opt}")
                print(f"    {'✓' if ok_one else '⚠'} {opt} : {'OK' if ok_one else 'failed - reduced functionality'}")
        else:
            print(f"  ERROR installing critical deps:\n  {err}")
            print(f"  Retry manuel : {venv_pip} install {' '.join(pip_args_crit)}")
            sys.exit(1)
    print("  ✓ Dependencies installed.")
    print("  Relaunching in venv...")
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

    print(f"  Installing: {', '.join(all_pkgs)}...")
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
        print(f"  Retry without optional ({', '.join(opt_pkgs)})...")
        ok, err = _pip_install(sys.executable, crit_pkgs, "courant-crit")
        if ok:
            importlib.invalidate_caches()
            still = [pkg for mod, pkg in _DEPS_CRITIQUES
                     if importlib.util.find_spec(mod) is None]
            if not still:
                print("  ✓ Critical deps installed (optional unavailable).")
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
        print(f"  Creating venv {venv_path}...")
        try:
            subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)
        except subprocess.CalledProcessError as e:
            print(f"  ERROR creating venv: {e}")
            print("  Install Python 3.9+ with the venv module (apt install python3-venv).")
            sys.exit(1)

    crit = [pkg for _, pkg in _DEPS_CRITIQUES]
    opt  = [pkg for _, pkg in _DEPS_OPTIONNELLES]
    print(f"  Installing dependencies in {venv_path} (3-5 min)...")
    ok, err = _pip_install(venv_py, crit + opt, "installer-deps")
    if not ok:
        print(f"  Bulk install failed, retrying critical deps only...")
        ok, err = _pip_install(venv_py, crit, "installer-deps-crit")
        if ok:
            for o in opt:
                ok_one, _ = _pip_install(venv_py, [o], f"opt-{o}")
                print(f"    {'✓' if ok_one else '⚠'} {o} : {'OK' if ok_one else 'failed - reduced functionality'}")
        else:
            print(f"  ERROR installing critical deps:\n  {err}")
            sys.exit(1)
    print(f"  ✓ Dependencies installed in {venv_path}")
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
        print("  [bootstrap] mode=frozen (PyInstaller binary) — bootstrap skipped")
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
        print("  [bootstrap] all critical dependencies are importable")
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

# Numba en lazy load : son import (~3-4 s, charge llvmlite.dll 76 MB) est
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
    logging.info("Numba loaded - JIT kernels (disk cache reused if present)")

    # Re-compile les 3 kernels chauds avec @jit, en remplaçant les versions
    # pure-Python définies plus bas.
    @_jit(nopython=True, cache=True)
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
import pysolar.solartime as pysolartime

def _unwrap_inplace(mod, names):
    for n in names:
        if hasattr(mod, n):
            f = getattr(mod, n)
            try:
                setattr(mod, n, inspect.unwrap(f))
            except Exception:
                pass

# Unwrap des fonctions chaudes du profil : retire le décorateur tzinfo_check
# (vérifications répétées inutiles — nos datetime sont toujours aware UTC).
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
_unwrap_inplace(pysolartime, ["get_julian_solar_day"])

# Neutralise le décorateur tzinfo_check lui-même (couvre les fonctions
# décorées après ce point ou non listées ci-dessus).
try:
    import pysolar.tzinfo_check as tz_check
    import functools

    def no_op_func_with_check(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            return f(*args, **kwargs)
        return wrapper

    tz_check.func_with_check = no_op_func_with_check
    logging.debug("Monkeypatch pysolar.tzinfo_check.func_with_check appliqué.")
except Exception as e:
    logging.warning(f"monkeypatch pysolar.tzinfo_check failed: {e}")


def fast_position(lat, lon, dt_utc):
    """Retourne (azimuth, altitude) en calculant la position topocentrique
    UNE seule fois. pysolar.get_position partage get_topocentric_position
    entre alt et az → résultats strictement identiques à un appel séparé de
    get_altitude + get_azimuth, mais ~2× moins de calcul VSOP."""
    f = pysolar_solar.get_position
    if hasattr(f, "__wrapped__"):
        return f.__wrapped__(lat, lon, dt_utc)
    return f(lat, lon, dt_utc)


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
    """Cache LRU de tuiles. Thread-safe : get() MUTE l'OrderedDict
    (move_to_end) et est appelé par les workers de compute_shadow_geotiff
    pendant qu'un autre thread fait put() — même hazard que celui documenté
    sur _BoundedDictCache, même réponse : un lock interne plutôt que compter
    sur la discipline des appelants. eviction_callback est appelé HORS lock
    (pas de risque de deadlock s'il repasse par le cache)."""
    def __init__(self, max_size=20):
        self.cache = OrderedDict()
        self.max_size = max_size
        self.eviction_callback = None
        self._lock = threading.Lock()

    def __contains__(self, key):
        with self._lock:
            return key in self.cache

    def get(self, key):
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            return None

    def put(self, key, value):
        evicted = None
        with self._lock:
            # N'évincer que si la clé est NOUVELLE (un simple update de clé
            # existante ne fait pas grossir le cache).
            if key not in self.cache and len(self.cache) >= self.max_size:
                evicted = self.cache.popitem(last=False)
            self.cache[key] = value
            self.cache.move_to_end(key)
        if evicted and self.eviction_callback:
            self.eviction_callback(*evicted)

    def __len__(self):
        with self._lock:
            return len(self.cache)



# *************************************************************
# CONSTANTES ET CONFIGURATION
# *************************************************************
OBSERVER_EYE_HEIGHT = 1.7
SHADOW_GPX_DIR = 'GPX_Ombres'
CONFIG_FILE = 'gpx_analyzer_config.json'
HISTORY_FILE = 'gpx_analyzer_history.json'
PREFS_FILE = 'gpx_analyzer_prefs.json'   # préférences UI (langue) — cf. load_lang/save_lang
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
        rows_f, cols_f : np.ndarray float64 — indices de pixel en convention
            CENTRES : le centre du pixel (i, j) est à (i, j). Un appelant qui
            part d'une transform rasterio (convention coins : ~transform donne
            (0.5, 0.5) au centre du premier pixel) doit donc retrancher 0.5.
            Sans ce décalage, l'échantillon au centre exact d'un pixel
            moyennait les 4 cellules voisines au lieu de rendre la valeur de
            la cellule (biais systématique d'un demi-pixel, ~15 m sur un DEM
            30 m). Les points hors du footprint [-0.5, dim-0.5] reçoivent
            `fallback`.
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
    # Domaine valide en convention CENTRES : le footprint de la tuile couvre
    # [-0.5, dim-0.5]. La première/dernière demi-cellule (indices dans
    # [-0.5, 0) ou (dim-1, dim-0.5]) est CLAMPÉE sur la grille (extension de
    # bord) au lieu d'invalider le point : un test strict excluait la dernière
    # demi-cellule de chaque tuile → fallback 0.0 : bande d'altitude 0 le
    # long des coutures de tuiles (ombres sous-détectées, et pente Tobler
    # aberrante si un point de trace y tombait). Avec le clip, les poids
    # bilinéaires dégénèrent proprement vers le voisin existant.
    valid = ((rows_f >= -0.5) & (rows_f <= h - 0.5) &
             (cols_f >= -0.5) & (cols_f <= w - 0.5))
    if not np.any(valid):
        return out

    rows_c = np.clip(rows_f[valid], 0.0, float(h - 1))
    cols_c = np.clip(cols_f[valid], 0.0, float(w - 1))
    r0v = np.floor(rows_c).astype(np.int32)
    c0v = np.floor(cols_c).astype(np.int32)
    fyv = rows_c - r0v
    fxv = cols_c - c0v
    r1v = np.minimum(r0v + 1, h - 1)
    c1v = np.minimum(c0v + 1, w - 1)

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

# Quantification du cache solaire (défauts ; le pas temporel effectif vient
# de --solar-step-s / la GUI, transmis en `step_s`/`solar_step_s`).
SOLAR_ROUND_SEC = 600   # 10 min
SOLAR_ROUND_DEG = 2e-3  # ≈ 220 m en latitude


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


class CalculationCancelled(Exception):
    """Arrêt demandé par l'utilisateur (bouton ■ de la GUI)."""
    pass


# Arrêt : depuis v1.2.2 le calcul de la GUI tourne dans un SOUS-PROCESSUS
# (mode headless relancé par Api.launch), tué net par Api.stop() via
# _kill_process_tree — modèle identique au jumeau lidar2map. Un thread Python
# ne se tue pas de l'extérieur ; un process, si. CANCEL_EVENT/check_cancelled
# restent comme garde coopérative secondaire (cheap no-op tant que l'event
# n'est pas posé) : utile si le moteur est piloté en direct hors GUI.
CANCEL_EVENT = threading.Event()


def check_cancelled():
    if CANCEL_EVENT.is_set():
        raise CalculationCancelled("stop requested")


@contextlib.contextmanager
def log_phase(label, log_func):
    """Chronomètre une étape et logue son temps à la fin : « ⏱ <étape> : N.Ns ».
    Le canal ndjson (mode enfant GUI) n'ajoute pas d'horodatage aux lignes, donc
    ce timing explicite est le seul moyen de voir où part le temps (typiquement
    le téléchargement des tuiles). Le temps est logué même si l'étape lève."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        log_func(f"⏱ {label} : {time.perf_counter() - t0:.1f}s")


# ── Canal enfant → parent (ndjson) ────────────────────────────────────────────
# Quand le moteur tourne comme sous-processus de la GUI (env GPXSOLAR_CHILD=1),
# il renvoie logs et progression au parent en JSON-lines sur stdout : une ligne
# = un dict {"line","tag"} ou {"progress":{"value","text"}}. Le parent
# (Api._pump) relit ces lignes et alimente le panneau de log + la barre.
# ndjson = protocole IPC parent/enfant standard. Spécialisation vs lidar2map
# (qui parse du texte brut + '\r' de GDAL) : ici le moteur est du Python pur
# avec un progress_callback structuré, donc on émet directement du structuré.
_GUI_IPC_STREAM = None
_GUI_IPC_LOCK = threading.Lock()


def _gui_ipc_emit(obj: dict) -> None:
    s = _GUI_IPC_STREAM
    if s is None:
        return
    try:
        with _GUI_IPC_LOCK:
            s.write(json.dumps(obj, ensure_ascii=False) + "\n")
            s.flush()
    except Exception:
        pass


def _install_gui_ipc_logging() -> None:
    """Bascule le logging racine vers le canal ndjson (mode enfant de la GUI).
    Frozen --windowed : sys.stdout peut être None, on rouvre le fd 1 (= le pipe
    fourni par le parent)."""
    global _GUI_IPC_STREAM
    stream = sys.stdout
    if stream is None:
        stream = os.fdopen(1, "w", encoding="utf-8", buffering=1)
        sys.stdout = stream
    _GUI_IPC_STREAM = stream
    tag_map = {"WARNING": "warn", "ERROR": "err", "CRITICAL": "err", "DEBUG": "dim"}

    class _NdjsonLogHandler(logging.Handler):
        def emit(self, record):
            try:
                _gui_ipc_emit({"line": record.getMessage() + "\n",
                               "tag": tag_map.get(record.levelname, "ok")})
            except Exception:
                pass

    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()
    root.addHandler(_NdjsonLogHandler())
    root.setLevel(logging.INFO)


def _kill_process_tree(proc) -> None:
    """Kill forcé de toute la hiérarchie d'un sous-processus (Windows/Unix).
    Repris tel quel du jumeau lidar2map (_kill_tree). taskkill /T tue l'arbre
    (défensif : le moteur n'a que des threads, mais on reste symétrique) ;
    killpg exige que le child ait été lancé avec start_new_session=True."""
    try:
        if proc.poll() is not None:
            return
    except Exception:
        return
    try:
        if os.name == "nt":
            subprocess.call(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _headless_base_cmd() -> list:
    """Commande de base pour relancer CE programme en mode headless.
    Frozen (PyInstaller) : l'exe lui-même reçoit les arguments (le launcher
    _loader.py les forwarde). Dev : python + le script."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.abspath(__file__)]

# Lock du cache départements (l'ancien TZ_CACHE qu'il protégeait n'existait
# plus ; seul get_department_from_coords l'utilise).
DEPARTMENT_CACHE_LOCK = threading.Lock()

# *************************************************************
# GESTION DE LA CONFIGURATION
# *************************************************************
def save_config(data: dict) -> None:
    """Sauvegarde la configuration utilisateur dans un fichier JSON local."""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
    except OSError as e:
        logging.warning(f"Cannot save configuration: {e}")


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
        logging.warning(f"Cannot save history: {e}")


def load_history() -> list:
    """Charge la liste des entrées d'historique (liste vide si absent/corrompu)."""
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
    except (OSError, ValueError) as e:
        logging.warning(f"Cannot read history: {e}")
    return []


def clear_history() -> bool:
    """Réinitialise complètement l'historique. Retourne True si OK."""
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f)
        return True
    except OSError as e:
        logging.warning(f"Cannot delete history: {e}")
        return False


# Préférences UI persistées (langue). Override manuel du toggle FR/EN ; absente
# = auto-détection par navigator.language côté JS. Pas en localStorage : sous
# QtWebEngine packagé il peut être éphémère ; un desktop range ses prefs en fichier.
def load_lang():
    """Retourne 'fr'/'en' si une préférence est sauvée, sinon None (= auto-détection JS)."""
    try:
        if os.path.exists(PREFS_FILE):
            with open(PREFS_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
            v = d.get("lang") if isinstance(d, dict) else None
            return v if v in ("fr", "en") else None
    except (OSError, ValueError) as e:
        logging.warning(f"Cannot read preferences: {e}")
    return None


def save_lang(code: str) -> bool:
    """Persiste le choix de langue de l'UI. 'fr' ou 'en' ; sinon ignoré."""
    if code not in ("fr", "en"):
        return False
    try:
        with open(PREFS_FILE, 'w', encoding='utf-8') as f:
            json.dump({"lang": code}, f, indent=2)
        return True
    except OSError as e:
        logging.warning(f"Cannot save preferences: {e}")
        return False


def load_config() -> dict:
    """Charge la configuration depuis un fichier JSON. Retourne {} si absent."""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except (OSError, ValueError) as e:
        logging.warning(f"Cannot load configuration: {e}")
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


def gpx_all_points(gpx_obj):
    """Points de trace d'un objet gpxpy : concatène tous les segments de tous
    les tracks. L'ancien accès direct tracks[0].segments[0] tronquait
    silencieusement un GPX multi-segments au premier segment, et plantait en
    IndexError brut sur un GPX à <rte> seul. Fallback sur les routes ;
    GPXRoutePoint porte aussi latitude/longitude/elevation/time."""
    pts = [p for trk in gpx_obj.tracks for seg in trk.segments for p in seg.points]
    if not pts:
        pts = [p for rte in gpx_obj.routes for p in rte.points]
    if not pts:
        raise MissingDataError("No track points in the GPX file (no <trk> nor <rte>).")
    return pts


def open_file_default_app(path):
    """Ouvre un fichier avec l'application par défaut de l'OS.
    os.startfile n'existe QUE sous Windows : le build macOS terminait en
    erreur quand « ouvrir le résultat après calcul » était coché."""
    system = platform.system()
    if system == "Windows":
        os.startfile(path)
    elif system == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])

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
    # step_s fait partie de la clé : les multiples de 600 s sont aussi des
    # multiples de 60 s, donc dans un même process mélangeant les deux pas
    # (moteur à --solar-step-s 60, préchargement corridor et rayons KML au
    # défaut 600) une entrée quantifiée à 600 s pouvait servir une valeur
    # calculée jusqu'à ~10 min de l'heure demandée — vidant de son sens le
    # pas fin demandé au moteur.
    key = (float(lat_q), float(lon_q), int(ts_q), int(step_s))

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

    Groupement par clé quantifiée via np.unique : un bloc de carte d'ombre
    de 65 000 pixels ne contient que quelques dizaines de clés distinctes
    (SOLAR_ROUND_DEG ≈ 220 m). L'ancienne boucle faisait n lookups
    SOLAR_CACHE en Python, chacun prenant le lock du cache — contendu entre
    les workers de compute_shadow_geotiff. On résout maintenant chaque clé
    UNE fois puis on redistribue par indexation NumPy. return_index donne la
    PREMIÈRE occurrence de chaque groupe comme représentant : même choix que
    l'ancienne itération séquentielle → résultats identiques."""
    n = len(lats)
    if n == 0:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)

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

    # float64 représente exactement les entiers < 2^53 : ts_q reste exact.
    keys = np.column_stack((lat_q, lon_q, ts_q.astype(np.float64)))
    uniq, first_idx, inverse = np.unique(
        keys, axis=0, return_index=True, return_inverse=True)

    u_alts = np.empty(len(uniq), dtype=np.float64)
    u_azs  = np.empty(len(uniq), dtype=np.float64)
    for k in range(len(uniq)):
        # step_i dans la clé : cf. solar_altaz_cached (contamination croisée
        # entre pas 60 s et 600 s au sein d'un même process sinon).
        key = (float(uniq[k, 0]), float(uniq[k, 1]), int(uniq[k, 2]), step_i)
        v = SOLAR_CACHE.get(key)
        if v is None:
            i = int(first_idx[k])   # représentant : coordonnées/temps ORIGINAUX
            dt_i = datetime.fromtimestamp(float(ts_arr[i]), tz=pytz.utc)
            z, a = GET_POSITION(lats_arr[i], lons_arr[i], dt_i)
            v = (a, z)
            SOLAR_CACHE[key] = v
        u_alts[k], u_azs[k] = v

    return u_alts[inverse], u_azs[inverse]

DEPARTMENT_CACHE = {} # Cache pour les résultats de get_department_from_coords

def get_department_from_coords(lat, lon):
    cache_key = (_q_coord_int(lat), _q_coord_int(lon))
    with DEPARTMENT_CACHE_LOCK:
        if cache_key in DEPARTMENT_CACHE:
            return DEPARTMENT_CACHE[cache_key]

    # Réseau HORS lock (l'ancienne version tenait le lock global pendant les
    # requêtes HTTP, elles-mêmes SANS timeout : un serveur muet gelait le
    # thread pour toujours, lock détenu).
    dept_code = None
    try:
        url = f"https://geo.api.gouv.fr/communes?lat={lat}&lon={lon}"
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()

        if data:
            # L'API peut retourner plusieurs communes si le point est à une
            # intersection ; la première est généralement la plus pertinente.
            commune_info = data[0]
            department_name = commune_info.get('departement', {}).get('nom')
            dept_code = commune_info.get('departement', {}).get('code')

            # Si les informations de département ne sont pas directement dans
            # la commune, il faut parfois faire un appel supplémentaire.
            if not department_name and 'codeDepartement' in commune_info:
                dept_url = ("https://geo.api.gouv.fr/departements/"
                            f"{commune_info['codeDepartement']}")
                dept_response = requests.get(dept_url, timeout=15)
                dept_response.raise_for_status()
                dept_data = dept_response.json()
                if dept_data:
                    department_name = dept_data.get('nom')
                    dept_code = dept_data.get('code')

            if not (department_name and dept_code):
                logging.warning(f"Cannot find department info for coordinates {lat}, {lon}.")
                dept_code = None
        else:
            logging.warning(f"No municipality found for coordinates {lat}, {lon}.")
    except requests.exceptions.RequestException as e:
        logging.warning(f"Cannot reach the Geo API for the department: {e}")
        dept_code = None
    except (IndexError, KeyError, ValueError) as e:
        logging.warning(f"Unexpected response from the Geo API for {lat}, {lon}: {e}")
        dept_code = None

    # None aussi mis en cache : évite de refaire l'appel pour le même point.
    with DEPARTMENT_CACHE_LOCK:
        DEPARTMENT_CACHE[cache_key] = dept_code
    return dept_code




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

        logging.info(f"Downloading vegetation {tile_name} (~1GB)...")
        try:
            # timeout=(connect, read) : en stream, `read` borne l'attente
            # ENTRE deux chunks, pas la durée totale — un gros fichier lent
            # mais vivant passe, un serveur muet ne gèle plus le thread.
            response = requests.get(url, stream=True, timeout=(10, 60))
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            block_size = 8192
            downloaded_size = 0

            # Écriture atomique : .part puis os.replace. Un kill du sous-processus
            # (bouton Arrêter) en plein download ne laisse jamais un .tif tronqué
            # que le prochain run prendrait pour un cache valide.
            tmp_path = output_path + ".part"
            with open(tmp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=block_size):
                    check_cancelled()
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    if self.progress_callback and total_size > 0:
                        progress = (downloaded_size / total_size) * 100
                        self.progress_callback(50 + progress * 0.05, f"DL WorldCover {tile_name}: {progress:.1f}%")
            os.replace(tmp_path, output_path)

            return self._load_single_tile(os.path.basename(output_path))
        except CalculationCancelled:
            # Arrêt utilisateur : purger le fichier partiel et PROPAGER
            # (le except Exception ci-dessous avalerait l'annulation).
            if os.path.exists(output_path + ".part"): os.remove(output_path + ".part")
            raise
        except Exception as e:
            logging.error(f"Vegetation DL error: {e}")
            if os.path.exists(output_path + ".part"): os.remove(output_path + ".part")
            return False
            
    def _load_tiles(self):
        logging.info(f" > Scanning vegetation directory ({self.worldcover_dir})...")
        count = sum(1 for f in os.listdir(self.worldcover_dir) if f.endswith('.tif') and self._load_single_tile(f))
        if count > 0: logging.info(f"   - {count} vegetation tiles loaded.")
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
            logging.error(f"Error loading vegetation tile {filename}: {e}")
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

        sun_az = sun_azs_batch[valid] # Azimuts du cache, déjà filtrés par valid

        rad_az = np.deg2rad(sun_az)

        # PAS de second [valid] ici : rad_az est déjà filtré. Le ré-appliquer
        # levait IndexError dès qu'un batch mélangeait points jour/nuit
        # (masque de longueur nb sur un tableau de longueur n_valid).
        sin_az = np.sin(rad_az)
        cos_az = np.cos(rad_az)

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
    # Téléchargements parallèles. Léger sur-provisionnement : le serveur WMS
    # met plusieurs secondes à RENDRE chaque dalle (calcul côté geopf, pas de
    # trafic), le transfert lui est bref. Avoir plus de requêtes en vol que la
    # bande passante stricte ne l'exige garde le tuyau plein — pendant qu'une
    # dalle se calcule côté serveur, une autre descend (comble les « creux »).
    # Borné pour ne pas se faire throttler par geopf.fr.
    DOWNLOAD_WORKERS = 8

    def __init__(self, log_func=print, progress_callback=None, *,
                 layer_map=None, cache_dir="LIDAR_CACHE", cache_prefix="LIDAR",
                 tile_px=2000):
        # Paramétrable pour servir aussi le WMS RGE ALTI (mode ombre) : même
        # lazy-load + cache LRU + échantillonnage, en changeant la couche
        # (MNT seul), le répertoire/préfixe de cache (pas de collision avec les
        # tuiles LiDAR HD) et la résolution pixel. Défauts = LiDAR HD inchangé.
        self.log = log_func
        self.progress_callback = progress_callback
        self.layer_map = layer_map if layer_map is not None else dict(self.LAYER_MAP)
        self.rasters = {k: LRUTileCache(max_size=self.MAX_CACHE_SIZE)
                        for k in self.layer_map}
        self.transformer = TransformerPool.wgs84_to_lambert()
        self.cache_dir = cache_dir
        self.cache_prefix = cache_prefix
        self.tile_px = tile_px
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
        self.enabled = False
        self.lock = threading.RLock() # RLock pour les appels imbriqués
        self.downloaded_tiles = set()  # Tuiles téléchargées
        self.used_tiles = set()  # Tuiles effectivement utilisées pour le ray-tracing

        # Couverture altimétrique : compte les échantillons servis SANS donnée
        # réelle (tuile absente/illisible, pixel nodata, point hors grille),
        # laissés à 0 m. 0 m étant une altitude légitime, elle ne peut pas
        # servir de sentinelle : sans ces compteurs, une couverture incomplète
        # produit un relief plat plausible en apparence (ombres sous-détectées,
        # pentes fausses) sans aucun signal. Agrégés en fin de calcul par
        # HGTDataManager.coverage_report().
        self._cov_lock = threading.Lock()
        self.cov_missing = 0
        self.cov_total = 0

        # Session HTTP PARTAGÉE + pool de connexions : sans elle, chaque tuile
        # rouvrait une connexion TCP + poignée TLS (plusieurs aller-retours au
        # serveur), coût payé à CHAQUE dalle. La Session garde les connexions
        # ouvertes (keep-alive) et les réutilise. Pool dimensionné au nombre de
        # workers pour ne pas jeter/rouvrir de connexions en parallèle. Thread-safe
        # pour des .get() concurrents (on ne mute pas la Session). max_retries=0 :
        # on gère nos propres retries dans _fetch_tile_to_disk.
        self.session = requests.Session()
        _adapter = requests.adapters.HTTPAdapter(
            pool_connections=self.DOWNLOAD_WORKERS,
            pool_maxsize=self.DOWNLOAD_WORKERS,
            max_retries=0)
        self.session.mount("https://", _adapter)
        self.session.mount("http://", _adapter)


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
            margin_degrees: Demi-angle du cône autour de l'azimut solaire
                (100 = ±100°, soit un cône de 200°)
            
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

    def _tile_cache_path(self, key, tx, ty):
        return os.path.join(
            self.cache_dir, f"{self.cache_prefix}_{key}_L93_1km_{tx}_{ty}.tif")

    def _fetch_tile_to_disk(self, key, tx, ty):
        """Télécharge UNE tuile WMS sur disque (écriture atomique), SANS prendre
        self.lock ni toucher au cache RAM. Isolé pour pouvoir paralléliser les
        téléchargements : le HTTP est le goulot (I/O réseau), et le tenir sous le
        lock global sérialisait tout. Retourne True si le fichier est présent en
        sortie (déjà là ou fraîchement téléchargé), False sinon."""
        layer_name = self.layer_map.get(key)
        if not layer_name:
            self.log(f"❌ Error: Couche {key} non reconnue")
            return False
        cache_path = self._tile_cache_path(key, tx, ty)
        if os.path.exists(cache_path):
            return True

        x0, y0 = tx * 1000, ty * 1000
        params = {
            'SERVICE': 'WMS', 'VERSION': '1.3.0', 'REQUEST': 'GetMap',
            'LAYERS': layer_name, 'FORMAT': 'image/geotiff', 'CRS': 'EPSG:2154',
            'BBOX': f"{x0},{y0},{x0+1000},{y0+1000}",
            'WIDTH': self.tile_px, 'HEIGHT': self.tile_px, 'STYLES': ''
        }
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                response = self.session.get(
                    "https://data.geopf.fr/wms-r", params=params, timeout=90)
                response.raise_for_status()

                # Un ServiceException WMS arrive en HTTP 200 avec un corps XML :
                # l'écrire en .tif créerait une tuile corrompue PERMANENTE en
                # cache. Magic bytes TIFF : 'II*\0' (LE) / 'MM\0*' (BE).
                if not response.content.startswith((b'II*\x00', b'MM\x00*')):
                    head = response.content[:200].decode('utf-8', 'replace')
                    raise requests.exceptions.RequestException(
                        f"réponse WMS non-GeoTIFF : {head!r}")

                # Écriture atomique via temp UNIQUE (pid+thread) : en parallèle,
                # deux threads sur la même tuile n'écrivent pas dans le même .part
                # (le dernier os.replace gagne, les deux fichiers étant valides).
                _tmp = f"{cache_path}.{os.getpid()}.{threading.get_ident()}.part"
                with open(_tmp, 'wb') as f:
                    f.write(response.content)
                os.replace(_tmp, cache_path)
                self.log(f"  ✓ Tuile {tx},{ty} téléchargée")
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

    def _ensure_tile_downloaded(self, key, tx, ty):
        """Garantit qu'une tuile est sur disque (chemin séquentiel/lazy, sous
        lock). Le vrai téléchargement est délégué à _fetch_tile_to_disk."""
        cache_path = self._tile_cache_path(key, tx, ty)
        if os.path.exists(cache_path):
            self.downloaded_tiles.add((tx, ty))
            return True
        with self.lock:
            # Double-check après verrouillage (une autre thread a pu télécharger).
            if os.path.exists(cache_path):
                self.downloaded_tiles.add((tx, ty))
                return True
            if self._fetch_tile_to_disk(key, tx, ty):
                self.downloaded_tiles.add((tx, ty))
                return True
        return False

    def prefetch_tiles_parallel(self, tasks, workers=None):
        """Télécharge en PARALLÈLE une liste de tuiles (key, tx, ty) distinctes.
        Le HTTP se fait hors lock (_fetch_tile_to_disk) → vrai parallélisme I/O ;
        seul l'enregistrement dans downloaded_tiles est verrouillé (rapide). Les
        tuiles déjà sur disque sont ignorées. Propage CalculationCancelled."""
        workers = workers or self.DOWNLOAD_WORKERS
        todo = [t for t in tasks if not os.path.exists(self._tile_cache_path(*t))]
        if not todo:
            return 0

        def _one(task):
            check_cancelled()
            key, tx, ty = task
            if self._fetch_tile_to_disk(key, tx, ty):
                with self.lock:
                    self.downloaded_tiles.add((tx, ty))
                return True
            return False

        n_ok = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_one, t) for t in todo]
            try:
                for fut in concurrent.futures.as_completed(futures):
                    if fut.result():
                        n_ok += 1
            except CalculationCancelled:
                for f in futures:
                    f.cancel()
                raise
        return n_ok

    def _load_tile_to_ram(self, key, tx, ty):
        """
        ✅ LAZY LOADING: Charge une tuile en RAM (télécharge si nécessaire)
        """
        with self.lock:
            tile_id = (tx, ty)
            
            if self.rasters[key].get(tile_id) is not None:
                return True
            
            cache_filename = f"{self.cache_prefix}_{key}_L93_1km_{tx}_{ty}.tif"
            cache_path = os.path.join(self.cache_dir, cache_filename)
            
            if not os.path.exists(cache_path):
                # Message générique : _load_tile_to_ram sert au raycasting d'ombre
                # ET à l'échantillonnage d'altitude du MNT (calcul de pente). Dire
                # « pour raycasting » induisait en erreur en mode pente.
                self.log(f"🔄 Lazy download: Tuile ({key.upper()}) {tx},{ty} nécessaire au calcul...")
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
        cov_miss = 0   # échantillons laissés à 0 m faute de donnée (cf. __init__)
        for k in present_layers:
            elevs = out[k]
            layer_tiles = tiles_by_layer[k]
            for unique_idx, (tx, ty) in enumerate(unique_tiles):
                mask = (inverse_indices == unique_idx)
                tile = layer_tiles.get((tx, ty))
                if tile is None:
                    # Tuile absente/illisible : points laissés à 0 m.
                    cov_miss += int(np.count_nonzero(mask))
                    continue
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
                cov_miss += int(xs_tile.size - np.count_nonzero(valid_mask))
                if not np.any(valid_mask):
                    continue
                vals = data[rows[valid_mask], cols[valid_mask]].astype(float)
                nd_mask = (vals == nodata)
                if np.any(nd_mask):
                    cov_miss += int(np.count_nonzero(nd_mask))
                    vals[nd_mask] = 0.0
                output_indices = np.where(mask)[0][valid_mask]
                elevs[output_indices] = vals
        with self._cov_lock:
            self.cov_missing += cov_miss
            self.cov_total += n * len(present_layers)
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
        self.wms_dem = None   # DEM WMS RGE ALTI (mode ombre, ign_rgealti/bdalti)
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

        # Couverture altimétrique (cf. LidarManager.__init__) : échantillons
        # servis à 0 m faute de donnée. coverage_report() agrège ces compteurs
        # avec ceux des managers délégués (LiDAR/WMS).
        self._cov_lock = threading.Lock()
        self.cov_missing = 0
        self.cov_total = 0

        source_info = self.SOURCES.get(self.source, {})
        self.resolution = source_info.get('resolution', 30)
        self.max_distance = max_shadow_distance
        self.step = min(self.analysis_resolution, self.resolution)


        if self.source == 'ign_lidar_hd':
            self.lidar_manager = LidarManager(self.log, self.progress_callback)
        elif self.source in ('ign_rgealti_5m', 'ign_bdalti_25m'):
            # DEM via le WMS Géoplateforme (couche ELEVATION.ELEVATIONGRIDCOVERAGE
            # = RGE ALTI 1 m, altitude float brute) au lieu de l'archive .7z
            # départementale (endpoint IGN mort depuis la migration cartes.gouv.fr).
            # Tuiles paresseuses 1 km, mêmes rouages que LiDAR HD. Résolution pixel
            # selon la source : 5 m (200 px/km) ou 25 m (40 px/km).
            if not PYPROJ_AVAILABLE: raise MissingDataError("pyproj non installé.")
            if not RASTERIO_AVAILABLE: raise MissingDataError("rasterio non installé.")
            self.wms_dem = LidarManager(
                self.log, self.progress_callback,
                layer_map={'mnt': "ELEVATION.ELEVATIONGRIDCOVERAGE"},
                cache_dir="RGEALTI_CACHE", cache_prefix="RGEALTI",
                tile_px=200 if self.source == 'ign_rgealti_5m' else 40)
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
            
        # Phase 1 : téléchargement PARALLÈLE (le HTTP est le goulot, hors lock).
        # Avant, la boucle séquentielle téléchargeait une tuile de 16 Mo à la
        # fois (LiDAR HD = 2000 px × 2 couches) : très long. Idée du jumeau
        # lidar2map (ThreadPoolExecutor pour les téléchargements de tuiles).
        all_tasks = [(key, tx, ty) for key in ['mnt', 'mnh'] for (tx, ty) in final_tiles]
        self.lidar_manager.prefetch_tiles_parallel(all_tasks)

        # Phase 2 : chargement en RAM séquentiel (lecture disque + cache LRU sous
        # lock). Les tuiles sont déjà sur disque (phase 1), donc pas de download
        # ici. Séquentiel car la mutation du cache LRU doit rester sérialisée.
        processed_tiles = 0
        for key in ['mnt', 'mnh']:
            for tx, ty in final_tiles:
                check_cancelled()
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







    # Endpoint altimétrique Géoplateforme (RGE ALTI 1 m, France). API de POINTS :
    # zéro téléchargement de tuile/raster, idéale pour la pente (on ne veut que
    # l'altitude aux points de la trace). Même source que le script colorer_pente
    # de Nico (module alti_ign). Ne remplace PAS le raster en mode ombre (le
    # ray-tracing a besoin d'une grille autour de la trace).
    GEOPF_ALTI_URL = "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json"
    GEOPF_ALTI_RESOURCE = "ign_rge_alti_wld"   # RGE ALTI (LiDAR 1 m sur la France)
    GEOPF_ALTI_MAXPTS = 5000                   # limite documentée du service
    GEOPF_ALTI_NODATA = -99998.0               # l'API renvoie -99999 hors couverture

    def _geopf_point_elevations(self, lats, lons):
        """Altitude RGE ALTI 1 m via l'API altimétrique Géoplateforme (points).
        POST par lots de 5000 (lat/lon pipe-délimités), `zonly`. Comble les
        trous de couverture par interpolation linéaire ; lève MissingDataError
        si l'API est injoignable ou la trace entièrement hors France."""
        lats = np.asarray(lats, dtype=np.float64)
        lons = np.asarray(lons, dtype=np.float64)
        n = lats.size
        if n == 0:
            return np.zeros(0, dtype=np.float64)
        out = np.full(n, np.nan, dtype=np.float64)
        try:
            for start in range(0, n, self.GEOPF_ALTI_MAXPTS):
                check_cancelled()
                sl = slice(start, min(start + self.GEOPF_ALTI_MAXPTS, n))
                # POST JSON obligatoire (le form-urlencoded renvoie 500, le GET
                # plafonne sur la longueur d'URL). timeout=(connect, read).
                resp = requests.post(self.GEOPF_ALTI_URL, json={
                    'lon': '|'.join(f"{v:.6f}" for v in lons[sl]),
                    'lat': '|'.join(f"{v:.6f}" for v in lats[sl]),
                    'resource': self.GEOPF_ALTI_RESOURCE,
                    'delimiter': '|',
                    'zonly': 'true',
                }, timeout=(10, 120))
                resp.raise_for_status()
                elevations = resp.json().get('elevations', [])
                for j, z in enumerate(elevations):
                    if isinstance(z, dict):
                        z = z.get('z')
                    if z is not None and float(z) > self.GEOPF_ALTI_NODATA:
                        out[start + j] = float(z)
                if self.progress_callback:
                    self.progress_callback(
                        min(40.0, 40.0 * (sl.stop / n)),
                        f"Altitude RGE ALTI (API) : {sl.stop}/{n} points")
        except CalculationCancelled:
            raise
        except Exception as e:
            raise MissingDataError(
                f"API altimétrique Géoplateforme indisponible : {e}. Réessayez, "
                f"ou choisissez un DEM mondial (SRTM1/Copernicus) pour la pente.")
        valid = ~np.isnan(out)
        if not valid.any():
            raise MissingDataError(
                "Trace hors couverture RGE ALTI (hors France ?). Pour une pente "
                "hors France, choisissez SRTM1 ou Copernicus comme DEM.")
        if not valid.all():
            idx = np.arange(n, dtype=np.float64)
            out[~valid] = np.interp(idx[~valid], idx[valid], out[valid])
            self.log(f"  {(~valid).sum()} point(s) hors couverture RGE ALTI "
                     f"comblé(s) par interpolation.")
        return out

    def get_ground_elevations_vec(self, lats, lons):
        """Méthode vectorielle pour l'altitude du sol."""
        if getattr(self, 'use_geopf_point_alti', False):
            # Mode pente : RGE ALTI 1 m par requête de points (aucune tuile).
            return self._geopf_point_elevations(lats, lons)
        if self.lidar_manager:
            return self.lidar_manager.get_values_vec('mnt', lats, lons)
        if self.wms_dem:
            # Mode ombre RGE ALTI/BD ALTI : MNT via tuiles WMS Géoplateforme.
            return self.wms_dem.get_values_vec('mnt', lats, lons)
        
        if self.source == 'srtm1' and self.srtm_data:
            # get_elevation → None hors donnée : compté pour coverage_report.
            # (L'ancien `or 0` confondait au passage une vraie altitude 0.0
            # avec « pas de donnée », sans la compter nulle part.)
            raw = [self.srtm_data.get_elevation(lat, lon) for lat, lon in zip(lats, lons)]
            n_miss = sum(1 for v in raw if v is None)
            with self._cov_lock:
                self.cov_missing += n_miss
                self.cov_total += len(raw)
            return np.array([0.0 if v is None else float(v) for v in raw])
        
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

    def coverage_report(self):
        """(échantillons sans donnée réelle, échantillons servis), agrégé sur
        les compteurs propres et ceux des managers délégués (LiDAR/WMS).
        Les points sans donnée reçoivent 0 m, plausible en apparence (relief
        plat, ombres sous-détectées) : ces compteurs sont le seul signal."""
        with self._cov_lock:
            miss, tot = self.cov_missing, self.cov_total
        for mgr in (self.lidar_manager, self.wms_dem):
            if mgr is not None:
                with mgr._cov_lock:
                    miss += mgr.cov_missing
                    tot += mgr.cov_total
        return miss, tot

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
        logging.info(f" > Querying the IGN API (HTML): {base_url} for BDALTI zone={params['zone']}")
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
                logging.info(f"   - BDALTI URL found (via regex): {download_url}")
                return download_url, archive_name
            logging.warning("   - Error: No .7z download link found in the HTML response.")
            return None, None
        except Exception as e:
            logging.error(f"   - Unexpected error retrieving BDALTI info: {e}")
            return None, None
    def _get_rgealti_download_info(self, department_code):
        dept_id = department_code.zfill(3) if department_code.isdigit() else department_code
        base_url = "https://geoservices.ign.fr/telechargement-api/RGEALTI"
        params = {"zone": f"D{dept_id}"}
        headers = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' }
        logging.info(f" > Querying the IGN API (HTML): {base_url} for RGEALTI zone={params['zone']}")
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
                    logging.info(f"   - RGEALTI 5M URL found (via regex): {download_url}")
                    return download_url, archive_name
            logging.warning("   - Error: No RGEALTI 5M .7z link found for this department.")
            return None, None
        except Exception as e:
            logging.error(f"   - Unexpected error retrieving RGEALTI info: {e}")
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
            # Référencer les tuiles SRTM1 du répertoire pour la visualisation
            # (l'add avait été perdu : la boucle calculait bbox_srtm pour rien
            # et visualize_tiles ne montrait jamais rien en source srtm1).
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
                        self.downloaded_tiles_info.add(('srtm1', filename, bbox_srtm))

        except Exception as e: logging.error(f"SRTM init error: {e}")
    def _init_copernicus(self):
        if not RASTERIO_AVAILABLE:
            logging.warning("rasterio unavailable, Copernicus DEM disabled")
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
            # timeout=(connect, read) : read borne l'attente entre deux chunks
            # (pas la durée totale) — un serveur muet ne gèle plus le thread.
            response = requests.get(url, stream=True, timeout=(10, 60))
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))
            block_size = 8192 # 8KB
            downloaded_size = 0

            # Écriture atomique : .part puis os.replace (kill-safe). Une archive
            # présente est toujours complète, jamais tronquée par un Arrêter.
            tmp_path = path + ".part"
            with open(tmp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=block_size):
                    check_cancelled()
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    if self.progress_callback and total_size > 0:
                        progress = (downloaded_size / total_size) * 100
                        self.progress_callback(50 + progress * 0.05, f"DL IGN {os.path.basename(path)}: {progress:.1f}%")
            os.replace(tmp_path, path)

        except CalculationCancelled:
            # Arrêt utilisateur : purger l'archive partielle et PROPAGER.
            if os.path.exists(path + ".part"):
                os.remove(path + ".part")
            raise
        except Exception as e:
            logging.error(f"\n✗ IGN archive DL error: {e}")
            # Purger l'archive partielle : sinon prepare_*_data la voit
            # exister, saute le re-téléchargement et échoue en boucle sur la
            # décompression (même filet que Copernicus/WorldCover).
            if os.path.exists(path + ".part"):
                os.remove(path + ".part")
    def _decompress_ign_archive(self, archive_path, dest_dir):
        if not PY7ZR_AVAILABLE:
            logging.error("✗ Error: py7zr not installed (pip install py7zr).")
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
            logging.error(f"✗ Decompression error: {e}")
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
                    except Exception as e: logging.warning(f"Unreadable .asc header {filename}: {e}")

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
                # Lecture des données. pandas.read_csv (parseur C) est ~5-10×
                # plus rapide que np.loadtxt sur les .asc de 1000×1000 valeurs ;
                # pandas est déjà une dépendance critique (import différé).
                import pandas as pd
                data_arr = pd.read_csv(filepath, sep=r'\s+', skiprows=6,
                                       header=None, dtype=np.float64).to_numpy()
                
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
            n_pts = int(np.asarray(lats).size)
            with self._cov_lock:
                self.cov_missing += n_pts
                self.cov_total += n_pts
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
        n_miss = 0   # échantillons laissés à 0 m faute de donnée (cf. coverage_report)
        for i, (lat_t, lon_t) in enumerate(unique_tile_coords):
            # Même helper que le téléchargement : clé garantie cohérente
            tile_key = ('copernicus', self.copernicus_tile_filename(lat_t, lon_t))

            tile_data = self.hgt_rasters.get(tile_key)
            
            if tile_data is None:
                # Tuile non chargée : points laissés à 0 m, comptés manquants.
                n_miss += int(np.count_nonzero(inverse_indices == i))
                continue

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
                # -0.5 : indices coins (~transform) → indices centres,
                # cf. _bilinear_sample_raster.
                vals = _bilinear_sample_raster(data_arr, rows_f - 0.5, cols_f - 0.5,
                                                nodata=nodata, fallback=0.0)
                elevations[np.where(mask)[0]] = vals
            else:
                # Voisin le plus proche (comportement original)
                rows_int = rows_f.astype(np.int32)
                cols_int = cols_f.astype(np.int32)
                h, w = data_arr.shape
                valid_mask_in_tile = (rows_int >= 0) & (rows_int < h) & \
                                     (cols_int >= 0) & (cols_int < w)
                n_miss += int(lats_tile.size - np.count_nonzero(valid_mask_in_tile))
                if not np.any(valid_mask_in_tile):
                    continue
                valid_rows = rows_int[valid_mask_in_tile]
                valid_cols = cols_int[valid_mask_in_tile]
                vals = data_arr[valid_rows, valid_cols].astype(np.float64)
                nd_mask = (vals == nodata)
                if np.any(nd_mask):
                    n_miss += int(np.count_nonzero(nd_mask))
                    vals[nd_mask] = 0.0
                output_indices = np.where(mask)[0][valid_mask_in_tile]
                elevations[output_indices] = vals

        with self._cov_lock:
            self.cov_missing += n_miss
            self.cov_total += int(elevations.size)
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
                    # -0.5 : indices coins (xllcorner/ymax) → indices centres,
                    # cf. _bilinear_sample_raster.
                    vals = _bilinear_sample_raster(data_arr, row_fs - 0.5, col_fs - 0.5,
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
    @staticmethod
    def copernicus_tile_filename(lat, lon):
        """Nom de tuile Copernicus pour des coordonnées : coin SW (floor) et
        préfixes lat/lon INDÉPENDANTS. Source de vérité unique — l'ancienne
        version, dupliquée en trois endroits, calculait les DEUX préfixes sur
        le signe de la seule latitude et tronquait via int() : pour lon < 0
        (ouest de la France, Espagne…) on téléchargeait une tuile 'E' erronée
        tandis que l'extraction vectorisée reconstruisait le bon nom 'W' et
        ne la retrouvait pas → élévations silencieusement à 0."""
        lat_t = math.floor(lat)
        lon_t = math.floor(lon)
        lat_prefix = 'N' if lat_t >= 0 else 'S'
        lon_prefix = 'E' if lon_t >= 0 else 'W'
        return (f"Copernicus_DSM_COG_10_{lat_prefix}{abs(lat_t):02d}_00_"
                f"{lon_prefix}{abs(lon_t):03d}_00_DEM.tif")

    def _download_copernicus_tile(self, lat, lon):
        if not RASTERIO_AVAILABLE: return None
        filename = self.copernicus_tile_filename(lat, lon)
        tile_name = filename[:-len(".tif")]
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

            # Écriture atomique : .part puis os.replace (kill-safe). Une tuile
            # présente est toujours complète, jamais tronquée par un Arrêter.
            tmp_path = output_path + ".part"
            with open(tmp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=block_size):
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    if self.progress_callback and total_size > 0:
                        progress = (55 + (downloaded_size / total_size) * 0.05) # De 55% à 60%
                        self.progress_callback(progress, f"DL Copernicus {filename}: {progress:.1f}%")
            os.replace(tmp_path, output_path)

            # Ajouter la tuile à downloaded_tiles_info ici, mais sans charger en RAM
            # Le _scan_copernicus_tiles au début du processus s'occupe de ça.
            # On pourrait le faire ici si on voulait une mise à jour immédiate
            # de downloaded_tiles_info, mais pour l'instant, c'est géré par le scan initial.
            return True

        except Exception as e:
            logging.error(f"Copernicus download error: {e}")
            if os.path.exists(output_path + ".part"):
                os.remove(output_path + ".part")
            return False

    def _ensure_copernicus_tile_metadata_loaded(self, lat, lon):
        tile_filename = self.copernicus_tile_filename(lat, lon)
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
            # Si la tuile SRTM1 a été utilisée, elle est considérée comme "chargée en RAM".
            # floor, pas int() : le nom de tuile est le coin SW (int() tronque
            # vers 0 et désigne la mauvaise tuile pour lat/lon négatifs).
            lat_int, lon_int = math.floor(lat), math.floor(lon)
            tile_name = f"{ 'N' if lat_int >= 0 else 'S' }{abs(lat_int):02d}{ 'E' if lon_int >= 0 else 'W' }{abs(lon_int):03d}.hgt"
            bbox_srtm = (float(lon_int), float(lat_int), float(lon_int + 1), float(lat_int + 1))
            self.loaded_in_ram_tiles.add(('srtm1', tile_name, bbox_srtm))

        with self.elevation_cache_lock:
            self.elevation_cache[cache_key] = elev
        return elev

def adaptive_distances(max_dist, initial_step=5.0):
    # Chaque zone est bornée à max_dist : sans ce min(), un max_dist < 300
    # traçait quand même jusqu'à ~300 m (la zone mid allait à 300 en dur).
    near = np.arange(initial_step, min(50, max_dist), initial_step)
    mid = np.arange(50, min(300, max_dist), initial_step * 3)
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
    valid_idx = np.where(valid_mask)[0]
    obs_lats_all = lats[valid_idx]
    obs_lons_all = lons[valid_idx]
    sun_alts_all = sun_alts[valid_idx]
    sun_azs_all  = sun_azs_full[valid_idx]

    # 4. Préparer le Ray-Tracing vectorisé
    step = hgt_manager.step
    max_dist = hgt_manager.max_distance
    distances = adaptive_distances(max_dist, initial_step=step)

    shadow_mode = getattr(hgt_manager, 'shadowmode', 'both')  # Récupérer le mode depuis hgtmanager
    TOLERANCE = 0.1

    # Sous-lots par BUDGET MÉMOIRE : chaque point matérialise len(distances)
    # échantillons dans ~8 tableaux float64 (rayons lat/lon/alt, profils
    # sol/objets/obstacles + temporaires). Sans borne, un bloc de carte
    # 256×256 en LiDAR (pas 0,5 m, portée 1 000 m → 441 échantillons) montait
    # à ~1,8 Go, multiplié par num_workers (défaut cpu_count) → OOM/swap.
    # 2 M d'éléments ≈ 16 Mo par tableau float64, soit ~150 Mo de pic par
    # worker. Pour la config standard 5 m / 1 000 m (44 échantillons) le lot
    # reste ≥ 45 000 points : surcoût de boucle négligeable.
    RAY_BUDGET_ELEMENTS = 2_000_000
    chunk_pts = max(1, RAY_BUDGET_ELEMENTS // max(1, int(distances.size)))

    temp_statuses = np.zeros(valid_idx.size, dtype=np.uint8)  # 0 = SUN

    for cs in range(0, valid_idx.size, chunk_pts):
        sl = slice(cs, min(cs + chunk_pts, valid_idx.size))
        observer_lats = obs_lats_all[sl]
        observer_lons = obs_lons_all[sl]
        rad_alts = np.deg2rad(sun_alts_all[sl])
        rad_azs = np.deg2rad(sun_azs_all[sl])
        cos_azs = np.cos(rad_azs)
        sin_azs = np.sin(rad_azs)

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
        ground_flat, object_flat = hgt_manager.get_ground_and_object_elevations_vec(
            ray_lats.ravel(), ray_lons.ravel())
        ground_profile = ground_flat.reshape(ray_lats.shape)
        object_heights = object_flat.reshape(ray_lats.shape)

        # Appliquer le shadow_mode à la SOURCE du ray-tracing
        if shadow_mode == 'relief':
            object_heights = np.zeros_like(object_heights)  # Ignorer toute végétation
        elif shadow_mode == 'vegetation':
            # Aplatir le terrain à l'altitude de l'observateur pour ignorer le relief
            ground_profile = np.full_like(ground_profile, observer_ground_elevs[:, None])

        obstacle_profile = ground_profile + object_heights

        # 7. Comparaison vectorisée (pour inclure RELIEF_VEG).
        # compute_ray_intersections_detailed pointe vers le fallback NumPy ou le
        # kernel Numba selon _try_load_numba — le rebind global fait l'aiguillage
        # (l'ancien if/else re-dupliquait le fallback inline).
        relief_is_blocking, veg_is_blocking = compute_ray_intersections_detailed(
            obstacle_profile, ground_profile, object_heights, ray_altitudes, TOLERANCE
        )

        # 8. Statuts du lot (codes uint8)
        chunk_statuses = np.zeros(observer_lats.size, dtype=np.uint8) # 0 = SUN
        chunk_statuses[veg_is_blocking] = 2 # VEGETATION
        chunk_statuses[relief_is_blocking] = 1 # RELIEF
        chunk_statuses[relief_is_blocking & veg_is_blocking] = 3 # RELIEF_VEG
        temp_statuses[sl] = chunk_statuses

        # 9. Empreintes d'ombre : premier point bloquant de chaque rayon
        all_rays_blocking_mask = obstacle_profile > (ray_altitudes + TOLERANCE)
        any_block_per_ray = np.any(all_rays_blocking_mask, axis=1)

        if np.any(any_block_per_ray):
            blocked_ray_indices = np.where(any_block_per_ray)[0]
            first_block_indices = np.argmax(all_rays_blocking_mask[blocked_ray_indices], axis=1)
            # Indices originaux dans le batch complet
            original_indices = valid_idx[sl][blocked_ray_indices]
            hit_lats_full[original_indices] = ray_lats[blocked_ray_indices, first_block_indices]
            hit_lons_full[original_indices] = ray_lons[blocked_ray_indices, first_block_indices]

    np.put(statuses, valid_idx, temp_statuses)

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
                 progresscallback=None, batch_size=256, solar_step_s=SOLAR_ROUND_SEC,
                 compute_shadows=True):

    # Charge numba à la demande (différé depuis l'import pour accélérer
    # l'ouverture de la GUI). Idempotent — no-op si déjà chargé.
    _try_load_numba()

    starttimesim = datetime.now()
    rawpoints = gpx_all_points(gpxobj)
    simpoints = rawpoints if direction == "CW" else rawpoints[::-1]
    totalpoints = len(simpoints)
    logfunc = hgtmanager.log

    # === (A) Pré-calcul elevations (déjà chez toi) ===

    alllats = np.array([p.latitude for p in simpoints], dtype=np.float64)
    alllons = np.array([p.longitude for p in simpoints], dtype=np.float64)
    cov_miss_before = hgtmanager.coverage_report()[0]
    allelevations = hgtmanager.get_ground_elevations_vec(alllats, alllons)
    trace_missing = hgtmanager.coverage_report()[0] - cov_miss_before
    if trace_missing > 0:
        # 0 m n'est pas une sentinelle fiable (altitude réelle possible) : on
        # s'appuie sur les compteurs de couverture des fournisseurs. Trace
        # majoritairement hors donnée → pentes, Tobler et ombres absurdes
        # garantis : on refuse au lieu de produire un résultat plausible en
        # apparence. Couverture partielle → avertissement explicite.
        if trace_missing * 2 >= totalpoints:
            raise MissingDataError(
                f"{trace_missing}/{totalpoints} points de trace sans donnée "
                f"altimétrique (tuiles manquantes ou zone hors couverture). "
                f"Choisissez une autre source DEM ou vérifiez la zone.")
        logfunc(f"⚠ {trace_missing}/{totalpoints} points de trace sans donnée "
                f"altimétrique : altitude 0 m utilisée (pente et durées "
                f"faussées localement).")

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
        # Fenêtre de lissage en distance : la pente entre deux points GPS
        # consécutifs (souvent 1-3 m) est dominée par le bruit
        # d'échantillonnage du MNT. On la mesure sur une sécante de `window_m`
        # mètres (idée reprise de colorer_pente : lissage EN DISTANCE,
        # insensible à la densité variable des points GPS).
        # Mode pente : fenêtre = analysis_resolution (choix utilisateur).
        # Mode ombre : fenêtre = résolution du MNT. En dessous de la taille de
        # cellule, la « pente » n'est que du bruit d'interpolation : sur SRTM
        # 30 m elle dépassait ±75 % par endroits et, via le plancher Tobler
        # 0,1 m/s, gonflait la durée du run témoin de 0:45 à 1:27. (En LiDAR
        # 0,5 m la fenêtre dégénère en sécante i-1/i+1, quasi point-à-point :
        # cohérent, la donnée y est fiable à cette échelle.)
        if compute_shadows:
            window_m = float(getattr(hgtmanager, "resolution", 0.0) or 0.0)
        else:
            window_m = float(getattr(hgtmanager, "analysis_resolution", 0.0) or 0.0)
        if window_m > 0.0:
            # Pipeline de colorer_pente (les deux étapes, fenêtre EN DISTANCE,
            # insensible à la densité des points GPS) :
            #  1. lisser l'ALTITUDE par moyenne glissante (lisser_en_distance) ;
            #  2. pente = SÉCANTE sur la fenêtre du profil lissé (pentes_en_distance).
            # Dériver un profil lissé par une sécante (et non point-à-point sur
            # ~10 m) donne un lissage MONOTONE : une fenêtre plus large réduit
            # toujours le bruit.
            cumdist = np.concatenate(([0.0], np.cumsum(seg_dists_m[:-1])))
            half = max(window_m / 2.0, 1.0)
            # 1. altitude lissée
            a1 = np.searchsorted(cumdist, cumdist - half, side='left')
            b1 = np.searchsorted(cumdist, cumdist + half, side='right')
            prefix = np.concatenate(([0.0], np.cumsum(allelevations)))
            elev_s = (prefix[b1] - prefix[a1]) / np.maximum(b1 - a1, 1)
            # 2. sécante sur la fenêtre ; repli i-1,i+1 si la fenêtre est plus
            # courte que l'espacement (sinon a==b -> pente nulle partout).
            a2 = np.clip(a1, 0, totalpoints - 1)
            b2 = np.clip(b1 - 1, 0, totalpoints - 1)
            idx = np.arange(totalpoints)
            collapsed = b2 <= a2
            a2 = np.where(collapsed, np.maximum(idx - 1, 0), a2)
            b2 = np.where(collapsed, np.minimum(idx + 1, totalpoints - 1), b2)
            dd = cumdist[b2] - cumdist[a2]
            slope_ratios = np.where(dd > 0.0,
                                    (elev_s[b2] - elev_s[a2]) / np.where(dd > 0.0, dd, 1.0),
                                    0.0)
        else:
            elev_diff = allelevations[1:] - allelevations[:-1]
            safe_dists = np.where(seg_dists_m[:-1] > 0.0, seg_dists_m[:-1], 1.0)
            slope_ratios[:-1] = np.where(seg_dists_m[:-1] > 0.0, elev_diff / safe_dists, 0.0)

    seg_slopes_percent = slope_ratios * 100.0

    # Tobler vectorisé : (6 * exp(-3.5 * |slope+0.05|)) / 3.6 [m/s]
    speeds_ms = (6.0 * np.exp(-3.5 * np.abs(slope_ratios + 0.05))) / 3.6
    # Plancher de vitesse 0,1 m/s appliqué à TOUT segment de distance > 0.
    # L'ancien code INVALIDAIT le segment (durée 0) dès que Tobler passait
    # sous 0,1 m/s (pente > ~+75 % ou < ~-85 %) : le randonneur « téléportait »
    # et toute l'horloge aval se décalait. Le lissage de pente ci-dessus rend
    # le cas rare (il faut une vraie pente extrême à l'échelle du MNT).
    seg_durs_s = np.where(seg_dists_m > 0.0,
                          seg_dists_m / np.maximum(speeds_ms, 0.1), 0.0)
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

    if not compute_shadows:
        # Analyse pente : le ray-tracing d'ombre est inutile — et coûteux,
        # c'est lui qui consomme MNH/végétation. Seuls servent le MNT le long
        # de la trace (pentes, déjà calculé en PASS 1) et les temps de parcours.
        allstatuses = ['SUN'] * totalpoints
        all_shadow_hits = [None] * totalpoints
    else:
        allstatuses = []
        all_shadow_hits = []
        # Mettre à jour le shadowmode dans hgtmanager avant le ray-tracing
        hgtmanager.shadowmode = shadowmode
        for i in range(0, totalpoints, batch_size):
            check_cancelled()
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
        "distnight": 0.0, "durnight": 0.0,
    }
    processeddata = []

    categorymap = {"RELIEF": "relief", "VEGETATION": "veg", "SUN": "sun",
                   "RELIEF_VEG": "relief_veg", "NIGHT": "night"}

    for i in range(totalpoints):
        p1 = simpoints[i]
        rawstatus = allstatuses[i]

        # Le filtrage est déjà fait à la source
        finalstatus = rawstatus

        dist = float(seg_dists_m[i])
        duration = float(seg_durs_s[i])

        # Les totaux couvrent TOUT le parcours ; la nuit est une catégorie à
        # part (distnight/durnight). L'ancien code excluait la nuit des
        # totaux : une rando entièrement nocturne sortait à 0 km / 0 s.
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
        logging.info("   Start: %s, End: %s",
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

        # _nearest_seg_with_param : fallback NumPy ou kernel Numba selon
        # _try_load_numba (rebind global — même pattern que wc_to_height et
        # compute_ray_intersections_detailed ; l'ancienne closure _nearest_numpy
        # dupliquait le fallback module-level).
        bi, bt = _nearest_seg_with_param(xs, ys, sx1, sy1, sx2, sy2)
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
    
    log_func(f"Grid: {width}x{height} pixels, resolution {res}m")
    
    
    # ✅ NOUVELLE LOGIQUE: Interpolation temporelle par segment (plus précise)
    trace_points_with_time = [item['point'] for item in processed_data]
    t_of_xy_vec = build_time_function_segmented(trace_points_with_time, transformer_l93)
    log_func("Per-segment temporal interpolation function created.")
    
    profile = {
        'driver': 'GTiff', 'dtype': 'uint8', 'nodata': 255,
        'width': width, 'height': height, 'count': 1,
        'crs': 'EPSG:2154', 'transform': transform, 'compress': 'lzw'
    }
    
    block_size = 256
    total_blocks = math.ceil(width / block_size) * math.ceil(height / block_size)
    processed_blocks = 0
    
    GUI_UPDATE_INTERVAL = max(1, total_blocks // 20)
    
    log_func(f"Processing {total_blocks} blocks with {num_workers} workers...")
    
    hgt_manager.shadowmode = shadow_mode

    cancelled = False
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
                if CANCEL_EVENT.is_set():
                    # Annule les blocs pas encore démarrés ; les num_workers
                    # blocs en cours finissent (le shutdown du with attend),
                    # leurs résultats sont ignorés.
                    for f in futures:
                        f.cancel()
                    cancelled = True
                    break
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

    if cancelled:
        # Carte partielle inutilisable : la purger avant de remonter.
        try:
            os.remove(out_tif)
        except OSError:
            pass
        raise CalculationCancelled("stop requested")

    # Rapport de couverture : les échantillons sans donnée ont reçu 0 m
    # (relief aplati → ombres sous-détectées). Rendre la dégradation VISIBLE ;
    # c'est le pendant côté carte du contrôle strict fait sur la trace dans
    # simulatehike.
    cov_miss, cov_tot = hgt_manager.coverage_report()
    if cov_tot > 0 and cov_miss > 0:
        log_func(f"⚠ Couverture altimétrique incomplète : {cov_miss}/{cov_tot} "
                 f"échantillons ({100.0 * cov_miss / cov_tot:.1f} %) sans donnée, "
                 f"altitude 0 m utilisée → ombres sous-détectées dans ces zones.")
    


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

# LUT code uint8 → RGBA : colorisation vectorisée en un coup (SHADOW_LUT[codes]),
# partagée par l'export KMZ et l'export MBTiles (rendu garanti identique).
SHADOW_LUT = np.zeros((256, 4), dtype=np.uint8)
for _code, _rgba in SHADOW_COLOR_MAP.items():
    SHADOW_LUT[_code] = _rgba


def geotiff_to_kml_groundoverlay(tif_path, kmz_output_path, log_func, existing_kml_obj=None, progress_callback=None):
    """
    Convertit un GeoTIFF de carte d'ombre en une image PNG colorisée
    et crée un fichier KML avec un GroundOverlay pour l'afficher.
    Utilise rasterio et Pillow pour éviter une dépendance à GDAL.
    """
    if not PIL_AVAILABLE:
        log_func("ERROR: Pillow is required for PNG conversion.")
        return None

    log_func("DEBUG: Starting GeoTIFF -> KML GroundOverlay conversion (with Pillow).")
    png_path = tif_path.replace(".tif", ".png")

    try:
        with rasterio.open(tif_path) as src:
            # Lire les données du raster et coloriser via la LUT partagée
            data = src.read(1)
            rgba = SHADOW_LUT[data]

            # Créer l'image avec Pillow et la sauvegarder
            img = Image.fromarray(rgba)
            img.save(png_path)
            log_func(f"DEBUG: GeoTIFF -> PNG conversion done: {png_path}")

    except Exception as e:
        log_func(f"ERROR: Cannot convert the GeoTIFF to PNG: {e}")
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
        log_func(f"DEBUG: KMZ GroundOverlay created: {kmz_output_path}")
        return kmz_output_path
    except Exception as e:
        log_func(f"ERROR: Cannot create the KMZ GroundOverlay: {e}")
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
        log_func("ERROR: Pillow required for MBTiles export.")
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

    # Colorisation via SHADOW_LUT (LUT partagée avec l'export KMZ).
    # Semi-transparence BAKÉE — l'utilisateur laisse l'opacité du calque à
    # 100 % côté app, sinon Locus recompose les tuiles et fait réapparaître
    # les coutures (cf. note de SHADOW_COLOR_MAP).
    # nodata (code 255) → (0,0,0,0) : hors emprise totalement transparent.
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
                        rgba = SHADOW_LUT[codes]   # (256, 256, 4)
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
        log_func(f"DEBUG: MBTiles overlay created: {mbtiles_path} "
                 f"(z{zoom_min}-{zoom_max}, {total} tuiles)")
        return mbtiles_path
    except Exception as e:
        log_func(f"ERROR: Cannot create the MBTiles: {e}")
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
    # Cap = azimut solaire TEL QUEL, même convention que le moteur de
    # ray-tracing (pysolar get_position : azimut Nord=0, horaire ; vérifié
    # empiriquement : ~180° au midi solaire). L'ancien `sun_az + 180.0`
    # (commenté « INVERSION CRUCIALE ») faisait monter le rayon dans la
    # direction ANTI-solaire : plein nord à midi.
    az_rad  = np.deg2rad(sun_az)

    dist = min(20000, max(4000, 2000 / np.tan(alt_rad)))

    dlat = (dist * np.cos(az_rad)) / m_lat
    dlon = (dist * np.sin(az_rad)) / m_lon

    lat2 = lat + dlat
    lon2 = lon + dlon

    # + d²/(2R) : même terme de courbure que le moteur (la surface terrestre
    # « tombe » le long du trajet, le rayon rectiligne gagne de l'altitude
    # apparente au-dessus du géoïde). Le signe négatif accompagnait
    # l'ancienne direction inversée.
    alt2 = (
        alt
        + dist * np.tan(alt_rad)
        + dist**2 / (2 * EARTH_RADIUS)
    )

    return lon2, lat2, alt2

def create_kml_file(original_path, processed_data, passage_interval_min=0, local_tz=None, hgt_manager=None, visualize_tiles=False, visualize_sun_rays=False, sun_ray_interval=20, analysis_type='ombre_soleil', show_slope_arrows=False):
    if not SIMPLEKML_AVAILABLE:
        logging.error("The 'simplekml' library is required to create KML files.")
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

                # Flèches de sens de parcours (chevron au milieu de chaque
                # segment coloré). Optionnel : la palette de pente est en valeur
                # ABSOLUE (ne distingue pas montée/descente), la flèche est donc
                # la seule indication de direction — mais décochée par défaut.
                if show_slope_arrows and len(coords) > 2:
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

                    # Flèches en clampToGround (cf. altitudemode ci-dessous) : le
                    # renderer plaque au sol et IGNORE la composante z. Inutile donc
                    # d'échantillonner l'altitude du terrain — c'était 1 appel PAR
                    # flèche, soit ~100 POST à l'API altimétrique en mode pente
                    # (les 103 s observées). z=0 est strictement équivalent à l'écran.
                    ls1 = kml.newlinestring(coords=[(mid_lon, mid_lat, 0), (end_lon1, end_lat1, 0)])
                    ls2 = kml.newlinestring(coords=[(mid_lon, mid_lat, 0), (end_lon2, end_lat2, 0)])
                    
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
                        logging.warning(f"IGN/LiDAR transformer not initialised for LiDAR tile {name}. Tile not shown.")
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
                        logging.warning("IGN transformer not initialised for IGN tiles.")
                
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
            logging.info(f"🌞 Creating {len(range(0, len(processed_data), sun_ray_interval))} sun rays (at the KML root)...")
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
                    logging.warning(f"Ray error at point {idx}: {e}")
                    continue
            
            logging.info(f"✓ {rays_created} sun rays created (at the KML root)")
            
        except Exception as e:
            logging.error(f"ERROR creating sun rays: {e}")
    
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
            logging.warning(f"Cannot create waypoints: {e}")

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
        log_output(f"FATAL ERROR: Impossible d'enregistrer le rapport de profilage dans {report_filename} : {e}")
        # Optionally re-raise or handle more gracefully
        # raise # No, just log
    
    return result

def run_gui_process(file_path, date_str, time_str, dem_source, analysis_resolution, max_distance, shadow_mode, direction, open_gpx_after_calc, tz_finder, output_default, log_func, progress_callback, args, batch_size, passage_interval_min, solar_step_s, visualize_tiles, generate_shadow_map=False, num_workers=4, margin_meters=500, visualize_sun_rays=False, sun_ray_interval=20, analysis_type='ombre_soleil', show_slope_arrows=False):

    try:

        progress_callback(0, "Démarrage du calcul...")



        start_dt_naive = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M")

        


        # L'analyse pente ne consomme AUCUNE donnée d'ombre : ni végétation
        # WorldCover (~1 GB/tuile), ni corridor solaire LiDAR (dizaines de
        # tuiles MNT+MNH) — sauf si la carte d'ombre est aussi demandée. Le
        # ray-tracing de la trace n'a de sens qu'en analyse ombre/soleil.
        compute_shadows = (analysis_type == 'ombre_soleil')
        # Mode pente = coloration de la trace uniquement. On force l'absence de
        # toute donnée d'ombre, même si « Générer carte d'ombre » était resté
        # coché : sinon on retéléchargerait tuiles LiDAR + végétation pour rien
        # (c'était le bug « la pente télécharge les tuiles »).
        if analysis_type == 'pente':
            generate_shadow_map = False
        want_shadow_data = compute_shadows or generate_shadow_map

        veg_manager = None
        # Le LiDAR HD inclut déjà la végétation (MNH), donc on n'active le
        # gestionnaire de végétation WorldCover que pour les autres sources.
        if want_shadow_data and dem_source != 'ign_lidar_hd' and not args.no_vegetation_shadow:

            veg_manager = VegetationManager(args.vegetation_dir, not args.no_download_vegetation, progress_callback=progress_callback)



        # args.hgt_dir / args.interpolation : options CLI enfin transmises
        # (elles étaient parsées mais 'HGT' et le défaut restaient codés en dur).
        hgt_manager = HGTDataManager(args.hgt_dir, veg_manager, dem_source,
                                     interpolation=args.interpolation,
                                     analysis_resolution=analysis_resolution,
                                     max_shadow_distance=max_distance,
                                     log_func=log_func, progress_callback=progress_callback,
                                     solar_step_s=solar_step_s)

        # utf-8-sig : tolère le BOM que certains éditeurs/exports posent en tête
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            gpx_raw = gpxpy.parse(f)

        points = gpx_all_points(gpx_raw)
        
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
                    log_func(f"Warning: cannot determine timezone for point {points[0].latitude},{points[0].longitude}. Using UTC.")
            except Exception as e:
                log_func(f"Warning: error determining timezone. Using UTC. Error: {e}")

        if analysis_type == 'pente' and dem_source.startswith('ign_'):
            # Mode pente + source IGN : altitude MNT via l'API altimétrique
            # Géoplateforme (RGE ALTI 1 m, requête de points) — zéro download de
            # tuile ni d'archive. Remplace le préchargement corridor (LiDAR) et
            # le download d'archive départementale RGE ALTI/BD ALTI (endpoint
            # mort depuis la migration IGN vers cartes.gouv.fr).
            hgt_manager.use_geopf_point_alti = True
            log_func("Mode pente : altitude MNT via l'API RGE ALTI 1 m "
                     "(Géoplateforme, points), sans téléchargement de tuile.")
        elif hgt_manager.source == 'ign_lidar_hd':
            if want_shadow_data:
                with log_phase("Préchargement tuiles LiDAR (corridor solaire)", log_func):
                    hgt_manager.prepare_lidar_data(points, start_dt_naive)
            else:
                # Pente seule : pas de préchargement du corridor solaire ; les
                # tuiles MNT sous la trace seront lazy-chargées par le calcul.
                log_func("Slope-only analysis: solar-corridor tile preload skipped "
                         "(MNT lazy-loaded along the track).")
        elif hgt_manager.wms_dem is not None:
            # RGE ALTI / BD ALTI en ombre : MNT via tuiles WMS paresseuses
            # (aucune archive à télécharger, endpoint .7z mort). Rien à préparer.
            log_func("Ombre RGE ALTI/BD ALTI : MNT via WMS Géoplateforme "
                     "(couche ELEVATION.ELEVATIONGRIDCOVERAGE, tuiles à la demande).")
        elif hgt_manager.source.startswith('ign_'):
             # Logique département pour BD ALTI...
            department = get_department_from_coords(points[0].latitude, points[0].longitude)
            if department:
                if hgt_manager.source == 'ign_bdalti_25m': hgt_manager.prepare_bdalti_data(department)
                elif hgt_manager.source == 'ign_rgealti_5m': hgt_manager.prepare_rgealti_data(department)
            else:
                log_func("⚠ Department not found. Cannot determine the department for IGN Alti sources.")
                return

        results, first_output_path = [], None
        directions_to_run = [("Sens Horaire", "CW"), ("Sens Anti-Horaire", "CCW")] if direction == 'both' else [(direction, direction.upper())]

        for label, code in directions_to_run:
            check_cancelled()

            # Cloner gpx_raw pour chaque simulation
            with open(file_path, 'r', encoding='utf-8-sig') as f_clone:
                gpx_clone = gpxpy.parse(f_clone)

            # Appel direct de simulatehike, le profiler est au-dessus
            _phase = "Analyse de la trace (pente)" if not compute_shadows else f"Ray-tracing ombre ({label})"
            with log_phase(_phase, log_func):
                data, stats = simulatehike(gpx_clone, start_dt_naive, hgt_manager,
                                           local_tz_for_trace, code, shadow_mode,
                                           progress_callback, batch_size=batch_size,
                                           solar_step_s=solar_step_s,
                                           compute_shadows=compute_shadows)
            
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
                analysis_type=analysis_type,
                show_slope_arrows=show_slope_arrows
            )

            shadow_map_tif_path = None # Initialiser ici
            # Étape 2: Décider de la sauvegarde (fusion ou KML seul)
            if generate_shadow_map:
                dem_name = "".join(filter(str.isalnum, dem_source.replace(" ", "")))
                timestamp = datetime.now().strftime("%H%M%S")
                shadow_map_name_base = f"{os.path.splitext(os.path.basename(file_path))[0]}_{formatted_date}_{formatted_time}_{dem_name}_{shadow_mode}_{code}_shadow_map"
                
                # Chemin pour le GeoTIFF
                shadow_map_tif_path = os.path.join(SHADOW_GPX_DIR, f"{shadow_map_name_base}_{timestamp}.tif")
                log_func(f"DEBUG: Shadow map GeoTIFF path: {shadow_map_tif_path}")

                
                # Générer le GeoTIFF. margin_meters transmis : le réglage GUI
                # « Marge bbox » était silencieusement ignoré (défaut 500 forcé).
                with log_phase("Calcul carte d'ombre (GeoTIFF)", log_func):
                    compute_shadow_geotiff(
                        data, hgt_manager, shadow_mode, float(analysis_resolution),
                        shadow_map_tif_path, progress_callback,
                        num_workers=num_workers, margin_meters=margin_meters
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
                        log_func(f"WARNING: track KML export skipped: {e_tr}")

                # Chemin pour le KMZ final
                kmz_output_path = os.path.join(SHADOW_GPX_DIR, f"{shadow_map_name_base}.kmz")

                with log_phase("Export KMZ + MBTiles", log_func):
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
                        log_func(f"WARNING: MBTiles export skipped: {e_mbt}")


            else:
                # Sauvegarder le KML de la trace uniquement
                dem_name_raw = HGTDataManager.SOURCES.get(dem_source, {}).get('name', dem_source)
                dem_name = "".join(filter(str.isalnum, dem_name_raw.replace(" ", "")))
                out_name = f"{os.path.splitext(os.path.basename(file_path))[0]}_{formatted_date}_{formatted_time}_{dem_name}_{analysis_type}_{code}.kml"
                out_path = os.path.join(SHADOW_GPX_DIR, out_name)
                
                if trace_kml_obj:
                    trace_kml_obj.save(out_path)
                    log_func(f"   ✓ Track KML file created: {out_name}")
                
                if first_output_path is None: first_output_path = out_path

            tot_dur = stats['totaldur']
            row = {
                "Fichier": out_name, "Départ": start_dt_naive,
                "Dist Totale (km)": round(stats['totaldist']/1000, 2), "Durée Totale": str(timedelta(seconds=int(tot_dur))),
                "% Ensoleillé": round(stats['dursun']/tot_dur*100, 1) if tot_dur else 0,
                "% Ombre Relief": round(stats['durrelief']/tot_dur*100, 1) if tot_dur else 0,
                "% Ombre Végét.": round(stats['durveg']/tot_dur*100, 1) if tot_dur else 0,
                # RELIEF_VEG était accumulé mais jamais exporté (les % ne
                # sommaient pas à 100) ; la nuit compte désormais dans les
                # totaux, sa part doit donc être visible elle aussi.
                "% Ombre R+V": round(stats['durrelief_veg']/tot_dur*100, 1) if tot_dur else 0,
                "% Nuit": round(stats['durnight']/tot_dur*100, 1) if tot_dur else 0,
            }
            if not compute_shadows:
                # Mode pente : pas de ray-tracing, un « 100 % ensoleillé »
                # serait trompeur → colonnes d'ombre laissées vides.
                row["% Ensoleillé"] = row["% Ombre Relief"] = row["% Ombre Végét."] = ""
                row["% Ombre R+V"] = row["% Nuit"] = ""
            results.append(row)

        if results:
            import pandas as pd  # import différé (charge ~0.75 s)
            df = pd.DataFrame(results)
            output_is_empty = not os.path.exists(output_default) or os.stat(output_default).st_size == 0
            df.to_csv(output_default, mode='a', header=output_is_empty, index=False, encoding='utf-8')
            
            if open_gpx_after_calc and first_output_path and os.path.exists(first_output_path):
                log_func(f"✓ Processing complete. Opening {first_output_path}")
                open_file_default_app(first_output_path)
            else:
                log_func(f"✓ Processing complete. Output: {output_default}")

    except CalculationCancelled:
        log_func("⚠ Calcul arrêté par l'utilisateur.")
        raise
    except Exception as e:
        traceback.print_exc()
        log_func(f"ERROR: {e}")
        raise

def show_form(args, tz_finder, output_default):
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

    # Alignée sur --version (l'ancien "v28.5" était un compteur interne
    # divergent de la version publiée).
    APP_VERSION = "v1.3.3"
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

    # 'i18n' = clé de traduction côté GUI (cf. dico I18N JS). buildLegend pose un
    # data-i18n sur le label → re-traduit automatiquement au toggle FR/EN.
    KML_LEGEND = [
        {'name': 'Soleil',       'color': '#FFFF00', 'description': 'Ensoleillé',        'i18n': 'leg.sun'},
        {'name': 'Ombre Relief', 'color': '#A0A0A0', 'description': 'Terrain/Montagne',  'i18n': 'leg.shaderelief'},
        {'name': 'Ombre Vég.',   'color': '#009900', 'description': 'Végétation haute',  'i18n': 'leg.shadeveg'},
        {'name': 'Ombre R+V',    'color': '#A52A15', 'description': 'Relief + Vég.',     'i18n': 'leg.shaderv'},
    ]
    SLOPE_LEGEND = [
        {'name': '0-5%',   'color': '#00FF00', 'description': 'Plat ou quasi-plat', 'i18n': 'leg.s05'},
        {'name': '5-10%',  'color': '#FFFF00', 'description': 'Pente faible',       'i18n': 'leg.s510'},
        {'name': '10-20%', 'color': '#FF8000', 'description': 'Pente moyenne',      'i18n': 'leg.s1020'},
        {'name': '20-30%', 'color': '#FF0000', 'description': 'Pente forte',        'i18n': 'leg.s2030'},
        {'name': '> 30%',  'color': '#8B0000', 'description': 'Pente très forte',   'i18n': 'leg.s30'},
    ]
    TILE_LEGEND = [
        {'name': 'Vert',  'color': '#00FF00', 'description': 'Tuile utilisée (ray-tracing)', 'i18n': 'leg.tgreen'},
        {'name': 'Bleu',  'color': '#0000FF', 'description': 'Tuile en RAM (cache LRU)',     'i18n': 'leg.tblue'},
        {'name': 'Jaune', 'color': '#FFFF00', 'description': 'Tuile sur disque (pas en RAM)','i18n': 'leg.tyellow'},
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
        'show_slope_arrows': bool(config.get('show_slope_arrows', False)),
    }

    class Api:
        # Le calcul tourne dans un SOUS-PROCESSUS (ce programme relancé en mode
        # headless), tué net par stop() → bouton Arrêter = kill immédiat. Modèle
        # identique au jumeau lidar2map (subprocess + _kill_tree + thread lecteur
        # du stdout). Un thread Python ne se tue pas ; un process, si.
        def __init__(self):
            self._proc = None
            self._reader_t = None
            self._stopped = False        # True entre le clic Arrêter et la mort du child
            self._done = False
            self._retcode = None
            self._last_error = ""
            self._progress = {"value": 0, "text": "En attente..."}
            self._t_launch = None
            self._cfg_launch = None
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

        def set_lang(self, code):
            """Persiste l'override manuel de langue de l'UI (toggle FR/EN)."""
            return {"ok": save_lang(code)}

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
                logging.warning(f"pick_gpx error: {e}")
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
            """Arrêt IMMÉDIAT : kill de tout l'arbre du sous-processus. Pas de
            grâce (contrairement à lidar2map) : les écritures de cache sont
            atomiques (.part + rename), donc un kill sec ne corrompt rien. Le
            thread lecteur pose _done quand le pipe se ferme."""
            proc = self._proc
            if not (proc and proc.poll() is None):
                return
            self._stopped = True
            log_queue.put({"line": "\n■ Arrêt demandé - calcul tué immédiatement.\n",
                           "tag": "warn"})
            _kill_process_tree(proc)

        def _headless_cmd(self, c):
            """Construit la ligne de commande headless équivalente à la config GUI."""
            cmd = _headless_base_cmd() + [
                "--gpx", c['gpx_file'],
                "--date", c['date'],
                "--time", c['time'],
                "--dem-source", c['dem_source'],
                "--analysis-resolution", str(c['analysis_resolution']),
                "--max-shadow-distance", str(c['max_distance']),
                "--shadow-mode", c['shadow_mode'],
                "--direction", c['direction'],
                "--analysis-type", c['analysis_type'],
                "--batch-size", str(c['batch_size']),
                "--solar-step-s", str(c['solar_step_s']),
                "--num-workers", str(c['num_workers']),
                "--margin-meters", str(c['margin_meters']),
                "--sun-ray-interval", str(c['sun_ray_interval']),
                "--passage-interval-min", str(c['passage_interval_min']),
                "--output", args.output,
                "--hgt-dir", args.hgt_dir,
                "--vegetation-dir", args.vegetation_dir,
                "--interpolation", args.interpolation,
            ]
            if c.get('open_gpx'):            cmd.append("--open")
            if c.get('visualize_tiles'):     cmd.append("--visualize-tiles")
            if c.get('generate_shadow_map'): cmd.append("--generate-shadow-map")
            if c.get('visualize_sun_rays'):  cmd.append("--visualize-sun-rays")
            if c.get('show_slope_arrows'):   cmd.append("--show-slope-arrows")
            if getattr(args, 'no_download_vegetation', False): cmd.append("--no-download-vegetation")
            if getattr(args, 'no_vegetation_shadow', False):   cmd.append("--no-vegetation-shadow")
            if getattr(args, 'profile', False):                cmd.append("--profile")
            return cmd

        def launch(self, cfg):
            proc = self._proc
            if proc and proc.poll() is None:
                if not self._stopped:
                    return {"error": "Un calcul est déjà en cours."}
                # Arrêter puis Lancer = intention sans ambiguïté : on tue
                # immédiatement l'ancien run et on attend brièvement sa mort.
                _kill_process_tree(proc)
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    return {"error": "Arrêt encore en cours, réessayez dans un instant."}
            # Laisser le thread lecteur de l'ANCIEN run finir (son finally pose
            # _done=True et écraserait le _done=False du nouveau run). Le pipe
            # étant clos par la mort du process, il sort en quelques ms.
            if self._reader_t and self._reader_t.is_alive():
                self._reader_t.join(timeout=5)

            f = (cfg.get("gpx_file") or "").strip()
            d = (cfg.get("date") or "").strip()
            t = (cfg.get("time") or "").strip()
            if not (f and d and t):
                return {"error": "Veuillez spécifier un fichier GPX, une date et une heure."}
            if not os.path.exists(f):
                return {"error": f"GPX file not found: {f}"}

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
                    'show_slope_arrows': bool(cfg.get('show_slope_arrows', False)),
                }
                save_config(new_config)
            except Exception as e:
                return {"error": f"Erreur paramètres : {e}"}

            self._stopped = False
            self._done = False
            self._retcode = None
            self._last_error = ""
            self._progress = {"value": 0, "text": "Starting..."}
            self._t_launch = datetime.now()
            self._cfg_launch = new_config
            while not log_queue.empty():
                try:
                    log_queue.get_nowait()
                except queue.Empty:
                    break

            cmd = self._headless_cmd(new_config)
            env = os.environ.copy()
            env["GPXSOLAR_CHILD"] = "1"          # bascule le child en canal ndjson
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"    # accents propres dans le pipe
            log_queue.put({"line": "$ " + " ".join(str(x) for x in cmd) + "\n\n", "tag": "dim"})
            try:
                if os.name == "nt":
                    # CREATE_NO_WINDOW : pas de console qui clignote (utile en dev,
                    # où le child est python.exe). Le stdout part dans notre pipe.
                    self._proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace", bufsize=1,
                        env=env, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    # start_new_session : groupe de process dédié pour killpg.
                    self._proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace", bufsize=1,
                        env=env, start_new_session=True)
            except Exception as e:
                self._done = True
                self._retcode = 1
                self._last_error = f"Lancement impossible : {e}"
                return {"error": self._last_error}

            self._reader_t = threading.Thread(target=self._pump, daemon=True)
            self._reader_t.start()
            return {"ok": True}

        def _consume_line(self, line):
            """Une ligne ndjson du child → item de log ou mise à jour de la barre."""
            try:
                obj = json.loads(line)
            except Exception:
                # Ligne non-JSON (traceback brut, print direct d'une lib) :
                # l'afficher telle quelle plutôt que la perdre.
                is_err = ("Error" in line or "Traceback" in line or "error:" in line)
                log_queue.put({"line": line + "\n", "tag": "err" if is_err else "ok"})
                return
            if not isinstance(obj, dict):
                return
            if "progress" in obj:
                pr = obj["progress"] or {}
                try:
                    self._progress = {"value": float(pr.get("value", 0)),
                                      "text": str(pr.get("text", ""))}
                except Exception:
                    pass
            elif "line" in obj:
                tag = obj.get("tag", "ok")
                if tag == "err":
                    self._last_error = obj["line"].strip()
                log_queue.put({"line": obj["line"], "tag": tag})

        def _pump(self):
            """Thread lecteur : draine le stdout ndjson du child jusqu'à EOF,
            puis finalise l'état (code de sortie, historique)."""
            proc = self._proc
            try:
                for line in proc.stdout:
                    line = line.rstrip("\r\n")
                    if line:
                        self._consume_line(line)
            except Exception:
                pass
            try:
                rc = proc.wait()
            except Exception:
                rc = -1
            if self._stopped:
                # 130 = convention SIGINT ; le JS l'affiche « Arrêté ».
                self._retcode = 130
                self._progress = {"value": 100, "text": "Stopped"}
                log_queue.put({"line": "\n■ Calcul arrêté.\n", "tag": "warn"})
            elif rc == 0:
                self._retcode = 0
                self._progress = {"value": 100, "text": "Done"}
                log_queue.put({"line": "\n✓ Calcul terminé.\n", "tag": "ok"})
                try:
                    duree = (datetime.now() - self._t_launch).total_seconds()
                    save_history(self._cfg_launch, duree, args.output)
                    log_queue.put({"line": f"  Historique enregistré ({HISTORY_FILE}).\n", "tag": "ok"})
                except Exception as he:
                    log_queue.put({"line": f"  Historique non enregistré : {he}\n", "tag": "warn"})
            else:
                self._retcode = rc if rc else 1
                if not self._last_error:
                    self._last_error = f"Le calcul s'est terminé avec le code {self._retcode}."
                log_queue.put({"line": f"\n✗ Échec (code {self._retcode}).\n", "tag": "err"})
            self._done = True

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
        "version": APP_VERSION,
        "historique": load_history(),
        "lang": load_lang(),   # None = auto-détection JS (navigator.language)
        # (texte d'aide déplacé dans le dico I18N JS — clé help.body)
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

    def _au_close():
        """Fermeture de la fenêtre : tuer un calcul en cours, puis sortie
        INCONDITIONNELLE via os._exit(0). Sans ce _exit, le process GUI survit à
        la fenêtre sous Qt/QtWebEngine (threads/QtWebEngineProcess qui traînent),
        et en dev le parent bloqué sur subprocess.run(venv) laisse le terminal
        occupé sur les lignes de bootstrap. Les écritures critiques (config,
        historique) sont atomiques et déjà faites, donc _exit est sûr. Modèle
        identique au jumeau lidar2map."""
        try:
            proc = getattr(api, "_process", None) or getattr(api, "_proc", None)
            if proc and proc.poll() is None:
                api.stop()
                try:
                    proc.wait(timeout=8)
                except Exception:
                    pass
        except Exception:
            pass
        os._exit(0)

    win.events.closed += _au_close
    # debug=True -> DevTools accessibles (clic droit -> Inspecter / F12). Via --debug.
    webview.start(debug=bool(getattr(args, "debug", False)))
    # Filet : si l'événement `closed` n'a pas été délivré mais que start() rend
    # la main, on repasse par le même chemin d'extinction (os._exit).
    _au_close()


def _build_gpxsolar_html():
    """Assemble le HTML du formulaire pywebview depuis gui/ : index.html +
    style.css + app.js, réunis via des sentinelles d'insertion. Le front est
    séparé du .py (comme le jumeau lidar2map) pour la lisibilité et le tooling
    (coloration, lint, diffs propres). La sentinelle /*__INIT_DATA__*/ reste
    dans index.html : show_form y injecte les données initiales après coup."""
    bases = []
    _mp = getattr(sys, "_MEIPASS", None)
    if _mp:
        bases.append(_mp)                                   # frozen : _internal/
    if "__file__" in globals():
        bases.append(os.path.dirname(os.path.abspath(__file__)))  # source
    bases.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    gui_dir = next((os.path.join(b, "gui") for b in bases
                    if os.path.exists(os.path.join(b, "gui", "index.html"))), None)
    if gui_dir is None:
        raise RuntimeError("GUI introuvable : gui/index.html absent "
                           f"(assets non bundlés ?). Cherché dans : {bases}")

    def _read(name):
        with open(os.path.join(gui_dir, name), encoding="utf-8") as fh:
            return fh.read()

    html = _read("index.html")
    html = html.replace("/*__GPXSOLAR_CSS__*/", _read("style.css"))
    html = html.replace("//__GPXSOLAR_JS__", _read("app.js"))
    return html


def run_headless(args, tz_finder):
    """Calcul en ligne de commande (sans GUI), même méthode que lidar2map :
    déclenché dès qu'un argument est passé. Requiert --gpx, --date, --time.
    Appelle directement run_gui_process (le moteur de calcul, indépendant de
    pywebview) puis retourne un code de sortie (0 = succès)."""
    if not args.gpx:
        logging.error("Command-line mode: --gpx is required (with --date "
                      "JJ/MM/AAAA et --time HH:MM). Lancez sans argument pour "
                      "ouvrir l'interface graphique.")
        return 2
    if not (args.date and args.time):
        logging.error("--gpx also requires --date (DD/MM/YYYY) and --time (HH:MM).")
        return 2
    if not os.path.exists(args.gpx):
        logging.error(f"GPX file not found: {args.gpx}")
        return 2

    def log_func(msg):
        logging.info(msg)

    _gui_child = os.environ.get("GPXSOLAR_CHILD") == "1"
    _last = {"pct": -1}
    def progress_cb(value, text=""):
        # Piloté par la GUI : progression structurée (ndjson) pour la barre du
        # parent, sans polluer le log. En terminal : ligne [ nn%] classique.
        if _gui_child:
            try:
                _gui_ipc_emit({"progress": {"value": float(value), "text": str(text)}})
            except Exception:
                pass
            return
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
            bool(args.show_slope_arrows),
        )
    except Exception as e:
        logging.error(f"FATAL ERROR: {e}")
        return 1
    logging.info(f"✓ Computation finished in {(datetime.now() - t0).total_seconds():.0f}s. "
                 f"Sorties dans {SHADOW_GPX_DIR} ; CSV : {args.output}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="GPX Solar Shadow Analyzer (LiDAR integrated)", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--version', action='version', version='gpxsolar 1.3.3 (2026-07)')
    parser.add_argument('--output', default='analyse_solaire.csv', help='Output CSV file')
    parser.add_argument('--hgt-dir', default='HGT', help='Directory for HGT files (for SRTM/Copernicus)')
    parser.add_argument('--dem-source', default='srtm1', choices=list(HGTDataManager.SOURCES.keys()), help='Default DEM source')
    # 'cubic' retiré des choices : accepté mais jamais implémenté (retombait
    # silencieusement sur nearest).
    parser.add_argument('--interpolation', default='bilinear', choices=['nearest', 'bilinear'], help='Interpolation method')
    parser.add_argument('--analysis-resolution', type=float, default=5.0, help='Analysis resolution for shadow computation (in metres)')
    parser.add_argument('--vegetation-dir', default='WorldCover', help='WorldCover directory')
    parser.add_argument('--passage-interval-min', type=int, default=0, help='Interval in minutes to create waypoints in the KML (0=none)')
    parser.add_argument('--no-download-vegetation', action='store_true', help='Disable automatic vegetation download')
    parser.add_argument('--no-vegetation-shadow', action='store_true', help='Fully disable vegetation shadow detection')
    parser.add_argument('--max-shadow-distance', type=float, default=1000.0,
                       help='Maximum shadow detection distance (in metres, default: 1000)')
    parser.add_argument('--profile', action='store_true', help='Enable performance profiling.')
    parser.add_argument('--temp-dir', type=str, default=tempfile.gettempdir(), help='Temporary directory for profiling reports.')
    parser.add_argument('--debug', action='store_true', help='Open the pywebview DevTools (right-click -> Inspect / F12) to see the JS console and the bridge.')

    # --- Mode ligne de commande (headless), même méthode que lidar2map ---------
    # Sans argument -> GUI. Dès qu'un argument est passé, on bascule en mode CLI
    # (calcul direct sans fenêtre). Le calcul a besoin de --gpx + --date + --time.
    grp_cli = parser.add_argument_group(
        'Command-line mode (headless)',
        "Passing --gpx (with --date and --time) runs the computation without a "
        "GUI. Handy for scripting / server / reproducing a render.")
    grp_cli.add_argument('--gpx', metavar='PATH', default=None,
                         help='GPX file to analyse. Its presence triggers CLI mode.')
    grp_cli.add_argument('--date', metavar='DD/MM/YYYY', default=None,
                         help='Start date (e.g. 21/06/2024).')
    grp_cli.add_argument('--time', metavar='HH:MM', default=None,
                         help='Start time (e.g. 09:00).')
    grp_cli.add_argument('--shadow-mode', choices=['relief', 'vegetation', 'both'],
                         default='both', help='Simulated shadow type (default: both).')
    grp_cli.add_argument('--direction', choices=['CW', 'CCW', 'both'], default='both',
                         help='Simulated travel direction (default: both).')
    grp_cli.add_argument('--analysis-type', choices=['ombre_soleil', 'pente'],
                         default='ombre_soleil', help='Analysis type (default: ombre_soleil).')
    grp_cli.add_argument('--visualize-tiles', action='store_true',
                         help='Draw the DEM tiles used, in the KML.')
    grp_cli.add_argument('--generate-shadow-map', action='store_true',
                         help='Generate the raster shadow map (basemap) as KMZ.')
    grp_cli.add_argument('--visualize-sun-rays', action='store_true',
                         help='Draw the simulated sun rays in the KML.')
    grp_cli.add_argument('--show-slope-arrows', action='store_true',
                         help='Draw travel-direction arrows on the slope-coloured KML.')
    grp_cli.add_argument('--sun-ray-interval', type=int, default=20,
                         help='Interval between sun rays (default: 20).')
    grp_cli.add_argument('--batch-size', type=int, default=256,
                         help='Computation batch size (default: 256).')
    grp_cli.add_argument('--solar-step-s', type=int, default=60,
                         help='Sun time step in seconds (default: 60).')
    grp_cli.add_argument('--num-workers', type=int, default=DEFAULT_NUM_WORKERS,
                         help=f'Parallel workers for the shadow map (default: {DEFAULT_NUM_WORKERS} = number of cores).')
    grp_cli.add_argument('--margin-meters', type=int, default=500,
                         help='Margin around the track in metres (default: 500).')
    grp_cli.add_argument('--open', action='store_true',
                         help='Open the result when done (Windows only).')

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

    # Mode « enfant de la GUI » (sous-processus tué net par le bouton Arrêter) :
    # rerouter tout le logging vers le canal ndjson lu par le parent.
    if os.environ.get("GPXSOLAR_CHILD") == "1":
        _install_gui_ipc_logging()

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
                logging.info("Loading TimezoneFinder (~2.5 s: module + data)...")
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
        show_form(args, tz_finder, args.output)
    else:
        sys.exit(run_headless(args, tz_finder))




if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"UNEXPECTED FATAL ERROR: {e}")
        traceback.print_exc()
        input("\nAppuyez sur Entrée pour fermer...")