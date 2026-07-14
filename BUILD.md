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
| `update_app.py` | Patch du bundle (local / archive mac / release 3 OS) |
| `deploy.py` | **Déploiement unifié en 1 commande** (cross-platform Win/Mac/Linux) : push, détection du diff, patch cloud/local ou tag pour rebuild |
| `ci_github.yml` → `…/ci.yml` | **CI** : tests 3 OS au push de `gpxsolar.py` |
| `release_github.yml` → `…/release.yml` | **Release** : compile 3 OS + publie, au push d'un tag `v*` |
| `update_github.yml` → `…/update.yml` | **Update** : patche le code des 3 bundles d'une release, sans rebuild (manuel) |

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

### Compiler (build) vs mettre à jour (patch) — quelle méthode ?

Deux opérations **distinctes**, à ne pas confondre :

| Opération | OS-spécifique ? | Depuis 1 seule machine ? |
|---|---|---|
| **Compiler** (PyInstaller : launcher + onedir avec libs natives) | Oui — `.exe` / ELF / `.app` + rasterio/Qt compilés | Non : chaque OS, ou le cloud |
| **Mettre à jour** `_internal/gpxsolar.py` d'un bundle existant | Non — simple manip de zip | **Oui, pour les 3 OS** |

C'est tout l'intérêt du bundle séparé du launcher : une fois les 3 binaires
compilés (une fois), **une seule machine met à jour les 3 OS** sans recompiler,
via `update_app.py --release`.

**Ce que `update_app.py` peut / ne peut PAS patcher :**

- ✅ **Sans rebuild** (vit dans `_internal/gpxsolar.py`, l'app *inner*) : `main`,
  `show_form`, la logique de calcul, les handlers GUI.
- ❌ **Rebuild obligatoire** : une **dépendance** (libs natives), un **spec**, le
  **bloc launcher** (recherche bundle / extraction / **lockfile**) — compilé *dans*
  l'exe launcher, pas dans le bundle —, la version de Python, un nouvel OS.
  > Exemple vécu : le durcissement du lockfile (bloc launcher) a exigé un rebuild ;
  > le muzzle Qt et le clamp fenêtre (dans `show_form`) auraient pu passer par
  > `update_app.py`.

**Trois méthodes de livraison — laquelle choisir :**

| Méthode | Compile ? | Upload ~1,5 Go depuis | Pour |
|---|---|---|---|
| ☁️ **`release.yml`** (tag `v*`) | oui (3 OS, clean-room) | réseau GitHub | deps / spec / **bloc launcher** / nouvelle version |
| ☁️ **`update.yml`** (manuel + tag) | non | réseau GitHub | **fix de code seul (recommandé)** |
| ⚡ **`update_app.py --release`** (local) | non | **ta** connexion | fix de code hors cloud |
| 🔧 **build local par-OS** (`*_build.*`) | oui (1 OS) | — | itérer / déboguer |

Détails :
- ☁️ **`release.yml`** — **source de vérité des binaires distribués** : runner neuf,
  reproductible (pas de dérive machine — ex. mauvaise version de dépendance). Seul
  moyen d'obtenir Linux/macOS sans posséder la machine. Déclenché par un tag `vX.Y.Z`.
- ☁️ **`update.yml`** — fait tourner `update_app.py --release` **sur un runner** :
  download + patch + ré-upload des ~1,5 Go d'assets se font sur le **réseau GitHub**,
  pas sur ta liaison montante. **La voie idéale pour livrer un fix de code.**
- ⚡ **`update_app.py --release` en local** — même résultat, mais re-pousse ~1,5 Go
  depuis **ta** connexion (DELETE+UPLOAD des assets entiers — GitHub ne patche pas
  partiellement) ; sur upload lent c'est plus long que le cloud. À réserver au cas
  hors-ligne / sans accès au repo.
- 🔧 **Build local** — ne jamais publier un build local comme asset (dérive machine
  + 1 seul OS) ; sert uniquement à tester sur ta plateforme.

**Règle** : pour livrer, **rester dans le cloud** — `release.yml` si deps/spec/
launcher changent, sinon `update.yml` (fix de code, sans rebuild ni upload local).
Le build local n'est que pour itérer/déboguer.

### Déploiement unifié en une commande — `deploy.py`

Un seul script (cross-platform : Windows / macOS / Linux) qui **détecte ce qui
a changé** et applique la bonne action, sur la voie de ton choix :

```bash
python deploy.py -m "mon correctif"                         # cloud, dernière release
python deploy.py -m "..." --patch-tag v1.0.2                # cibler un tag existant
python deploy.py -m "..." --mode local                      # patch local au lieu de cloud
python deploy.py -m "..." --new-tag v1.0.3                  # créer un NOUVEAU tag -> release.yml
python deploy.py -m "..." --skip-push                       # patch direct, sans push ni détection
python deploy.py -m "..." --dry-run                         # voir le diff sans pousser
```

Sous Mac/Linux : `python3 deploy.py ...` ou `./deploy.py ...` (le script a un
shebang `#!/usr/bin/env python3`, faire `chmod +x deploy.py` la 1ère fois).

| `--mode` | Voie | Qui upload les ~1,5 Go ? | Prérequis |
|---|---|---|---|
| `cloud` (défaut) | `update.yml` sur runner GitHub | **GitHub** | `gh auth status` |
| `local` | `python update_app.py --release` ici | **ta connexion** | `python` + `GH_TOKEN`/`GITHUB_TOKEN` (ou `gh auth token`) |

| Ce qui a changé (diff réel) | Action automatique (identique cloud / local) |
|---|---|
| `gpxsolar.py` seul (code interne) | push **+ patch** (cloud ou local selon `--mode`) sur la dernière release |
| `.spec` / `_loader.py` / `*_build.*` / `setup_build_*` | push **puis STOP** : indique de relancer avec `--new-tag` (rebuild ; version = choix humain) |
| docs / meta seules (README, BUILD, workflows, LICENSE, screenshots) | **push seul** — aucun binaire à toucher |

**Deux sémantiques de tag distinctes** :
- `--patch-tag vX.Y.Z` = cibler une release **existante** pour le patch (défaut : la dernière).
- `--new-tag vX.Y.Z` = créer un **nouveau** tag git → déclenche `release.yml` (rebuild complet 3 OS, ~30 min).

> ⚠️ **Angle mort** assumé : le bloc launcher et les dépendances vivent *dans*
> `gpxsolar.py`. Si seul `gpxsolar.py` change, le script suppose un fix de code
> interne (→ patch) et **affiche un avertissement** : si tu as touché au
> bloc launcher ou aux deps, relance avec `--new-tag <vX.Y.Z>` pour un rebuild.

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

### Spécifique Linux

- **Qt « xcb plugin » au démarrage** : libs système manquantes.
  `sudo apt install libxcb-cursor0 libegl1 libgl1` (Debian/Ubuntu).
- **`ModuleNotFoundError: No module named 'venv'`** : le module venv de Python
  est packagé séparément sur Debian/Ubuntu. `sudo apt install python3.12-venv`.
- **Wayland, artefacts d'affichage Qt** : forcer X11 :
  `QT_QPA_PLATFORM=xcb python3 gpxsolar.py`.

### Spécifique macOS

- **« application endommagée / développeur non identifié »** (Gatekeeper sur
  `.app` non signé) : `xattr -dr com.apple.quarantine GPXSOLAR.app`, ou
  clic droit → Ouvrir → Ouvrir quand même.
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
```

La CI (`.github/workflows/ci.yml`) les exécute sur les 3 OS à chaque push
touchant `gpxsolar.py`, `test_gpxsolar.py`, `_loader.py` ou `deploy.py`.

### Run témoin (validation manuelle de bout en bout)

Pour vérifier qu'un changement ne modifie pas les résultats (ou documenter
qu'il les modifie volontairement), rejouer la trace témoin. Elle est locale
et non versionnée (trace GPS personnelle) : `2026-05-16_13-31.gpx` dans le
dossier de travail.

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
