# -*- coding: utf-8 -*-
"""Tests de caractérisation de gpxsolar.py.

Exécution :
    python test_gpxsolar.py          # runner intégré (aucune dépendance test)
    pytest test_gpxsolar.py          # ou via pytest, mêmes fonctions

Particularité : on n'importe PAS gpxsolar. Son bootstrap s'exécute au niveau
module (installation de dépendances, relance de process possibles) ; on
extrait donc le SOURCE des fonctions pures via ast et on les exécute dans un
namespace contrôlé avec des stubs. Ça teste le code réellement présent dans
le fichier, sans ses effets de bord d'import.

Prérequis : numpy (les tests moteur utilisent aussi un stub soleil, pas
pysolar).
"""
import ast
import math
import os
import sys

import numpy as np

SRC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpxsolar.py")

# Constantes répliquées (valeurs stables du module ; les tests qui en
# dépendent échoueraient franchement si elles divergeaient un jour).
EARTH_RADIUS = 6371000.0
OBSERVER_EYE_HEIGHT = 1.7
WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
WGS84_E2 = 2 * WGS84_F - WGS84_F ** 2
SOLAR_ROUND_SEC = 600
SOLAR_ROUND_DEG = 2e-3

_WANTED = {
    "_bilinear_sample_raster", "adaptive_distances", "equirect_m_vec",
    "get_meters_per_degree_wgs84", "get_meters_per_degree_wgs84_vec",
    "compute_ray_intersections_detailed", "get_sun_blocking_type_vec",
    "extend_to_sun", "solar_altaz_cached", "_q_time", "_q_coord",
}


def _extract_segments():
    with open(SRC_PATH, encoding="utf-8") as f:
        src = f.read()
    segments = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in _WANTED:
            segments[node.name] = ast.get_source_segment(src, node)
    missing = _WANTED - set(segments)
    assert not missing, f"fonctions introuvables dans gpxsolar.py : {missing}"
    return segments


_SEG = _extract_segments()


def _make_ns(**extra):
    ns = {"np": np, "math": math, "EARTH_RADIUS": EARTH_RADIUS,
          "OBSERVER_EYE_HEIGHT": OBSERVER_EYE_HEIGHT, "WGS84_A": WGS84_A,
          "WGS84_E2": WGS84_E2, "SOLAR_ROUND_SEC": SOLAR_ROUND_SEC,
          "SOLAR_ROUND_DEG": SOLAR_ROUND_DEG}
    ns.update(extra)
    return ns


def _load(ns, *names):
    for n in names:
        exec(_SEG[n], ns)
    return ns


# ---------------------------------------------------------------------------
# Interpolation bilinéaire (convention centres de pixels + extension de bord)
# ---------------------------------------------------------------------------

def test_bilinear_center_convention():
    ns = _load(_make_ns(), "_bilinear_sample_raster")
    bil = ns["_bilinear_sample_raster"]
    arr = np.array([[0.0, 10.0], [20.0, 30.0]])

    def one(r, c, **kw):
        return bil(arr, np.array([r]), np.array([c]), **kw)[0]

    # Centre exact du pixel (0,0) : la valeur de la cellule, pas une moyenne.
    assert np.isclose(one(0.0, 0.0), 0.0)
    # Coin partagé des 4 pixels : moyenne.
    assert np.isclose(one(0.5, 0.5), 15.0)
    # Première/dernière demi-cellule : extension de bord (pas de bande à 0).
    assert np.isclose(one(-0.3, -0.3), 0.0)
    assert np.isclose(bil(arr + 5.0, np.array([-0.3]), np.array([-0.3]))[0], 5.0)
    assert np.isclose(one(1.3, 0.0), 20.0)
    # Hors footprint -> fallback.
    assert one(-0.6, 0.0, fallback=-777.0) == -777.0
    # Voisin nodata -> retombée plus proche voisin (q00).
    arr_nd = np.array([[0.0, 10.0], [-9999.0, 30.0]])
    assert np.isclose(bil(arr_nd, np.array([0.5]), np.array([0.5]))[0], 0.0)
    # Valeur interpolée quelconque.
    expected = (0 * 0.25 + 10 * 0.75) * 0.75 + (20 * 0.25 + 30 * 0.75) * 0.25
    assert np.isclose(one(0.25, 0.75), expected)


# ---------------------------------------------------------------------------
# Distance equirectangulaire (antiméridien)
# ---------------------------------------------------------------------------

def test_equirect_antimeridian():
    ns = _load(_make_ns(), "equirect_m_vec")
    eq = ns["equirect_m_vec"]
    # 179,9° -> -179,9° à l'équateur = 0,2° de longitude ≈ 22,26 km.
    d = eq(np.array([0.0]), np.array([179.9]), np.array([0.0]), np.array([-179.9]))[0]
    assert 22000 < d < 22500, f"antiméridien : {d} m"
    # Segment ordinaire inchangé par la normalisation.
    d2 = eq(np.array([45.0]), np.array([6.0]), np.array([45.001]), np.array([6.001]))[0]
    assert 130 < d2 < 140, f"segment ordinaire : {d2} m"


# ---------------------------------------------------------------------------
# Échantillonnage adaptatif des rayons
# ---------------------------------------------------------------------------

def test_adaptive_distances_counts():
    ns = _load(_make_ns(), "adaptive_distances")
    ad = ns["adaptive_distances"]
    assert ad(1000.0, initial_step=0.5).size == 441   # LiDAR (budget mémoire)
    assert ad(1000.0, initial_step=5.0).size == 44    # config standard
    d = ad(200.0, initial_step=5.0)
    assert d.size > 0 and d.max() < 200.0             # borné par max_dist


# ---------------------------------------------------------------------------
# Cache solaire : le pas temporel fait partie de la clé
# ---------------------------------------------------------------------------

def test_solar_cache_key_includes_step():
    from datetime import datetime, timezone
    calls = []

    def fake_get_position(lat, lon, dtutc):
        calls.append(dtutc)
        return (180.0, 45.0)  # (azimut, hauteur), convention pysolar

    ns = _make_ns(GET_POSITION=fake_get_position, SOLAR_CACHE={})
    _load(ns, "_q_time", "_q_coord", "solar_altaz_cached")
    f = ns["solar_altaz_cached"]

    # Timestamp multiple de 600 s : sans le pas dans la clé, les entrées
    # 600 s et 60 s entreraient en collision (multiples de 600 ⊂ de 60).
    dt = datetime.fromtimestamp(1_780_000_200, tz=timezone.utc)
    assert 1_780_000_200 % 600 == 0
    f(45.0, 6.0, dt, step_s=600)
    f(45.0, 6.0, dt, step_s=60)
    assert len(calls) == 2, "collision de clés entre pas 600 s et 60 s"
    assert len(ns["SOLAR_CACHE"]) == 2
    # Et le hit fonctionne toujours à pas égal.
    f(45.0, 6.0, dt, step_s=60)
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Moteur de ray-tracing : chunké == référence point à point, et invariant
# au chunking ; need_hits/as_codes équivalents au chemin historique
# ---------------------------------------------------------------------------

def _ground_f(lats, lons):
    lats = np.asarray(lats, dtype=np.float64)
    lons = np.asarray(lons, dtype=np.float64)
    return 200.0 + 150.0 * np.sin(lats * 300.0) + 100.0 * np.cos(lons * 200.0)


def _obj_f(lats, lons):
    lats = np.asarray(lats, dtype=np.float64)
    lons = np.asarray(lons, dtype=np.float64)
    return 15.0 * (np.sin(lats * 700.0 + lons * 300.0) > 0.6)


def _fake_sun(lats, lons, ts, step_s=SOLAR_ROUND_SEC):
    lats = np.asarray(lats)
    alts = 35.0 * np.sin(lats * 500.0) + 5.0   # mélange jour/nuit
    azs = (np.abs(np.asarray(lons)) * 9000.0) % 360.0
    return alts, azs


class _FakeMgr:
    step = 5.0
    max_distance = 1000.0
    shadowmode = "both"

    def get_ground_elevations_vec(self, lats, lons):
        return _ground_f(lats, lons)

    def get_ground_and_object_elevations_vec(self, lats, lons):
        return _ground_f(lats, lons), _obj_f(lats, lons)


def _engine_ns(budget):
    src = _SEG["get_sun_blocking_type_vec"]
    marker = "RAY_BUDGET_ELEMENTS = 2_000_000"
    assert marker in src, "constante budget introuvable dans le source"
    ns = _make_ns(solar_altaz_cached_vec=_fake_sun)
    _load(ns, "adaptive_distances", "get_meters_per_degree_wgs84_vec",
          "compute_ray_intersections_detailed")
    exec(src.replace(marker, f"RAY_BUDGET_ELEMENTS = {budget}"), ns)
    return ns


def _reference(lats, lons, shadow_mode):
    ns = _load(_make_ns(), "adaptive_distances", "get_meters_per_degree_wgs84_vec")
    alts_sun, azs_sun = _fake_sun(lats, lons, None)
    distances = ns["adaptive_distances"](_FakeMgr.max_distance, initial_step=_FakeMgr.step)
    mpd = ns["get_meters_per_degree_wgs84_vec"]
    st_out, hit_out = [], []
    for i in range(len(lats)):
        if alts_sun[i] <= 0:
            st_out.append("NIGHT"); hit_out.append(None); continue
        m_lat, m_lon = mpd(np.array([lats[i]]))
        g0 = _ground_f([lats[i]], [lons[i]])[0]
        rad_alt = np.deg2rad(alts_sun[i]); rad_az = np.deg2rad(azs_sun[i])
        rl = lats[i] + distances * np.cos(rad_az) / m_lat[0]
        rn = lons[i] + distances * np.sin(rad_az) / m_lon[0]
        ray = g0 + OBSERVER_EYE_HEIGHT + distances * np.tan(rad_alt) \
            + distances ** 2 / (2 * EARTH_RADIUS)
        g = _ground_f(rl, rn); o = _obj_f(rl, rn)
        if shadow_mode == "relief":
            o = np.zeros_like(o)
        elif shadow_mode == "vegetation":
            g = np.full_like(g, g0)
        obst = g + o
        relief_b = bool(np.any(g > ray + 0.1))
        veg_b = bool(np.any((obst > ray + 0.1) & (o > 0)))
        code = 3 if (relief_b and veg_b) else (1 if relief_b else (2 if veg_b else 0))
        if shadow_mode == "relief":
            code = {2: 0, 3: 1}.get(code, code)
        elif shadow_mode == "vegetation":
            code = {1: 0, 3: 2}.get(code, code)
        st_out.append({0: "SUN", 1: "RELIEF", 2: "VEGETATION", 3: "RELIEF_VEG"}[code])
        blocking = obst > ray + 0.1
        hit_out.append((rl[int(np.argmax(blocking))], rn[int(np.argmax(blocking))])
                       if np.any(blocking) else None)
    return st_out, hit_out


def _hits_equal(a, b):
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if (x is None) != (y is None):
            return False
        if x is not None and not (np.isclose(x[0], y[0]) and np.isclose(x[1], y[1])):
            return False
    return True


def _test_points(n=400, seed=42):
    rng = np.random.default_rng(seed)
    return (45.0 + rng.uniform(-0.01, 0.01, n),
            6.0 + rng.uniform(-0.01, 0.01, n),
            np.full(n, 1_780_000_000.0))


def test_ray_tracing_matches_reference_and_chunk_invariant():
    lats, lons, ts = _test_points()
    ns_small = _engine_ns(900)       # nombreux sous-lots
    ns_big = _engine_ns(10 ** 9)     # un seul sous-lot
    for mode in ("both", "relief", "vegetation"):
        mgr = _FakeMgr(); mgr.shadowmode = mode
        ref_st, ref_hits = _reference(lats, lons, mode)
        st_s, hits_s = ns_small["get_sun_blocking_type_vec"](lats, lons, ts, mgr)
        st_b, hits_b = ns_big["get_sun_blocking_type_vec"](lats, lons, ts, mgr)
        assert st_s == ref_st, f"statuts != référence (mode {mode})"
        assert _hits_equal(hits_s, ref_hits), f"hits != référence (mode {mode})"
        assert st_s == st_b and _hits_equal(hits_s, hits_b), \
            f"résultat dépend du chunking (mode {mode})"


def test_need_hits_as_codes_equivalence():
    STATUS = {0: "SUN", 1: "RELIEF", 2: "VEGETATION", 3: "RELIEF_VEG", 4: "NIGHT"}
    lats, lons, ts = _test_points(seed=7)
    ns = _engine_ns(900)
    fn = ns["get_sun_blocking_type_vec"]
    for mode in ("both", "relief"):
        mgr = _FakeMgr(); mgr.shadowmode = mode
        strings, hits = fn(lats, lons, ts, mgr)
        codes, no_hits = fn(lats, lons, ts, mgr, need_hits=False, as_codes=True)
        assert no_hits is None
        assert isinstance(codes, np.ndarray) and codes.dtype == np.uint8
        assert [STATUS[c] for c in codes] == strings, \
            f"as_codes diverge du chemin strings (mode {mode})"
        assert hits is not None and len(hits) == len(strings)


# ---------------------------------------------------------------------------
# Rayons KML : même convention géométrique que le moteur (cap = azimut)
# ---------------------------------------------------------------------------

def test_extend_to_sun_heads_toward_azimuth():
    ns = _load(_make_ns(), "get_meters_per_degree_wgs84", "extend_to_sun")
    m_lat, m_lon = ns["get_meters_per_degree_wgs84"](45.0)
    for az in (0.0, 90.0, 180.0, 270.0):
        lon2, lat2, alt2 = ns["extend_to_sun"](45.0, 6.0, 100.0, az, 45.0)
        north = (lat2 - 45.0) * m_lat
        east = (lon2 - 6.0) * m_lon
        bearing = math.degrees(math.atan2(east, north)) % 360.0
        assert abs((bearing - az + 180) % 360 - 180) < 0.5, \
            f"cap {bearing:.1f}° pour azimut {az}° (rayon inversé ?)"
        assert alt2 > 100.0, "le rayon vers le soleil doit monter"
    # Soleil sous l'horizon : pas de rayon.
    assert ns["extend_to_sun"](45.0, 6.0, 100.0, 180.0, -1.0) is None


# ---------------------------------------------------------------------------
# Runner autonome (sans pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    failures = 0
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        try:
            fn()
            print(f"[OK ] {name}")
        except AssertionError as e:
            failures += 1
            print(f"[FAIL] {name} : {e}")
        except Exception as e:
            failures += 1
            print(f"[ERR ] {name} : {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests OK")
    sys.exit(1 if failures else 0)
