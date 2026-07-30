#!/usr/bin/env python3
"""smoke_gpxsolar.py - test de fumee reseau des sources de donnees.

Importe gpxsolar et appelle SES vraies fonctions d'acces aux donnees, sur un
point connu couvert (Toulouse par defaut). Pas de reimplementation HTTP : c'est
le pipeline reel qui est exerce (projection -> tuile -> download -> lecture
raster -> echantillonnage), donc un changement de format ou de nom de couche
est detecte, pas seulement une panne DNS.

Pourquoi : l'endpoint IGN historique (archives .7z BDALTI/RGEALTI) est mort a
la migration cartes.gouv.fr et on ne l'a su qu'en l'utilisant. Les sources
actuelles (WMS + API altimetrique Geoplateforme) peuvent casser pareil.

Deux familles de checks :
  - pipeline reel, charge utile minuscule : API altimetrique (JSON), WMS avec
    tile_px reduit (~6 Ko/tuile au lieu de 16 Mo), lookup commune.
  - HEAD seul pour les tuiles lourdes (WorldCover ~120 Mo, Copernicus ~44 Mo) :
    on valide l'URL construite PAR LE CODE (constantes de classe) et le type
    de contenu, sans tirer la tuile.

Statuts :
  PASS  donnee plausible obtenue                  -> exit 0
  FAIL  endpoint casse / vide / hors plage        -> le harness sort en 1
  SKIP  dependance absente

Usage :
  python smoke_gpxsolar.py
  python smoke_gpxsolar.py --only geopf-alti,geopf-wms-rgealti
  python smoke_gpxsolar.py --skip srtm1
Reseau requis. Pense pour tourner regulierement (cron CI ou manuel).
"""
import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("GPXSOLAR_BOOTSTRAP", "none")   # pas de bootstrap/venv

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests                       # noqa: E402
import gpxsolar as G                  # noqa: E402

# Point de test : Toulouse. Couvert par TOUTES les sources (LiDAR HD inclus,
# meme point que le provider fr-ign du smoke lidar2map).
LAT, LON = 43.604, 1.444
ALTI_MIN, ALTI_MAX = 120.0, 200.0     # altitude plausible a Toulouse (~146 m)


def _check_geo_api_gouv(tmp):
    """geo.api.gouv.fr : lookup commune -> code departement."""
    dept = G.get_department_from_coords(LAT, LON)
    if dept != "31":
        return False, f"departement '{dept}' au lieu de '31'"
    return True, "departement 31"


def _check_geopf_alti(tmp):
    """API altimetrique Geoplateforme (elevation.json) = coeur du mode Pente."""
    mgr = G.HGTDataManager(str(tmp), source="ign_rgealti_5m", log_func=lambda *a: None)
    z = mgr._geopf_point_elevations([LAT], [LON])
    val = float(z[0])
    if not (ALTI_MIN <= val <= ALTI_MAX):
        return False, f"altitude {val:.1f} m hors plage {ALTI_MIN}-{ALTI_MAX}"
    return True, f"z = {val:.1f} m"


def _wms_check(tmp, layer_map, prefix, label):
    """WMS Geoplateforme via le vrai LidarManager, tuile reduite a 40 px."""
    mgr = G.LidarManager(
        log_func=lambda *a: None,
        layer_map=layer_map,
        cache_dir=str(tmp / prefix), cache_prefix=prefix,
        tile_px=40)                    # 40x40 float32 = 6,4 Ko (defaut : 2000 px)
    key = next(iter(layer_map))
    vals = mgr.get_values_vec(key, [LAT], [LON])
    val = float(vals[0])
    if val == 0.0:
        return False, "0 m rendu (tuile absente, nodata ou couche renommee)"
    if not (ALTI_MIN <= val <= ALTI_MAX):
        return False, f"{label} = {val:.1f} m hors plage {ALTI_MIN}-{ALTI_MAX}"
    return True, f"{label} = {val:.1f} m"


def _check_geopf_wms_rgealti(tmp):
    """Couche ELEVATION.ELEVATIONGRIDCOVERAGE = DEM du mode Ombre."""
    return _wms_check(tmp, {"mnt": "ELEVATION.ELEVATIONGRIDCOVERAGE"},
                      "SMOKE_RGEALTI", "MNT RGE ALTI")


def _check_geopf_wms_lidarhd(tmp):
    """Couches LiDAR HD MNT (le MNH sort une hauteur, pas une altitude)."""
    return _wms_check(tmp, {"mnt": G.LidarManager.LAYER_MAP["mnt"]},
                      "SMOKE_LIDARHD", "MNT LiDAR HD")


def _head(url, expect_type=None):
    """HEAD sur une URL construite par le code (tuiles trop lourdes a tirer)."""
    r = requests.head(url, timeout=30, allow_redirects=True,
                      headers={"User-Agent": "gpxsolar-smoke"})
    if r.status_code != 200:
        return False, f"HTTP {r.status_code} sur {url.rsplit('/', 1)[-1]}"
    size = int(r.headers.get("content-length", 0))
    ctype = r.headers.get("content-type", "")
    if expect_type and expect_type not in ctype:
        return False, f"content-type '{ctype}' au lieu de '{expect_type}'"
    if size <= 0:
        return False, "content-length nul ou absent"
    return True, f"{size / 1e6:.0f} Mo, {ctype}"


def _check_worldcover(tmp):
    """S3 ESA WorldCover : URL batie par VegetationManager (tuile ~120 Mo)."""
    tile = G.VegetationManager.tile_indices_to_name(
        int(LAT // 3) * 3, int(LON // 3) * 3)    # grille WorldCover : pas de 3 deg
    return _head(G.VegetationManager.WORLDCOVER_URL.format(tile=tile))


def _check_copernicus(tmp):
    """S3 Copernicus DEM 30 m : nom de tuile calcule par le code."""
    fn = G.HGTDataManager.copernicus_tile_filename(LAT, LON)
    url = f"{G.HGTDataManager.COPERNICUS_BASE_URL}/{fn[:-len('.tif')]}/{fn}"
    return _head(url)


def _check_srtm1(tmp):
    """srtm.py (source par defaut) : telecharge une tuile puis echantillonne."""
    try:
        import srtm    # noqa: F401  (import differe cote gpxsolar : cf. _init_srtm)
    except ImportError:
        return None, "srtm.py absent"
    mgr = G.HGTDataManager(str(tmp), source="srtm1", log_func=lambda *a: None)
    val = mgr.get_elevation(LAT, LON)
    if val is None:
        return False, "get_elevation a rendu None"
    val = float(val)
    if not (ALTI_MIN <= val <= ALTI_MAX):
        return False, f"z = {val:.1f} m hors plage {ALTI_MIN}-{ALTI_MAX}"
    return True, f"z = {val:.1f} m"


CHECKS = {
    "geo-api-gouv":        _check_geo_api_gouv,
    "geopf-alti":          _check_geopf_alti,
    "geopf-wms-rgealti":   _check_geopf_wms_rgealti,
    "geopf-wms-lidarhd":   _check_geopf_wms_lidarhd,
    "worldcover-s3":       _check_worldcover,
    "copernicus-s3":       _check_copernicus,
    "srtm1":               _check_srtm1,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="", help="liste de checks (virgules)")
    ap.add_argument("--skip", default="", help="checks a exclure (virgules)")
    args = ap.parse_args()

    names = [n.strip() for n in args.only.split(",") if n.strip()] or list(CHECKS)
    skip = {n.strip() for n in args.skip.split(",") if n.strip()}
    inconnus = [n for n in names if n not in CHECKS]
    if inconnus:
        sys.exit(f"check inconnu : {', '.join(inconnus)}\n"
                 f"disponibles : {', '.join(CHECKS)}")

    n_fail = n_skip = n_pass = 0
    print(f"Smoke reseau gpxsolar - point ({LAT}, {LON})\n")
    with tempfile.TemporaryDirectory(prefix="gpxsolar_smoke_") as td:
        tmp = Path(td)
        for name in names:
            if name in skip:
                print(f"[SKIP] {name:22s} exclu (--skip)")
                n_skip += 1
                continue
            t0 = time.time()
            try:
                ok, msg = CHECKS[name](tmp)
            except Exception as e:
                ok, msg = False, f"{type(e).__name__}: {e}"
            dt = time.time() - t0
            if ok is None:
                print(f"[SKIP] {name:22s} {msg}")
                n_skip += 1
            elif ok:
                print(f"[PASS] {name:22s} {msg}  ({dt:.1f}s)")
                n_pass += 1
            else:
                print(f"[FAIL] {name:22s} {msg}  ({dt:.1f}s)")
                n_fail += 1

    print(f"\n{n_pass} PASS, {n_fail} FAIL, {n_skip} SKIP")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
