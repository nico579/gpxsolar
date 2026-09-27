# -*- mode: python ; coding: utf-8 -*-
"""
Spec PyInstaller pour gpxsolar — macOS ARM64, onedir.

Usage :
    ~/.gpxsolar/venv/bin/pyinstaller gpxsolar_mac.spec --clean --noconfirm

Résultat (le programme livré tel quel depuis la 1.5.0) :
    dist/GPXSOLAR.app                      (BUNDLE, voir la fin du fichier)
        Contents/MacOS/gpxsolar
        Contents/Frameworks/               (sys._MEIPASS : binaires)
        Contents/Resources/                (données : gpxsolar.py, gui/, ...)
    dist/gpxsolar/                         (dossier onedir intermédiaire)

Architecture (miroir lidar2map) :
  - Entry point = _loader.py, qui exécute _internal/gpxsolar.py.
  - 2 passes Analysis (détection sur gpxsolar.py, build sur _loader.py).
  - Jusqu'à la 1.4, ce onedir était zippé dans un lanceur .app qui
    l'extrayait ; depuis la 1.5, le .app est le programme lui-même, que
    gpxsolar_mac_build.sh signe puis archive.
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

# pywebview/PyQt6/QtWebEngine retirés en 1.6.0 : l'interface est servie en
# HTTP local et ouverte dans le navigateur (voir gpxsolar_win.spec). L'icône
# de la barre des menus (pystray, via PyObjC) passe par les hooks standard.

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

# ── Runtime hook : bundle CA de certifi ──────────────────────────────────────
# Ses lignes Qt/QtWebEngine sont parties avec pywebview ; reste le bundle CA
# (même raison que dans gpxsolar_win.spec).
_hook = SRC / "build" / "hook_mac_runtime.py"
_hook.parent.mkdir(parents=True, exist_ok=True)
_hook.write_text("""\
import os
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
                   ("gui/app.js", "gui"),     # livré tel quel
                   ("gui/web_bridge.js", "gui"),
                   ("gui/gpxsolar_icon.png", "gui")],
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

# Même nom et même identifiant que le .app du lanceur des versions <= 1.4 :
# remplacé par celui-ci au même endroit, il garde sa place dans le Dock.
app = BUNDLE(
    coll,
    name="GPXSOLAR.app",
    icon=None,
    bundle_identifier="fr.nicolas.gpxsolar",
    info_plist={
        "NSHighResolutionCapable": "True",
        "NSRequiresAquaSystemAppearance": "No",
        "NSAppTransportSecurity": {
            "NSAllowsArbitraryLoads": True,
        },
    },
)
