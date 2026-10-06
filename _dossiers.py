"""Dossiers de gpxsolar depuis la 1.4.0 : l'état dans le dossier de données
standard de l'OS, les sorties dans un dossier visible.

L'état, ce sont les réglages, l'historique et les préférences : de petits
fichiers que l'utilisateur n'a pas à voir. Les sorties, ce sont GPX_Ombres et
les caches de relief et de végétation (HGT, WorldCover, LIDAR_CACHE,
RGEALTI_CACHE) : des gigaoctets qu'il cherche, copie et supprime lui-même,
d'où Documents/gpxsolar par défaut. GPXSOLAR_HOME, s'il est fourni, regroupe
l'un et l'autre, comme le dossier courant le faisait jusqu'à la 1.3.

Toute la logique (calcul des dossiers, reprise unique de l'état d'une version
<= 1.3) est dans nico579_commons.dossiers, la même pour les quatre
applications. Ce qui reste ici est propre à gpxsolar : ses noms et ses
fichiers.
"""

from nico579_commons.dossiers import Dossiers

DOSSIERS = Dossiers(
    "gpxsolar",
    nom_etat="gpxsolar-data",
    nom_sorties="gpxsolar",
    variable_home="GPXSOLAR_HOME",
    preferences="gpx_analyzer_prefs.json",
    cle_sorties="dossier_sorties",
    # Ce qu'une version <= 1.3 rangeait dans son dossier courant.
    fichiers_etat=("gpx_analyzer_prefs.json", "gpx_analyzer_config.json",
                   "gpx_analyzer_history.json"),
    dossiers_sorties=("GPX_Ombres", "HGT", "WorldCover", "LIDAR_CACHE", "RGEALTI_CACHE"),
    marqueur=".gpxsolar_etat_migre.json",
)
