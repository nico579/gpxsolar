# -*- mode: python ; coding: utf-8 -*-
"""
Spec PyInstaller pour le LAUNCHER gpxsolar — Windows onefile.

Construit la même source gpxsolar.py en mode onefile minimal, en excluant
toutes les deps lourdes (le launcher n'utilise que stdlib).

Le bundle gpxsolar_bundle.zip N'EST PAS embarqué dans le binaire : il est
copié à côté du .exe par gpxsolar_win_build.ps1, ce qui le rend remplaçable
sans rebuilder (cf. update_app.py).

Au runtime, le bloc launcher en tête de gpxsolar.py cherche le bundle à côté
de l'exe, l'extrait dans %LOCALAPPDATA%\\gpxsolar (avec contrôle SHA), puis
spawn l'exe interne avec la sentinelle --__gpxsolar_inner__.

Prérequis (orchestré par gpxsolar_win_build.ps1) :
  1. pyinstaller gpxsolar_win.spec          -> dist_onedir/gpxsolar/...
  2. zip dist_onedir/gpxsolar               -> build/gpxsolar_bundle.zip
  3. pyinstaller gpxsolar_win_launcher.spec -> dist/gpxsolar.exe  (livrable)
  4. copie  gpxsolar_bundle.zip             -> dist/  (à côté du .exe)
"""

from pathlib import Path

BUNDLE_ZIP = Path(SPECPATH) / "build" / "gpxsolar_bundle.zip"
if not BUNDLE_ZIP.exists():
    raise SystemExit(
        f"[gpxsolar_win_launcher.spec] Bundle introuvable : {BUNDLE_ZIP}\n"
        "Exécute d'abord :\n"
        "  pyinstaller gpxsolar_win.spec --clean --noconfirm\n"
        "  Compress-Archive dist_onedir\\gpxsolar\\* build\\gpxsolar_bundle.zip\n"
    )

# Le zip N'EST PAS embarqué : il sera copié à côté du .exe par le build script.
datas         = []
hiddenimports = []

# Exclure agressivement toutes les deps lourdes — le launcher n'utilise que
# stdlib (os, sys, hashlib, zipfile, subprocess, shutil, pathlib).
excludes = [
    "numpy", "scipy", "pandas",
    "rasterio", "fiona", "shapely", "pyproj",
    "numba", "llvmlite",
    "PIL", "Pillow",
    "webview", "clr_loader", "pythonnet", "clr",
    "pysolar", "gpxpy", "srtm", "pytz",
    "simplekml", "timezonefinder", "py7zr",
    "requests", "urllib3", "charset_normalizer", "certifi", "idna",
    "tkinter", "matplotlib",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy",
    "test", "unittest", "pydoc_data", "IPython", "jupyter",
]

a = Analysis(
    ["gpxsolar.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="gpxsolar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # stdout du child visible dans le terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
