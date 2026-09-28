# -*- coding: utf-8 -*-
"""Tests de l'interface web de gpxsolar (depuis la 1.6.0) : le serveur
_serve_web.py, les fonctions de gpxsolar.py qui l'entourent (instance déjà
ouverte, port libre, relance, parcours des fichiers) et le pont
gui/web_bridge.js. Même périmètre que tests/test_serve_web.py chez lidar2map,
le jumeau, réduit à ce que gpxsolar reprend.

Comme test_gpxsolar.py, on n'importe PAS gpxsolar : son bootstrap
s'exécuterait au niveau module. Ses fonctions sont extraites du source (ast)
et exécutées dans un espace de noms contrôlé. _serve_web.py, lui, ne dépend
que de la bibliothèque standard : on l'importe tel quel.

Exécution : python test_serve_web.py
"""
import argparse
import ast
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import _serve_web  # noqa: E402

SOURCE = (ROOT / "gpxsolar.py").read_text(encoding="utf-8")


def _extraire(*noms, **espace):
    """Fonctions et constantes nommées de gpxsolar.py, exécutées dans un
    espace de noms contrôlé (voir le docstring du module)."""
    ns = {"Path": Path, "os": os, "sys": sys, "json": json, "time": time,
          "subprocess": subprocess, "argparse": argparse,
          "__file__": str(ROOT / "gpxsolar.py"), **espace}
    trouves = set()
    for noeud in ast.parse(SOURCE).body:
        nom = (noeud.name if isinstance(noeud, ast.FunctionDef)
               else noeud.targets[0].id if isinstance(noeud, ast.Assign)
               and isinstance(noeud.targets[0], ast.Name) else None)
        if nom in noms:
            exec(ast.get_source_segment(SOURCE, noeud), ns)
            trouves.add(nom)
    manquants = set(noms) - trouves
    assert not manquants, f"introuvables dans gpxsolar.py : {manquants}"
    return ns


def _port_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _requete(url, methode="GET", corps=None, entetes=None):
    """(statut, corps) d'une requête ; une erreur HTTP est un résultat."""
    requete = urllib.request.Request(url, data=corps, method=methode,
                                     headers=entetes or {})
    try:
        with urllib.request.urlopen(requete, timeout=10) as reponse:
            return reponse.status, reponse.read()
    except urllib.error.HTTPError as erreur:
        return erreur.code, erreur.read()


class ServeurTests(unittest.TestCase):
    """_serve_web.py sur un vrai port de la boucle locale."""

    @classmethod
    def setUpClass(cls):
        cls._gui = tempfile.TemporaryDirectory()
        gui = Path(cls._gui.name)
        for nom, contenu in (("index.html", "<title>page de test</title>"),
                             ("app.js", "// app"), ("style.css", "/* css */"),
                             ("web_bridge.js", "// pont")):
            (gui / nom).write_text(contenu, encoding="utf-8")
        (gui / "icone.ico").write_bytes(b"\x00\x00\x01\x00icone")
        cls.recus = []

        def boom():
            raise RuntimeError("route cassée")

        def parcourir(path, kind, exts, mode):
            cls.recus.append((path, kind, exts, mode))
            return {"ok": True}

        cls.serveur = _serve_web.demarrer(
            bind="127.0.0.1", port=0, trusted_host="", gui_dir=gui,
            api_routes={"init": lambda: {"app": "gpxsolar"}, "boom": boom,
                        "browse-dir": parcourir},
            post_routes={"echo": lambda payload: {"recu": payload}},
            favicon=gui / "icone.ico")
        cls.base = f"http://127.0.0.1:{cls.serveur.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.serveur.shutdown()
        cls.serveur.server_close()
        cls._gui.cleanup()

    def test_page_et_fichiers_servis(self):
        statut, corps = _requete(self.base + "/")
        self.assertEqual(statut, 200)
        self.assertIn(b"page de test", corps)
        for chemin, attendu in (("/app.js", b"// app"), ("/style.css", b"/* css */"),
                                ("/web_bridge.js", b"// pont")):
            self.assertEqual(_requete(self.base + chemin), (200, attendu), chemin)

    def test_icone_de_l_onglet(self):
        # Comme blink2video : /favicon.ico, gardé une semaine par le navigateur.
        with urllib.request.urlopen(self.base + "/favicon.ico", timeout=10) as reponse:
            self.assertEqual(reponse.headers["Content-Type"], "image/x-icon")
            self.assertEqual(reponse.headers["Cache-Control"], "public, max-age=604800")
            self.assertEqual(reponse.read(), b"\x00\x00\x01\x00icone")

    def test_route_get_en_json(self):
        statut, corps = _requete(self.base + "/api/init")
        self.assertEqual((statut, json.loads(corps)), (200, {"app": "gpxsolar"}))

    def test_routes_inconnues(self):
        self.assertEqual(_requete(self.base + "/api/absente")[0], 404)
        self.assertEqual(_requete(self.base + "/autre.html")[0], 404)

    def test_exception_de_route_en_500_json(self):
        statut, corps = _requete(self.base + "/api/boom")
        self.assertEqual(statut, 500)
        self.assertIn("route cassée", json.loads(corps)["error"])

    def test_post_json(self):
        statut, corps = _requete(self.base + "/api/echo", "POST", b'{"a": 1}',
                                 {"Content-Type": "application/json"})
        self.assertEqual((statut, json.loads(corps)), (200, {"recu": {"a": 1}}))

    def test_post_corps_illisible_et_route_inconnue(self):
        self.assertEqual(_requete(self.base + "/api/echo", "POST", b"{pas du json")[0], 400)
        self.assertEqual(_requete(self.base + "/api/absente", "POST", b"{}")[0], 404)

    def test_provenances_etrangeres_refusees(self):
        for entetes in ({"Host": "evil.example"},
                        {"Origin": "http://evil.example"},
                        {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(entetes=entetes):
                self.assertEqual(_requete(self.base + "/api/init", entetes=entetes)[0], 403)
                self.assertEqual(_requete(self.base + "/api/echo", "POST", b"{}",
                                          entetes)[0], 403)
        self.assertEqual(_requete(self.base + "/api/init",
                                  entetes={"Sec-Fetch-Site": "same-origin"})[0], 200)

    def test_browse_dir_lit_ses_parametres(self):
        type(self).recus.clear()
        statut, _ = _requete(self.base + "/api/browse-dir?path=C%3A%2Fgpx"
                             "&exts=.gpx,.GPX&mode=file")
        self.assertEqual(statut, 200)
        self.assertEqual(self.recus, [("C:/gpx", "", [".gpx", ".GPX"], "file")])


class FonctionsGpxsolarTests(unittest.TestCase):
    """Les fonctions de gpxsolar.py autour du serveur, extraites du source."""

    def test_parcours_liste_dossiers_et_gpx(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            (racine / "sous-dossier").mkdir()
            for nom in ("a.gpx", "B.GPX", "notes.txt"):
                (racine / nom).write_text("x", encoding="utf-8")
            ns = _extraire("_api_browse_dir", _dossiers=types.SimpleNamespace(
                _documents=lambda: racine))
            fichiers = ns["_api_browse_dir"](str(racine), "", [".gpx"], "file")
            self.assertEqual(fichiers["dirs"], ["sous-dossier"])
            self.assertEqual(fichiers["files"], ["a.gpx", "B.GPX"])
            self.assertEqual(ns["_api_browse_dir"](str(racine), "", [".gpx"], "")["files"], [])
            # Sans chemin : le dossier Documents ; chemin invalide : le dossier
            # personnel, jamais une erreur.
            self.assertEqual(ns["_api_browse_dir"]()["path"], str(racine.resolve()))
            self.assertEqual(ns["_api_browse_dir"](str(racine / "absent"))["path"],
                             str(Path.home().resolve()))

    def test_commande_relance(self):
        ns = _extraire("_commande_relance")
        relance = ns["_commande_relance"]
        self.assertEqual(relance(frozen=True, executable="C:/P/gpxsolar.exe",
                                 argv=["C:/P/_internal/gpxsolar.py", "--serve-gui"]),
                         ["C:/P/gpxsolar.exe", "--serve-gui"])
        self.assertEqual(relance(frozen=False, executable="python",
                                 argv=["gpxsolar.py"]),
                         ["python", "gpxsolar.py"])

    def test_textes_instance_existante(self):
        ns = _extraire("_textes_instance_existante")
        for lang in ("fr", "en"):
            question, annonce = ns["_textes_instance_existante"](lang)
            self.assertIn("{url}", annonce)
            self.assertIn("[N]", question)

    def test_terminal_interactif(self):
        ns = _extraire("_terminal_interactif")
        interactif = ns["_terminal_interactif"]
        tty = types.SimpleNamespace(isatty=lambda: True)
        pas_tty = types.SimpleNamespace(isatty=lambda: False)
        self.assertFalse(interactif(stdin=pas_tty, plateforme="linux"))
        self.assertTrue(interactif(stdin=tty, plateforme="linux"))
        # Windows : une console masquée ne verrait pas la question.
        self.assertFalse(interactif(stdin=tty, plateforme="win32",
                                    console_visible=lambda: False))
        self.assertTrue(interactif(stdin=tty, plateforme="win32",
                                   console_visible=lambda: True))

    def test_parser_serve_gui(self):
        ns = _extraire("PORT_GUI", "PORT_RANGE_SIZE", "_construire_parser_serve_gui")
        parser = ns["_construire_parser_serve_gui"]()
        defaut = parser.parse_args([])
        self.assertEqual((defaut.port, defaut.no_browser, defaut.no_tray,
                          defaut.new_instance), (8768, False, False, False))
        options = parser.parse_args(["--serve-gui", "--port", "9100", "--no-browser",
                                     "--no-tray", "--new-instance"])
        self.assertEqual((options.port, options.no_browser, options.no_tray,
                          options.new_instance), (9100, True, True, True))

    def test_instance_existante_ne_reconnait_que_gpxsolar(self):
        class Repondeur(http.server.BaseHTTPRequestHandler):
            app = "gpxsolar"

            def do_GET(self):
                corps = json.dumps({"app": self.app}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(corps)))
                self.end_headers()
                self.wfile.write(corps)

            def log_message(self, *args):
                pass

        ns = _extraire("HOTE_GUI", "_instance_existante")
        existe = ns["_instance_existante"]
        serveur = http.server.HTTPServer(("127.0.0.1", 0), Repondeur)
        threading.Thread(target=serveur.serve_forever, daemon=True).start()
        try:
            port = serveur.server_address[1]
            self.assertTrue(existe(port))
            Repondeur.app = "lidar2map"
            self.assertFalse(existe(port))
        finally:
            serveur.shutdown()
            serveur.server_close()
        self.assertFalse(existe(_port_libre()))

    def _nouvelle_instance(self, **options):
        ns = _extraire("HOTE_GUI", "PORT_RANGE_SIZE", "SCRIPT", "_port_libre",
                       "_demarrer_nouvelle_instance")
        return ns["_demarrer_nouvelle_instance"](port_depart=_port_libre(),
                                                 attendre=lambda s: None, **options)

    def test_nouvelle_instance_refusee_sans_icone(self):
        resultat = self._nouvelle_instance(sans_icone=True)
        self.assertFalse(resultat["ok"])

    def test_nouvelle_instance_demarre_puis_attend_sa_reponse(self):
        commandes, appels = [], []

        def popen(commande, **options):
            commandes.append(commande)
            return types.SimpleNamespace(poll=lambda: None, returncode=None)

        def instance_existante(port):
            appels.append(port)
            return len(appels) >= 2

        resultat = self._nouvelle_instance(popen=popen,
                                           instance_existante=instance_existante)
        self.assertTrue(resultat["ok"])
        self.assertEqual(commandes[0][-5:], ["--serve-gui", "--new-instance", "--port",
                                             str(resultat["port"]), "--no-browser"])

    def test_nouvelle_instance_morte_au_demarrage(self):
        resultat = self._nouvelle_instance(
            popen=lambda commande, **o: types.SimpleNamespace(poll=lambda: 1, returncode=1),
            instance_existante=lambda port: False)
        self.assertFalse(resultat["ok"])
        self.assertIn("code 1", resultat["error"])


class PontJsTests(unittest.TestCase):
    """gui/ : la page, le pont et les routes du serveur restent d'accord."""

    def setUp(self):
        self.app = (ROOT / "gui" / "app.js").read_text(encoding="utf-8")
        self.pont = (ROOT / "gui" / "web_bridge.js").read_text(encoding="utf-8")
        self.page = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")

    def test_plus_aucune_trace_de_pywebview_dans_le_code(self):
        self.assertNotIn("pywebview.api", self.app)
        self.assertNotIn("__INIT_DATA__", self.page)
        self.assertNotIn("__GPXSOLAR_JS__", self.page)

    def test_la_page_charge_le_pont_avant_app_js(self):
        self.assertLess(self.page.index('src="/web_bridge.js"'),
                        self.page.index('src="/app.js"'))
        self.assertIn('href="/style.css"', self.page)

    def test_icones_rangees_comme_les_autres_apps(self):
        # Comme blink2video, watch2notif et lidar2map : assets/<app>.png de
        # 1254 px pour l'exécutable, assets/<app>.ico aux neuf tailles de
        # celui de blink2video pour la zone de notification et l'onglet.
        self.assertIn('<link rel="icon" href="/favicon.ico">', self.page)
        png = (ROOT / "assets" / "gpxsolar.png").read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(png[16:24], (1254).to_bytes(4, "big") * 2)
        ico = (ROOT / "assets" / "gpxsolar.ico").read_bytes()
        self.assertEqual(ico[:4], b"\x00\x00\x01\x00")
        self.assertEqual(int.from_bytes(ico[4:6], "little"), 9)

    def test_chaque_appel_de_app_js_existe_dans_le_pont(self):
        definis = set(re.findall(r"^\s+(\w+):\s*\(", self.pont, re.M))
        utilises = set(re.findall(r"\bapi\.(\w+)\(", self.app))
        self.assertTrue(utilises)
        self.assertEqual(utilises - definis, set())

    def test_chaque_route_du_pont_existe_cote_serveur(self):
        routes_pont = set(re.findall(r"'/api/([\w-]+)'", self.pont))
        n = SOURCE.index("def main_serve_gui(")
        corps = SOURCE[n:SOURCE.index("\ndef ", n + 1)]
        routes_serveur = set(re.findall(r'^\s+"([\w-]+)":', corps, re.M))
        self.assertTrue(routes_pont)
        self.assertEqual(routes_pont - routes_serveur, set())
        # browse-dir est appelée directement par app.js, hors du pont.
        self.assertIn("browse-dir", routes_serveur)


if __name__ == "__main__":
    unittest.main()
