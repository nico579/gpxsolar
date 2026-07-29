# -*- mode: python ; coding: utf-8 -*-
"""
Spec PyInstaller pour gpxsolar — macOS ARM64, onedir.

Usage :
    ~/.gpxsolar/venv/bin/pyinstaller gpxsolar_mac.spec --clean --noconfirm

Résultat :
    dist_onedir/gpxsolar/gpxsolar          (exécutable interne)
    dist_onedir/gpxsolar/_internal/
        gpxsolar.py          (livré EN CLAIR → maj sans rebuild via update_app.py)
        PyQt6/, pyproj/, rasterio/, ...

Architecture (miroir lidar2map) :
  - Entry point = _loader.py, qui exécute _internal/gpxsolar.py.
  - 2 passes Analysis (détection sur gpxsolar.py, build sur _loader.py).
  - Ce onedir est ensuite zippé et le bundle est copié dans
    GPXSOLAR.app/Contents/Resources/ par gpxsolar_mac_build.sh (étapes 2-4).
"""

from pathlib import Path
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    collect_dynamic_libs,
    collect_all,
)

ONEFILE = False
CONSOLE = False
NAME    = "gpxsolar"

SRC = Path(SPECPATH)

datas         = []
binaries      = []
hiddenimports = []

# ── PyQt6 + pywebview + qtpy (GUI Qt — backend headless-safe sur Mac) ─────────
for lib in ("PyQt6", "pywebview", "qtpy"):
    try:
        d, b, h = collect_all(lib)
        datas += d; binaries += b; hiddenimports += h
    except Exception as e:
        print(f"  [WARN] collect_all({lib!r}) : {e}")
hiddenimports += [
    "webview.platforms.qt",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebChannel",
]

# ── pyproj ────────────────────────────────────────────────────────────────────
datas         += collect_data_files("pyproj")
hiddenimports += collect_submodules("pyproj")
hiddenimports += ["pyproj._compat", "pyproj.crs._cf1x8", "pyproj.transformer"]

# ── rasterio ──────────────────────────────────────────────────────────────────
datas         += collect_data_files("rasterio")
binaries      += collect_dynamic_libs("rasterio")
hiddenimports += collect_submodules("rasterio")
hiddenimports += [
    "rasterio._features", "rasterio._io", "rasterio._warp",
    "rasterio.sample", "rasterio.vrt", "rasterio.windows",
    "rasterio.warp", "rasterio.transform", "rasterio.enums",
    "rasterio.control", "rasterio.crs",
]

# ── shapely ───────────────────────────────────────────────────────────────────
binaries      += collect_dynamic_libs("shapely")
hiddenimports += ["shapely.geometry", "shapely.strtree", "shapely.ops"]

# ── pysolar ───────────────────────────────────────────────────────────────────
hiddenimports += collect_submodules("pysolar")
hiddenimports += [
    "pysolar.solartime", "pysolar.solar",
    "pysolar.tzinfo_check", "pysolar.constants",
]

# ── simplekml ─────────────────────────────────────────────────────────────────
datas += collect_data_files("simplekml")

# ── srtm.py ───────────────────────────────────────────────────────────────────
try:
    datas += collect_data_files("srtm")
except Exception:
    pass

# ── timezonefinder ────────────────────────────────────────────────────────────
datas += collect_data_files("timezonefinder")

# ── numba + llvmlite ──────────────────────────────────────────────────────────
try:
    hiddenimports += collect_submodules("numba")
    binaries      += collect_dynamic_libs("numba")
    datas         += collect_data_files("numba")
    binaries      += collect_dynamic_libs("llvmlite")
    datas         += collect_data_files("llvmlite")
except Exception:
    pass

# ── pandas ────────────────────────────────────────────────────────────────────
hiddenimports += [
    "pandas._libs.tslibs.np_datetime",
    "pandas._libs.tslibs.nattype",
]

# ── gpxpy ─────────────────────────────────────────────────────────────────────
hiddenimports += ["gpxpy.geo", "gpxpy.parser"]

# ── py7zr (lazy) ──────────────────────────────────────────────────────────────
try:
    d, b, h = collect_all("py7zr")
    datas += d; binaries += b; hiddenimports += h
except Exception:
    pass

# ── PIL ───────────────────────────────────────────────────────────────────────
hiddenimports += ["PIL.Image", "PIL.PngImagePlugin", "PIL.JpegImagePlugin"]

# ── certifi ───────────────────────────────────────────────────────────────────
datas         += collect_data_files("certifi")
hiddenimports += ["urllib3", "charset_normalizer", "idna", "certifi"]

# ── Runtime hook : PYWEBVIEW_GUI=qt + chemins Qt WebEngine + certifi ─────────
_hook = SRC / "build" / "hook_mac_runtime.py"
_hook.parent.mkdir(parents=True, exist_ok=True)
_hook.write_text("""\
import os, sys
_base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.executable)))
os.environ.setdefault('PYWEBVIEW_GUI', 'qt')
_candidates = [
    os.path.join(_base, 'PyQt6', 'Qt6', 'lib', 'QtWebEngineCore.framework',
                 'Helpers', 'QtWebEngineProcess.app', 'Contents', 'MacOS',
                 'QtWebEngineProcess'),
    os.path.join(_base, 'QtWebEngineProcess'),
    os.path.join(_base, 'PyQt6', 'QtWebEngineProcess'),
]
for _p in _candidates:
    if os.path.isfile(_p):
        os.environ.setdefault('QTWEBENGINEPROCESS_PATH', _p)
        break
_res = os.path.join(_base, 'PyQt6', 'Qt6', 'Resources')
if os.path.isdir(_res):
    os.environ.setdefault('QTWEBENGINE_RESOURCES_PATH', _res)
try:
    import certifi
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
    os.environ.setdefault('REQUESTS_CA_BUNDLE', certifi.where())
except Exception:
    pass
""")

_excludes_mac = [
    "tkinter", "matplotlib", "scipy",
    "PyQt5", "PySide2", "PySide6",
    "webview.platforms.cocoa", "webview.platforms.gtk",
    "clr_loader", "pythonnet",
    "test", "unittest", "pydoc_data", "IPython", "jupyter",
]

# ── 2 passes PyInstaller ─────────────────────────────────────────────────────
# Passe 1 : analyse de gpxsolar.py pour détecter tous ses imports
a_detect = Analysis(
    ["gpxsolar.py"],
    pathex=[], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=_excludes_mac, noarchive=False, optimize=0,
)

# Passe 2 : build réel depuis _loader.py
a = Analysis(
    ["_loader.py"],
    pathex=[], binaries=binaries,
    datas=datas + [("gpxsolar.py", "."),     # gpxsolar.py en clair dans _internal/
                   ("gui/index.html", "gui"), # front séparé (comme lidar2map),
                   ("gui/style.css", "gui"),  # bundlé dans _internal/gui/ ;
                   ("gui/app.js", "gui")],    # patchable sans rebuild via update_app
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
    runtime_hooks=[str(_hook)], excludes=_excludes_mac, noarchive=False, optimize=0,
)

# Fusion des TOC de sortie après les deux analyses
a.binaries += a_detect.binaries
a.datas    += a_detect.datas
a.pure     += [e for e in a_detect.pure if not e[0].startswith("gpxsolar")]

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=NAME, debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False,
    console=CONSOLE, disable_windowed_traceback=False,
    # target_arch=None : PyInstaller construit pour l'archi du Python courant
    # (arm64 sur runner Apple Silicon, x86_64 sur runner Intel). Pas de valeur
    # en dur, sinon le build Intel produirait un binaire arm64 inutilisable.
    argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None, icon=None,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, upx_exclude=[], name=NAME,
)
