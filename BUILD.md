# gpxsolar — Build & déploiement

Documentation technique de l'empaquetage de gpxsolar en exécutable autonome
(Windows `.exe`, Linux ELF, macOS `.app`) et de la livraison d'une version.

L'architecture est calquée sur celle de lidar2map.

---

## 1. Vue d'ensemble

Depuis la 1.5.0, chaque archive de release contient le programme lui-même,
comme celles de lidar2map, blink2video et watch2notif : le dossier onedir de
PyInstaller sous Windows et Linux, un `.app` sous macOS.

```
gpxsolar-windows-x86_64/           (gpxsolar-linux-x86_64/ sous Linux)
  gpxsolar.exe                     entry point = _loader.py
  _internal/
    gpxsolar.py                    exécuté en texte par _loader.py
    gui/, PyQt6/, pyproj/, rasterio/, ...

GPXSOLAR.app/                      (macOS)
  Contents/MacOS/gpxsolar
  Contents/Frameworks/             sys._MEIPASS : binaires (Qt compris),
                                   liens vers les données
  Contents/Resources/              données (gpxsolar.py, gui/...)
```

Le onedir (`gpxsolar_win.spec` / `gpxsolar_mac.spec`) est la vraie
application, lourde (numpy, rasterio, pyproj, shapely, pysolar, pywebview…) :
lente à packager, rapide à lancer. Sous macOS, `BUNDLE()` en fait
`GPXSOLAR.app`, sous le nom et l'identifiant qu'avait le `.app` du lanceur.

Jusqu'à la 1.4, un lanceur onefile contenait ce dossier zippé
(`gpxsolar_bundle.zip`) et l'extrayait au premier lancement dans
`%LOCALAPPDATA%\gpxsolar\`, `~/Library/Application Support/gpxsolar/` ou
`~/.local/share/gpxsolar/` : deux exemplaires sur disque, et une extraction
de 20 à 30 s après chaque mise à jour. Au démarrage, la 1.5 retire ce qu'un
tel lanceur a laissé : son extraction, reconnue à sa marque `.bundle_sha`, et
le `gpxsolar_bundle.zip` resté à côté du programme quand l'archive est
décompressée par-dessus (`_installation.nettoyer_ancienne_extraction`). Si
une ancienne instance tourne encore depuis l'extraction, son renommage
échoue sous Windows et le ménage attend le lancement suivant. Le nom des
archives et de leur dossier racine n'a pas changé : décompressée par-dessus,
une archive met le programme au chemin du lanceur, raccourcis compris.

### Backend GUI : Qt sur les 3 OS

La GUI (pywebview) utilise le backend **Qt** (PyQt6 + QtWebEngine) sur **Windows,
Linux et macOS** — forcé via `PYWEBVIEW_GUI=qt` (posé dans `show_form`, et par le
runtime hook `_runtime_hook_qt.py` en frozen). Sous Windows c'est un choix
délibéré : le backend WinForms/WebView2 par défaut passe par pythonnet/.NET, et
**pythonnet 3.1.0** y régresse — récursion infinie dans la sérialisation d'objets
.NET (`Rectangle.Empty.Empty…`) → le bridge JS↔Python ne se finalise pas → GUI
gelée au clic, plus des freezes WinForms intermittents. Qt supprime toute la
couche .NET et donne le même moteur Chromium partout.

Conséquence : une archive Windows plus grosse (QtWebEngine, ~390 Mo zippée
contre ~180 en WinForms). Les specs `*_win.spec`
bundlent PyQt6 (`collect_all`) et excluent winforms/clr/pythonnet ; le venv de
build doit donc contenir `PyQt6 PyQt6-WebEngine qtpy` (installés par
`--installer-deps` / `setup_build_*`).

### Le rôle de `_loader.py`

L'entry point PyInstaller du onedir est `_loader.py` (et **non** `gpxsolar.py`).
`_loader.py` ne change jamais : il se contente d'exécuter `_internal/gpxsolar.py`
via `runpy.run_path`. `gpxsolar.py` est donc livré **en clair** dans le
programme (`_internal/gpxsolar.py`). C'était le support du patch sans
reconstruction, retiré le 26 septembre 2026 comme sur lidar2map ; le
mécanisme reste en place, sans autre usage.

Le onedir est buildé en **2 passes Analysis** :
- Passe 1 : analyse `gpxsolar.py` pour détecter ses imports (sqlite3, ssl, xml…).
- Passe 2 : build réel depuis `_loader.py`, avec `gpxsolar.py` ajouté en *data*.
- Les TOC de sortie (3-tuples) sont fusionnés après les deux analyses.

---

## 2. Fichiers du déploiement

| Fichier | Rôle |
|---|---|
| `_loader.py` | Entry point du binaire (ne change jamais) |
| `_dossiers.py` | Dossiers d'état (`gpxsolar-data`) et de sorties (`Documents/gpxsolar`), reprise de l'état d'une 1.3 : jumeau de celui de lidar2map |
| `_atomic_files.py` | Écriture atomique et verrou entre processus, sous-ensemble de celui de lidar2map |
| `_installation.py` | Dossier du programme, ménage de ce qu'un lanceur ≤ 1.4 laissait, `--desinstaller` : jumeau des fonctions de lidar2map |
| `gpxsolar_win.spec` | Spec onedir **Windows ET Linux** (ELF) |
| `gpxsolar_win_build.ps1` | Build Windows (une passe PyInstaller) |
| `setup_build_windows.ps1` | Prépare la machine Windows |
| `gpxsolar_linux_build.sh` | Build Linux (réutilise `_win.spec`) |
| `setup_build_linux.sh` | Prépare la machine Linux |
| `gpxsolar_mac.spec` | Spec macOS (arm64 ou x86_64) : onedir puis `GPXSOLAR.app` (`BUNDLE`) |
| `gpxsolar_mac_build.sh` | Build macOS (3 étapes : PyInstaller, signature ad hoc du `.app`, archive ditto) |
| `setup_build_mac.sh` | Prépare la machine macOS |
| `exe_smoke.py` | Épreuve du binaire publié (démarrage, ménage de l'ancien lanceur), lancée par `release.yml` sur les 4 runners |
| `deploy.py` | **Déploiement unifié en 1 commande** (cross-platform Win/Mac/Linux) : tests, commit et push depuis ce dépôt, puis tag et suivi du build de release |
| `.github/workflows/ci.yml` | **CI** : tests 3 OS au push de `gpxsolar.py` |
| `.github/workflows/release.yml` | **Release** : compile 3 OS + publie, au push d'un tag `v*` |

Le venv de build est `~/.gpxsolar/venv` sur les 3 OS, créé par le setup via
`gpxsolar.py --installer-deps` (qui installe toutes les deps puis quitte sans
lancer la GUI), avec PyInstaller ajouté ensuite.

---

## 3. Builder

### Windows
```powershell
.\setup_build_windows.ps1     # une fois : Python 3.12, deps, PyInstaller
.\gpxsolar_win_build.ps1      # à chaque maj de gpxsolar.py
# -> dist\gpxsolar\ (gpxsolar.exe + _internal\)
```

### Linux (Ubuntu / Debian)
```bash
bash setup_build_linux.sh
bash gpxsolar_linux_build.sh
# -> dist/gpxsolar/ (gpxsolar + _internal/)
```
Le binaire dépend de la libc de
la machine de build (build sur Ubuntu 22.04 → tourne sur Ubuntu ≥ 22.04 /
Debian 12+). Sur Linux, le backend GUI est PyQt6 + WebEngine.

### macOS (Apple Silicon)
```bash
bash setup_build_mac.sh
bash gpxsolar_mac_build.sh
# -> dist/GPXSOLAR.app + dist/gpxsolar-macos-<arch>.zip (+ SHA256 affiché)
```
Le `.app` est signé ad hoc, pas notarisé : Gatekeeper bloque le premier
lancement d'une archive téléchargée. Contourner :
`xattr -dr com.apple.quarantine GPXSOLAR.app`.

---

## 4. Livrer une version

Toute livraison passe par une **release reconstruite** : `release.yml`
construit les 4 archives (Windows, Linux, macOS Apple Silicon et Intel) sur
des runners neufs, puis publie. Le patch d'un bundle existant sans
reconstruction (`update_app.py`, `update.yml`) a été retiré le 26 septembre
2026, comme sur lidar2map en 1.53. Il ne livrait que `gpxsolar.py` et `gui/`,
jamais un module `_*.py`, une dépendance ou une spec ; et sous macOS, il
modifiait l'intérieur de `GPXSOLAR.app` depuis un runner Ubuntu, sans pouvoir
le re-signer, ce qui rompt le sceau de sa signature (c'est ce qui valait
« LIDAR2MAP.app is damaged » à lidar2map en juillet 2026).

Côté utilisateur, il suffit de décompresser la nouvelle archive par-dessus
l'ancienne.

### Déploiement en une commande : `deploy.py`

Ce dossier de travail est le dépôt GitHub lui-même. `deploy.py` y lance les
tests hors réseau de la CI (`test_gpxsolar.py`, `test_dossiers.py`) et
`ruff`, commit, pousse sur `main` et, avec `--new-tag`, pose le tag
`v<VERSION>` qui déclenche `release.yml`, dont il suit le build.

```bash
python deploy.py -m "mon correctif"            # tests + push, pas de release
python deploy.py -m "..." --new-tag            # tests + push + tag v<VERSION> + suivi du build
python deploy.py -m "..." --dry-run            # voir le diff sans rien pousser
```

- La version a une **source unique**, la constante `VERSION` de
  `gpxsolar.py` : `--new-tag` en dérive le tag et refuse toute autre valeur.
- `deploy.py` refuse de partir si la branche n'est pas `main`, si `origin`
  n'est pas le dépôt officiel ou si `HEAD` diffère de `origin/main` (commit
  poussé ailleurs, par exemple une PR fusionnée : `git pull` d'abord).
- Seuls les fichiers déjà suivis partent. Un fichier nouveau non ignoré bloque
  le déploiement : l'ajouter (`git add`) ou l'ignorer (`.gitignore`, ou
  `.git/info/exclude` pour un fichier personnel).

Sous Mac/Linux : `python3 deploy.py ...` ou `./deploy.py ...` (shebang
`#!/usr/bin/env python3`, `chmod +x deploy.py` la première fois).

### Build local

Les scripts `gpxsolar_*_build.*` servent à itérer et déboguer sur sa propre
plateforme. Ne jamais publier un build local comme asset : dérive de la
machine (versions des dépendances) et un seul OS. `release.yml` reste la
source de vérité des binaires distribués.

---

## 5. Détails par étape du build

1. **PyInstaller** → `dist/gpxsolar/` (Windows, Linux : `gpxsolar(.exe)` +
   `_internal/`), ou `dist/GPXSOLAR.app` (macOS, par `BUNDLE`).
2. **macOS seulement** : signature ad hoc du `.app` complet, vérifiée, puis
   archive `ditto` distribuable + SHA256. Rien ne modifie le `.app` après
   cette signature : jusqu'à la 1.4, le bundle copié après coup dans
   `Contents/Resources/` rompait son sceau.
3. **release.yml** : le dossier devient la racine de l'archive
   (`gpxsolar-<os>-x86_64`), puis `exe_smoke.py` lance le binaire tel qu'il
   sera publié, avant tout envoi.

---

## 6. Dépannage

- **`PyInstaller introuvable`** : le venv `~/.gpxsolar/venv` n'a pas PyInstaller.
  Relance le `setup_build_*` correspondant.
- **GUI ne s'ouvre pas sous Linux** : backend Qt manquant. `--installer-deps`
  installe `PyQt6 PyQt6-WebEngine qtpy` ; vérifie qu'ils sont dans le venv.
- **macOS « application endommagée »** : quarantaine Gatekeeper, voir §3.
- **Ancienne extraction toujours présente (Windows)** : la 1.5 la retire à son
  premier démarrage, sauf si une instance d'une version ≤ 1.4 y tourne
  encore ; le ménage attend alors le lancement suivant. Pour forcer, fermer
  toutes les instances de gpxsolar, puis supprimer `%LOCALAPPDATA%\gpxsolar\`
  (jamais `gpxsolar-data`, qui contient les réglages).

### Spécifique Linux

- **Qt « xcb plugin » au démarrage** : libs système manquantes.
  `sudo apt install libxcb-cursor0 libegl1 libgl1` (Debian/Ubuntu).
- **`ModuleNotFoundError: No module named 'venv'`** : le module venv de Python
  est packagé séparément sur Debian/Ubuntu. `sudo apt install python3.12-venv`.
- **Wayland, artefacts d'affichage Qt** : forcer X11 :
  `QT_QPA_PLATFORM=xcb python3 gpxsolar.py`.

### Spécifique macOS

- **« application endommagée / développeur non identifié »** (Gatekeeper sur
  un `.app` signé ad hoc, non notarisé) : `xattr -dr com.apple.quarantine
  GPXSOLAR.app`, ou clic droit → Ouvrir → Ouvrir quand même.
- **`PermissionError`, `slice is not valid mach-o`, `.app` qui ne démarre
  pas** : l'archive a été extraite par un outil qui ne restitue ni les liens
  symboliques, ni les bits d'exécution, ni les attributs du `.app` (le module
  `zipfile` de Python, par exemple). Réextraire avec le Finder ou
  `ditto -x -k gpxsolar-macos-arm64.zip .`.
- **Écran blanc dans la GUI** : QtWebEngine n'a pas trouvé son helper.
  Vérifier que le build PyInstaller a généré le runtime hook
  (cf. `gpxsolar_mac.spec`, section *Runtime hook*). En dev (script Python
  direct), c'est piloté par `pyobjc-framework-WebKit` côté Cocoa.
- **Apple Silicon, crash au démarrage / `mach-o, but wrong architecture`** :
  vérifier que `python3` est arm64 :
  `python3 -c "import platform; print(platform.machine())"` doit dire `arm64`
  (sinon installer Python depuis python.org ou via Homebrew ARM).

## 7. Tests

### Tests unitaires (CI + local)

`test_gpxsolar.py` à la racine : tests de caractérisation des fonctions
numériques (interpolation bilinéaire en convention centres, distance
equirectangulaire et antiméridien, clé du cache solaire, moteur de
ray-tracing comparé à une référence point à point sur les 3 modes d'ombre,
géométrie des rayons KML). Particularité : le module n'est PAS importé (son
bootstrap s'exécute à l'import) ; le source des fonctions est extrait via
`ast` et exécuté avec des stubs. Seul numpy est requis.

```bash
python test_gpxsolar.py     # runner intégré, code de sortie != 0 si échec
pytest test_gpxsolar.py     # équivalent via pytest
python test_dossiers.py     # dossiers d'état et de sorties (unittest)
python test_installation.py # ménage de l'ancien lanceur, désinstallation
```

`test_dossiers.py` éprouve `_dossiers.py` et son branchement dans
`gpxsolar.py` : reprise unique de l'état d'une 1.3, chemins de la ligne de
commande, et un vrai `gpxsolar.py --version` qui ne doit rien créer. Il isole
toujours ses dossiers : jamais ceux de l'utilisateur.

`test_installation.py` éprouve `_installation.py` sur de vrais fichiers
temporaires : ménage de ce qu'un lanceur ≤ 1.4 laissait, jamais le dossier
d'où tourne le programme, et une désinstallation qui ne supprime jamais le
programme lui-même.

La CI (`.github/workflows/ci.yml`) les exécute sur les 3 OS à chaque push
touchant `gpxsolar.py`, un module `_*.py`, un des fichiers de tests ou
`deploy.py`. Le binaire construit, lui, est éprouvé par `exe_smoke.py` dans
`release.yml`, sur les 4 runners, avant toute publication.

### Run témoin (validation manuelle de bout en bout)

Pour vérifier qu'un changement ne modifie pas les résultats (ou documenter
qu'il les modifie volontairement), rejouer la trace témoin
`2026-05-16_13-31.gpx`, versionnée à la racine du repo.

```bash
python gpxsolar.py --bootstrap=none --gpx 2026-05-16_13-31.gpx \
    --date 16/05/2026 --time 13:31 --dem-source srtm1 --direction CW \
    --generate-shadow-map --output temoin.csv
```

Valeurs de référence (code de juillet 2026) :

| Dist. totale | Durée | % Soleil | % Relief | % Végét. | % R+V | % Nuit |
|--------------|---------|------|-----|------|------|-----|
| 2,71 km | 0:50:04 | 12,5 | 0,0 | 69,6 | 17,9 | 0,0 |

Notes :

- comparer des cartes d'ombre (GeoTIFF) UNIQUEMENT à `--num-workers` égal :
  l'ordre de remplissage du cache solaire par les workers peut déplacer
  ~1 pixel d'un run à l'autre ;
- le CSV de la trace, lui, est déterministe (calcul séquentiel) : toute
  différence y est réelle ;
- si un changement modifie légitimement le témoin (correction du modèle,
  nouveau lissage...), mettre à jour ce tableau dans le même commit.
