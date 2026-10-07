"""Tests de _installation.py : le programme livré tel quel depuis la 1.5.0.

Ce qu'un lanceur <= 1.4 laissait (son extraction, son bundle zippé) est
retiré au démarrage, jamais le dossier d'où tourne le programme ; les
fichiers de Qt que la 1.5.0 laisse dans _internal aussi, seulement eux ; et
--desinstaller ne supprime jamais le programme lui-même. Fichiers réels dans
un dossier temporaire, jamais les vrais dossiers de l'utilisateur. Sous
Linux, les programmes du système lancés par le binaire retrouvent leur
LD_LIBRARY_PATH d'origine.

Exécution :
    python test_installation.py
"""

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _amorcage
import _installation

RACINE = Path(__file__).resolve().parent


class DossierProgramme(unittest.TestCase):
    def test_exe_figé_son_propre_dossier(self):
        exe = Path("/opt/gpxsolar/gpxsolar")
        self.assertEqual(_installation.dossier_programme(exe), exe.resolve().parent)

    def test_app_macos_le_dossier_qui_contient_le_app(self):
        exe = Path("/Applications/GPXSOLAR.app/Contents/MacOS/gpxsolar")
        self.assertEqual(_installation.dossier_programme(exe),
                         Path("/Applications").resolve())


class _Racine(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(prefix="gpxsolar-installation-")
        self.addCleanup(temporaire.cleanup)
        self.racine = Path(temporaire.name)
        self.home = self.racine / "home"
        self.home.mkdir()
        self.programme = self.racine / "Programs" / "gpxsolar" / "gpxsolar.exe"
        self.programme.parent.mkdir(parents=True)
        self.programme.write_bytes(b"")

    def extraction(self, marque=True) -> Path:
        dossier = _installation.dossier_extraction(systeme="Linux", home=self.home)
        (dossier / "_internal").mkdir(parents=True)
        if marque:
            (dossier / _installation.MARQUE_EXTRACTION).write_text("sha\n0", encoding="utf-8")
        return dossier

    def nettoyer(self):
        return _installation.nettoyer_ancienne_extraction(
            systeme="Linux", home=self.home, executable=self.programme)


class MenageAncienLanceur(_Racine):
    def test_extraction_et_bundle_voisin_retires(self):
        dossier = self.extraction()
        bundle = self.programme.parent / _installation.NOM_BUNDLE
        bundle.write_bytes(b"zip")

        retires = self.nettoyer()

        self.assertFalse(dossier.exists())
        self.assertFalse(bundle.exists())
        self.assertEqual(set(retires), {dossier, bundle.resolve()})
        self.assertFalse(dossier.with_name(dossier.name + ".ancienne-extraction").exists())

    def test_dossier_sans_la_marque_du_lanceur_laisse_tel_quel(self):
        dossier = self.extraction(marque=False)
        self.assertEqual(self.nettoyer(), [])
        self.assertTrue(dossier.is_dir())

    def test_extraction_occupee_attend_le_lancement_suivant(self):
        # Sous Windows, une ancienne instance qui tourne encore depuis
        # l'extraction en bloque le renommage.
        dossier = self.extraction()
        with mock.patch.object(_installation.os, "rename",
                               side_effect=PermissionError("occupé")):
            self.assertEqual(self.nettoyer(), [])
        self.assertTrue((dossier / _installation.MARQUE_EXTRACTION).is_file())

    def test_programme_installe_dans_l_extraction_laisse_tel_quel(self):
        dossier = self.extraction()
        self.programme = dossier / "gpxsolar.exe"
        self.programme.write_bytes(b"")
        self.assertEqual(self.nettoyer(), [])
        self.assertTrue(self.programme.is_file())


class Desinstallation(_Racine):
    def test_supprime_extraction_et_venv(self):
        dossier = self.extraction()
        venv = self.home / ".gpxsolar" / "venv"
        (venv / "bin").mkdir(parents=True)
        messages = []

        complet = _installation.desinstaller(
            systeme="Linux", home=self.home, ecrire=messages.append)

        self.assertTrue(complet)
        self.assertFalse(dossier.exists())
        self.assertFalse(venv.exists())
        self.assertEqual(messages.count("    ✓ removed"), 2)

    def test_garde_le_dossier_d_ou_tourne_le_programme(self):
        dossier = self.extraction()
        programme = dossier / "gpxsolar"
        programme.write_bytes(b"")
        messages = []

        complet = _installation.desinstaller(
            systeme="Linux", home=self.home, executable=str(programme),
            ecrire=messages.append)

        self.assertTrue(complet)
        self.assertTrue(programme.is_file())
        self.assertTrue(any("kept, the running program lives there" in ligne
                            for ligne in messages))

    def test_windows_suit_localappdata(self):
        local = self.racine / "Local"
        cibles = _installation.chemins_desinstallation(
            systeme="Windows", home=self.home, localappdata=str(local))
        self.assertEqual(cibles[0][0], local / "gpxsolar")
        self.assertEqual(cibles[1][0], self.home / ".gpxsolar" / "venv")


class RestesDeQt(unittest.TestCase):
    """La 1.6 décompressée par-dessus la 1.5.0 : seuls les noms des listes,
    plus anciens que le reste de la version en place, quittent _internal."""

    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(prefix="gpxsolar-restes-qt-")
        self.addCleanup(temporaire.cleanup)
        self.interne = Path(temporaire.name) / "_internal"
        self.interne.mkdir()
        repere = self.interne / _installation.REPERE_VERSION
        repere.write_bytes(b"zip")
        self.date_version = repere.stat().st_mtime
        self.ancienne = self.date_version - 86400    # la 1.5.0, la veille

    def poser(self, nom, date, dossier=True):
        chemin = self.interne / nom
        if dossier:
            (chemin / "sous").mkdir(parents=True)
            fichiers = [chemin / "a.bin", chemin / "sous" / "b.bin"]
        else:
            fichiers = [chemin]
        for fichier in fichiers:
            fichier.write_bytes(b"x")
            os.utime(fichier, (date, date))
        return chemin

    def nettoyer(self, systeme="Windows"):
        return _installation.nettoyer_restes_de_qt(systeme=systeme, interne=self.interne)

    def test_restes_anciens_retires_version_en_place_gardee(self):
        pyqt6 = self.poser("PyQt6", self.ancienne)
        libpq = self.poser("LIBPQ.dll", self.ancienne, dossier=False)
        numpy = self.poser("numpy", self.ancienne)       # hors liste, même ancien

        self.assertEqual(set(self.nettoyer()), {pyqt6, libpq})

        self.assertFalse(pyqt6.exists())
        self.assertFalse(libpq.exists())
        self.assertTrue((numpy / "a.bin").is_file())
        self.assertTrue((self.interne / _installation.REPERE_VERSION).is_file())
        self.assertEqual(self.nettoyer(), [])

    def test_nom_de_la_liste_livre_de_nouveau_reste(self):
        # Une version future qui embarquerait de nouveau qtpy : ses fichiers
        # portent la date du reste de la version.
        qtpy = self.poser("qtpy", self.date_version)
        self.assertEqual(self.nettoyer(), [])
        self.assertTrue(qtpy.is_dir())

    def test_un_seul_fichier_recent_garde_l_entree(self):
        webview = self.poser("webview", self.ancienne)
        os.utime(webview / "sous" / "b.bin", (self.date_version, self.date_version))
        self.assertEqual(self.nettoyer(), [])
        self.assertTrue((webview / "a.bin").is_file())

    def test_moins_d_une_heure_d_ecart_reste(self):
        # Décompression qui ne rend pas les dates de l'archive : les fichiers
        # de la version en place s'échelonnent sur quelques minutes.
        pyqt6 = self.poser("PyQt6", self.date_version - 600)
        self.assertEqual(self.nettoyer(), [])
        self.assertTrue(pyqt6.is_dir())

    def test_une_liste_par_systeme_rien_sous_macos(self):
        libqt = self.poser("libQt6Core.so.6", self.ancienne, dossier=False)
        libpq = self.poser("LIBPQ.dll", self.ancienne, dossier=False)
        self.assertEqual(self.nettoyer("Darwin"), [])
        self.assertEqual(self.nettoyer("Windows"), [libpq])
        self.assertEqual(self.nettoyer("Linux"), [libqt])

    def test_lien_symbolique_retire_sans_toucher_sa_cible(self):
        # Sous Linux, PyInstaller relie des bibliothèques de premier niveau
        # à leur copie rangée dans un sous-dossier.
        if os.utime not in os.supports_follow_symlinks:
            self.skipTest("date d'un lien non modifiable ici")
        cible = self.poser("numpy", self.ancienne)
        lien = self.interne / "libQt6Core.so.6"
        try:
            lien.symlink_to(cible / "a.bin")
        except OSError:
            self.skipTest("liens symboliques indisponibles ici")
        os.utime(lien, (self.ancienne, self.ancienne), follow_symlinks=False)

        self.assertEqual(self.nettoyer("Linux"), [lien])

        self.assertFalse(os.path.lexists(lien))
        self.assertTrue((cible / "a.bin").is_file())

    def test_sans_repere_rien_ne_part(self):
        pyqt6 = self.poser("PyQt6", self.ancienne)
        (self.interne / _installation.REPERE_VERSION).unlink()
        self.assertEqual(self.nettoyer(), [])
        self.assertTrue(pyqt6.is_dir())

    def test_entree_occupee_attend_le_lancement_suivant(self):
        pyqt6 = self.poser("PyQt6", self.ancienne)
        with mock.patch.object(_installation.shutil, "rmtree",
                               side_effect=PermissionError("occupé")):
            self.assertEqual(self.nettoyer(), [])
        self.assertTrue(pyqt6.is_dir())


class EnvironnementSysteme(unittest.TestCase):
    """Programmes du système lancés depuis le binaire Linux (xdg-open, le
    navigateur) : LD_LIBRARY_PATH d'origine, pas celui que préfixe
    PyInstaller. La fonction est celle de nico579_commons.environnement, et
    ses comportements y sont éprouvés ; il ne reste à vérifier ici que le
    câblage de gpxsolar.py."""

    def test_appelee_avant_tout_lancement_de_processus(self):
        source = (RACINE / "gpxsolar.py").read_text(encoding="utf-8")
        appel = source.index("environnement.retablir_environnement_systeme()")
        # Le premier processus que lance le programme est celui de l'amorçage (venv, pip).
        self.assertLess(appel, source.index("_amorcage.Amorcage("))

    def test_seulement_dans_l_executable_ou_la_bibliotheque_est_embarquee(self):
        # Depuis les sources, la bibliothèque peut ne pas être installée avant
        # le bootstrap, et la fonction n'y fait rien : l'import est gardé.
        source = (RACINE / "gpxsolar.py").read_text(encoding="utf-8")
        appel = source.index("environnement.retablir_environnement_systeme()")
        garde = source.rindex('if getattr(sys, "frozen", False):', 0, appel)
        self.assertLess(appel - garde, 400)

    def test_la_copie_locale_n_existe_plus(self):
        self.assertFalse(hasattr(_installation, "retablir_environnement_systeme"))


class Dependances(unittest.TestCase):
    """Dépendances déclarées une fois (requirements.in), verrouillées pour
    les trois systèmes (requirements.txt), plus PyInstaller pour construire
    (requirements-build.txt). Jumelle de VerrouTests de lidar2map ; la lecture
    du verrou elle-même (absents, commande, empreinte) est testée dans
    nico579-commons (tests/test_amorcage.py)."""

    @staticmethod
    def _pins(fichier):
        pins = {}
        for ligne in (RACINE / fichier).read_text(encoding="utf-8").splitlines():
            if ligne[:1].isalnum() and "==" in ligne:
                nom, reste = ligne.split("==", 1)
                version, _, marqueur = reste.partition(";")
                pins[(_amorcage.nom_normalise(nom),
                      marqueur.replace("\\", "").strip())] = version.strip()
        return pins

    def test_dependances_directes_sans_versions_ni_marqueurs(self):
        noms = _amorcage.dependances_directes(RACINE / "requirements.in", conditionnelles=True)
        for attendu in ("pytz", "srtm.py", "pysolar", "pandas", "rasterio", "pystray",
                        "nico579-commons", "numba", "py7zr"):
            self.assertIn(attendu, noms)
        for nom in noms:
            self.assertRegex(nom, r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

    def test_les_conditionnelles_ne_sont_pas_exigees_au_demarrage(self):
        # numba n'a pas de roue pour les Mac Intel : absent à bon droit de ce
        # système, le contrôle au démarrage ne l'exige pas.
        requises = _amorcage.dependances_directes(RACINE / "requirements.in")
        self.assertIn("rasterio", requises)
        self.assertNotIn("numba", requises)
        with tempfile.TemporaryDirectory() as dossier:
            fichier = Path(dossier) / "requirements.in"
            fichier.write_text("# commentaire\n-c contraintes.txt\nPillow>=10  # image\n"
                               "numba ; sys_platform != 'darwin'\nlaspy[lazrs]\n",
                               encoding="utf-8")
            self.assertEqual(_amorcage.dependances_directes(fichier),
                             ["Pillow", "laspy"])
            self.assertEqual(
                _amorcage.dependances_directes(fichier, conditionnelles=True),
                ["Pillow", "numba", "laspy"])

    def test_chaque_dependance_directe_est_dans_les_deux_verrous(self):
        for fichier in ("requirements.txt", "requirements-build.txt"):
            verrouilles = {nom for nom, _ in self._pins(fichier)}
            with self.subTest(verrou=fichier):
                for nom in _amorcage.dependances_directes(RACINE / "requirements.in",
                                                          conditionnelles=True):
                    self.assertIn(_amorcage.nom_normalise(nom), verrouilles)

    def test_le_verrou_de_construction_ajoute_pyinstaller_aux_memes_versions(self):
        execution = self._pins("requirements.txt")
        construction = self._pins("requirements-build.txt")
        self.assertEqual({k: construction.get(k) for k in execution}, execution)
        self.assertIn("pyinstaller", {nom for nom, _ in construction})
        self.assertNotIn("pyinstaller", {nom for nom, _ in execution})

    def test_chaque_paquet_verrouille_porte_ses_empreintes(self):
        # Chaque entrée commence par son nom en début de ligne ; les lignes
        # d'empreintes et de commentaires qui la suivent sont indentées.
        for fichier in ("requirements.txt", "requirements-build.txt"):
            texte = (RACINE / fichier).read_text(encoding="utf-8")
            entrees = [e for e in re.split(r"\n(?=[A-Za-z0-9])", texte)
                       if e[:1].isalnum()]
            self.assertGreater(len(entrees), 20)
            for entree in entrees:
                with self.subTest(verrou=fichier, paquet=entree.split("==", 1)[0]):
                    self.assertIn("--hash=sha256:", entree)


if __name__ == "__main__":
    os.environ.setdefault("GPXSOLAR_BOOTSTRAP", "none")
    unittest.main()
