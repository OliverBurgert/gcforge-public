// ── GCForge Settings — Locations Map (MapLibre) ─────────────────────────────
//
// Replacement for the Leaflet-based locations map in settings.
// Preserves all original behavior: per-location markers, checkbox visibility
// toggle, click-to-add-new-location, click-to-move-edited-marker, draggable
// marker while editing, tab-shown resize. Adds a unified layer switcher.
//
// Depends on: maplibre-gl, map-styles.js

var _gcfRpMap = null;
var _gcfRpMarkers = {};            // id → maplibregl.Marker
var _gcfRpActiveEditId = null;
var _gcfRpOriginalLngLat = {};     // id → {lng, lat}
var _gcfRpAddMarker = null;
var _gcfRpAddDetails = null;       // cached <details id="add-location-details">
var _gcfRpStyleId = 'street';

var _GCF_RP_STORAGE_KEY = 'gcforge_rp_checks';

// ── Init ────────────────────────────────────────────────────────────────────

function gcfRpMapInit() {
  var mapEl = document.getElementById('rp-map');
  if (!mapEl || _gcfRpMap) return;

  var cfg = document.getElementById('settings-config');
  var rpData = [];
  try { rpData = JSON.parse(cfg ? cfg.dataset.rpList : '[]'); } catch(e) {}

  var savedStyle = localStorage.getItem('gcforge_map_style') || 'street';
  if (!GCF_STYLES[savedStyle]) savedStyle = 'street';
  _gcfRpStyleId = savedStyle;

  _gcfRpMap = new maplibregl.Map({
    container: 'rp-map',
    style: GCF_STYLES[savedStyle],
    center: [10, 51],
    zoom: 4,
    attributionControl: true,
    transformRequest: gcfMapTransformRequest
  });
  _gcfRpMap.addControl(new maplibregl.NavigationControl(), 'top-left');
  if (typeof gcfSuppressMissingImages === 'function') gcfSuppressMissingImages(_gcfRpMap);

  _gcfRpHighlightLayerButton();

  // Build markers from rpData, restore saved checkbox state
  var saved = _gcfRpLoadChecks();
  rpData.forEach(function(rp) {
    var marker = _gcfRpBuildMarker(rp);
    _gcfRpMarkers[rp.id] = marker;
    var cb = document.getElementById('rp-cb-' + rp.id);
    var isChecked = (String(rp.id) in saved) ? saved[String(rp.id)] : rp.is_home;
    if (cb) cb.checked = isChecked;
    if (isChecked) marker.addTo(_gcfRpMap);
  });

  _gcfRpUpdateSelectAll();
  _gcfRpMap.on('load', _gcfRpFitVisible);

  // Checkbox interactions
  document.querySelectorAll('.rp-check').forEach(function(cb) {
    cb.addEventListener('change', function() {
      var id = parseInt(this.dataset.rpId);
      if (this.checked) _gcfRpMarkers[id].addTo(_gcfRpMap);
      else _gcfRpMarkers[id].remove();
      _gcfRpUpdateSelectAll();
      _gcfRpSaveChecks();
      _gcfRpFitVisible();
    });
  });

  var checkAll = document.getElementById('rp-check-all');
  if (checkAll) {
    checkAll.addEventListener('change', function() {
      document.querySelectorAll('.rp-check').forEach(function(cb) {
        cb.checked = checkAll.checked;
        var id = parseInt(cb.dataset.rpId);
        if (checkAll.checked) _gcfRpMarkers[id].addTo(_gcfRpMap);
        else _gcfRpMarkers[id].remove();
      });
      _gcfRpSaveChecks();
      _gcfRpFitVisible();
    });
  }

  // Edit collapse hooks — activate fine-tune when an edit row opens
  document.querySelectorAll('[id^="edit-rp-"]').forEach(function(coll) {
    var rpId = parseInt(coll.id.replace('edit-rp-', ''));
    coll.addEventListener('shown.bs.collapse', function() {
      _gcfRpActivateFineTune(rpId);
    });
    coll.addEventListener('hidden.bs.collapse', function() {
      if (_gcfRpActiveEditId === rpId) _gcfRpDeactivateFineTune(rpId, true);
    });
  });

  // Map click handler: repositions edited marker or places new-location pin
  _gcfRpMap.on('click', _gcfRpMapClick);

  // Add-location expandable
  _gcfRpAddDetails = document.getElementById('add-location-details');
  if (_gcfRpAddDetails) {
    _gcfRpAddDetails.addEventListener('toggle', function() {
      if (_gcfRpAddDetails.open) {
        _gcfRpMap.getCanvas().style.cursor = 'crosshair';
        _gcfRpSetStatus('Click on the map to set the new location position.');
      } else {
        if (_gcfRpActiveEditId === null) {
          _gcfRpMap.getCanvas().style.cursor = '';
          _gcfRpSetStatus('');
        }
        if (_gcfRpAddMarker) { _gcfRpAddMarker.remove(); _gcfRpAddMarker = null; }
        var latEl = document.getElementById('rp-lat-new');
        var lonEl = document.getElementById('rp-lon-new');
        if (latEl) latEl.value = '';
        if (lonEl) lonEl.value = '';
      }
    });
  }

  // Tab-show hook: ensure the map is sized correctly after becoming visible
  var tab = document.querySelector('#settingsTabs a[href="#reference-points"]');
  if (tab) {
    tab.addEventListener('shown.bs.tab', function() {
      _gcfRpMap.resize();
      _gcfRpFitVisible();
    });
  }
}

// Exposed for settings.js tab-shown handler
window.gcfRpMapResize = function() {
  if (_gcfRpMap) { _gcfRpMap.resize(); _gcfRpFitVisible(); }
};

// ── Markers ─────────────────────────────────────────────────────────────────

function _gcfRpBuildMarker(rp) {
  var el = document.createElement('div');
  el.className = 'gcf-rp-marker';
  el.title = rp.name;
  return new maplibregl.Marker({ element: el, anchor: 'center' })
    .setLngLat([rp.lon, rp.lat])
    .setPopup(new maplibregl.Popup({ offset: 10 }).setText(rp.name));
}

// ── Fine-tune (edit) activation ─────────────────────────────────────────────

function _gcfRpActivateFineTune(id) {
  if (_gcfRpActiveEditId !== null && _gcfRpActiveEditId !== id) {
    _gcfRpDeactivateFineTune(_gcfRpActiveEditId, true);
  }
  _gcfRpActiveEditId = id;

  var marker = _gcfRpMarkers[id];
  if (!marker) return;

  var ll = marker.getLngLat();
  _gcfRpOriginalLngLat[id] = { lng: ll.lng, lat: ll.lat };

  // Ensure marker is visible
  var cb = document.getElementById('rp-cb-' + id);
  if (cb && !cb.checked) {
    cb.checked = true;
    marker.addTo(_gcfRpMap);
    _gcfRpUpdateSelectAll();
  }

  marker.getElement().classList.add('edit');
  marker.setDraggable(true);

  // Attach drag handlers; remember them so we can detach on deactivate
  var onDrag = function() { _gcfRpSyncFields(id, marker.getLngLat()); };
  marker._gcfDragHandler = onDrag;
  marker.on('drag', onDrag);
  marker.on('dragend', onDrag);

  _gcfRpMap.getCanvas().style.cursor = 'crosshair';
  _gcfRpMap.panTo(marker.getLngLat());

  var hint = document.getElementById('finetune-hint-' + id);
  if (hint) hint.classList.remove('d-none');

  _gcfRpSetStatus('Fine tune active — drag the marker or click on the map to reposition.');
}

function _gcfRpDeactivateFineTune(id, restore) {
  if (_gcfRpActiveEditId !== id) return;
  _gcfRpActiveEditId = null;

  var marker = _gcfRpMarkers[id];
  if (marker) {
    if (marker._gcfDragHandler) {
      marker.off('drag', marker._gcfDragHandler);
      marker.off('dragend', marker._gcfDragHandler);
      marker._gcfDragHandler = null;
    }
    marker.setDraggable(false);
    marker.getElement().classList.remove('edit');
    if (restore && _gcfRpOriginalLngLat[id]) {
      marker.setLngLat([_gcfRpOriginalLngLat[id].lng, _gcfRpOriginalLngLat[id].lat]);
    }
  }

  _gcfRpMap.getCanvas().style.cursor = (_gcfRpAddDetails && _gcfRpAddDetails.open) ? 'crosshair' : '';

  var hint = document.getElementById('finetune-hint-' + id);
  if (hint) hint.classList.add('d-none');

  _gcfRpSetStatus('');
}

// ── Click handling ──────────────────────────────────────────────────────────

function _gcfRpMapClick(e) {
  if (_gcfRpActiveEditId !== null) {
    var m = _gcfRpMarkers[_gcfRpActiveEditId];
    m.setLngLat(e.lngLat);
    _gcfRpSyncFields(_gcfRpActiveEditId, e.lngLat);
  } else if (_gcfRpAddDetails && _gcfRpAddDetails.open) {
    _gcfRpAddPick(e.lngLat);
  }
}

function _gcfRpAddPick(lngLat) {
  var latEl = document.getElementById('rp-lat-new');
  var lonEl = document.getElementById('rp-lon-new');
  if (latEl) latEl.value = lngLat.lat.toFixed(6);
  if (lonEl) lonEl.value = lngLat.lng.toFixed(6);
  if (_gcfRpAddMarker) {
    _gcfRpAddMarker.setLngLat(lngLat);
  } else {
    var el = document.createElement('div');
    el.className = 'gcf-rp-add-marker';
    el.title = gettext('New location');
    _gcfRpAddMarker = new maplibregl.Marker({ element: el, anchor: 'center' })
      .setLngLat(lngLat)
      .addTo(_gcfRpMap);
  }
}

function _gcfRpSyncFields(id, lngLat) {
  var latEl = document.getElementById('rp-lat-' + id);
  var lonEl = document.getElementById('rp-lon-' + id);
  if (latEl) latEl.value = lngLat.lat.toFixed(6);
  if (lonEl) lonEl.value = lngLat.lng.toFixed(6);
}

// ── Fit / status / select-all ───────────────────────────────────────────────

function _gcfRpFitVisible() {
  if (!_gcfRpMap) return;
  var pts = [];
  Object.keys(_gcfRpMarkers).forEach(function(id) {
    var cb = document.getElementById('rp-cb-' + id);
    if (cb && cb.checked) {
      var ll = _gcfRpMarkers[id].getLngLat();
      pts.push([ll.lng, ll.lat]);
    }
  });
  if (pts.length === 0) {
    _gcfRpMap.jumpTo({ center: [10, 51], zoom: 4 });
  } else if (pts.length === 1) {
    _gcfRpMap.jumpTo({ center: pts[0], zoom: 14 });
  } else {
    var bounds = new maplibregl.LngLatBounds();
    pts.forEach(function(p) { bounds.extend(p); });
    _gcfRpMap.fitBounds(bounds, { padding: 40, maxZoom: 16, duration: 0 });
  }
}

function _gcfRpSetStatus(msg) {
  var el = document.getElementById('rp-map-status');
  if (el) el.textContent = msg;
}

function _gcfRpUpdateSelectAll() {
  var checkAll = document.getElementById('rp-check-all');
  if (!checkAll) return;
  var cbs = document.querySelectorAll('.rp-check');
  var checked = document.querySelectorAll('.rp-check:checked');
  checkAll.indeterminate = checked.length > 0 && checked.length < cbs.length;
  checkAll.checked = checked.length === cbs.length;
}

// ── Persist checkbox state ──────────────────────────────────────────────────

function _gcfRpLoadChecks() {
  try { return JSON.parse(sessionStorage.getItem(_GCF_RP_STORAGE_KEY) || 'null') || {}; }
  catch(e) { return {}; }
}

function _gcfRpSaveChecks() {
  var state = {};
  document.querySelectorAll('.rp-check').forEach(function(cb) {
    state[cb.dataset.rpId] = cb.checked;
  });
  try { sessionStorage.setItem(_GCF_RP_STORAGE_KEY, JSON.stringify(state)); } catch(e) {}
}

// ── Layer switcher ──────────────────────────────────────────────────────────

window.gcfRpSetLayer = function(name, btn) {
  if (!GCF_STYLES[name] || !_gcfRpMap) return;
  _gcfRpStyleId = name;
  localStorage.setItem('gcforge_map_style', name);
  _gcfRpMap.setStyle(GCF_STYLES[name]);
  document.querySelectorAll('#rp-layer-switcher .btn').forEach(function(b) {
    b.classList.remove('active');
  });
  if (btn) btn.classList.add('active');
};

function _gcfRpHighlightLayerButton() {
  document.querySelectorAll('#rp-layer-switcher .btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.layer === _gcfRpStyleId);
  });
}
