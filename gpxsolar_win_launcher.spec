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

import re
from pathlib import Path

SRC = Path(SPECPATH)

BUNDLE_ZIP = SRC / "build" / "gpxsolar_bundle.zip"
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

# Ressource VERSIONINFO du binaire Windows. Un PE PyInstaller sans editeur,
# description ni copyright renseignes ressemble statistiquement aux
# echantillons malveillants des jeux d'entrainement de plusieurs moteurs
# antivirus a heuristique ML (constate sur blink2video : faux positifs
# Reddit, confirmes par VirusTotal sur plusieurs versions et sur lidar2map
# malgre un comportement different). C'est ce launcher qui est le binaire
# reellement distribue aux utilisateurs.
def _version_info(version: str) -> str:
    parties = (version.split(".") + ["0", "0", "0"])[:3]
    tuple_version = tuple(int(p) for p in parties) + (0,)
    chemin = SRC / ".version_info.txt"
    chemin.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={tuple_version},
    prodvers={tuple_version},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'nico579'),
         StringStruct(u'FileDescription', u'gpxsolar - simulation solaire de parcours GPX'),
         StringStruct(u'FileVersion', u'{version}'),
         StringStruct(u'InternalName', u'gpxsolar'),
         StringStruct(u'LegalCopyright', u'GPLv3 - nico579'),
         StringStruct(u'OriginalFilename', u'gpxsolar.exe'),
         StringStruct(u'ProductName', u'gpxsolar'),
         StringStruct(u'ProductVersion', u'{version}')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
    return str(chemin)


_texte_version = (SRC / "gpxsolar.py").read_text(encoding="utf-8")
_m_version = re.search(r'^VERSION\s*=\s*"([^"]+)"', _texte_version, re.M)
VERSION = _m_version.group(1) if _m_version else "0.0.0"

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
    version=_version_info(VERSION),
)
