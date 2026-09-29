# -*- coding: utf-8 -*-
"""Tests du bootstrap des dépendances de gpxsolar : le verrou requirements.txt
installé dans un venv (mode auto et force), dans l'environnement courant
(mode pip), vérifié seulement (mode none), et par --installer-deps.

Comme test_serve_web.py, on n'importe PAS gpxsolar : son bootstrap
s'exécuterait au niveau module. Ses fonctions sont extraites du source (ast)
et exécutées dans un espace de noms contrôlé, avec le vrai _installation.
pip, venv et la relance dans le venv sont simulés : jamais de vrai venv, de
vrai pip, ni le vrai dossier personnel.

Exécution : python test_bootstrap.py
"""
import ast
import contextlib
import importlib
import io
import os
import platform
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import _installation

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "gpxsolar.py").read_text(encoding="utf-8")

FONCTIONS = ("_afficher_erreur_deps", "_installer_verrou", "_bootstrap_venv_auto",
             "_bootstrap_pip_courant", "_installer_deps_et_quitter",
             "_bootstrap_environnement")


def _charger():
    """Les fonctions du bootstrap, dans un espace de noms contrôlé."""
    ns = {"Path": Path, "os": os, "sys": sys, "subprocess": subprocess,
          "platform": platform, "importlib": importlib,
          "_installation": _installation, "__file__": str(ROOT / "gpxsolar.py"),
          "_relancer": mock.Mock(), "_resoudre_mode_bootstrap": mock.Mock(return_value="auto")}
    for noeud in ast.parse(SOURCE).body:
        if isinstance(noeud, ast.FunctionDef) and noeud.name in FONCTIONS:
            exec(ast.get_source_segment(SOURCE, noeud), ns)
    manquantes = set(FONCTIONS) - set(ns)
    assert not manquantes, f"introuvables dans gpxsolar.py : {manquantes}"
    return ns


def _termine(resultat=0, stderr="", stdout=""):
    return types.SimpleNamespace(returncode=resultat, stderr=stderr, stdout=stdout)


class _Bootstrap(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(prefix="gpxsolar-bootstrap-")
        self.addCleanup(temporaire.cleanup)
        self.home = Path(temporaire.name)
        self.venv = self.home / ".gpxsolar" / "venv"
        self.ns = _charger()
        for correctif in (mock.patch.object(Path, "home", return_value=self.home),
                          mock.patch.object(platform, "system", return_value="Linux"),
                          mock.patch.object(sys, "prefix", str(self.home / "systeme")),
                          mock.patch.object(sys, "base_prefix", str(self.home / "systeme")),
                          mock.patch.object(sys, "executable", str(self.home / "python"))):
            correctif.start()
            self.addCleanup(correctif.stop)
        self.sortie = io.StringIO()

    @property
    def venv_python(self):
        return self.venv / "bin" / "python"

    @property
    def marque(self):
        return self.venv / _installation.MARQUE_VERROU

    def venv_installe(self, marque=None):
        self.venv_python.parent.mkdir(parents=True)
        self.venv_python.touch()
        if marque is not None:
            self.marque.write_text(marque + "\n")

    def absentes(self, noms):
        return mock.patch.object(_installation, "dependances_absentes", return_value=noms)

    def lancer(self, fonction, *args, run=None):
        """Appelle la fonction du bootstrap ; rend (code de sortie, appels de
        subprocess.run). ``run`` : faux subprocess.run."""
        appels = []

        def faux(commande, **options):
            appels.append(list(commande))
            return (run or (lambda c, **o: _termine()))(commande, **options)

        code = None
        with mock.patch.object(subprocess, "run", side_effect=faux), \
                contextlib.redirect_stdout(self.sortie):
            try:
                self.ns[fonction](*args)
            except SystemExit as fin:
                code = fin.code
        return code, appels

    def creer_le_venv(self, commande, **options):
        """Faux subprocess.run : « python -m venv » crée l'exécutable du venv."""
        if commande[1:3] == ["-m", "venv"]:
            self.venv_python.parent.mkdir(parents=True)
            self.venv_python.touch()
        return _termine()


class BootstrapVenv(_Bootstrap):
    def test_deja_dans_le_venv_rien_a_faire(self):
        self.venv.mkdir(parents=True)
        with mock.patch.object(sys, "prefix", str(self.venv)), self.absentes(["numpy"]):
            code, appels = self.lancer("_bootstrap_venv_auto")
        self.assertIsNone(code)
        self.assertEqual(appels, [])
        self.ns["_relancer"].assert_not_called()
        self.assertIn("inside venv", self.sortie.getvalue())

    def test_tout_installe_pas_de_venv_en_mode_auto(self):
        with self.absentes([]):
            code, appels = self.lancer("_bootstrap_venv_auto")
        self.assertIsNone(code)
        self.assertEqual(appels, [])
        self.assertFalse(self.venv.exists())
        self.ns["_relancer"].assert_not_called()
        self.assertIn("venv not created", self.sortie.getvalue())

    def test_force_cree_le_venv_meme_si_tout_est_installe(self):
        with self.absentes([]):
            code, appels = self.lancer("_bootstrap_venv_auto", True, run=self.creer_le_venv)
        self.assertIsNone(code)
        self.assertEqual(appels[0], [sys.executable, "-m", "venv", str(self.venv)])
        self.assertEqual(appels[1], _installation.commande_installation(self.venv_python))
        self.assertEqual(self.marque.read_text().strip(), _installation.empreinte_verrou())
        self.ns["_relancer"].assert_called_once_with(self.venv_python, False)

    def test_venv_manquant_cree_installe_puis_relance(self):
        with self.absentes(["numpy"]):
            code, appels = self.lancer("_bootstrap_venv_auto", run=self.creer_le_venv)
        self.assertIsNone(code)
        self.assertEqual(len(appels), 2)
        self.assertIn("--require-hashes", appels[1])
        self.assertIn(str(_installation.VERROU), appels[1])
        self.assertTrue(self.marque.exists())
        self.ns["_relancer"].assert_called_once_with(self.venv_python, False)

    def test_venv_installe_depuis_le_meme_verrou_relance_sans_pip(self):
        self.venv_installe(marque=_installation.empreinte_verrou())
        with self.absentes(["numpy"]):
            code, appels = self.lancer("_bootstrap_venv_auto")
        self.assertIsNone(code)
        self.assertEqual(appels, [])
        self.ns["_relancer"].assert_called_once_with(self.venv_python, False)

    def test_venv_d_un_ancien_verrou_reinstalle_sans_le_recreer(self):
        # Nouvelle version de gpxsolar, verrou changé : le venv existant est
        # remis aux versions du verrou actuel. Un venv de la 1.6, sans marque,
        # est dans le même cas.
        for marque in ("ancienne-empreinte", None):
            with self.subTest(marque=marque):
                if self.venv.exists():
                    for chemin in sorted(self.venv.rglob("*"), reverse=True):
                        chemin.unlink() if chemin.is_file() else chemin.rmdir()
                    self.venv.rmdir()
                self.venv_installe(marque=marque)
                self.ns["_relancer"].reset_mock()
                with self.absentes(["numpy"]):
                    code, appels = self.lancer("_bootstrap_venv_auto")
                self.assertIsNone(code)
                self.assertEqual(appels, [_installation.commande_installation(self.venv_python)])
                self.assertEqual(self.marque.read_text().strip(),
                                 _installation.empreinte_verrou())
                self.ns["_relancer"].assert_called_once_with(self.venv_python, False)

    def test_installation_en_echec_sortie_1_sans_marque_ni_relance(self):
        self.venv_installe()
        with self.absentes(["numpy"]):
            code, _ = self.lancer(
                "_bootstrap_venv_auto",
                run=lambda c, **o: _termine(1, stderr="a\nb\nhash mismatch"))
        self.assertEqual(code, 1)
        self.assertFalse(self.marque.exists())
        self.ns["_relancer"].assert_not_called()
        self.assertIn("hash mismatch", self.sortie.getvalue())

    def test_creation_du_venv_impossible_sortie_1(self):
        erreur = subprocess.CalledProcessError(2, ["python", "-m", "venv"])

        def run(commande, **options):
            raise erreur

        with self.absentes(["numpy"]):
            code, appels = self.lancer("_bootstrap_venv_auto", run=run)
        self.assertEqual(code, 1)
        self.assertEqual(len(appels), 1)
        self.assertIn("ERROR creating venv", self.sortie.getvalue())
        self.ns["_relancer"].assert_not_called()

    def test_windows_utilise_scripts(self):
        with mock.patch.object(platform, "system", return_value="Windows"), \
                self.absentes(["numpy"]):
            self.venv_python.parent.parent.joinpath("Scripts").mkdir(parents=True)
            (self.venv / "Scripts" / "python.exe").touch()
            code, appels = self.lancer("_bootstrap_venv_auto")
        self.assertIsNone(code)
        self.assertEqual(appels[0][0], str(self.venv / "Scripts" / "python.exe"))
        self.ns["_relancer"].assert_called_once_with(self.venv / "Scripts" / "python.exe", True)


class BootstrapPip(_Bootstrap):
    def test_rien_ne_manque_rien_n_est_lance(self):
        with self.absentes([]):
            code, appels = self.lancer("_bootstrap_pip_courant")
        self.assertIsNone(code)
        self.assertEqual(appels, [])

    def test_trois_strategies_hors_venv_la_premiere_qui_reussit_arrete(self):
        resultats = iter([_termine(1, stderr="externally-managed"), _termine(0)])
        with self.absentes(["numpy"]):
            code, appels = self.lancer("_bootstrap_pip_courant",
                                       run=lambda c, **o: next(resultats))
        self.assertIsNone(code)
        self.assertEqual(len(appels), 2)
        self.assertNotIn("--break-system-packages", appels[0])
        self.assertIn("--break-system-packages", appels[1])
        for commande in appels:
            self.assertIn("--require-hashes", commande)

    def test_toutes_en_echec_sortie_1_avec_le_dernier_message(self):
        with self.absentes(["numpy", "pandas"]):
            code, appels = self.lancer("_bootstrap_pip_courant",
                                       run=lambda c, **o: _termine(1, stderr="denied"))
        self.assertEqual(code, 1)
        self.assertEqual(len(appels), 3)
        self.assertIn("--user", appels[2])
        sortie = self.sortie.getvalue()
        self.assertIn("Missing: numpy, pandas", sortie)
        self.assertIn("denied", sortie)
        self.assertIn(f"pip install -r {_installation.VERROU}", sortie)

    def test_dans_un_venv_seule_la_strategie_standard(self):
        with mock.patch.object(sys, "prefix", str(self.home / "venv")), \
                self.absentes(["numpy"]):
            code, appels = self.lancer("_bootstrap_pip_courant",
                                       run=lambda c, **o: _termine(1, stderr="hors ligne"))
        self.assertEqual(code, 1)
        self.assertEqual(len(appels), 1)


class InstallerDeps(_Bootstrap):
    def test_cree_le_venv_installe_le_verrou_note_l_empreinte_et_quitte_0(self):
        code, appels = self.lancer("_installer_deps_et_quitter", run=self.creer_le_venv)
        self.assertEqual(code, 0)
        self.assertEqual(appels[0], [sys.executable, "-m", "venv", str(self.venv)])
        self.assertEqual(appels[1], _installation.commande_installation(self.venv_python))
        self.assertEqual(self.marque.read_text().strip(), _installation.empreinte_verrou())

    def test_venv_existant_pas_recree(self):
        self.venv_installe()
        code, appels = self.lancer("_installer_deps_et_quitter")
        self.assertEqual(code, 0)
        self.assertEqual(appels, [_installation.commande_installation(self.venv_python)])

    def test_echec_sortie_1_sans_marque(self):
        self.venv_installe()
        code, _ = self.lancer("_installer_deps_et_quitter",
                              run=lambda c, **o: _termine(1, stderr="hash mismatch"))
        self.assertEqual(code, 1)
        self.assertFalse(self.marque.exists())


class Orchestrateur(_Bootstrap):
    def lancer_mode(self, mode, absentes=()):
        self.ns["_resoudre_mode_bootstrap"].return_value = mode
        for nom in ("_bootstrap_venv_auto", "_bootstrap_pip_courant",
                    "_installer_deps_et_quitter"):
            self.ns[nom] = mock.Mock()
        with self.absentes(list(absentes)):
            code, _ = self.lancer("_bootstrap_environnement")
        return code

    def test_mode_none_tout_installe(self):
        self.assertIsNone(self.lancer_mode("none"))
        self.assertIn("all dependencies are installed", self.sortie.getvalue())

    def test_mode_none_paquet_manquant_sortie_1(self):
        self.assertEqual(self.lancer_mode("none", absentes=["srtm.py"]), 1)
        self.assertIn("Mode --bootstrap=none actif", self.sortie.getvalue())

    def test_chaque_mode_appelle_son_moteur(self):
        attendus = {"pip": ("_bootstrap_pip_courant", ()), "force": ("_bootstrap_venv_auto", (True,)),
                    "auto": ("_bootstrap_venv_auto", (False,))}
        for mode, (moteur, args) in attendus.items():
            with self.subTest(mode=mode):
                self.assertIsNone(self.lancer_mode(mode))
                appel = self.ns[moteur]
                if args:
                    appel.assert_called_once_with(force=args[0])
                else:
                    appel.assert_called_once_with()

    def test_binaire_fige_pas_de_bootstrap(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "argv", ["gpxsolar", "--installer-deps"]):
            self.assertIsNone(self.lancer_mode("auto"))
            self.assertNotIn("--installer-deps", sys.argv)
        self.ns["_bootstrap_venv_auto"].assert_not_called()
        self.ns["_installer_deps_et_quitter"].assert_not_called()
        self.assertIn("bootstrap skipped", self.sortie.getvalue())

    def test_installer_deps_route_vers_l_installation_du_venv(self):
        with mock.patch.object(sys, "argv", ["gpxsolar.py", "--installer-deps"]):
            self.lancer_mode("auto")
        self.ns["_installer_deps_et_quitter"].assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
