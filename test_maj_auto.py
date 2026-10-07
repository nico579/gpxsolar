"""Installation automatique de gpxsolar : ce qui lui est propre.

Le protocole (préparation, assistant, retour arrière) est testé dans
nico579_commons (maj_install) ; ici, la description de gpxsolar (noms
d'archive, installation dédiée), l'auto-test que passe un bundle téléchargé,
l'entrée du menu de l'icône et la page.

    python -m unittest test_maj_auto
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gpxsolar
from nico579_commons import maj_archive, maj_install

RACINE = Path(__file__).resolve().parent


def _systeme_onedir():
    """Un dossier PyInstaller « onedir » (Windows, Linux) quel que soit le système
    des tests : sous macOS, le bundle publié est une .app, autre disposition."""
    nom = "Windows" if sys.platform == "win32" else "Linux"
    return mock.patch.multiple(maj_install.platform, system=lambda: nom,
                               machine=lambda: "x86_64")


class AutoTest(unittest.TestCase):
    def lancer(self, *argv):
        return gpxsolar._auto_test_version(list(argv))

    def test_accepte_sa_version_avec_ou_sans_v(self):
        self.assertEqual(self.lancer("--self-test-version", gpxsolar.VERSION), 0)
        self.assertEqual(self.lancer("--self-test-version", "v" + gpxsolar.VERSION), 0)

    def test_refuse_une_autre_version(self):
        self.assertEqual(self.lancer("--self-test-version", "0.0.1"), 1)

    def test_sans_valeur(self):
        self.assertEqual(self.lancer("--self-test-version"), 2)

    def test_refuse_un_bundle_sans_les_fichiers_communs(self):
        # Un exécutable dont le .spec oublie les données de nico579_commons : refusé par
        # la CI, et par la mise à jour automatique avant de remplacer quoi que ce soit.
        from nico579_commons import serveweb
        with mock.patch.object(serveweb, "fichiers_manquants", return_value=["reglages.js"]):
            self.assertEqual(self.lancer("--self-test-version", gpxsolar.VERSION), 1)

    def test_refuse_si_l_interface_est_introuvable(self):
        with mock.patch.object(gpxsolar, "_resoudre_gui_dir", side_effect=RuntimeError("absente")):
            self.assertEqual(self.lancer("--self-test-version", gpxsolar.VERSION), 1)

    def test_main_court_circuite_tout_le_reste(self):
        with mock.patch.object(sys, "argv", ["gpxsolar", "--self-test-version", gpxsolar.VERSION]):
            with self.assertRaises(SystemExit) as sortie:
                gpxsolar.main()
        self.assertEqual(sortie.exception.code, 0)


class VerificationDeVersion(unittest.TestCase):
    def test_la_derniere_reponse_est_gardee_dans_le_dossier_d_etat(self):
        # Un redémarrage ne repose pas la question à GitHub tant qu'elle a moins d'une heure.
        verificateur = gpxsolar._verificateur_de_version()
        self.assertEqual(verificateur._cache, gpxsolar._dossiers.DOSSIERS.dossier_etat() / "maj.json")
        self.assertEqual(verificateur.fraicheur_s, 3600)


class Description(unittest.TestCase):
    def test_application_sans_donnee_a_recopier_et_memes_arguments(self):
        with mock.patch.object(sys, "argv", ["gpxsolar", "--port", "9000"]):
            app = gpxsolar._application_installation()
        self.assertEqual(app.nom, "gpxsolar")
        self.assertEqual(app.donnees_preservees, ())
        self.assertEqual(app.arguments_relance, ("--port", "9000"))

    def test_noms_d_archive_des_systemes_publies(self):
        for systeme, machine, attendu in (
                ("Windows", "AMD64", "gpxsolar-windows-x86_64.zip"),
                ("Linux", "x86_64", "gpxsolar-linux-x86_64.tar.gz"),
                ("Darwin", "arm64", "gpxsolar-macos-arm64.zip"),
                ("Darwin", "x86_64", "gpxsolar-macos-x86_64.zip")):
            with self.subTest(systeme=systeme, machine=machine):
                self.assertEqual(maj_install.archive_standard(
                    "gpxsolar", racine_macos="GPXSOLAR.app", systeme=systeme,
                    machine=machine)[0], attendu)

    def test_depuis_les_sources_pas_d_installation_automatique(self):
        with self.assertRaises(maj_archive.ErreurMiseAJour) as c:
            gpxsolar._disposition_installation()
        self.assertEqual(c.exception.code, "source_mode")

    def test_bundle_fige_dedie_accepte(self):
        with tempfile.TemporaryDirectory() as tmp:
            dossier = Path(tmp).resolve() / "gpxsolar"
            (dossier / "_internal").mkdir(parents=True)
            exe = dossier / ("gpxsolar.exe" if sys.platform == "win32" else "gpxsolar")
            exe.write_bytes(b"x")
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", str(exe)), _systeme_onedir():
                disposition = gpxsolar._disposition_installation()
        self.assertEqual(disposition.install_root, dossier)
        self.assertTrue(disposition.asset_name.startswith("gpxsolar-"))

    def test_un_dossier_general_est_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            dossier = Path(tmp).resolve() / "Telechargements"
            (dossier / "_internal").mkdir(parents=True)
            (dossier / "photo.jpg").write_bytes(b"x")
            exe = dossier / ("gpxsolar.exe" if sys.platform == "win32" else "gpxsolar")
            exe.write_bytes(b"x")
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", str(exe)), _systeme_onedir():
                with self.assertRaises(maj_archive.ErreurMiseAJour) as c:
                    gpxsolar._disposition_installation()
        self.assertEqual(c.exception.code, "unsafe_install")


class FauxVerificateur:
    page_des_releases = "https://github.com/nico579/gpxsolar/releases/latest"

    def __init__(self, version="9.9.9"):
        self.info = {"version": version, "page": "https://x/r", "assets": []} if version else None

    def disponible(self):
        return self.info

    def verifier(self):
        return True


class FauxInstallateur:
    def __init__(self, possible=True):
        self._possible = possible
        self.demarre = 0

    def possible(self):
        return (self._possible, "" if self._possible else "source_mode")

    def demarrer(self):
        self.demarre += 1
        return True


class MenuIcone(unittest.TestCase):
    def actions(self, installateur, verificateur=None):
        return gpxsolar._actions_tray(
            "http://127.0.0.1:8765/", RACINE / "gui", lambda: None,
            verificateur or FauxVerificateur(), installateur)

    def test_mise_a_jour_installe_quand_c_est_possible(self):
        installateur = FauxInstallateur(True)
        with mock.patch("webbrowser.open") as ouvrir:
            self.actions(installateur).mettre_a_jour()
        self.assertEqual(installateur.demarre, 1)
        ouvrir.assert_not_called()

    def test_mise_a_jour_ouvre_la_page_sinon(self):
        installateur = FauxInstallateur(False)
        with mock.patch("webbrowser.open") as ouvrir:
            self.actions(installateur).mettre_a_jour()
        self.assertEqual(installateur.demarre, 0)
        ouvrir.assert_called_once_with("https://x/r")

    def test_sans_installateur_comme_avant(self):
        with mock.patch("webbrowser.open") as ouvrir:
            self.actions(None).mettre_a_jour()
        ouvrir.assert_called_once_with("https://x/r")

    def test_l_icone_ne_se_ferme_pas_pendant_le_telechargement(self):
        # C'est l'installateur qui lève l'arrêt quand la nouvelle version est prête.
        self.assertFalse(self.actions(FauxInstallateur()).mettre_a_jour_referme)


class Page(unittest.TestCase):
    def test_la_page_place_le_bouton_reglages_avant_le_choix_de_langue(self):
        html = (RACINE / "gui" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="/nico579-reglages.js"></script>', html)
        self.assertLess(html.index("/app.js"), html.index("/nico579-reglages.js"))
        # L'emplacement du bouton : juste avant FR / EN, comme dans blink2video.
        self.assertLess(html.index('id="nico579-reglages"'), html.index('data-nico579-langue'))

    def test_les_executables_embarquent_les_fichiers_communs(self):
        # PyInstaller n'embarque les donnees d'un paquet que si le .spec le demande ;
        # sans cela la page reclame /nico579-maj.js et /nico579-reglages.js en 404.
        for spec in ("gpxsolar_win.spec", "gpxsolar_mac.spec"):
            texte = (RACINE / spec).read_text(encoding="utf-8")
            self.assertIn('collect_data_files("nico579_commons")', texte, spec)

    def test_la_page_charge_le_bandeau_commun(self):
        html = (RACINE / "gui" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="/nico579-maj.js"></script>', html)
        self.assertLess(html.index("/app.js"), html.index("/nico579-maj.js"))


if __name__ == "__main__":
    unittest.main()
