"""Réglages de gpxsolar pour le serveur web local de nico579-commons.

Tout le serveur (provenance des requêtes, routes /api/*, fichiers statiques,
réponses JSON) est dans nico579_commons.serveweb, le même pour les quatre
applications. Ce qui reste ici est propre à gpxsolar : le nom de sa variable
d'environnement de proxy local, et la lecture des paramètres de la route
« browse-dir », la seule qui en prenne.
"""

from nico579_commons import serveweb


def _arguments_browse_dir(requete: dict) -> tuple:
    chemin = (requete.get("path") or [""])[0]
    genre = (requete.get("kind") or [""])[0]
    extensions = [e for e in (requete.get("exts") or [""])[0].split(",") if e]
    mode = (requete.get("mode") or [""])[0]
    return chemin, genre, extensions, mode


class Handler(serveweb.Handler):
    variable_proxy_local = "GPXSOLAR_TRUSTED_LOOPBACK_PROXY"
    arguments_get = {"browse-dir": _arguments_browse_dir}
