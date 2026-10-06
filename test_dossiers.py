"""_dossiers.py : l'état dans le dossier standard de l'OS, les sorties dans
un dossier visible, et la reprise unique de l'état d'une version <= 1.3.
Jumeau de tests/test_dossiers.py de lidar2map.

Isolation stricte : dans chaque test, le dossier standard et Documents sont
remplacés par des dossiers temporaires. Changer LOCALAPPDATA ne suffirait
pas : sous Windows, platformdirs interroge le shell, qui l'ignore.
"""
import ast

import json
import logging
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ["GPXSOLAR_BOOTSTRAP"] = "none"
# Jamais les vrais dossiers d'état et de sorties de l'utilisateur (voir
# _dossiers.py) : un dossier temporaire, sauf s'il en est déjà fourni un.
if not os.environ.get("GPXSOLAR_HOME"):
    os.environ["GPXSOLAR_HOME"] = tempfile.mkdtemp(prefix="gpxsolar-tests-")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import _dossiers  # noqa: E402
import _installation  # noqa: E402


class _Isole(unittest.TestCase):
    """Dossier standard, Documents et ancien dossier courant dans un dossier
    temporaire au nom accentué ; GPXSOLAR_HOME retiré."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.racine = Path(tmp.name).resolve() / "Données é"
        self.etat = self.racine / "AppData" / "Local" / "gpxsolar-data"
        self.documents = self.racine / "Documents"
        self.ancien = self.racine / "Randos" / "gpxsolar-windows-x86_64"
        self.ancien.mkdir(parents=True)
        sans_home = {cle: valeur for cle, valeur in os.environ.items()
                     if cle != "GPXSOLAR_HOME"}
        for correctif in (
                mock.patch.dict(os.environ, sans_home, clear=True),
                mock.patch.object(_dossiers.DOSSIERS, "dossier_etat_standard",
                                  return_value=self.etat),
                mock.patch.object(_dossiers.DOSSIERS, "documents",
                                  return_value=self.documents)):
            correctif.start()
            self.addCleanup(correctif.stop)

    def ecrire_ancien(self, nom, contenu):
        (self.ancien / nom).write_text(contenu, encoding="utf-8")

    def preferences(self):
        return json.loads((self.etat / _dossiers.DOSSIERS.preferences).read_text(encoding="utf-8"))


class ConfigurationTests(unittest.TestCase):
    """Les paramètres propres à gpxsolar. Les règles (calcul des dossiers,
    reprise unique, repli sans platformdirs) sont celles de
    nico579_commons.dossiers et y sont éprouvées."""

    def test_noms_et_fichiers_de_gpxsolar(self):
        d = _dossiers.DOSSIERS
        self.assertEqual((d.application, d.nom_etat, d.nom_sorties, d.variable_home),
                         ("gpxsolar", "gpxsolar-data", "gpxsolar", "GPXSOLAR_HOME"))
        self.assertEqual((d.preferences, d.cle_sorties, d.marqueur),
                         ("gpx_analyzer_prefs.json", "dossier_sorties",
                          ".gpxsolar_etat_migre.json"))
        self.assertEqual(d.fichiers_etat, ("gpx_analyzer_prefs.json",
                                           "gpx_analyzer_config.json",
                                           "gpx_analyzer_history.json"))
        self.assertEqual(d.dossiers_sorties, ("GPX_Ombres", "HGT", "WorldCover",
                                              "LIDAR_CACHE", "RGEALTI_CACHE"))

    def test_les_fichiers_d_etat_sont_ceux_que_gpxsolar_ecrit(self):
        source = (ROOT / "gpxsolar.py").read_text(encoding="utf-8")
        for nom in ("gpx_analyzer_config.json", "gpx_analyzer_history.json"):
            self.assertIn(nom, source)
            self.assertIn(nom, _dossiers.DOSSIERS.fichiers_etat)
        self.assertIn("_dossiers.DOSSIERS.preferences", source)

    def test_la_copie_locale_de_la_logique_n_existe_plus(self):
        for nom in ("dossier_etat", "dossier_sorties", "preparer_etat", "_documents",
                    "_dossier_etat_standard", "_force", "_reprendre"):
            self.assertFalse(hasattr(_dossiers, nom), nom)
        self.assertFalse((ROOT / "_atomic_files.py").exists())


def _preparer_dossiers_de_gpxsolar(etat):
    """_preparer_dossiers() et ses arguments de chemin, extraits du source de
    gpxsolar.py comme le fait test_gpxsolar.py : son import lancerait le
    bootstrap et chargerait toutes ses dépendances."""
    source = (ROOT / "gpxsolar.py").read_text(encoding="utf-8")
    # sys à part : un test du programme figé le remplace dans cet espace,
    # jamais dans le vrai module sys.
    espace = {"Path": Path, "os": os, "logging": logging, "_dossiers": _dossiers,
              "_installation": _installation, "sys": types.SimpleNamespace(
                  frozen=False, executable=sys.executable),
              "VERSION": "1.4.0", "DOSSIER_ETAT": etat}
    for noeud in ast.parse(source).body:
        cible = (noeud.name if isinstance(noeud, ast.FunctionDef) else
                 noeud.targets[0].id if isinstance(noeud, ast.Assign)
                 and isinstance(noeud.targets[0], ast.Name) else None)
        if cible in ("_ARGUMENTS_CHEMINS", "_preparer_dossiers"):
            exec(ast.get_source_segment(source, noeud), espace)
    return espace["_preparer_dossiers"]


class PreparerDossiersTests(_Isole):
    """Le branchement dans gpxsolar.py : chemins de la ligne de commande
    rendus absolus, reprise, puis dossier des sorties comme dossier courant."""

    def setUp(self):
        super().setUp()
        self.addCleanup(os.chdir, os.getcwd())
        self.preparer = _preparer_dossiers_de_gpxsolar(self.etat)

    def arguments(self, **valeurs):
        defauts = {"gpx": None, "output": "analyse_solaire.csv", "hgt_dir": "HGT",
                   "vegetation_dir": "WorldCover", "temp_dir": tempfile.gettempdir()}
        return types.SimpleNamespace(**{**defauts, **valeurs})

    def test_lancement_depuis_l_ancien_dossier(self):
        self.ecrire_ancien("gpx_analyzer_history.json", "[]")
        (self.ancien / "GPX_Ombres").mkdir()
        os.chdir(self.ancien)
        args = self.arguments(gpx="trace.gpx", vegetation_dir="Couverts")

        self.preparer(args)

        self.assertEqual(args.gpx, str(self.ancien / "trace.gpx"))
        self.assertEqual(args.vegetation_dir, str(self.ancien / "Couverts"))
        # Les valeurs par défaut restent relatives : elles tombent dans le
        # dossier des sorties, désormais courant.
        self.assertEqual((args.output, args.hgt_dir), ("analyse_solaire.csv", "HGT"))
        self.assertTrue((self.etat / "gpx_analyzer_history.json").is_file())
        self.assertEqual(Path.cwd(), self.ancien)

    def test_nouvelle_installation_sorties_dans_documents(self):
        os.chdir(self.ancien)
        self.preparer(self.arguments())
        self.assertEqual(Path.cwd(), self.documents / "gpxsolar")
        self.assertTrue(self.etat.is_dir())
        self.assertFalse((self.etat / _dossiers.DOSSIERS.marqueur).exists())

    def test_sous_processus_de_l_interface_ne_reprend_rien(self):
        self.ecrire_ancien("gpx_analyzer_history.json", "[]")
        os.chdir(self.ancien)
        with mock.patch.dict(os.environ, {"GPXSOLAR_CHILD": "1"}):
            self.preparer(self.arguments())
        self.assertFalse((self.etat / "gpx_analyzer_history.json").exists())
        self.assertEqual(Path.cwd(), self.documents / "gpxsolar")

    def test_programme_fige_reprend_l_etat_de_son_propre_dossier(self):
        # Sans lanceur depuis la 1.5, le programme figé peut partir de
        # n'importe où (« / » depuis le Finder, le dossier d'un raccourci) :
        # l'état d'une 1.3 se reprend dans le dossier du programme, où le
        # lanceur le lançait, pas dans le dossier courant.
        self.ecrire_ancien("gpx_analyzer_history.json", "[]")
        ailleurs = self.racine / "ailleurs"
        ailleurs.mkdir()
        os.chdir(ailleurs)
        self.preparer.__globals__["sys"] = types.SimpleNamespace(
            frozen=True, executable=str(self.ancien / "gpxsolar.exe"))

        self.preparer(self.arguments())

        self.assertTrue((self.etat / "gpx_analyzer_history.json").is_file())


def _version_du_code():
    """La constante VERSION de gpxsolar.py. Une version écrite en dur dans le
    test est à changer à chaque release, et un oubli ne se voit qu'en CI :
    ici, sans les dépendances, le test est sauté."""
    source = (ROOT / "gpxsolar.py").read_text(encoding="utf-8")
    for noeud in ast.parse(source).body:
        if (isinstance(noeud, ast.Assign) and isinstance(noeud.targets[0], ast.Name)
                and noeud.targets[0].id == "VERSION"):
            return ast.literal_eval(noeud.value)
    raise AssertionError("constante VERSION introuvable dans gpxsolar.py")


class LancementReelTests(unittest.TestCase):
    """Le vrai programme : --version sort avant main(), et ni l'import ni
    l'analyse des arguments ne créent quoi que ce soit."""

    def test_version_ne_cree_aucun_dossier(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            env = dict(os.environ, GPXSOLAR_HOME=str(home), GPXSOLAR_BOOTSTRAP="none",
                       PYTHONUTF8="1")
            resultat = subprocess.run(
                [sys.executable, str(ROOT / "gpxsolar.py"), "--version"],
                cwd=tmp, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            sortie = resultat.stdout + resultat.stderr
            if resultat.returncode != 0 and "missing critical Python modules" in sortie:
                # bootstrap=none : ce Python n'a pas les dépendances (elles
                # vivent dans ~/.gpxsolar/venv) ; la CI, elle, les installe.
                self.skipTest("dépendances critiques absentes de ce Python")
            self.assertEqual(resultat.returncode, 0, sortie)
            self.assertIn(f"gpxsolar {_version_du_code()}", resultat.stdout)
            self.assertFalse(home.exists(), "--version a créé le dossier d'état")
            self.assertEqual(list(Path(tmp).iterdir()), [], "--version a écrit dans le dossier courant")


if __name__ == "__main__":
    unittest.main()
