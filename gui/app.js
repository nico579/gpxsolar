let _polling = null;
let _initialized = false;

// ── i18n ───────────────────────────────────────────────────────────────────────
// Même mécanisme que lidar2map (jumeau) : dico inline par locale + attribut
// data-i18n. On ne tague que les chaînes qui DIFFÈRENT entre fr et en ; les
// tokens identiques (Modes, GeoTIFF, KML, DEM…) restent en dur (fallback fr).
// Variantes : data-i18n (textContent), -placeholder, -title, -html (innerHTML).
const I18N = {
  fr: {
    "btn.run":"▶ Lancer le calcul", "btn.stop":"■ Arrêter", "btn.help":"? Aide", "btn.hist":"⏱ Historique",
    "sec.file":"Fichier GPX & date", "sec.dem":"Modèle de données d'altitude (DEM)",
    "sec.params":"Options de simulation", "sec.out":"Sorties & visualisations", "sec.legend":"Légendes",
    "sec.modes":"Modes",
    "f.gpx":"Fichier GPX", "f.date":"Date de départ", "f.time":"Heure",
    "f.res":"Résolution analyse (m)", "f.maxdist":"Distance max. ombre (m)", "f.margin":"Marge bbox (m)",
    "f.batch":"Taille des lots (batch)", "f.workers":"Workers (parallèle)", "f.ptint":"Intervalle pts (min)",
    "hint.none":"0 = aucun", "f.solarstep":"Pas solaire (s) — cache", "hint.solarstep":"Ex : 60 (rapide), 10 (précis)",
    "f.shadowcalc":"Calcul d'ombre", "sh.relief":"Relief seul", "sh.veg":"Végétation seule", "both":"Les deux",
    "f.dir":"Sens du parcours", "di.cw":"Horaire", "di.ccw":"Anti-horaire",
    "f.kmltype":"Type d'analyse KML", "an.ombre":"Ombre / Soleil", "an.pente":"Pente (depuis MNT)",
    "f.slopearrows":"Flèches de sens sur la trace", "hint.slopewin":"= fenêtre de lissage de la pente",
    "out.openkml":"Ouvrir le KML résultat après calcul", "out.tiles":"Visualiser les tuiles (KML)",
    "out.shadowmap":"Générer carte d'ombre (GeoTIFF)", "out.sunrays":"Visualiser rayons solaires (KML)",
    "f.rayint":"Intervalle rayons",
    "leg.kml":"Couleurs KML — Ombre/Soleil", "leg.slope":"Couleurs KML — Pentes (abs)", "leg.tile":"Tuiles (visualisation)",
    // Items de légende (label complet « nom — description »), clé portée par les données Python
    "leg.sun":"Soleil — Ensoleillé", "leg.shaderelief":"Ombre Relief — Terrain/Montagne",
    "leg.shadeveg":"Ombre Vég. — Végétation haute", "leg.shaderv":"Ombre R+V — Relief + Vég.",
    "leg.s05":"0-5% — Plat ou quasi-plat", "leg.s510":"5-10% — Pente faible", "leg.s1020":"10-20% — Pente moyenne",
    "leg.s2030":"20-30% — Pente forte", "leg.s30":"> 30% — Pente très forte",
    "leg.tgreen":"Vert — Tuile utilisée (ray-tracing)", "leg.tblue":"Bleu — Tuile en RAM (cache LRU)",
    "leg.tyellow":"Jaune — Tuile sur disque (pas en RAM)",
    "hist.title":"⏱ Historique des calculs", "clear":"🗑 Vider",
    "log.ready":"Prêt", "log.copy":"⎘ Copier", "log.hidetip":"Masquer (ré-affichable via le bouton Logs)",
    "help.title":"Aide — Simu Rando Solaire", "close":"Fermer",
    "help.body":"Ce script analyse l'ensoleillement d'une trace GPX.\n\n1. Choisissez un fichier GPX.\n2. Sélectionnez une date et une heure de départ.\n3. Choisissez un modèle de données d'altitude:\n   - SRTM/Copernicus: Mondiaux, basse résolution.\n   - IGN ALTI: France, moyenne résolution.\n   - IGN LiDAR HD: France, très haute résolution.\n4. Choisissez les options de simulation (type d'ombre et sens).\n   - En mode LiDAR, les options contrôlent les couches (MNT, MNS, MNH).\n5. Lancez le calcul.\n\nLes résultats sont un fichier KML à ouvrir dans Google Earth et un rapport Excel.",
    // Dynamiques (JS) — {x} = placeholders pour tf()
    "initerr":"Erreur init : ", "log.init1":"Interface graphique initialisée.\n", "log.init2":"Prêt à lancer une simulation.\n",
    "hist.empty":"Aucun calcul enregistré.", "hist.recalled":"Paramètres rappelés depuis l'historique ({date})",
    "hist.alreadyempty":"L'historique est déjà vide.", "hist.confirm":"Supprimer {n} entrée(s) de l'historique ?",
    "hist.cleared":"✓ Historique vidé", "del.error":"Erreur lors de la suppression.", "err.generic":"Erreur : ",
    "done":"✓ Terminé", "err.code":"✗ Erreur (code {c})",
    "fail.detail":"Le traitement a échoué (code {c}).\n\n{msg}\n\nVoir le panneau de log pour les détails.",
    "req.gpx":"Veuillez sélectionner un fichier GPX.", "req.date":"Veuillez sélectionner une date.",
    "req.time":"Veuillez sélectionner une heure.", "req.dem":"Veuillez sélectionner un modèle d'altitude.",
    "running":"En cours…", "launcherr":"Erreur de lancement : ", "stopped":"⚠ Arrêté",
    "stopping":"⏳ Arrêt en cours…",
    // Parcours des fichiers et instance déjà ouverte (repris de lidar2map)
    "browse.title.dir":"📁 Choisir un dossier", "browse.title.file":"📄 Choisir un fichier",
    "browse.choose":"Choisir ce dossier", "browse.select":"Valider la sélection",
    "browse.empty":"(dossier vide)", "browse.selected":"sélectionné(s) :",
    "instance.title":"gpxsolar est déjà ouvert",
    "instance.hint":"Une instance tourne déjà et s'affiche dans cet onglet. Pour lancer un calcul en parallèle, démarrez une nouvelle instance (second serveur).",
    "instance.keep":"Continuer avec cette instance", "instance.new":"➕ Nouvelle instance",
    "newinst.starting":"Démarrage d'une nouvelle instance gpxsolar…", "apiunavail":"API non disponible",
  },
  en: {
    "btn.run":"▶ Run", "btn.stop":"■ Stop", "btn.help":"? Help", "btn.hist":"⏱ History",
    "sec.file":"GPX file & date", "sec.dem":"Elevation model (DEM)",
    "sec.params":"Simulation options", "sec.out":"Outputs & visualisations", "sec.legend":"Legends",
    "sec.modes":"Modes",
    "f.gpx":"GPX file", "f.date":"Start date", "f.time":"Time",
    "f.res":"Analysis resolution (m)", "f.maxdist":"Max shadow distance (m)", "f.margin":"BBox margin (m)",
    "f.batch":"Batch size", "f.workers":"Workers (parallel)", "f.ptint":"Point interval (min)",
    "hint.none":"0 = none", "f.solarstep":"Solar step (s) — cache", "hint.solarstep":"e.g. 60 (fast), 10 (precise)",
    "f.shadowcalc":"Shadow calculation", "sh.relief":"Relief only", "sh.veg":"Vegetation only", "both":"Both",
    "f.dir":"Route direction", "di.cw":"Clockwise", "di.ccw":"Counter-clockwise",
    "f.kmltype":"KML analysis type", "an.ombre":"Shade / Sun", "an.pente":"Slope (from DEM)",
    "f.slopearrows":"Direction arrows on the track", "hint.slopewin":"= slope smoothing window",
    "out.openkml":"Open result KML after run", "out.tiles":"Visualise tiles (KML)",
    "out.shadowmap":"Generate shadow map (GeoTIFF)", "out.sunrays":"Visualise sun rays (KML)",
    "f.rayint":"Ray interval",
    "leg.kml":"KML colors — Shade/Sun", "leg.slope":"KML colors — Slope (abs)", "leg.tile":"Tiles (visualisation)",
    "leg.sun":"Sun — Sunlit", "leg.shaderelief":"Relief shade — Terrain/Mountain",
    "leg.shadeveg":"Vegetation shade — Tall vegetation", "leg.shaderv":"Relief+Veg shade — Relief + Vegetation",
    "leg.s05":"0-5% — Flat or near-flat", "leg.s510":"5-10% — Gentle slope", "leg.s1020":"10-20% — Moderate slope",
    "leg.s2030":"20-30% — Steep slope", "leg.s30":"> 30% — Very steep slope",
    "leg.tgreen":"Green — Tile used (ray-tracing)", "leg.tblue":"Blue — Tile in RAM (LRU cache)",
    "leg.tyellow":"Yellow — Tile on disk (not in RAM)",
    "hist.title":"⏱ Calculation history", "clear":"🗑 Clear",
    "log.ready":"Ready", "log.copy":"⎘ Copy", "log.hidetip":"Hide (re-show via the Logs button)",
    "help.title":"Help — Simu Rando Solaire", "close":"Close",
    "help.body":"This tool analyses the sunlight along a GPX track.\n\n1. Choose a GPX file.\n2. Select a start date and time.\n3. Choose an elevation data model:\n   - SRTM/Copernicus: global, low resolution.\n   - IGN ALTI: France, medium resolution.\n   - IGN LiDAR HD: France, very high resolution.\n4. Choose the simulation options (shadow type and direction).\n   - In LiDAR mode, the options control the layers (DTM, DSM, CHM).\n5. Run the calculation.\n\nThe results are a KML file to open in Google Earth and an Excel report.",
    "initerr":"Init error: ", "log.init1":"GUI initialised.\n", "log.init2":"Ready to run a simulation.\n",
    "hist.empty":"No saved run.", "hist.recalled":"Parameters recalled from history ({date})",
    "hist.alreadyempty":"History is already empty.", "hist.confirm":"Delete {n} history entry(ies)?",
    "hist.cleared":"✓ History cleared", "del.error":"Error while deleting.", "err.generic":"Error: ",
    "done":"✓ Done", "err.code":"✗ Error (code {c})",
    "fail.detail":"Processing failed (code {c}).\n\n{msg}\n\nSee the log panel for details.",
    "req.gpx":"Please select a GPX file.", "req.date":"Please select a date.",
    "req.time":"Please select a time.", "req.dem":"Please select an elevation model.",
    "running":"Running…", "launcherr":"Launch error: ", "stopped":"⚠ Stopped",
    "stopping":"⏳ Stopping…",
    "browse.title.dir":"📁 Choose a folder", "browse.title.file":"📄 Choose a file",
    "browse.choose":"Choose this folder", "browse.select":"Confirm selection",
    "browse.empty":"(empty folder)", "browse.selected":"selected:",
    "instance.title":"gpxsolar is already open",
    "instance.hint":"An instance is already running and is shown in this tab. To run a computation in parallel, start a new instance (second server).",
    "instance.keep":"Continue with this instance", "instance.new":"➕ New instance",
    "newinst.starting":"Starting a new gpxsolar instance…", "apiunavail":"API unavailable",
  },
};
let _lang = 'fr';
function t(k){ return (I18N[_lang] && I18N[_lang][k]) || I18N.fr[k] || k; }
function tf(k, v){ let s = t(k); for (const p in (v||{})) s = s.split('{'+p+'}').join(v[p]); return s; }
function detectLang(){ return (navigator.language || 'en').toLowerCase().startsWith('fr') ? 'fr' : 'en'; }
function applyI18n(){
  document.documentElement.lang = _lang;
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const v = t(el.dataset.i18n); if (v) el.textContent = v; });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const v = t(el.dataset.i18nPlaceholder); if (v) el.placeholder = v; });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    const v = t(el.dataset.i18nTitle); if (v) el.title = v; });
  document.querySelectorAll('[data-i18n-html]').forEach(el => {
    const v = t(el.dataset.i18nHtml); if (v) el.innerHTML = v; });  // contenu statique de confiance
  document.querySelectorAll('[data-lang-btn]').forEach(b =>
    b.classList.toggle('active', b.dataset.langBtn === _lang));
}
function setLang(code, persist){
  _lang = (code === 'en') ? 'en' : 'fr';
  applyI18n();
  if (persist) {
    api.set_lang(_lang).catch(e => console.error('set_lang error:', e));
  }
}

// ── Init ─────────────────────────────────────────────────────────────────────
// Les données initiales viennent du serveur (/api/init), relues à chaque
// chargement : une page rechargée après un calcul retrouve sa configuration.
// Avant la 1.6.0, pywebview les injectait dans le HTML.
document.addEventListener('DOMContentLoaded', async () => {
  installerResize();
  let d = {}, erreur = null;
  try { d = (await api.get_init_data()) || {}; } catch(e) { erreur = e; }
  setLang((d.lang === 'fr' || d.lang === 'en') ? d.lang : detectLang(), false);
  try {
    if (erreur) throw erreur;
    initFromData(d);
    // Le titre de la fenêtre pywebview portait la version : l'onglet aussi.
    if (d.version) document.title = 'Simu Rando Solaire ' + d.version;
  } catch(e) {
    document.getElementById('footer-status').textContent = t('initerr') + e;
    console.error('init error:', e);
  }
  // Journal et progression interrogés dès le départ : le serveur est prêt
  // puisqu'il vient de servir cette page.
  if (!_polling) boucleDeSondage();
  verifierInstanceDejaOuverte();
});

function initFromData(d) {
  if (_initialized) return;
  _initialized = true;
  buildTimeOptions(d.time_options || []);
  buildDemSources(d.dem_sources || []);
  buildLegend('legend-kml',   d.kml_legend || []);
  buildLegend('legend-slope', d.slope_legend || []);
  buildLegend('legend-tile',  d.tile_legend || []);
  loadDefaults(d.defaults || {});
  buildHistorique(d.historique || []);
  ajouterLigneLog(t('log.init1'), 'dim');
  ajouterLigneLog(t('log.init2'), 'dim');
}

function buildTimeOptions(opts) {
  const sel = document.getElementById('f-time');
  sel.innerHTML = '';
  opts.forEach(t => {
    const o = document.createElement('option');
    o.value = t; o.textContent = t;
    sel.appendChild(o);
  });
}

function buildDemSources(sources) {
  const c = document.getElementById('dem-radios');
  c.innerHTML = '';
  const seg = document.createElement('div');
  seg.className = 'seg';
  sources.forEach(s => {
    const id = 'dem-' + s.key;
    const inp = document.createElement('input');
    inp.type = 'radio'; inp.name = 'dem'; inp.id = id; inp.value = s.key;
    const lab = document.createElement('label');
    lab.setAttribute('for', id);
    lab.textContent = s.label;
    lab.title = s.coverage || '';
    seg.appendChild(inp); seg.appendChild(lab);
  });
  c.appendChild(seg);
}

function buildLegend(containerId, entries) {
  const root = document.getElementById(containerId);
  if (!root) return;
  const items = root.querySelector('.legend-items');
  if (!items) return;
  items.innerHTML = '';
  entries.forEach(e => {
    const row = document.createElement('div');
    row.className = 'legend-row';
    const sw = document.createElement('span');
    sw.className = 'legend-swatch';
    sw.style.background = e.color;
    const txt = document.createElement('span');
    if (e.i18n) { txt.dataset.i18n = e.i18n; txt.textContent = t(e.i18n); }  // re-traduit au toggle
    else { txt.textContent = `${e.name} — ${e.description}`; }
    row.appendChild(sw); row.appendChild(txt);
    items.appendChild(row);
  });
}

// ── Chargement valeurs par défaut ────────────────────────────────────────────
function loadDefaults(d) {
  document.getElementById('f-gpx').value = d.gpx_file || '';
  document.getElementById('f-date').value = ddmmyyyy_to_iso(d.date);
  if (d.time && document.getElementById('f-time').querySelector(`option[value="${d.time}"]`)) {
    document.getElementById('f-time').value = d.time;
  }
  setRadio('dem', d.dem_source);
  document.getElementById('f-analysis-resolution').value = d.analysis_resolution || '5';
  document.getElementById('f-max-distance').value       = d.max_distance || '1000';
  document.getElementById('f-margin-meters').value      = d.margin_meters || '500';
  document.getElementById('f-batch-size').value         = d.batch_size || '256';
  document.getElementById('f-num-workers').value        = d.num_workers || '4';
  document.getElementById('f-passage-interval').value   = d.passage_interval_min || '0';
  if (d.solar_step_s) document.getElementById('f-solar-step').value = d.solar_step_s;
  setRadio('shadow',    d.shadow_mode    || 'both');
  setRadio('direction', d.direction      || 'both');
  setRadio('analysis',  d.analysis_type  || 'ombre_soleil');
  document.getElementById('f-open-gpx').checked            = !!d.open_gpx;
  document.getElementById('f-visualize-tiles').checked     = !!d.visualize_tiles;
  document.getElementById('f-generate-shadow-map').checked = !!d.generate_shadow_map;
  document.getElementById('f-visualize-sun-rays').checked  = !!d.visualize_sun_rays;
  document.getElementById('f-sun-ray-interval').value      = d.sun_ray_interval || '20';
  document.getElementById('f-show-slope-arrows').checked   = !!d.show_slope_arrows;
  appliquerLegendes();
  attacherListeners();
}

function attacherListeners() {
  document.querySelectorAll('input[name=analysis]').forEach(r =>
    r.addEventListener('change', appliquerLegendes));
  document.getElementById('f-visualize-tiles')
    .addEventListener('change', appliquerLegendes);
}

function appliquerLegendes() {
  const at = (document.querySelector('input[name=analysis]:checked') || {}).value || 'ombre_soleil';
  // Mode « Pente depuis MNT » = coloration de la trace seule : on masque tous
  // les champs liés au calcul d'ombre (classe .shadow-only, via body.mode-pente).
  document.body.classList.toggle('mode-pente', at === 'pente');
  document.getElementById('legend-kml').classList.toggle('hidden',  at !== 'ombre_soleil');
  document.getElementById('legend-slope').classList.toggle('hidden', at !== 'pente');
  const vt = document.getElementById('f-visualize-tiles').checked;
  document.getElementById('legend-tile').classList.toggle('hidden', !vt);
}

function setRadio(name, value) {
  const el = document.querySelector(`input[name=${name}][value="${value}"]`);
  if (el) el.checked = true;
}
function getRadio(name) {
  const el = document.querySelector(`input[name=${name}]:checked`);
  return el ? el.value : '';
}

// ── Conversion date DD/MM/YYYY <-> YYYY-MM-DD ────────────────────────────────
function ddmmyyyy_to_iso(s) {
  if (!s) return '';
  const m = String(s).match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (m) return `${m[3]}-${m[2]}-${m[1]}`;
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  return '';
}
function iso_to_ddmmyyyy(s) {
  if (!s) return '';
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : s;
}

// ── Dialogs ──────────────────────────────────────────────────────────────────
// Bouton « … » du fichier GPX : parcours du disque côté serveur, positionné
// sur le dossier du fichier déjà choisi (sinon Documents, côté serveur).
async function pickGpx() {
  const cur = (document.getElementById('f-gpx').value || '').trim();
  const dossier = cur ? cur.replace(/[\\/][^\\/]*$/, '') : '';
  const p = await browseOuvrir({ mode: 'file', exts: ['.gpx'], start: dossier });
  if (p) document.getElementById('f-gpx').value = p;
}

// ── Navigateur de dossiers/fichiers côté serveur ───────────────────────────
// Repris de lidar2map. Remplace le sélecteur natif de pywebview (retiré) :
// un navigateur ne peut pas parcourir le disque du SERVEUR lui-même, donc
// /api/browse-dir liste et cette UI navigue dedans (clic sur un dossier =
// descendre, ⬆ = remonter).
let _browseState = null;   // {mode:'dir'|'file', multiple, exts, kind, path, selection, resolve}

function _browseCheminEnfant(base, nom) {
  const sep = base.includes('\\') && !base.includes('/') ? '\\' : '/';
  return base.endsWith(sep) ? base + nom : base + sep + nom;
}

function _browseMajSelection() {
  const n = _browseState.selection.size;
  document.getElementById('browse-selection').textContent = n ? `${n} ${t('browse.selected')}` : '';
}

function _browseRender(data) {
  _browseState.path = data.path;
  document.getElementById('browse-path').textContent = data.path;
  document.getElementById('browse-up').disabled = !data.parent;
  const liste = document.getElementById('browse-list');
  liste.innerHTML = '';
  if (!data.dirs.length && !data.files.length) {
    const vide = document.createElement('div');
    vide.style.cssText = 'color:var(--dim);padding:4px';
    vide.textContent = t('browse.empty');
    liste.appendChild(vide);
  }
  data.dirs.forEach(nom => {
    const ligne = document.createElement('div');
    ligne.textContent = '📁 ' + nom;
    ligne.style.cssText = 'padding:4px 6px;cursor:pointer;border-radius:4px';
    ligne.onmouseenter = () => { ligne.style.background = 'var(--bg3)'; };
    ligne.onmouseleave = () => { ligne.style.background = ''; };
    ligne.onclick = () => _browseCharger(_browseCheminEnfant(data.path, nom));
    liste.appendChild(ligne);
  });
  data.files.forEach(nom => {
    const chemin = _browseCheminEnfant(data.path, nom);
    const ligne = document.createElement('div');
    const choisi = _browseState.selection.has(chemin);
    ligne.textContent = (choisi ? '☑ ' : '☐ ') + '📄 ' + nom;
    ligne.style.cssText = 'padding:4px 6px;cursor:pointer;border-radius:4px';
    ligne.onclick = () => {
      if (_browseState.multiple) {
        if (_browseState.selection.has(chemin)) _browseState.selection.delete(chemin);
        else _browseState.selection.add(chemin);
        _browseRender(data);
        _browseMajSelection();
      } else {
        _browseState.selection = new Set([chemin]);
        browseConfirmer();
      }
    };
    liste.appendChild(ligne);
  });
}

async function _browseCharger(path) {
  const params = new URLSearchParams();
  if (path) params.set('path', path);
  else if (_browseState.kind) params.set('kind', _browseState.kind);
  if (_browseState.exts && _browseState.exts.length) params.set('exts', _browseState.exts.join(','));
  if (_browseState.mode) params.set('mode', _browseState.mode);
  const reponse = await fetch('/api/browse-dir?' + params.toString());
  _browseRender(await reponse.json());
}

function browseMonter() {
  if (!_browseState) return;
  fetch('/api/browse-dir?path=' + encodeURIComponent(_browseState.path)).then(r => r.json()).then(data => {
    if (data.parent) _browseCharger(data.parent);
  });
}

function browseConfirmer() {
  if (!_browseState) return;
  document.getElementById('browse-modal').style.display = 'none';
  const { mode, multiple, path, selection, resolve } = _browseState;
  _browseState = null;
  if (mode === 'dir') { resolve(path); return; }
  const choix = Array.from(selection);
  resolve(multiple ? choix : (choix[0] || null));
}

function fermerBrowse() {
  document.getElementById('browse-modal').style.display = 'none';
  if (_browseState) {
    const resolve = _browseState.resolve;
    _browseState = null;
    resolve(null);
  }
}

// mode 'dir' : navigue puis "Choisir ce dossier" renvoie le dossier affiché.
// mode 'file' : clic sur un fichier renvoie direct (multiple=false) ou coche
// pour une sélection multiple validée par "Valider la sélection".
function browseOuvrir({ mode, multiple = false, exts = [], kind = '', start = '' }) {
  return new Promise(resolve => {
    _browseState = { mode, multiple, exts, kind, path: '', selection: new Set(), resolve };
    document.getElementById('browse-title').textContent =
      t(mode === 'dir' ? 'browse.title.dir' : 'browse.title.file');
    const bouton = document.getElementById('browse-confirm');
    bouton.textContent = t(mode === 'dir' ? 'browse.choose' : 'browse.select');
    bouton.style.display = (mode === 'file' && !multiple) ? 'none' : '';
    document.getElementById('browse-selection').textContent = '';
    document.getElementById('browse-modal').style.display = 'flex';
    _browseCharger(start);
  });
}

// ── Instance déjà ouverte / nouvelle instance ─────────────────────────────
// Repris de lidar2map. Second serveur pour un calcul en parallèle (sans
// terminal, c'est le seul moyen : un relancement de l'application rejoint
// l'instance existante). L'onglet est ouvert TOUT DE SUITE, pendant le geste
// utilisateur : ouvert après l'attente du démarrage (plusieurs secondes), il
// serait bloqué comme popup. dansCetOnglet=true (choix posé à la relance,
// cf. verifierInstanceDejaOuverte) : cet onglet, ouvert pour la question,
// part vers la nouvelle instance au lieu d'en ouvrir un autre.
function nouvelleInstance(dansCetOnglet) {
  if (dansCetOnglet) {
    document.getElementById('footer-status').textContent = t('newinst.starting');
    Promise.resolve(api.new_instance()).then(r => {
      if (!r || !r.ok) { alert((r && r.error) || t('apiunavail')); return; }
      location.href = location.protocol + '//' + location.hostname + ':' + r.port + '/';
    }).catch(e => alert(t('apiunavail') + ' : ' + e));
    return;
  }
  const onglet = window.open('', '_blank');
  if (onglet) {
    try { onglet.document.title = 'gpxsolar'; onglet.document.body.textContent = t('newinst.starting'); }
    catch (e) { /* page d'attente facultative */ }
  }
  Promise.resolve(api.new_instance()).then(r => {
    if (!r || !r.ok) {
      if (onglet) onglet.close();
      alert((r && r.error) || t('apiunavail'));
      return;
    }
    const url = location.protocol + '//' + location.hostname + ':' + r.port + '/';
    if (onglet) onglet.location.href = url; else window.open(url, '_blank');
  }).catch(e => {
    if (onglet) onglet.close();
    alert(t('apiunavail') + ' : ' + e);
  });
}
// Relance sans terminal visible alors qu'une instance tourne : le serveur a
// ouvert cette page avec ?deja-ouverte=1 pour poser ici la question. Le
// paramètre est retiré de l'adresse (un rechargement ne la repose pas).
function verifierInstanceDejaOuverte() {
  if (!new URLSearchParams(location.search).has('deja-ouverte')) return;
  history.replaceState(null, '', location.pathname);
  const modal = document.getElementById('instance-modal');
  if (modal) modal.style.display = 'flex';
  const garder = document.getElementById('instance-keep');
  if (garder) garder.focus();
}
function fermerInstanceModal() {
  const modal = document.getElementById('instance-modal');
  if (modal) modal.style.display = 'none';
}

// ── Aide ─────────────────────────────────────────────────────────────────────
function afficherAide() {
  document.getElementById('help-modal').classList.add('show');
}
function fermerAide() {
  document.getElementById('help-modal').classList.remove('show');
}

// ── Panneau Logs (show/hide) ─────────────────────────────────────────────────
function toggleLogPanel() {
  const p = document.getElementById('panneau-log');
  const m = document.getElementById('main');
  const b = document.getElementById('btn-log');
  if (!p) return;
  p.classList.add('animating');
  setTimeout(() => p.classList.remove('animating'), 200);
  p.classList.toggle('hidden');
  const visible = !p.classList.contains('hidden');
  if (m) m.classList.toggle('log-visible', visible);
  if (b) b.classList.toggle('active', visible);
}

// ── Historique (panneau latéral) ─────────────────────────────────────────────
let _historique = [];

function toggleHistorique() {
  const p = document.getElementById('panneau-hist');
  const b = document.getElementById('btn-hist');
  if (!p) return;
  p.classList.toggle('hidden');
  if (b) b.classList.toggle('active', !p.classList.contains('hidden'));
}

function buildHistorique(hist) {
  _historique = hist || [];
  const list = document.getElementById('hist-list');
  if (!list) return;
  if (!_historique.length) {
    list.innerHTML = '<div class="hist-empty">' + t('hist.empty') + '</div>';
    return;
  }
  list.innerHTML = _historique.map((e, i) => {
    const gpx  = e.gpx_name || '(sans nom)';
    const dem  = e.dem_source || '';
    const day  = e.date_rando ? `${e.date_rando} ${e.time_rando || ''}`.trim() : '';
    const typ  = e.analysis_type === 'pente' ? 'Pente' : 'Ombre/Soleil';
    return `<div class="hist-entry" onclick="rappelHistorique(${i})">
      <div class="hist-top">
        <strong>${escapeHtml(gpx)}</strong>
        <span style="color:var(--dim);font-size:11px">${escapeHtml(e.date || '')}</span>
      </div>
      <div class="hist-meta">${escapeHtml(typ)} · ${escapeHtml(dem)} · ${escapeHtml(day)} · ${escapeHtml(e.duree || '')}</div>
    </div>`;
  }).join('');
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

function rappelHistorique(i) {
  const e = _historique[i];
  if (!e || !e.params) return;
  loadDefaults(e.params);
  toggleHistorique();
  document.getElementById('footer-status').textContent =
    tf('hist.recalled', {date: e.date || ''});
}

async function viderHistorique() {
  if (!_historique.length) { alert(t('hist.alreadyempty')); return; }
  if (!confirm(tf('hist.confirm', {n: _historique.length}))) return;
  try {
    const r = await api.clear_historique();
    if (r && r.ok) {
      buildHistorique([]);
      document.getElementById('footer-status').textContent = t('hist.cleared');
    } else {
      alert(t('del.error'));
    }
  } catch(e) { alert(t('err.generic') + e); }
}

async function rafraichirHistorique() {
  try {
    const hist = await api.get_historique();
    if (Array.isArray(hist)) buildHistorique(hist);
  } catch(e) { /* silencieux */ }
}

// ── Log panel ────────────────────────────────────────────────────────────────
function ajouterLigneLog(line, tag) {
  const c = document.getElementById('log-content');
  const span = document.createElement('span');
  span.className = 'log-' + (tag || 'ok');
  span.textContent = line;
  c.appendChild(span);
  c.scrollTop = c.scrollHeight;
}
function viderLog() {
  document.getElementById('log-content').innerHTML = '';
}
function copierLog() {
  const txt = document.getElementById('log-content').innerText;
  // execCommand plutôt que navigator.clipboard : hérité de la fenêtre
  // pywebview (<= 1.5), où l'API du presse-papiers ne marchait pas ; il
  // marche aussi dans un navigateur.
  const ta = document.createElement('textarea');
  ta.value = txt; document.body.appendChild(ta);
  ta.select(); try { document.execCommand('copy'); } catch(e) {}
  document.body.removeChild(ta);
}
function setLogProgress(pct, cls) {
  const bar = document.getElementById('log-progress-bar');
  if (!bar) return;
  bar.style.width = (pct >= 0 && pct <= 100 ? pct : 0) + '%';
  bar.className = '';
  if (cls) bar.classList.add(cls);
}

// ── Resize handle du panneau de log ──────────────────────────────────────────
function installerResize() {
  const handle = document.getElementById('log-resize-handle');
  const panel  = document.getElementById('panneau-log');
  if (!handle || !panel) return;
  let dragging = false; let startY = 0; let startH = 0;
  handle.addEventListener('mousedown', e => {
    dragging = true; startY = e.clientY;
    startH = panel.getBoundingClientRect().height;
    handle.classList.add('dragging');
    document.body.classList.add('log-resizing');
    e.preventDefault();
  });
  window.addEventListener('mousemove', e => {
    if (!dragging) return;
    const dy = startY - e.clientY;
    const newH = Math.max(60, Math.min(window.innerHeight * 0.85, startH + dy));
    panel.style.height = newH + 'px';
    // ajuster le padding-bottom du main pour ne pas masquer la fin
    document.getElementById('main').style.paddingBottom = (newH + 20) + 'px';
  });
  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('dragging');
    document.body.classList.remove('log-resizing');
  });
}

// ── Polling logs/progress ────────────────────────────────────────────────────
// Sondage SÉQUENTIEL (setTimeout relancé après chaque réponse), pas
// setInterval : leçon de lidar2map, un callback async sous setInterval se
// chevauche dès qu'une réponse dépasse 250 ms (lignes de journal dans le
// désordre, fin de calcul traitée deux fois, double alerte).
async function boucleDeSondage() {
  await pollOnce();
  _polling = setTimeout(boucleDeSondage, 250);
}

let _echecsPoll = 0;
async function pollOnce() {
  let r;
  try {
    r = await api.poll_log();
    _echecsPoll = 0;
  } catch(e) {
    // Serveur arrêté ou relancé (icône) : quelques échecs tolérés, puis un
    // calcul en cours est rendu en erreur plutôt que figé sur « En cours ».
    if (++_echecsPoll >= 20 && _running) {
      _running = false;
      ajouterLigneLog('\n✗ ' + t('apiunavail') + ' : ' + e + '\n', 'err');
      setLogProgress(100, 'err');
      btnReset();
    }
    return;
  }
  try {
    if (r && r.items) {
      r.items.forEach(it => {
        if (it.line !== undefined) ajouterLigneLog(it.line, it.tag || 'ok');
      });
    }
    if (r && r.progress) {
      const p = r.progress;
      const v = Math.max(0, Math.min(100, p.value || 0));
      setLogProgress(v, '');
      const fs = document.getElementById('footer-status');
      if (fs && _running) fs.textContent = `${Math.round(v)}% — ${(p.text||'').substring(0,80)}`;
    }
    if (_running && r && r.done) {
      _running = false;
      const code = r.code;
      const stopped = (code === 130);   // annulation utilisateur, pas une erreur
      const statusTxt = code === 0 ? t('done')
                      : stopped ? t('stopped') : tf('err.code', {c: code});
      document.getElementById('log-status').textContent = statusTxt;
      document.getElementById('footer-status').textContent = statusTxt;
      setLogProgress(100, code === 0 ? 'ok' : stopped ? '' : 'err');
      if (code === 0) {
        rafraichirHistorique();
      } else if (!stopped) {
        // Forcer l'affichage du log en cas d'erreur
        const panLog = document.getElementById('panneau-log');
        if (panLog && panLog.classList.contains('hidden')) toggleLogPanel();
        try {
          const err = await api.get_last_error();
          if (err && err.msg) {
            alert(tf('fail.detail', {c: err.retcode, msg: err.msg}));
          }
        } catch(e) { /* silencieux */ }
      }
      btnReset();
    }
  } catch(e) { console.error('poll error:', e); }
}

// ── Lancement ────────────────────────────────────────────────────────────────
let _running = false;

function getConfig() {
  const analysisType = getRadio('analysis');
  const pente = (analysisType === 'pente');
  return {
    gpx_file:             document.getElementById('f-gpx').value.trim(),
    date:                 iso_to_ddmmyyyy(document.getElementById('f-date').value),
    time:                 document.getElementById('f-time').value,
    dem_source:           getRadio('dem'),
    analysis_resolution:  document.getElementById('f-analysis-resolution').value,
    max_distance:         document.getElementById('f-max-distance').value,
    margin_meters:        document.getElementById('f-margin-meters').value,
    batch_size:           document.getElementById('f-batch-size').value,
    num_workers:          document.getElementById('f-num-workers').value,
    passage_interval_min: document.getElementById('f-passage-interval').value,
    solar_step_s:         document.getElementById('f-solar-step').value,
    shadow_mode:          getRadio('shadow'),
    // Mode pente : la pente est indépendante du sens de marche, on force un
    // seul sens (sinon deux KML identiques). Champs ombre forcés à off, même
    // si des cases étaient restées cochées avant de basculer en pente.
    direction:            pente ? 'CW' : getRadio('direction'),
    analysis_type:        analysisType,
    open_gpx:             document.getElementById('f-open-gpx').checked,
    visualize_tiles:      document.getElementById('f-visualize-tiles').checked,
    generate_shadow_map:  pente ? false : document.getElementById('f-generate-shadow-map').checked,
    visualize_sun_rays:   pente ? false : document.getElementById('f-visualize-sun-rays').checked,
    sun_ray_interval:     document.getElementById('f-sun-ray-interval').value,
    // Flèches de sens : pertinent seulement en pente (case masquée en ombre).
    show_slope_arrows:    pente ? document.getElementById('f-show-slope-arrows').checked : false,
  };
}

function setFormLocked(locked) {
  document.querySelectorAll('#form-inner input,#form-inner select,#form-inner button')
    .forEach(el => el.disabled = locked);
}

async function lancer() {
  const cfg = getConfig();
  if (!cfg.gpx_file) { alert(t('req.gpx')); return; }
  if (!cfg.date)     { alert(t('req.date')); return; }
  if (!cfg.time)     { alert(t('req.time')); return; }
  if (!cfg.dem_source) { alert(t('req.dem')); return; }

  document.getElementById('btn-run').disabled  = true;
  document.getElementById('btn-stop').disabled = false;
  setFormLocked(true);
  _running = true;
  document.getElementById('log-status').textContent = t('running');
  document.getElementById('footer-status').textContent = t('running');
  setLogProgress(0, '');
  // Ouvrir automatiquement le panneau de log
  const panLog = document.getElementById('panneau-log');
  if (panLog && panLog.classList.contains('hidden')) toggleLogPanel();

  try {
    const res = await api.launch(cfg);
    if (res && res.error) {
      alert(res.error);
      btnReset();
      _running = false;
    }
  } catch(e) {
    alert(t('launcherr') + e);
    btnReset(); _running = false;
  }
}

async function arreter() {
  // Kill immédiat du sous-processus côté Python (Api.stop). On laisse _running
  // actif : pollOnce détecte la fin réelle (code 130) et réarme les boutons.
  // Un process tué est mort sans ambiguïté, donc relancer ne répond jamais à
  // tort « un calcul est déjà en cours ».
  try { await api.stop(); } catch(e) {}
  document.getElementById('btn-stop').disabled = true;
  document.getElementById('log-status').textContent = t('stopping');
  document.getElementById('footer-status').textContent = t('stopping');
}

function btnReset() {
  document.getElementById('btn-run').disabled  = false;
  document.getElementById('btn-stop').disabled = true;
  setFormLocked(false);
}
