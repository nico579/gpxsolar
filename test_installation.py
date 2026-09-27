"""Tests de _installation.py : le programme livré tel quel depuis la 1.5.0.

Ce qu'un lanceur <= 1.4 laissait (son extraction, son bundle zippé) est
retiré au démarrage, jamais le dossier d'où tourne le programme ; et
--desinstaller ne supprime jamais le programme lui-même. Fichiers réels dans
un dossier temporaire, jamais les vrais dossiers de l'utilisateur.

Exécution :
    python test_installation.py
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _installation


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


if __name__ == "__main__":
    os.environ.setdefault("GPXSOLAR_BOOTSTRAP", "none")
    unittest.main()
