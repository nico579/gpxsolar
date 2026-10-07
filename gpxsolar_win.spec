# -*- mode: python ; coding: utf-8 -*-
"""
Spec PyInstaller pour gpxsolar — Windows & Linux, onedir.

Usage :
    %USERPROFILE%\\.gpxsolar\\venv\\Scripts\\pyinstaller.exe gpxsolar_win.spec --clean --noconfirm   (Windows)
    ~/.gpxsolar/venv/bin/pyinstaller gpxsolar_win.spec --clean --noconfirm                            (Linux)

Résultat (le programme livré tel quel depuis la 1.5.0) :
    dist/gpxsolar/gpxsolar(.exe)
    dist/gpxsolar/_internal/
        gpxsolar.py          (livré en clair, exécuté par _loader.py)
        pyproj/, rasterio/, ...

Architecture (miroir lidar2map) :
  - Entry point = _loader.py (ne change jamais), qui exécute _internal/gpxsolar.py.
  - 2 passes Analysis : passe 1 détecte les imports de gpxsolar.py, passe 2
    construit depuis _loader.py. Fusion des TOC après coup.
  - release.yml fait de ce dossier la racine de l'archive publiée. Jusqu'à la
    1.4, il était zippé dans un bundle qu'un lanceur extrayait.

Cette spec sert AUSSI pour Linux (le nom _win est trompeur — PyInstaller
produit un ELF sous Linux). Depuis la 1.6.0, aucun backend graphique à
embarquer sur l'un ou l'autre : l'interface est servie en HTTP local et
ouverte dans le navigateur de l'utilisateur, comme celle de lidar2map.
"""

import re
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
# Console masquée dès le démarrage seulement si elle appartient au programme
# (double-clic, raccourci, relance) : lancé depuis un terminal, il y écrit
# normalement. Repris de lidar2map ; sans lui, la 1.5.0 ouvrait une fenêtre
# de console à côté de son interface.
HIDE_CONSOLE = "hide-early" if sys.platform == "win32" else None
NAME    = "gpxsolar"

SRC = Path(SPECPATH)
# Icônes rangées comme celles de blink2video, watch2notif et lidar2map :
# assets/gpxsolar.png pour l'exécutable (Windows ; sans effet sous Linux),
# converti par PyInstaller à l'aide de Pillow, et assets/gpxsolar.ico,
# embarqué plus bas, pour la zone de notification et l'onglet.
APP_ICON = SRC / "assets" / "gpxsolar.png"


# Ressource VERSIONINFO du binaire Windows (ignoree sans effet sous Linux).
# Un PE PyInstaller sans editeur, description ni copyright renseignes
# ressemble statistiquement aux echantillons malveillants des jeux
# d'entrainement de plusieurs moteurs antivirus a heuristique ML (constate
# sur blink2video : faux positifs Reddit, confirmes par VirusTotal sur
# plusieurs versions et sur lidar2map malgre un comportement different).
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
# Les fichiers JavaScript du paquet commun (bandeau de mise à jour, bouton Réglages) :
# PyInstaller n'embarque pas les données d'un paquet sans qu'on le lui demande.
datas         += collect_data_files("nico579_commons")
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

# pywebview/PyQt6/QtWebEngine retirés en 1.6.0 (l'interface est servie en
# HTTP local et consultée depuis le navigateur déjà installé, voir
# main_serve_gui() dans gpxsolar.py) : plus de backend graphique à bundler.
# C'était le poste le plus lourd du programme, 557 Mo sur 941. L'icône de
# zone de notification (pystray) passe par les hooks standard de PyInstaller,
# comme chez lidar2map.

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
    "PyQt5", "PySide2", "PySide6",
    "scipy",                                  # gpxsolar n'utilise pas scipy
    "test", "unittest", "pydoc_data",
    "IPython", "jupyter",
]

# ── Runtime hook : bundle CA de certifi ──────────────────────────────────────
# Ses lignes Qt/QtWebEngine sont parties avec pywebview. Reste le bundle CA :
# lidar2map a pu retirer son hook parce qu'il pose ces variables dans son
# code (_bootstrap_tls.py), gpxsolar non, d'où ce hook réduit à certifi.
_hook = SRC / "build" / "_runtime_hook_certifi.py"
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
    datas=datas + [("gpxsolar.py", "."),     # gpxsolar.py en clair dans _internal/
                   ("gui/index.html", "gui"), # front séparé (comme lidar2map),
                   ("gui/style.css", "gui"),  # bundlé dans _internal/gui/ ;
                   ("gui/app.js", "gui"),     # livré tel quel
                   ("gui/web_bridge.js", "gui"),
                   ("assets/gpxsolar.ico", "assets")],  # notification, onglet

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
        hide_console=HIDE_CONSOLE,
        disable_windowed_traceback=False, argv_emulation=False,
        target_arch=None, codesign_identity=None, entitlements_file=None,
        icon=str(APP_ICON),
        version=_version_info(VERSION),
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True, name=NAME, debug=False,
        bootloader_ignore_signals=False, strip=False, upx=False,
        console=CONSOLE, hide_console=HIDE_CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False, target_arch=None,
        codesign_identity=None, entitlements_file=None, icon=str(APP_ICON),
        version=_version_info(VERSION),
    )
    coll = COLLECT(
        exe, a.binaries, a.datas,
        strip=False, upx=False, upx_exclude=[], name=NAME,
    )
