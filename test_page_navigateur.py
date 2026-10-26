# -*- coding: utf-8 -*-
"""La page de gpxsolar dans un vrai navigateur.

test_serve_web.py vérifie le serveur et le texte de la page, sans jamais
exécuter son JavaScript. Ici, gpxsolar est lancé depuis les sources comme par
un utilisateur (sans navigateur ni icône, dossier d'état jetable, sans
amorçage), et Playwright (bibliothèque de Microsoft qui pilote un vrai
Chromium) charge sa page : aucune erreur JavaScript ni réponse en échec,
bascule FR / EN, panneau Réglages commun (version, case de démarrage
automatique), aide, historique et parcours des fichiers par le serveur.

Sans Playwright, le test est sauté ; la CI l'installe (job « navigateur »).

    python -m pip install playwright
    python -m playwright install chromium
    python test_page_navigateur.py
"""
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from nico579_commons import serveweb

try:
    from playwright.sync_api import Error as ErreurPlaywright, expect, sync_playwright
except ImportError:
    sync_playwright = None

RACINE = Path(__file__).resolve().parent
VERSION = re.search(r'^VERSION\s*=\s*"([^"]+)"', (RACINE / "gpxsolar.py").read_text(encoding="utf-8"),
                    re.MULTILINE).group(1)


def port_libre():
    with socket.socket() as sonde:
        sonde.bind(("127.0.0.1", 0))
        return sonde.getsockname()[1]


@unittest.skipIf(sync_playwright is None, "Playwright absent")
class PageGpxsolar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dossier = tempfile.TemporaryDirectory()
        cls.journal = open(Path(cls.dossier.name) / "serveur.log", "wb")
        depart = port_libre()
        environnement = dict(os.environ, GPXSOLAR_HOME=cls.dossier.name, GPXSOLAR_BOOTSTRAP="none")
        cls.processus = subprocess.Popen(
            [sys.executable, str(RACINE / "gpxsolar.py"), "--serve-gui", "--port", str(depart),
             "--no-browser", "--no-tray"],
            cwd=cls.dossier.name, env=environnement, stdout=cls.journal, stderr=subprocess.STDOUT)
        cls.adresse = None
        limite = time.monotonic() + 120
        while cls.adresse is None and time.monotonic() < limite:
            if cls.processus.poll() is not None:
                break
            for port in range(depart, depart + 10):
                if serveweb.instance_existante("gpxsolar", "127.0.0.1", port, timeout=.3):
                    cls.adresse = f"http://127.0.0.1:{port}/"
                    break
            time.sleep(.2)
        if cls.adresse is None:
            cls.arreter()
            raise AssertionError("gpxsolar n'a pas démarré : "
                                 + (Path(cls.dossier.name) / "serveur.log").read_text(errors="replace")[-2000:])
        cls.playwright = sync_playwright().start()
        try:
            cls.navigateur = cls.playwright.chromium.launch(headless=True)
        except ErreurPlaywright as erreur:
            cls.playwright.stop()
            cls.arreter()
            raise unittest.SkipTest(f"Chromium absent : {erreur}")

    @classmethod
    def arreter(cls):
        # Sans icône, gpxsolar ne s'arrête que par Ctrl+C : le test le termine.
        if cls.processus.poll() is None:
            cls.processus.terminate()
            try:
                cls.processus.wait(15)
            except subprocess.TimeoutExpired:
                cls.processus.kill()
                cls.processus.wait()
        cls.journal.close()
        cls.dossier.cleanup()

    @classmethod
    def tearDownClass(cls):
        cls.navigateur.close()
        cls.playwright.stop()
        cls.arreter()

    def setUp(self):
        self.contexte = self.navigateur.new_context(locale="fr-FR", viewport={"width": 1280, "height": 800})
        self.page = self.contexte.new_page()
        self.erreurs = []
        self.page.on("pageerror", lambda erreur: self.erreurs.append(str(erreur)))
        self.page.on("console", lambda message: self.erreurs.append(message.text)
                     if message.type == "error" else None)
        self.page.on("response", lambda r: self.erreurs.append(f"HTTP {r.status} {r.url}")
                     if r.status >= 400 else None)
        self.page.goto(self.adresse)
        expect(self.page.locator("#btn-run")).to_be_visible()

    def tearDown(self):
        self.contexte.close()
        self.assertEqual(self.erreurs, [])

    def test_la_page_se_charge_et_change_de_langue(self):
        expect(self.page.locator("#btn-run")).to_have_text("▶ Lancer le calcul")
        self.page.get_by_role("button", name="EN", exact=True).click()
        expect(self.page.locator("html")).to_have_attribute("lang", "en")
        expect(self.page.locator("#btn-run")).to_have_text("▶ Run")
        expect(self.page.locator("#nico579-reglages-bouton")).to_have_text("⚙ Settings…")
        self.page.get_by_role("button", name="FR", exact=True).click()
        expect(self.page.locator("#btn-run")).to_have_text("▶ Lancer le calcul")
        expect(self.page.locator("#nico579-maj")).to_be_hidden()

    def test_le_panneau_reglages_commun(self):
        self.page.locator("#nico579-reglages-bouton").click()
        panneau = self.page.get_by_role("dialog", name="Réglages")
        expect(panneau).to_contain_text(f"Version {VERSION}")
        # La case « Démarrer automatiquement avec le système » : affichée, jamais
        # cochée ici, puisqu'elle écrit dans le système.
        expect(panneau.get_by_role("checkbox")).to_be_visible()
        self.page.keyboard.press("Escape")
        expect(panneau).to_be_hidden()

    def test_aide_et_historique(self):
        # L'aide est le README de la version installée, dans la langue de la
        # page, ouvert dans un nouvel onglet : pas de fenêtre d'aide à part.
        aide = self.page.locator("#aide")
        expect(aide).to_have_attribute("target", "_blank")
        expect(aide).to_have_attribute(
            "href", re.compile(rf"/nico579/gpxsolar/blob/v{re.escape(VERSION)}/README(\.fr)?\.md$"))
        self.page.locator("#btn-hist").click()
        expect(self.page.locator("#panneau-hist")).to_be_visible()
        self.page.locator("#btn-hist").click()
        expect(self.page.locator("#panneau-hist")).to_be_hidden()

    def test_le_serveur_parcourt_les_dossiers(self):
        self.page.locator("#f-gpx + button").click()
        parcours = self.page.locator("#browse-modal")
        expect(parcours).to_be_visible()
        expect(self.page.locator("#browse-path")).not_to_be_empty()
        expect(self.page.locator("#browse-list > *").first).to_be_visible()
        parcours.get_by_role("button", name="Fermer").click()
        expect(parcours).to_be_hidden()


if __name__ == "__main__":
    unittest.main()
