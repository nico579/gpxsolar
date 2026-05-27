# gpxsolar — Build & déploiement

Documentation technique de l'empaquetage de gpxsolar en exécutable autonome
(Windows `.exe`, Linux ELF, macOS `.app`) et de la mise à jour sans rebuild.

L'architecture est calquée sur celle de lidar2map.

---

## 1. Vue d'ensemble

Un même `gpxsolar.py` est buildé en **deux** binaires PyInstaller :

```
┌─────────────────────────┐        ┌──────────────────────────────────────┐
│  launcher (onefile)     │ spawn  │  app interne (onedir)                  │
│  gpxsolar.exe / .app /  │ ─────► │  %LOCALAPPDATA%\gpxsolar\gpxsolar.exe  │
│  gpxsolar (Linux)       │        │  (extrait depuis le bundle au 1er run) │
│  ~9-15 Mo, stdlib seul  │        │  ~500-650 Mo (Qt + deps géo)           │
└─────────────────────────┘        └──────────────────────────────────────┘
            │
            │ lit, à CÔTÉ de lui (pas embarqué) :
            ▼
   gpxsolar_bundle.zip   ← le onedir zippé, REMPLAÇABLE sans rebuild
```

1. **onedir** (`gpxsolar_win.spec` / `gpxsolar_mac.spec`) : la vraie application,
   lourde (numpy, rasterio, pyproj, shapely, pysolar, pywebview…). Lente à
   packager, rapide à lancer.
2. **launcher onefile** (`*_launcher.spec`) : un petit binaire qui n'utilise que
   la stdlib. À l'exécution, il cherche `gpxsolar_bundle.zip` **à côté de lui**,
   l'extrait dans le dossier applicatif système (avec contrôle SHA + mtime pour
   détecter les mises à jour), puis spawn l'exe interne avec la sentinelle
   `--__gpxsolar_inner__`.

Le bloc launcher vit en tête de `gpxsolar.py` (gardé par `if getattr(sys,
"frozen", False)`). En mode développement (`python gpxsolar.py`) il est inerte.

### Backend GUI : Qt sur les 3 OS

La GUI (pywebview) utilise le backend **Qt** (PyQt6 + QtWebEngine) sur **Windows,
Linux et macOS** — forcé via `PYWEBVIEW_GUI=qt` (posé dans `show_form`, et par le
runtime hook `_runtime_hook_qt.py` en frozen). Sous Windows c'est un choix
délibéré : le backend WinForms/WebView2 par défaut passe par pythonnet/.NET, et
**pythonnet 3.1.0** y régresse — récursion infinie dans la sérialisation d'objets
.NET (`Rectangle.Empty.Empty…`) → le bridge JS↔Python ne se finalise pas → GUI
gelée au clic, plus des freezes WinForms intermittents. Qt supprime toute la
couche .NET et donne le même moteur Chromium partout.

Conséquences : bundle Windows plus gros (QtWebEngine, ~390 Mo zippé vs ~180 en
WinForms) et 1ʳᵉ extraction plus longue (~20-30 s). Les specs `*_win.spec`
bundlent PyQt6 (`collect_all`) et excluent winforms/clr/pythonnet ; le venv de
build doit donc contenir `PyQt6 PyQt6-WebEngine qtpy` (installés par
`--installer-deps` / `setup_build_*`).

### Pourquoi le bundle est À CÔTÉ et pas embarqué

Le `.zip` séparé est **remplaçable sans rebuild**. Pour livrer un nouveau
`gpxsolar.py`, on régénère juste l'entrée `_internal/gpxsolar.py` dans le zip —
aucun PyInstaller, aucune machine de build par OS. C'est ce qu'automatise
[`update_app.py`](update_app.py).

### Le rôle de `_loader.py`

L'entry point PyInstaller du onedir est `_loader.py` (et **non** `gpxsolar.py`).
`_loader.py` ne change jamais : il se contente d'exécuter `_internal/gpxsolar.py`
via `runpy.run_path`. `gpxsolar.py` est donc livré **en clair** dans le bundle
(`_internal/gpxsolar.py`), ce qui le rend éditable/remplaçable.

Le onedir est buildé en **2 passes Analysis** :
- Passe 1 : analyse `gpxsolar.py` pour détecter ses imports (sqlite3, ssl, xml…).
- Passe 2 : build réel depuis `_loader.py`, avec `gpxsolar.py` ajouté en *data*.
- Les TOC de sortie (3-tuples) sont fusionnés après les deux analyses.

---

## 2. Fichiers du déploiement

| Fichier | Rôle |
|---|---|
| `_loader.py` | Entry point du binaire (ne change jamais) |
| `gpxsolar_win.spec` | Spec onedir **Windows ET Linux** (ELF) |
| `gpxsolar_win_launcher.spec` | Spec launcher Windows onefile |
| `gpxsolar_win_build.ps1` | Build Windows (3 étapes) |
| `setup_build_windows.ps1` | Prépare la machine Windows |
| `gpxsolar_linux_build.sh` | Build Linux (réutilise `_win.spec`) |
| `setup_build_linux.sh` | Prépare la machine Linux |
| `gpxsolar_mac.spec` | Spec onedir macOS ARM64 |
| `gpxsolar_mac_launcher.spec` | Spec launcher macOS (`.app`) |
| `gpxsolar_mac_build.sh` | Build macOS (4 étapes) |
| `setup_build_mac.sh` | Prépare la machine macOS |
| `update_app.py` | Maj du bundle (local / archive mac / release 3 OS) |
| `push_github.ps1` | Synchronise les sources vers le repo GitHub |
| `ci_github.yml` | CI GitHub Actions (3 OS) |

Le venv de build est `~/.gpxsolar/venv` sur les 3 OS, créé par le setup via
`gpxsolar.py --installer-deps` (qui installe toutes les deps puis quitte sans
lancer la GUI), avec PyInstaller ajouté ensuite.

---

## 3. Builder

### Windows
```powershell
.\setup_build_windows.ps1     # une fois : Python 3.12, deps, PyInstaller
.\gpxsolar_win_build.ps1      # à chaque maj de gpxsolar.py
# -> dist\gpxsolar.exe + dist\gpxsolar_bundle.zip
```

### Linux (Ubuntu / Debian)
```bash
bash setup_build_linux.sh
bash gpxsolar_linux_build.sh
# -> dist/gpxsolar + dist/gpxsolar_bundle.zip
```
Prérequis : `sudo apt install zip` si absent. Le binaire dépend de la libc de
la machine de build (build sur Ubuntu 22.04 → tourne sur Ubuntu ≥ 22.04 /
Debian 12+). Sur Linux, le backend GUI est PyQt6 + WebEngine.

### macOS (Apple Silicon)
```bash
bash setup_build_mac.sh
bash gpxsolar_mac_build.sh
# -> dist/GPXSOLAR.app + dist/gpxsolar-macos-arm64.zip (+ SHA256 affiché)
```
Le `.app` n'est pas signé → Gatekeeper bloque au 1er lancement. Contourner :
`xattr -dr com.apple.quarantine GPXSOLAR.app`.

---

## 4. Mise à jour sans rebuild

Le bundle `_internal/gpxsolar.py` est un fichier texte. Pour livrer une nouvelle
version sans repasser par PyInstaller :

**Manuel** : ouvrir `gpxsolar_bundle.zip` → `_internal/` → remplacer `gpxsolar.py`.

**Automatique** ([`update_app.py`](update_app.py)) :

```bash
# 1. Bundle local (à côté du script ou dans dist/)
python update_app.py

# 2. Archive macOS (depuis n'importe quel OS — préserve les permissions Unix)
python update_app.py gpxsolar-macos-arm64.zip

# 3. Release GitHub complète (Win + Linux + macOS) en une commande
python update_app.py --release --tag v1.0.0
python update_app.py --release --tag v1.0.0 --dry-run   # simulation
```

Le mode `--release` télécharge les 3 assets de la release, patche le bundle
interne de chacun, corrige au passage le bit exécutable du launcher Linux,
réuploade (DELETE + UPLOAD car GitHub n'accepte pas de PATCH binaire), puis met
à jour les SHA256 dans le corps de la release. Requiert un token GitHub
(`GH_TOKEN`, `GITHUB_TOKEN` ou `git credential`).

Au prochain lancement, le launcher détecte le SHA différent et ré-extrait le
bundle automatiquement.

---

## 5. Détails par étape du build

1. **PyInstaller onedir** → `dist_onedir/gpxsolar/` (exe interne + `_internal/`).
2. **Compression** du contenu du onedir → `build/gpxsolar_bundle.zip` (structure
   plate, sans dossier parent).
3. **PyInstaller launcher** → `dist/gpxsolar(.exe)` (ou `GPXSOLAR.app`).
4. **Copie** de `gpxsolar_bundle.zip` à côté du launcher (Windows/Linux) ou dans
   `Contents/Resources/` (macOS). Sur macOS, étape 4 bis : archive `ditto` zippée
   distribuable + SHA256.

---

## 6. Dépannage

- **`PyInstaller introuvable`** : le venv `~/.gpxsolar/venv` n'a pas PyInstaller.
  Relance le `setup_build_*` correspondant.
- **`Bundle introuvable` dans un `*_launcher.spec`** : tu as lancé l'étape 3 sans
  l'étape 2. Lance le script de build complet, pas le spec launcher seul.
- **GUI ne s'ouvre pas sous Linux** : backend Qt manquant. `--installer-deps`
  installe `PyQt6 PyQt6-WebEngine qtpy` ; vérifie qu'ils sont dans le venv.
- **macOS « application endommagée »** : quarantaine Gatekeeper, voir §3.
- **Extraction concurrente bloquée** : supprime le lockfile
  `.gpxsolar_extracting` à côté du dossier applicatif, puis relance.
