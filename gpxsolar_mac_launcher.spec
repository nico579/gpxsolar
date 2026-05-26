# -*- mode: python ; coding: utf-8 -*-
"""
Spec PyInstaller pour le LAUNCHER gpxsolar — macOS ARM64 (.app bundle).

Construit la même source gpxsolar.py en mode onefile minimal, en excluant
toutes les deps lourdes (le launcher n'utilise que stdlib).

Le bundle gpxsolar_bundle.zip N'EST PAS embarqué dans le binaire : il est
copié dans GPXSOLAR.app/Contents/Resources/ par gpxsolar_mac_build.sh, ce qui
le rend remplaçable sans rebuilder (cf. update_app.py).

Au runtime, le bloc launcher en tête de gpxsolar.py cherche le bundle dans
Contents/Resources/, l'extrait dans ~/Library/Application Support/gpxsolar/,
puis spawn l'exe interne avec la sentinelle --__gpxsolar_inner__.

Prérequis (orchestré par gpxsolar_mac_build.sh) :
  1. pyinstaller gpxsolar_mac.spec          -> dist_onedir/gpxsolar/
  2. zip dist_onedir/gpxsolar               -> build/gpxsolar_bundle.zip
  3. pyinstaller gpxsolar_mac_launcher.spec -> dist/GPXSOLAR.app
  4. copie  gpxsolar_bundle.zip             -> GPXSOLAR.app/Contents/Resources/
"""

from pathlib import Path

BUNDLE_ZIP = Path(SPECPATH) / "build" / "gpxsolar_bundle.zip"
if not BUNDLE_ZIP.exists():
    raise SystemExit(
        f"[gpxsolar_mac_launcher.spec] Bundle introuvable : {BUNDLE_ZIP}\n"
        "Execute d'abord :\n"
        "  pyinstaller gpxsolar_mac.spec --clean --noconfirm\n"
        "  cd dist_onedir/gpxsolar && ditto -c -k . ../../build/gpxsolar_bundle.zip\n"
    )

datas         = []   # zip dans Contents/Resources/, pas embarqué dans le binaire
hiddenimports = []

excludes = [
    "numpy", "scipy", "pandas",
    "rasterio", "fiona", "shapely", "pyproj",
    "numba", "llvmlite",
    "PIL", "Pillow",
    "webview", "clr_loader", "pythonnet", "clr",
    "PyQt6", "PyQt5", "PySide2", "PySide6", "qtpy",
    "pysolar", "gpxpy", "srtm", "pytz",
    "simplekml", "timezonefinder", "py7zr",
    "requests", "urllib3", "charset_normalizer", "certifi", "idna",
    "tkinter", "matplotlib",
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
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,        # double-clic sur fichier -> args forwards
    target_arch='arm64',
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

app = BUNDLE(
    exe,
    name='GPXSOLAR.app',
    icon=None,
    bundle_identifier='fr.nicolas.gpxsolar',
    info_plist={
        'NSHighResolutionCapable':        'True',
        'NSRequiresAquaSystemAppearance':  'No',
        'NSAppTransportSecurity': {
            'NSAllowsArbitraryLoads': True,
        },
        'com.apple.security.cs.allow-jit':                        True,
        'com.apple.security.cs.allow-unsigned-executable-memory': True,
    },
)
