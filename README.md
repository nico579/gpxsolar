# gpxsolar

**Analyse d'ensoleillement d'une randonnée GPX — ombres de relief (MNT/MNH) et de végétation — sortie KML/KMZ pour Google Earth + CSV.**

Script Python autonome qui prend une trace GPX, une date et une heure de départ,
puis calcule pour chaque point du parcours s'il est au soleil ou à l'ombre, en
tenant compte du relief (modèle numérique de terrain) et de la végétation
(ESA WorldCover). Il produit un KML/KMZ colorisé à ouvrir dans Google Earth et
un récapitulatif CSV.

> ⚠️ **Statut** : usage personnel diffusé. Développé et testé sur Windows 10/11.
> Linux et macOS sont supportés par l'architecture de build mais testés
> partiellement — voir [TEST_LINUX_MAC.md](TEST_LINUX_MAC.md). Retours bienvenus
> via les [issues GitHub](https://github.com/nico579/gpxsolar/issues).

---

## Ce que ça produit

À partir d'un fichier GPX + date/heure :

- **Profil d'ensoleillement** le long de la trace : chaque point est classé
  *soleil* / *ombre* en simulant la position du soleil (azimut + hauteur via
  `pysolar`) et en projetant un rayon contre le relief et la canopée.
- **Ombres de relief** depuis un MNT, avec plusieurs sources d'altitude au choix :
  - **SRTM1 / Copernicus DEM** — mondial, basse/moyenne résolution
  - **IGN BD ALTI 25 m** — France, moyenne résolution
  - **IGN RGE ALTI 5 m** — France, haute résolution
  - **IGN LiDAR HD 0.5 m** — France, très haute résolution (MNT, MNS, MNH)
- **Ombres de végétation** via ESA WorldCover (hauteur de canopée estimée),
  désactivables.
- **Sorties** : KML/KMZ colorisé (Google Earth) + CSV agrégé. Option de points de
  passage horodatés tout au long du parcours.

---

## Installation et utilisation

Deux façons d'utiliser gpxsolar :

| | **A. Script Python** | **B. Exécutable autonome** |
|---|---|---|
| **Prérequis** | Python 3.12 | Aucun |
| **Première install** | ~5 min (bootstrap deps) | Aucun |
| **Mises à jour** | `git pull` + relance | Patcher le bundle en une commande : `python update_app.py` (ou `--release` pour les 3 OS — voir [`update_app.py`](update_app.py)) |
| **Distribuable** | Non — chaque utilisateur installe Python | Oui — `.exe` / `.app` / binaire Linux + `gpxsolar_bundle.zip` côte à côte |
| **Idéal pour** | dev / Linux / contribuer | utilisateur final / distribuer |

### A. Script Python

Au premier lancement, le script crée `~/.gpxsolar/venv` et y installe ses
dépendances (numpy, pyproj, rasterio, shapely, pysolar, pywebview, simplekml,
timezonefinder, gpxpy, pandas…). ~80 Mo, **une seule fois**.

#### Windows 10+
```powershell
git clone https://github.com/nico579/gpxsolar
cd gpxsolar
python gpxsolar.py
```

#### macOS 11+
```bash
brew install python@3.12
git clone https://github.com/nico579/gpxsolar
cd gpxsolar
python3.12 gpxsolar.py
```

#### Linux (Debian / Ubuntu)
```bash
sudo apt install python3.12 python3.12-venv git
git clone https://github.com/nico579/gpxsolar
cd gpxsolar
python3.12 gpxsolar.py
```

Modes de bootstrap : `--bootstrap=auto` (défaut, venv isolé), `--bootstrap=pip`
(install dans le Python courant), `--bootstrap=none` (vérifie seulement),
`--help-bootstrap` (aide).

### B. Exécutable autonome

Pas de Python à installer côté utilisateur final. Le livrable contient son propre
runtime (Python embarqué + dépendances).

#### 1. Obtenir le livrable

**Option a — Télécharger depuis [Releases](https://github.com/nico579/gpxsolar/releases)** :

| OS | Archive | Extraire avec |
|----|---------|---------------|
| Windows 10/11 (x86_64) | `gpxsolar-windows-x86_64.zip` | `Expand-Archive` ou double-clic |
| Linux Ubuntu 24.04+ (x86_64) | `gpxsolar-linux-x86_64.tar.gz` | `tar xzf` |
| macOS 12+ (Apple Silicon) | `gpxsolar-macos-arm64.zip` | `unzip` puis `xattr -dr com.apple.quarantine GPXSOLAR.app` |

L'archive contient le binaire/launcher et son `gpxsolar_bundle.zip` côte à côte.

**Option b — Builder soi-même.** Un script de setup machine (à faire **une fois**)
puis un script de build (à relancer à chaque mise à jour de `gpxsolar.py`).

##### Windows
```powershell
git clone https://github.com/nico579/gpxsolar
cd gpxsolar
.\setup_build_windows.ps1     # 1. Setup : Python 3.12, deps, PyInstaller
.\gpxsolar_win_build.ps1      # 2. Build -> dist\gpxsolar.exe + dist\gpxsolar_bundle.zip
```

##### macOS (Apple Silicon)
```bash
git clone https://github.com/nico579/gpxsolar
cd gpxsolar
bash setup_build_mac.sh       # 1. Setup
bash gpxsolar_mac_build.sh    # 2. Build -> dist/GPXSOLAR.app + dist/gpxsolar-macos-arm64.zip
```

##### Linux (Ubuntu / Debian)
Linux réutilise la spec `gpxsolar_win.spec` (PyInstaller produit un ELF sous Linux,
le nom `_win` est trompeur).
```bash
git clone https://github.com/nico579/gpxsolar
cd gpxsolar
bash setup_build_linux.sh       # 1. Setup
bash gpxsolar_linux_build.sh    # 2. Build -> dist/gpxsolar + dist/gpxsolar_bundle.zip
```

Documentation complète du build (architecture du bundle, mise à jour sans rebuild,
dépannage) : **[BUILD.md](BUILD.md)**.

#### 2. Lancer le livrable

| OS | Commande |
|----|----------|
| Windows | Double-clic sur `gpxsolar.exe` (ou terminal pour voir le log) |
| Linux | `chmod +x gpxsolar && ./gpxsolar` dans le dossier extrait |
| macOS | Double-clic sur `GPXSOLAR.app`. 1er lancement bloqué par Gatekeeper : `xattr -dr com.apple.quarantine GPXSOLAR.app` puis double-clic |

Le premier lancement extrait le bundle (~5-10 s, une fois) dans :
- Windows : `%LOCALAPPDATA%\gpxsolar\`
- macOS : `~/Library/Application Support/gpxsolar/`
- Linux : `~/.local/share/gpxsolar/`

Désinstallation propre : `gpxsolar(.exe) --desinstaller` (supprime le bundle
extrait + le venv).

---

## Utilisation

gpxsolar s'utilise via son **interface graphique** (pywebview). Lancez-le sans
argument :

```bash
python gpxsolar.py        # (ou gpxsolar.exe / GPXSOLAR.app)
```

Puis dans la fenêtre :
1. Choisissez un fichier **GPX**.
2. Sélectionnez **date** et **heure de départ**.
3. Choisissez une **source d'altitude** : SRTM/Copernicus (mondial), IGN ALTI
   (France), IGN LiDAR HD (France, MNT/MNS/MNH).
4. Réglez les options (type d'ombre, végétation, résolution d'analyse).
5. **Lancez le calcul** → KML/KMZ + CSV.

Options en ligne de commande (passées avant le lancement de la GUI) :

```
--dem-source {srtm1,copernicus,ign_bdalti,ign_rgealti,ign_lidarhd}
--analysis-resolution 5.0        # pas d'échantillonnage du calcul d'ombre (m)
--max-shadow-distance 1000       # portée max de détection d'ombre (m)
--interpolation {nearest,bilinear,cubic}
--passage-interval-min 0         # points de passage horodatés (0=aucun)
--no-vegetation-shadow           # ignorer l'ombre de la végétation
--no-download-vegetation         # ne pas télécharger WorldCover
--output analyse_solaire.csv     # nom du CSV de sortie
```

`python gpxsolar.py --help` liste toutes les options.

---

## Documentation

- **README utilisateur** : ce fichier
- **Build & déploiement** : [BUILD.md](BUILD.md) — architecture du bundle, scripts par OS, mise à jour sans rebuild, dépannage
- **Tests Linux/Mac** : [TEST_LINUX_MAC.md](TEST_LINUX_MAC.md)
- **Aide intégrée** : `python gpxsolar.py --help`

## Licence

Code distribué sous **GNU General Public License v3.0** — voir [LICENSE](LICENSE).
Vous êtes libre d'utiliser, modifier et redistribuer selon les termes de la GPL v3.

## Auteur

Conçu et architecturé par **Nicolas Martin** ([@nico579](https://github.com/nico579)).
Code développé avec l'assistance de Claude (Anthropic) comme outil de développement.

## Remerciements

Données et outils :
- **IGN** — BD ALTI, RGE ALTI, LiDAR HD (licence Etalab 2.0)
- **NASA / USGS** — SRTM ; **Copernicus** — DEM GLO-30
- **ESA WorldCover** — couverture du sol / végétation
- Bibliothèques : pysolar, pyproj, rasterio, shapely, numpy, pandas, gpxpy,
  simplekml, timezonefinder, pywebview, Pillow, numba.
