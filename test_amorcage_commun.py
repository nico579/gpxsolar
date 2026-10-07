# -*- coding: utf-8 -*-
"""Le moteur d'amorçage de gpxsolar est celui des quatre applications.

``_amorcage.py`` est une copie octet pour octet de ``nico579_commons.amorcage`` :
il tourne avant l'installation de la bibliothèque commune, qu'il ne peut donc
pas importer. Ces tests vérifient que la copie n'a pas dérivé du paquet
installé, que gpxsolar l'appelle comme il faut, et qu'un vrai lancement lit
bien son verrou. Le moteur lui-même (modes, venv, pip, relance) est testé dans
nico579-commons (tests/test_amorcage.py) : pip et venv n'y sont jamais
exécutés pour de bon, hors un essai sur un verrou vide.

Exécution : python test_amorcage_commun.py
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _amorcage

ROOT = Path(__file__).resolve().parent


def _lignes(chemin):
    """Le texte sans égard aux fins de ligne : un dépôt récupéré sous Windows
    peut les convertir, sans que la copie ait dérivé."""
    return Path(chemin).read_bytes().decode("utf-8").splitlines()


class CopieDuCommun(unittest.TestCase):
    def test_la_copie_est_identique_au_module_du_paquet(self):
        from nico579_commons import amorcage
        self.assertEqual(
            _lignes(ROOT / "_amorcage.py"), _lignes(amorcage.__file__),
            "_amorcage.py a dérivé de nico579_commons.amorcage : recopier le "
            "fichier du paquet (et monter l'épingle de nico579-commons).")


class Branchement(unittest.TestCase):
    def test_gpxsolar_declare_son_amorcage(self):
        source = (ROOT / "gpxsolar.py").read_text(encoding="utf-8")
        self.assertIn("_amorcage.Amorcage(\"gpxsolar\"", source)
        self.assertIn("reutiliser_environnement=True", source)

    def test_le_verrou_et_ses_dependances_sont_lus_au_bon_endroit(self):
        moteur = _amorcage.Amorcage("gpxsolar", ROOT, reutiliser_environnement=True)
        self.assertEqual(moteur.fichier_verrou, ROOT / "requirements.txt")
        self.assertEqual(moteur.fichier_dependances, ROOT / "requirements.in")
        self.assertEqual(moteur.variable, "GPXSOLAR_BOOTSTRAP")
        self.assertEqual(moteur.marque, "gpxsolar-verrou.sha256")
        self.assertIn("rasterio", _amorcage.dependances_directes(moteur.fichier_dependances))

    def test_aide_du_mode_sans_rien_installer(self):
        # Un vrai lancement : l'aide sort avant tout import de dépendance.
        resultat = subprocess.run([sys.executable, str(ROOT / "gpxsolar.py"), "--help-bootstrap"],
                                  capture_output=True, text=True, encoding="utf-8",
                                  timeout=120, cwd=tempfile.gettempdir())
        self.assertEqual(resultat.returncode, 0, resultat.stderr)
        for attendu in ("--bootstrap=auto", "--bootstrap=force", "GPXSOLAR_BOOTSTRAP",
                        "~/.gpxsolar/venv"):
            self.assertIn(attendu, resultat.stdout)

    def test_un_mode_invalide_sort_en_2(self):
        resultat = subprocess.run([sys.executable, str(ROOT / "gpxsolar.py"), "--bootstrap=oups"],
                                  capture_output=True, text=True, encoding="utf-8",
                                  timeout=120, cwd=tempfile.gettempdir())
        self.assertEqual(resultat.returncode, 2)
        self.assertIn("--bootstrap", resultat.stderr)


if __name__ == "__main__":
    os.environ.setdefault("GPXSOLAR_BOOTSTRAP", "none")
    unittest.main()
