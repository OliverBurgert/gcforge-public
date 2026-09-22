// ── GCForge — AL Founds import map (MapLibre) ────────────────────────────────
//
// Mirrors the settings/locations map (settings-map.js): explicit container,
// per-marker maplibregl.Marker objects, layer switcher, fit-to-visible.
//
// Differences from settings:
//  - Markers are AL adventures (with the advlab.svg icon + name underneath)
//  - Visibility is driven by .al-item-check checkboxes (not stored locally)
//  - No fine-tune / drag / new-pin behavior
//
// Depends on: maplibre-gl, map-styles.js
//
// Public functions used by the template / preview partial:
//   gcfAlFoundsMapInit()              - called from gcfLoadMapLibre.onReady
//   gcfAlFoundsSyncMarkers()          - rebuild marker set from current checks
//   gcfAlFoundsOnCheck(cb)            - per-checkbox onchange handler
//   gcfAlFoundsToggleSection(s, btn)  - select-all toggle for a section
//   gcfAlFoundsSetLayer(id, btn)      - layer switcher

var _gcfAlMap = null;
var _gcfAlMarkers = {};        // guid → maplibregl.Marker
var _gcfAlStyleId = 'street';
var _gcfAlIconUrl = null;      // resolved at init time from data-attr

// ── Init ────────────────────────────────────────────────────────────────────

function _gcfAlSetStatus(msg, isError) {
  var s = document.getElementById('al-map-status');
  if (s) {
    s.textContent = msg;
    s.className = 'ms-2 small ' + (isError ? 'text-danger' : 'text-muted');
  }
  if (window.console) console.log('[al-founds-map]', msg);
}

function gcfAlFoundsMapInit() {
  var mapEl = document.getElementById('al-founds-map');
  if (!mapEl) { _gcfAlSetStatus('map container missing', true); return; }
  if (_gcfAlMap) { _gcfAlSetStatus('map already initialised'); return; }

  if (typeof maplibregl === 'undefined') {
    _gcfAlSetStatus('maplibregl global not available', true); return;
  }
  if (typeof GCF_STYLES === 'undefined') {
    _gcfAlSetStatus('GCF_STYLES not loaded (map-styles.js)', true); return;
  }

  var rect = mapEl.getBoundingClientRect();
  _gcfAlSetStatus('init: container ' + Math.round(rect.width) + 'x' + Math.round(rect.height));

  var cfg = document.getElementById('al-founds-config');
  _gcfAlIconUrl = cfg ? cfg.dataset.advlabIcon : null;

  var savedStyle = localStorage.getItem('gcforge_map_style') || 'street';
  if (!GCF_STYLES[savedStyle]) savedStyle = 'street';
  _gcfAlStyleId = savedStyle;

  try {
    _gcfAlMap = new maplibregl.Map({
      container: 'al-founds-map',
      style: GCF_STYLES[savedStyle],
      center: [10, 51],
      zoom: 4,
      attributionControl: true,
      transformRequest: gcfMapTransformRequest
    });
  } catch (e) {
    _gcfAlSetStatus('Map() threw: ' + e.message, true);
    return;
  }
  _gcfAlMap.addControl(new maplibregl.NavigationControl(), 'top-left');

  _gcfAlHighlightLayerButton();

  _gcfAlMap.on('load', function() {
    _gcfAlMap.resize();
    gcfAlFoundsSyncMarkers();
    _gcfAlSetStatus('');
  });
  _gcfAlMap.on('error', function(e) {
    _gcfAlSetStatus('map error: ' + (e.error && e.error.message || 'unknown'), true);
  });

  // Force resize at multiple points — the container may have reported 0 size
  // at construction time (CSS/flex still settling), or before maplibre-gl.css
  // finished loading.
  function forceResize(label) {
    if (!_gcfAlMap) return;
    _gcfAlMap.resize();
    var r = mapEl.getBoundingClientRect();
    _gcfAlSetStatus('resize ' + label + ': ' + Math.round(r.width) + 'x' + Math.round(r.height));
  }
  requestAnimationFrame(function() { forceResize('raf'); });
  setTimeout(function() { forceResize('100ms'); }, 100);
  setTimeout(function() { forceResize('500ms'); }, 500);
  setTimeout(function() { _gcfAlSetStatus(''); }, 1500);

  if (window.ResizeObserver) {
    new ResizeObserver(function() { if (_gcfAlMap) _gcfAlMap.resize(); }).observe(mapEl);
  }
  window.addEventListener('resize', function() { if (_gcfAlMap) _gcfAlMap.resize(); });
}

// ── Layer switcher (mirrors settings-map) ───────────────────────────────────

function gcfAlFoundsSetLayer(styleId, btn) {
  if (!_gcfAlMap || !GCF_STYLES[styleId]) return;
  _gcfAlStyleId = styleId;
  localStorage.setItem('gcforge_map_style', styleId);
  _gcfAlMap.setStyle(GCF_STYLES[styleId]);
  _gcfAlHighlightLayerButton();
}

function _gcfAlHighlightLayerButton() {
  document.querySelectorAll('#al-layer-switcher button[data-layer]').forEach(function(b) {
    b.classList.toggle('active', b.dataset.layer === _gcfAlStyleId);
  });
}

// ── Markers ─────────────────────────────────────────────────────────────────

function _gcfAlBuildEl(title) {
  var wrapper = document.createElement('div');
  wrapper.className = 'gcf-al-marker';

  var dot = document.createElement('div');
  dot.className = 'gcf-al-marker-dot';
  if (_gcfAlIconUrl) {
    dot.style.backgroundImage = "url('" + _gcfAlIconUrl + "')";
  }

  var label = document.createElement('div');
  label.className = 'gcf-al-marker-label';
  label.textContent = title;

  wrapper.appendChild(dot);
  wrapper.appendChild(label);
  return wrapper;
}

function gcfAlFoundsSyncMarkers() {
  if (!_gcfAlMap) return;
  Object.keys(_gcfAlMarkers).forEach(function(g) { _gcfAlMarkers[g].remove(); });
  _gcfAlMarkers = {};

  document.querySelectorAll('.al-item-check:checked').forEach(function(cb) {
    _gcfAlAddMarker(cb);
  });
  gcfAlFoundsFitVisible();
}

function _gcfAlAddMarker(cb) {
  if (!_gcfAlMap) return;
  var guid  = cb.dataset.guid;
  var lat   = parseFloat(cb.dataset.lat);
  var lon   = parseFloat(cb.dataset.lon);
  var title = cb.dataset.title || guid;
  if (!guid || isNaN(lat) || isNaN(lon) || (lat === 0 && lon === 0)) return;
  if (_gcfAlMarkers[guid]) return;

  var marker = new maplibregl.Marker({ element: _gcfAlBuildEl(title), anchor: 'top' })
    .setLngLat([lon, lat])
    .setPopup(new maplibregl.Popup({ offset: 10, closeButton: false }).setText(title))
    .addTo(_gcfAlMap);

  _gcfAlMarkers[guid] = marker;
}

function gcfAlFoundsFitVisible() {
  if (!_gcfAlMap) return;
  var keys = Object.keys(_gcfAlMarkers);
  if (!keys.length) return;
  var bounds = new maplibregl.LngLatBounds();
  keys.forEach(function(g) { bounds.extend(_gcfAlMarkers[g].getLngLat()); });
  _gcfAlMap.fitBounds(bounds, { padding: 50, maxZoom: 12, duration: 400 });
}

// ── Checkbox / select-all handlers ──────────────────────────────────────────

function gcfAlFoundsOnCheck(cb) {
  if (!_gcfAlMap) return;
  if (cb.checked) {
    _gcfAlAddMarker(cb);
    gcfAlFoundsFitVisible();
  } else if (_gcfAlMarkers[cb.dataset.guid]) {
    _gcfAlMarkers[cb.dataset.guid].remove();
    delete _gcfAlMarkers[cb.dataset.guid];
  }
  gcfAlFoundsUpdateCount();
  gcfAlFoundsUpdateSectionBtn(cb.dataset.section);
}

function gcfAlFoundsToggleSection(section, btn) {
  var checks = document.querySelectorAll('.al-item-check[data-section="' + section + '"]');
  var allChecked = Array.from(checks).every(function(c) { return c.checked; });
  checks.forEach(function(c) {
    c.checked = !allChecked;
    if (c.checked) {
      _gcfAlAddMarker(c);
    } else if (_gcfAlMarkers[c.dataset.guid]) {
      _gcfAlMarkers[c.dataset.guid].remove();
      delete _gcfAlMarkers[c.dataset.guid];
    }
  });
  if (!allChecked) gcfAlFoundsFitVisible();
  btn.textContent = allChecked ? 'Select all' : 'Deselect all';
  gcfAlFoundsUpdateCount();
}

function gcfAlFoundsUpdateSectionBtn(section) {
  var checks = document.querySelectorAll('.al-item-check[data-section="' + section + '"]');
  var btn = document.querySelector('[data-select-section="' + section + '"]');
  if (!btn) return;
  var allChecked = Array.from(checks).every(function(c) { return c.checked; });
  btn.textContent = allChecked ? 'Deselect all' : 'Select all';
}

function gcfAlFoundsUpdateCount() {
  var n = document.querySelectorAll('.al-item-check:checked').length;
  var el = document.getElementById('al-selected-count');
  if (el) el.textContent = n ? n + ' selected' : 'None selected';
}

// After a successful import: move each imported row from its "Needs update"
// section into the matching "Already complete/tracked" section, uncheck it,
// drop its map marker. Tab totals stay the same; per-section counts shift.
// No re-fetch from the API.
function gcfAlFoundsMoveImportedToConfirmed(guids) {
  if (!guids || !guids.length) return;

  var moveMap = {
    'comp-update':    'comp-confirmed',
    'prog-update':    'prog-confirmed',
    // already-confirmed sections: leave the row where it is
  };

  guids.forEach(function(guid) {
    var cb = document.getElementById('al-' + guid);
    if (!cb) return;

    // Drop the marker — it now represents an item that's already done.
    if (_gcfAlMarkers[guid]) {
      _gcfAlMarkers[guid].remove();
      delete _gcfAlMarkers[guid];
    }
    cb.checked = false;

    var srcSection = cb.dataset.section;
    var dstSection = moveMap[srcSection];
    if (!dstSection) return;

    var row = cb.closest('.form-check');
    var dstSec = document.getElementById('section-' + dstSection);
    var dstItems = dstSec && dstSec.querySelector('.al-section-items');
    if (!row || !dstItems) return;

    // Restyle as a "confirmed" row (muted label + link)
    cb.dataset.section = dstSection;
    var label = row.querySelector('label.form-check-label');
    if (label) label.classList.add('text-muted');
    var link = row.querySelector('label a');
    if (link && !link.classList.contains('text-muted')) link.classList.add('text-muted');

    dstItems.appendChild(row);
  });

  // Refresh per-section counts, hide empties, refresh select-all button labels
  ['comp-update', 'comp-confirmed', 'prog-update', 'prog-confirmed'].forEach(function(section) {
    var sec = document.getElementById('section-' + section);
    if (!sec) return;
    var n = sec.querySelectorAll('.al-item-check').length;
    var countEl = sec.querySelector('.al-section-count');
    if (countEl) countEl.textContent = n;
    sec.style.display = n > 0 ? '' : 'none';
    gcfAlFoundsUpdateSectionBtn(section);
  });

  // Tab badges: tab totals remain unchanged, but recount defensively.
  var compCount = document.querySelectorAll('.al-item-check[data-section^="comp-"]').length;
  var progCount = document.querySelectorAll('.al-item-check[data-section^="prog-"]').length;
  var compBadge = document.querySelector('#tab-completed-btn .badge');
  var progBadge = document.querySelector('#tab-inprogress-btn .badge');
  if (compBadge) compBadge.textContent = compCount;
  if (progBadge) progBadge.textContent = progCount;

  gcfAlFoundsUpdateCount();
}

// Re-sync markers whenever the HTMX preview partial is (re)loaded
document.addEventListener('htmx:afterSwap', function(evt) {
  if (!evt.detail.target || evt.detail.target.id !== 'preview-area') return;
  if (!_gcfAlMap) return;
  if (_gcfAlMap.loaded()) gcfAlFoundsSyncMarkers();
  else _gcfAlMap.once('load', gcfAlFoundsSyncMarkers);
});
