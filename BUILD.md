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
    gui/, pyproj/, rasterio/, ...

GPXSOLAR.app/                      (macOS)
  Contents/MacOS/gpxsolar
  Contents/Frameworks/             sys._MEIPASS : binaires,
                                   liens vers les données
  Contents/Resources/              données (gpxsolar.py, gui/...)
```

Le onedir (`gpxsolar_win.spec` / `gpxsolar_mac.spec`) est la vraie
application, lourde (numpy, rasterio, pyproj, shapely, pysolar…) :
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

### Interface : un serveur web local

Depuis la 1.6.0, l'interface n'est plus une fenêtre : `gpxsolar.py` la sert en
HTTP local (`_serve_web.py`, bibliothèque standard seulement) et l'ouvre dans
le navigateur déjà installé, comme lidar2map, blink2video et watch2notif. Une
icône de zone de notification ([nico579-commons](https://github.com/nico579/nico579-commons),
sur pystray), au même menu que ces trois applications, permet de la rouvrir,
d'ouvrir la page d'une version plus récente quand il y en a une, de redémarrer
ou d'arrêter le serveur, et de poser un raccourci sur le Bureau. Jusqu'à la
1.5, pywebview l'affichait dans une fenêtre
Qt (PyQt6 + QtWebEngine), qui pesait 557 Mo sur les 941 du programme ; Qt y
avait lui-même remplacé le backend WinForms de Windows, dont la couche
pythonnet 3.1.0 gelait l'interface.

Le serveur n'écoute que sur la boucle locale (127.0.0.1), à partir du port
8768 : blink2video prend 8765, lidar2map 8766 et watch2notif 8767. Un second
lancement rejoint l'instance en cours ou, sur demande, en démarre une nouvelle
sur le port suivant, pour un calcul en parallèle. Options :
`gpxsolar --serve-gui --help`.

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
| `_dossiers.py` | Les noms et fichiers de gpxsolar pour `nico579_commons.dossiers` (état dans `gpxsolar-data`, sorties dans `Documents/gpxsolar`, reprise de l'état d'une 1.3) : la logique est dans la bibliothèque commune |
| `_installation.py` | Dossier du programme, ménage de ce qu'un lanceur ≤ 1.4 laissait, `--desinstaller` : jumeau des fonctions de lidar2map ; et la lecture du verrou (paquets absents, empreinte, ligne de commande de pip) |
| `_serve_web.py` | Les réglages de gpxsolar pour `nico579_commons.serveweb` (variable de proxy local, lecture des paramètres de `browse-dir`) : le serveur HTTP local (`gui/`, routes `/api/*`, refus des provenances étrangères) est dans la bibliothèque commune |
| `requirements.in` | Les dépendances, déclarées une seule fois (les noms, sans versions) |
| `requirements.txt` | Le verrou : version exacte et empreinte SHA-256 de chaque paquet, pour les trois systèmes à la fois, généré par uv (voir § 8) |
| `requirements-build.in`, `requirements-build.txt` | Le même verrou plus PyInstaller, pour construire le binaire |
| `test_amorcage_commun.py` | `_amorcage.py` (copie de `nico579_commons.amorcage`, testé dans le commun) n'a pas dérivé du paquet installé, et gpxsolar l'appelle comme il faut |
| `gpxsolar_win.spec` | Spec onedir **Windows ET Linux** (ELF) |
| `gpxsolar_win_build.ps1` | Build Windows (une passe PyInstaller) |
| `setup_build_windows.ps1` | Prépare la machine Windows |
| `gpxsolar_linux_build.sh` | Build Linux (réutilise `_win.spec`) |
| `setup_build_linux.sh` | Prépare la machine Linux |
| `gpxsolar_mac.spec` | Spec macOS (arm64 ou x86_64) : onedir puis `GPXSOLAR.app` (`BUNDLE`) |
| `gpxsolar_mac_build.sh` | Build macOS (3 étapes : PyInstaller, signature ad hoc du `.app`, archive ditto) |
| `setup_build_mac.sh` | Prépare la machine macOS |
| `exe_smoke.py` | Épreuve du binaire publié (démarrage, ménage de l'ancien lanceur, serveur de l'interface), lancée par `release.yml` sur les 4 runners |
| `deploy.py` | **Déploiement unifié en 1 commande** (cross-platform Win/Mac/Linux) : tests, commit et push depuis ce dépôt, puis tag et suivi du build de release |
| `.github/workflows/ci.yml` | **CI** : tests 3 OS au push de `gpxsolar.py` |
| `.github/workflows/release.yml` | **Release** : compile 3 OS + publie, au push d'un tag `v*` |

Le venv de build est `~/.gpxsolar/venv` sur les 3 OS, créé par le setup via
`gpxsolar.py --installer-deps` (qui installe le verrou `requirements.txt` puis
quitte sans lancer la GUI), avec PyInstaller ajouté ensuite par
`requirements-build.txt`.

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
Debian 12+). Sur Linux, l'icône de notification passe par pystray (X11, ou
AppIndicator s'il est installé) ; sans affichage, le serveur tourne sans elle.

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
tests hors réseau de la CI (`test_gpxsolar.py`, `test_dossiers.py`,
`test_installation.py`, `test_serve_web.py`) et `ruff`, commit, pousse sur
`main` et, avec `--new-tag`, pose le tag
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
- **L'interface ne s'ouvre pas dans le navigateur** : le programme affiche
  l'adresse de son serveur (`gpxsolar web GUI: http://127.0.0.1:8768/`),
  à ouvrir à la main ; l'icône de notification a aussi une entrée Ouvrir.
- **Un second lancement ne démarre rien** : une instance tourne déjà, et ce
  lancement la rouvre dans le navigateur. Pour un calcul en parallèle,
  répondre N à la question du terminal, cliquer « Nouvelle instance » dans
  la page, ou lancer `gpxsolar --serve-gui --new-instance`.
- **macOS « application endommagée »** : quarantaine Gatekeeper, voir §3.
- **Ancienne extraction toujours présente (Windows)** : la 1.5 la retire à son
  premier démarrage, sauf si une instance d'une version ≤ 1.4 y tourne
  encore ; le ménage attend alors le lancement suivant. Pour forcer, fermer
  toutes les instances de gpxsolar, puis supprimer `%LOCALAPPDATA%\gpxsolar\`
  (jamais `gpxsolar-data`, qui contient les réglages).

### Spécifique Linux

- **Pas d'icône de notification** : pystray a besoin d'un affichage (X11) ou
  d'AppIndicator. Sans eux, le programme l'annonce, tourne sans icône et
  s'arrête par Ctrl+C.
- **`ModuleNotFoundError: No module named 'venv'`** : le module venv de Python
  est packagé séparément sur Debian/Ubuntu. `sudo apt install python3.12-venv`.

### Spécifique macOS

- **« application endommagée / développeur non identifié »** (Gatekeeper sur
  un `.app` signé ad hoc, non notarisé) : `xattr -dr com.apple.quarantine
  GPXSOLAR.app`, ou clic droit → Ouvrir → Ouvrir quand même.
- **`PermissionError`, `slice is not valid mach-o`, `.app` qui ne démarre
  pas** : l'archive a été extraite par un outil qui ne restitue ni les liens
  symboliques, ni les bits d'exécution, ni les attributs du `.app` (le module
  `zipfile` de Python, par exemple). Réextraire avec le Finder ou
  `ditto -x -k gpxsolar-macos-arm64.zip .`.
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
python test_serve_web.py    # interface web : serveur, instances, pont JS
```

`test_serve_web.py` éprouve l'interface web sur un vrai port de la boucle
locale : pages et routes servies, refus des provenances étrangères, instance
déjà ouverte, nouvelle instance, parcours des fichiers, et accord entre la
page, le pont `gui/web_bridge.js` et les routes du serveur.

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

---

## 8. Dépendances

Les dépendances de gpxsolar sont déclarées **une seule fois**, dans
`requirements.in` (les noms, sans versions), et verrouillées dans
`requirements.txt` : la version exacte et l'empreinte SHA-256 de chacun des
paquets, indirects compris, pour Windows, macOS et Linux à la fois. Tout le
reste l'installe, sans liste à tenir :

| Qui | Comment |
|---|---|
| Mode sources (`python gpxsolar.py`) | crée `~/.gpxsolar/venv` et y installe le verrou, réinstallé quand un verrou plus récent arrive avec une mise à jour |
| Construction (`setup_build_*`) | le même, puis `requirements-build.txt` (le même verrou plus PyInstaller) |
| CI | `pip install --require-hashes -r requirements.txt` |

Avant ce verrou, la liste des paquets vivait à quatre endroits (deux listes
dans `gpxsolar.py`, une dans `ci.yml`, une dans `smoke.yml`) et aucune version
n'était figée : deux constructions du même commit, à un mois d'écart,
n'embarquaient pas les mêmes bibliothèques.

### Ajouter, retirer ou mettre à jour un paquet

Modifier `requirements.in` (ou demander la mise à jour d'un paquet), puis
régénérer les deux verrous avec [uv](https://docs.astral.sh/uv/) (0.9 ou plus ;
`pip install uv`) :

```bash
uv pip compile requirements.in --universal --python-version 3.9 --generate-hashes -o requirements.txt
uv pip compile requirements-build.in --universal --python-version 3.9 --generate-hashes -o requirements-build.txt
# une seule mise à jour, sans toucher au reste :
uv pip compile requirements.in --universal --python-version 3.9 --generate-hashes \
    --upgrade-package rasterio -o requirements.txt
```

`--universal` produit un seul fichier valable pour tous les systèmes et toutes
les versions de Python depuis 3.9 : les versions qui diffèrent (numpy, scipy...)
portent un marqueur d'environnement, que pip évalue à l'installation.
`pip-compile` de pip-tools ne sait pas le faire (il résout pour la machine où
il tourne), d'où uv. Le fichier produit est un `requirements.txt` ordinaire,
que le pip d'un Python nu comprend : le bootstrap n'a pas besoin d'uv.

Le job « Verrous des dependances » de la CI vérifie, à chaque changement, que
les deux verrous se résolvent pour les quatre systèmes de construction de
`release.yml` (Windows, Linux, macOS Apple Silicon et Intel), en roues, sauf
les paquets qui n'en ont pas et que la CI nomme explicitement : un verrou
régénéré qui choisirait une version sans roue pour l'un des systèmes
casserait sa construction le jour de la release.

### Quatre points à connaître

- **Mac Intel** : numba ne publie plus de roue macOS x86_64 depuis la 0.61.
  Le verrou l'exclut sur ce seul système (marqueur dans `requirements.in`) et
  gpxsolar y calcule en NumPy pur, comme avant le verrou : son installation
  y échouait déjà, sans bloquer le démarrage. lidar2map, lui, garde sur ces
  Mac la dernière pile qui a des roues (numba 0.60).
- **Paquets installés depuis leurs sources** : `simplekml` et `srtm.py` ne
  sont publiés qu'en source, `timezonefinder` n'a de roues que pour Linux
  depuis la 8.2, et `h3` n'en a pas pour les Mac Intel. pip les construit à
  l'installation (l'archive source a son empreinte dans le verrou). Le job de
  la CI nomme exactement ceux-là, système par système : un nouveau paquet sans
  roue le ferait échouer au lieu de passer inaperçu.
- **Tout ou rien** : `pip install --require-hashes -r` n'installe rien si un
  seul paquet échoue. Avant le verrou, un paquet facultatif qui ne s'installait
  pas était laissé de côté et gpxsolar démarrait sans lui. Désormais, un
  paquet sans roue pour votre version de Python (une 3.13 ou 3.14 trop
  récente, par exemple) bloque l'installation, avec le message de pip. La CI
  garantit les roues pour Python 3.12 sur les quatre systèmes ; pour une autre
  version de Python, en cas d'échec, utiliser 3.12.
- Au démarrage, `python gpxsolar.py` ne relance pas pip tant que les paquets
  de `requirements.in` sont installés ; ceux qui portent un marqueur (numba)
  n'y sont pas exigés, car ils manquent à bon droit sur certains systèmes.
  `--bootstrap=pip` installe le verrou dans l'environnement courant, **quitte
  à changer la version de paquets déjà installés** : préférer le venv par
  défaut, ou `--bootstrap=none` pour gérer soi-même.

Le bootstrap (`_installation.py`) est le jumeau de celui de lidar2map
(`_bootstrap_runtime.py`) : il ne peut pas vivre dans `nico579-commons`, car il
s'exécute avant que le moindre paquet soit installé, celui-ci compris. Deux
différences voulues : numba est exclu sur Mac Intel ici (lidar2map le fixe à
0.60), et gpxsolar n'a ni JRE ni osmosis à installer.
