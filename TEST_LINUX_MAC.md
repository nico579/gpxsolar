# Procédure de test Linux / macOS — gpxsolar

gpxsolar est développé et testé en priorité sur Windows 10/11. Linux et macOS
sont supportés par l'architecture de build mais testés partiellement. Ce
document liste les vérifications à faire et les problèmes connus.

---

## 1. Script Python (mode dev)

### Linux (Ubuntu / Debian)
```bash
sudo apt install python3.12 python3.12-venv git
git clone https://github.com/nico579/gpxsolar
cd gpxsolar
python3.12 gpxsolar.py
```
Au 1er lancement, `~/.gpxsolar/venv` est créé et les deps installées (dont
PyQt6 + WebEngine pour le backend GUI). La fenêtre pywebview doit s'ouvrir.

Vérifier :
- [ ] le venv se crée sans erreur (`python3.12-venv` présent)
- [ ] PyQt6 s'installe (wheels manylinux disponibles pour 3.12)
- [ ] la GUI s'ouvre (serveur X / Wayland requis ; en SSH headless, prévoir
      `xvfb-run` ou un display déporté)
- [ ] un calcul GPX produit un KML + un CSV

Problèmes connus :
- **`ModuleNotFoundError: No module named 'venv'`** → `sudo apt install python3.12-venv`.
- **Qt « xcb plugin »** → installer les libs système :
  `sudo apt install libxcb-cursor0 libegl1 libgl1`.
- **SSL / certificats** → le bundle `certifi` est utilisé ; en cas d'erreur,
  vérifier l'horloge système.

### macOS 11+
```bash
brew install python@3.12     # ou python.org
git clone https://github.com/nico579/gpxsolar
cd gpxsolar
python3.12 gpxsolar.py
```
Le bootstrap installe pyobjc (backend Cocoa natif) **et** PyQt6 (filet pour les
Mac headless). La GUI doit s'ouvrir.

---

## 2. Exécutable autonome

### Linux
```bash
bash setup_build_linux.sh
bash gpxsolar_linux_build.sh
cd dist && ./gpxsolar
```
Vérifier :
- [ ] le launcher extrait le bundle dans `~/.local/share/gpxsolar/`
- [ ] les lancements suivants sautent l'extraction (SHA/mtime inchangés)
- [ ] `./gpxsolar --desinstaller` supprime bundle extrait + venv

### macOS (Apple Silicon)
```bash
bash setup_build_mac.sh
bash gpxsolar_mac_build.sh
xattr -dr com.apple.quarantine dist/GPXSOLAR.app
open dist/GPXSOLAR.app
```
Vérifier :
- [ ] extraction dans `~/Library/Application Support/gpxsolar/`
- [ ] QtWebEngineProcess trouvé (hook runtime) → la GUI s'affiche
- [ ] `dist/gpxsolar-macos-arm64.zip` est distribuable (perms préservées par `ditto`)

Problèmes connus :
- **Gatekeeper « application endommagée / développeur non identifié »** :
  `.app` non signé. `xattr -dr com.apple.quarantine GPXSOLAR.app` ou
  clic droit → Ouvrir → Ouvrir quand même.
- **Écran blanc dans la GUI** : QtWebEngine mal résolu — vérifier que le hook
  runtime `hook_mac_runtime.py` positionne `QTWEBENGINEPROCESS_PATH`.

---

## 3. Mise à jour sans rebuild (cross-OS)

Depuis Windows, patcher l'archive macOS sans Mac :
```bash
python update_app.py gpxsolar-macos-arm64.zip
```
- [ ] le SHA256 change, les permissions Unix internes sont préservées
- [ ] au prochain lancement sur Mac, le bundle est ré-extrait

---

## 4. CI

`ci_github.yml` exécute sur ubuntu / macos / windows : check de syntaxe, import
complet (`GPXSOLAR_BOOTSTRAP=none`) et smoke test `--help`. Un échec sur un OS
n'arrête pas les autres (`fail-fast: false`).
