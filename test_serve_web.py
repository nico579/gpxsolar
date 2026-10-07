# -*- coding: utf-8 -*-
"""Tests de l'interface web de gpxsolar (depuis la 1.6.0) : le serveur
_serve_web.py (ses réglages pour nico579_commons.serveweb, dont les tests de
fond sont dans la bibliothèque), les fonctions de gpxsolar.py qui l'entourent
(instance déjà ouverte, relance, parcours des fichiers) et le pont
gui/web_bridge.js.

Comme test_gpxsolar.py, on n'importe PAS gpxsolar : son bootstrap
s'exécuterait au niveau module. Ses fonctions sont extraites du source (ast)
et exécutées dans un espace de noms contrôlé. _serve_web.py, lui, ne dépend
que de nico579_commons : on l'importe tel quel.

Exécution : python test_serve_web.py
"""
import argparse
import ast
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import _serve_web  # noqa: E402
from nico579_commons import serveweb  # noqa: E402

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
    """Le serveur de gpxsolar (_serve_web.Handler) sur un vrai port de la boucle locale."""

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

        cls.serveur = serveweb.demarrer(
            handler=_serve_web.Handler, bind="127.0.0.1", port=0, trusted_host="", gui_dir=gui,
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

    def test_variable_de_proxy_local_est_celle_de_gpxsolar(self):
        # Le mécanisme (provenance des requêtes, proxy local) est celui de
        # nico579_commons.serveweb, éprouvé là ; ici, le nom que gpxsolar lui donne.
        self.assertEqual(_serve_web.Handler.variable_proxy_local,
                         "GPXSOLAR_TRUSTED_LOOPBACK_PROXY")
        self.assertTrue(issubclass(_serve_web.Handler, serveweb.Handler))

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
                DOSSIERS=types.SimpleNamespace(documents=lambda: racine)))
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
        # Figé, _loader.py remplace argv[0] par _internal/gpxsolar.py : la
        # relance passe par nico579_commons.relance, qui relance l'exécutable.
        from nico579_commons import relance
        self.assertEqual(relance.commande(fige=True, executable="C:/P/gpxsolar.exe",
                                          argv=["C:/P/_internal/gpxsolar.py", "--serve-gui"]),
                         ["C:/P/gpxsolar.exe", "--serve-gui"])
        self.assertEqual(relance.commande(fige=False, executable="python",
                                          argv=["gpxsolar.py"]),
                         ["python", "gpxsolar.py"])

    def _actions(self, verificateur, journal):
        ns = _extraire("_actions_tray", _langue_console=lambda: "fr",
                       _creer_raccourci_bureau=lambda gui_dir: journal.append("raccourci"))
        return ns["_actions_tray"]("http://127.0.0.1:8765/", ROOT / "gui",
                                   lambda: journal.append("arrete"), verificateur)

    def test_menu_commun(self):
        # Le même menu que blink2video, lidar2map et watch2notif, dont
        # « Mettre à jour vers x.y » quand une version plus récente est connue.
        from nico579_commons import tray as apptray
        verificateur = types.SimpleNamespace(disponible=lambda: None,
                                             page_des_releases="")
        actions = self._actions(verificateur, [])
        faux = types.SimpleNamespace(
            MenuItem=lambda texte, action, default=False, checked=None: texte)
        self.assertEqual(apptray.entrees(actions, faux, lambda action: None),
                         ["Ouvrir", "Redémarrer", "Arrêter",
                          "Créer un raccourci sur le Bureau"])
        verificateur.disponible = lambda: {"version": "1.7.0", "page": "p"}
        self.assertEqual(apptray.entrees(actions, faux, lambda action: None)[1],
                         "Mettre à jour vers 1.7.0")

    def test_redemarrer_arrete_puis_relance(self):
        from unittest import mock
        from nico579_commons import relance
        journal = []
        actions = self._actions(types.SimpleNamespace(disponible=lambda: None), journal)
        with mock.patch.object(relance, "relancer",
                               side_effect=lambda commande, **o: journal.append(o["nom"])), \
                mock.patch.object(sys, "argv", ["gpxsolar.py", "--port", "8765"]):
            actions.redemarrer()
        self.assertEqual(journal, ["arrete", "gpxsolar"])

    def test_mettre_a_jour_ouvre_la_page_de_la_release(self):
        from unittest import mock
        import webbrowser
        page = "https://github.com/nico579/gpxsolar/releases/tag/v1.7.0"
        actions = self._actions(types.SimpleNamespace(
            disponible=lambda: {"version": "1.7.0", "page": page}), [])
        self.assertFalse(actions.mettre_a_jour_referme)
        self.assertEqual(actions.version_disponible(), "1.7.0")
        with mock.patch.object(webbrowser, "open") as ouvrir:
            actions.mettre_a_jour()
        ouvrir.assert_called_once_with(page)

    def test_raccourci_lance_le_programme_sans_argument(self):
        # Jamais le vrai Bureau : raccourci.creer est remplacé.
        from unittest import mock
        from nico579_commons import raccourci
        ns = _extraire("SCRIPT", "_fichier_icone", "_commande_raccourci",
                       "_creer_raccourci_bureau", _langue_console=lambda: "en")
        commande, dossier = ns["_commande_raccourci"]()
        self.assertEqual(commande[-1], str(ROOT / "gpxsolar.py"))
        self.assertEqual(dossier, ROOT)
        with mock.patch.object(raccourci, "creer", return_value=0) as creer:
            self.assertEqual(ns["_creer_raccourci_bureau"](ROOT / "gui"), 0)
        args, kwargs = creer.call_args
        self.assertEqual(args[:2], ("gpxsolar", commande))
        self.assertTrue(kwargs["reduit"])
        self.assertTrue(Path(kwargs["icone"]).is_file())

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

    def test_instance_existante_interroge_le_commun_avec_le_nom_gpxsolar(self):
        # La reconnaissance d'une instance (route /api/init, champ « app ») est
        # celle de nico579_commons.serveweb, éprouvée là ; ici, seulement le
        # nom et l'hôte que gpxsolar lui donne.
        faux = types.SimpleNamespace(
            instance_existante=lambda *a: appels.append(a) or True,
            port_libre=lambda hote, port: True)
        appels = []
        resultat = self._nouvelle_instance(
            serveweb=faux, instance_existante=None,
            popen=lambda commande, **o: types.SimpleNamespace(poll=lambda: None))
        self.assertTrue(resultat["ok"])
        self.assertEqual(appels[0][:2], ("gpxsolar", "127.0.0.1"))

    def _nouvelle_instance(self, **options):
        espace = {"serveweb": serveweb}
        espace.update({k: options.pop(k) for k in ("serveweb",) if k in options})
        ns = _extraire("HOTE_GUI", "PORT_RANGE_SIZE", "SCRIPT",
                       "_demarrer_nouvelle_instance", **espace)
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


class CaseDemarrageAutomatique(unittest.TestCase):
    """La case « Démarrer automatiquement avec le système » du panneau Réglages : celle du
    commun (nico579_commons.demarrage.routes, testée chez lui), branchée sur l'entrée de
    gpxsolar. Jamais le vrai dossier Démarrage : seuls des objets de description sont lus."""

    def test_l_entree_decrit_le_serveur_sans_navigateur(self):
        import _autostart
        entree = _autostart.entree()
        self.assertEqual(entree.nom, "gpxsolar")
        self.assertEqual(entree.commande[-2:], ("--serve-gui", "--no-browser"))
        self.assertEqual(_autostart.LINUX_SERVICE_NAME, "gpxsolar.service")
        self.assertEqual(entree.label_macos, "com.nico.gpxsolar")

    def test_fige_le_programme_en_cours_est_celui_a_relancer(self):
        import _autostart
        from unittest import mock
        programme = ROOT / "gpxsolar-programme"
        with mock.patch.object(_autostart.sys, "frozen", True, create=True), \
                mock.patch.object(_autostart.sys, "executable", str(programme)):
            self.assertEqual(_autostart.commande(), [str(programme), "--serve-gui", "--no-browser"])
            self.assertEqual(_autostart.dossier_lancement(), programme.parent)

    def test_les_routes_du_commun_sont_branchees_sur_cette_entree(self):
        self.assertIn("demarrage.routes(_autostart.entree", SOURCE)
        self.assertIn("unite_systemd=_autostart.LINUX_SERVICE_NAME", SOURCE)

    def test_la_page_n_a_pas_de_case_propre(self):
        for nom in ("index.html", "app.js", "web_bridge.js"):
            texte = (ROOT / "gui" / nom).read_text(encoding="utf-8")
            self.assertNotIn("autostart", texte, nom)


if __name__ == "__main__":
    unittest.main()
