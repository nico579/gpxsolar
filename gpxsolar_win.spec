# -*- mode: python ; coding: utf-8 -*-
"""
Spec PyInstaller pour gpxsolar — Windows & Linux, onedir.

Usage :
    %USERPROFILE%\\.gpxsolar\\venv\\Scripts\\pyinstaller.exe gpxsolar_win.spec --clean --noconfirm   (Windows)
    ~/.gpxsolar/venv/bin/pyinstaller gpxsolar_win.spec --clean --noconfirm                            (Linux)

Résultat :
    dist_onedir/gpxsolar/gpxsolar(.exe)
    dist_onedir/gpxsolar/_internal/
        gpxsolar.py          (livré EN CLAIR → maj sans rebuild via update_app.py)
        pyproj/, rasterio/, ...

Architecture (miroir lidar2map) :
  - Entry point = _loader.py (ne change jamais), qui exécute _internal/gpxsolar.py.
  - 2 passes Analysis : passe 1 détecte les imports de gpxsolar.py, passe 2
    construit depuis _loader.py. Fusion des TOC après coup.
  - Ce build onedir est ensuite zippé en bundle (gpxsolar_win_build.ps1 /
    gpxsolar_linux_build.sh), bundle placé À CÔTÉ du launcher (pas embarqué).

Cette spec sert AUSSI pour Linux (le nom _win est trompeur — PyInstaller
produit un ELF sous Linux) :
  Windows : pywebview -> WinForms / Edge WebView2 (pas de Qt)
  Linux   : pywebview -> PyQt6 + WebEngine (seul backend pip viable)
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    collect_dynamic_libs,
    collect_all,
)

IS_LINUX = sys.platform.startswith("linux")

ONEFILE = False
CONSOLE = True
NAME    = "gpxsolar"

SRC = Path(SPECPATH)

datas         = []
binaries      = []
hiddenimports = []

# ── pyproj : proj.db indispensable pour les transformations CRS ───────────────
datas         += collect_data_files("pyproj")
hiddenimports += collect_submodules("pyproj")
hiddenimports += [
    "pyproj._compat", "pyproj.crs._cf1x8", "pyproj.transformer",
]

# ── rasterio : drivers GDAL + données auxiliaires ─────────────────────────────
datas         += collect_data_files("rasterio")
binaries      += collect_dynamic_libs("rasterio")
hiddenimports += collect_submodules("rasterio")
hiddenimports += [
    "rasterio._features", "rasterio._io", "rasterio._warp",
    "rasterio.sample", "rasterio.vrt", "rasterio.windows",
    "rasterio.warp", "rasterio.transform", "rasterio.enums",
    "rasterio.control", "rasterio.crs",
]

# ── shapely : libgeos ─────────────────────────────────────────────────────────
binaries      += collect_dynamic_libs("shapely")
hiddenimports += ["shapely.geometry", "shapely.strtree", "shapely.ops"]

# ── pysolar : sous-modules pas tous détectés automatiquement ─────────────────
hiddenimports += collect_submodules("pysolar")
hiddenimports += [
    "pysolar.solartime", "pysolar.solar",
    "pysolar.tzinfo_check", "pysolar.constants",
]

# ── pywebview : backend Qt forcé (Windows ET Linux) ──────────────────────────
# Cette spec sert Windows et Linux ; les deux utilisent désormais le backend Qt
# (PyQt6 + QtWebEngine). Sous Windows ça remplace WinForms/WebView2+pythonnet
# (régression pythonnet 3.1.0 + freezes WinForms) -> plus de couche .NET, moteur
# Chromium identique sur les 3 OS.
datas         += collect_data_files("webview")
hiddenimports += collect_submodules("webview")
for _lib in ("PyQt6", "qtpy"):
    try:
        d, b, h = collect_all(_lib)
        datas += d; binaries += b; hiddenimports += h
    except Exception as _e:
        print(f"  [WARN] collect_all({_lib}) a échoué : {_e}")
hiddenimports += [
    "webview.platforms.qt",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebChannel",
]

# ── simplekml : templates XML embarqués ───────────────────────────────────────
datas += collect_data_files("simplekml")

# ── srtm.py : data files (rarement présents, par sécurité) ───────────────────
try:
    datas += collect_data_files("srtm")
except Exception:
    pass

# ── timezonefinder : fichiers de données (.bin) ──────────────────────────────
datas += collect_data_files("timezonefinder")

# ── numba + llvmlite (accélération JIT — lazy) ───────────────────────────────
try:
    hiddenimports += collect_submodules("numba")
    binaries      += collect_dynamic_libs("numba")
    datas         += collect_data_files("numba")
    binaries      += collect_dynamic_libs("llvmlite")
    datas         += collect_data_files("llvmlite")
except Exception:
    pass

# ── pandas : moteurs C ────────────────────────────────────────────────────────
hiddenimports += [
    "pandas._libs.tslibs.np_datetime",
    "pandas._libs.tslibs.nattype",
]

# ── gpxpy : parseur XML interne ───────────────────────────────────────────────
hiddenimports += ["gpxpy.geo", "gpxpy.parser"]

# ── py7zr (archives IGN BDALTI/RGEALTI — lazy) ───────────────────────────────
try:
    d, b, h = collect_all("py7zr")
    datas += d; binaries += b; hiddenimports += h
except Exception:
    pass

# ── PIL : codecs image (PNG pour overlay KML) ─────────────────────────────────
hiddenimports += [
    "PIL.Image", "PIL.PngImagePlugin", "PIL.JpegImagePlugin",
]

# ── certifi : bundle CA à jour (fix SSL Windows 11 / macOS) ──────────────────
datas         += collect_data_files("certifi")
hiddenimports += ["urllib3", "charset_normalizer", "idna", "certifi"]

# ── excludes ──────────────────────────────────────────────────────────────────
_excludes = [
    "tkinter", "matplotlib",
    "scipy",                                  # gpxsolar n'utilise pas scipy
    "test", "unittest", "pydoc_data",
    "IPython", "jupyter",
]
# Backend Qt sur Windows+Linux : on garde PyQt6, on exclut WinForms/Cocoa et
# toute la couche .NET (plus utilisée), ainsi que les autres bindings Qt.
_excludes += ["webview.platforms.winforms", "webview.platforms.cocoa",
              "clr", "clr_loader", "clr_loader.netfx", "pythonnet",
              "PyQt5", "PySide2", "PySide6"]

# ── Runtime hook : forcer PYWEBVIEW_GUI=qt + chemins QtWebEngine ─────────────
# S'applique Windows ET Linux (backend Qt sur les deux). Les gardes os.path
# rendent les chemins inexistants inoffensifs sur l'OS qui ne les a pas.
_hook = SRC / "build" / "_runtime_hook_qt.py"
_hook.parent.mkdir(parents=True, exist_ok=True)
_hook.write_text("""\
import os, sys
_base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.executable)))
os.environ.setdefault('PYWEBVIEW_GUI', 'qt')
_plugins = os.path.join(_base, 'PyQt6', 'Qt6', 'plugins')
if os.path.isdir(_plugins):
    os.environ.setdefault('QT_PLUGIN_PATH', _plugins)
for _cand in (
    os.path.join(_base, 'PyQt6', 'Qt6', 'bin', 'QtWebEngineProcess.exe'),   # Windows
    os.path.join(_base, 'PyQt6', 'Qt6', 'libexec', 'QtWebEngineProcess'),   # Linux
    os.path.join(_base, 'PyQt6', 'QtWebEngineProcess'),
):
    if os.path.isfile(_cand):
        os.environ.setdefault('QTWEBENGINEPROCESS_PATH', _cand)
        break
_res = os.path.join(_base, 'PyQt6', 'Qt6', 'resources')
if os.path.isdir(_res):
    os.environ.setdefault('QTWEBENGINE_RESOURCES_PATH', _res)
_loc = os.path.join(_base, 'PyQt6', 'Qt6', 'translations')
if os.path.isdir(_loc):
    os.environ.setdefault('QTWEBENGINE_LOCALES_PATH',
                          os.path.join(_loc, 'qtwebengine_locales'))
try:
    import certifi
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
    os.environ.setdefault('REQUESTS_CA_BUNDLE', certifi.where())
except Exception:
    pass
""")
_runtime_hooks = [str(_hook)]

# ── 2 passes PyInstaller ─────────────────────────────────────────────────────
# gpxsolar.py est livré en data (fichier texte) → PyInstaller ne l'analyse pas
# via _loader.py. On lance une Analysis séparée sur gpxsolar.py pour détecter
# tous ses imports, puis on fusionne les TOC APRÈS les deux analyses.
# Important : les binaries/datas d'ENTRÉE sont des 2-tuples ; les TOC de SORTIE
# (a.binaries, a.datas, a.pure) sont des 3-tuples → fusion après coup.

# Passe 1 : analyse de gpxsolar.py pour la détection des imports
a_detect = Analysis(
    ["gpxsolar.py"],
    pathex=[], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
    runtime_hooks=_runtime_hooks, excludes=_excludes, noarchive=False, optimize=0,
)

# Passe 2 : build réel depuis _loader.py (même entrées 2-tuples)
a = Analysis(
    ["_loader.py"],
    pathex=[], binaries=binaries,
    datas=datas + [("gpxsolar.py", ".")],   # gpxsolar.py en clair dans _internal/
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
    runtime_hooks=_runtime_hooks, excludes=_excludes, noarchive=False, optimize=0,
)

# Fusion des TOC de sortie (3-tuples) — après les deux analyses
a.binaries += a_detect.binaries
a.datas    += a_detect.datas
a.pure     += [e for e in a_detect.pure if not e[0].startswith("gpxsolar")]

pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name=NAME, debug=False,
        bootloader_ignore_signals=False, strip=False, upx=False,
        upx_exclude=[], runtime_tmpdir=None, console=CONSOLE,
        disable_windowed_traceback=False, argv_emulation=False,
        target_arch=None, codesign_identity=None, entitlements_file=None,
        icon=None,
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True, name=NAME, debug=False,
        bootloader_ignore_signals=False, strip=False, upx=False,
        console=CONSOLE, disable_windowed_traceback=False,
        argv_emulation=False, target_arch=None,
        codesign_identity=None, entitlements_file=None, icon=None,
    )
    coll = COLLECT(
        exe, a.binaries, a.datas,
        strip=False, upx=False, upx_exclude=[], name=NAME,
    )
