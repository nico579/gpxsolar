// Pont entre l'interface (app.js) et le serveur HTTP de gpxsolar
// (_serve_web.py) : chaque méthode de window.api appelle une route /api/*
// et rend une promesse. index.html le charge avant app.js. Repris de
// lidar2map, son jumeau.
//
// window.api remplace l'objet qu'injectait pywebview (retiré en 1.6.0). Pas
// d'alias sous l'ancien nom : ce fichier et app.js sont toujours livrés
// ensemble. pick_gpx n'y figure plus : remplacé par browseOuvrir() (app.js),
// qui appelle /api/browse-dir directement.
function _post(route, payload) {
  return fetch(route, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  }).then(r => r.json());
}

window.api = {
  get_init_data: () => fetch('/api/init').then(r => r.json()),
  get_historique: () => fetch('/api/historique').then(r => r.json()),
  get_last_error: () => fetch('/api/last-error').then(r => r.json()),
  poll_log: () => fetch('/api/poll-log').then(r => r.json()),

  launch: (cfg) => _post('/api/launch', cfg),
  stop: () => _post('/api/stop'),
  clear_historique: () => _post('/api/clear-historique'),
  set_lang: (code) => _post('/api/set-lang', { code }),
  new_instance: () => _post('/api/new-instance'),
};
