// ── GCForge Offline Maps — settings tab map ────────────────────────────────
//
// Manages the always-visible map in the Offline Areas settings tab.
// Depends on: maplibregl, map-styles.js
//
// Public API (called from template / settings.js):
//   gcfOfflineTabShown()          — called when #offline tab becomes active
//   gcfOfflineResetView()         — fit all checked areas, 10% margin
//   gcfOfflineZoomToArea(...)     — zoom to a specific bbox
//   gcfOfflineSetLayer(name, btn) — layer switcher
//   gcfOfflineToggleForm()        — show/hide add-area form
//   gcfOfflineStartDraw()         — enter draw mode on the main map
//   gcfOfflineSetZoomPreset(z1,z2)
//   gcfOfflineUpdateEstimate()

var _gcfOfflineMap = null;
var _gcfOfflineStyleId = 'street';
var _gcfOfflineAreaLayers = {};  // areaId (string) -> {srcId, fill, outline, minLon, minLat, maxLon, maxLat}

// ── Tab entry-point ──────────────────────────────────────────────────────────

function gcfOfflineTabShown() {
  if (!_gcfOfflineMap) {
    _gcfOfflineMainMapInit();
  } else {
    _gcfOfflineMap.resize();
    gcfOfflineResetView();
  }
}

// Exposed for settings.js
window.gcfOfflineTabShown = gcfOfflineTabShown;

// ── Map init ─────────────────────────────────────────────────────────────────

function _gcfOfflineMainMapInit() {
  var mapEl = document.getElementById('offline-main-map');
  if (!mapEl || _gcfOfflineMap) return;

  var savedStyle = localStorage.getItem('gcforge_map_style') || 'street';
  if (!GCF_STYLES[savedStyle]) savedStyle = 'street';
  _gcfOfflineStyleId = savedStyle;

  _gcfOfflineMap = new maplibregl.Map({
    container: 'offline-main-map',
    style: GCF_STYLES[savedStyle],
    center: [10, 51],
    zoom: 4,
    attributionControl: true,
    transformRequest: gcfMapTransformRequest
  });
  _gcfOfflineMap.addControl(new maplibregl.NavigationControl(), 'top-left');
  if (typeof gcfSuppressMissingImages === 'function') gcfSuppressMissingImages(_gcfOfflineMap);
  _gcfOfflineHighlightLayerBtn();

  _gcfOfflineMap.on('load', function() {
    gcfOfflineRefreshMapLayers();
  });

  // Re-sync after HTMX table refresh (progress poller swaps #offline-areas-table)
  document.addEventListener('htmx:afterSettle', function(e) {
    var node = e.detail ? e.detail.target : e.target;
    if (node && node.id === 'offline-areas-table') {
      gcfOfflineRefreshMapLayers();
    }
  });

  // Checkbox interactions via event delegation (survives HTMX swaps)
  document.addEventListener('change', function(e) {
    if (!e.target.classList.contains('offline-area-check')) return;
    _gcfOfflineSetAreaVisible(e.target.dataset.areaId, e.target.checked);
    _gcfOfflineUpdateSelectAll();
    gcfOfflineResetView();
  });

  document.addEventListener('change', function(e) {
    if (e.target.id !== 'offline-check-all') return;
    var checked = e.target.checked;
    document.querySelectorAll('.offline-area-check').forEach(function(cb) {
      cb.checked = checked;
      _gcfOfflineSetAreaVisible(cb.dataset.areaId, checked);
    });
    gcfOfflineResetView();
  });
}

// ── Layers ────────────────────────────────────────────────────────────────────

function gcfOfflineRefreshMapLayers() {
  if (!_gcfOfflineMap || !_gcfOfflineMap.isStyleLoaded()) return;

  // Remove layers for areas no longer in the table
  var currentIds = {};
  document.querySelectorAll('tr[data-area-id]').forEach(function(row) {
    currentIds[row.dataset.areaId] = true;
  });
  Object.keys(_gcfOfflineAreaLayers).forEach(function(id) {
    if (!currentIds[id]) _gcfOfflineRemoveAreaLayer(id);
  });

  // Add/update layers for each row
  document.querySelectorAll('tr[data-area-id]').forEach(function(row) {
    var id      = row.dataset.areaId;
    var minLon  = parseFloat(row.dataset.minLon);
    var minLat  = parseFloat(row.dataset.minLat);
    var maxLon  = parseFloat(row.dataset.maxLon);
    var maxLat  = parseFloat(row.dataset.maxLat);
    if (isNaN(minLon) || isNaN(minLat) || isNaN(maxLon) || isNaN(maxLat)) return;

    var cb = row.querySelector('.offline-area-check');
    var visible = cb ? cb.checked : true;

    if (_gcfOfflineAreaLayers[id]) {
      // Already exists — just sync visibility
      _gcfOfflineSetAreaVisible(id, visible);
    } else {
      _gcfOfflineAddAreaLayer(id, minLon, minLat, maxLon, maxLat, visible);
    }
  });

  _gcfOfflineUpdateSelectAll();
  gcfOfflineResetView();
}

function _gcfOfflineAddAreaLayer(id, minLon, minLat, maxLon, maxLat, visible) {
  var srcId    = 'gcf-ol-' + id;
  var fillId   = srcId + '-fill';
  var outlineId = srcId + '-outline';
  var vis = visible ? 'visible' : 'none';

  var geojson = {
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [[
      [minLon, minLat], [maxLon, minLat], [maxLon, maxLat], [minLon, maxLat], [minLon, minLat]
    ]]}
  };

  if (!_gcfOfflineMap.getSource(srcId)) {
    _gcfOfflineMap.addSource(srcId, { type: 'geojson', data: geojson });
  }
  if (!_gcfOfflineMap.getLayer(fillId)) {
    _gcfOfflineMap.addLayer({ id: fillId, type: 'fill', source: srcId,
      paint: { 'fill-color': '#0d6efd', 'fill-opacity': 0.12 },
      layout: { visibility: vis } });
  }
  if (!_gcfOfflineMap.getLayer(outlineId)) {
    _gcfOfflineMap.addLayer({ id: outlineId, type: 'line', source: srcId,
      paint: { 'line-color': '#0d6efd', 'line-width': 2, 'line-dasharray': [4, 2] },
      layout: { visibility: vis } });
  }

  _gcfOfflineAreaLayers[id] = { srcId: srcId, fill: fillId, outline: outlineId,
    minLon: minLon, minLat: minLat, maxLon: maxLon, maxLat: maxLat };
}

function _gcfOfflineRemoveAreaLayer(id) {
  var l = _gcfOfflineAreaLayers[id];
  if (!l) return;
  if (_gcfOfflineMap.getLayer(l.fill))    _gcfOfflineMap.removeLayer(l.fill);
  if (_gcfOfflineMap.getLayer(l.outline)) _gcfOfflineMap.removeLayer(l.outline);
  if (_gcfOfflineMap.getSource(l.srcId))  _gcfOfflineMap.removeSource(l.srcId);
  delete _gcfOfflineAreaLayers[id];
}

function _gcfOfflineSetAreaVisible(id, visible) {
  var l = _gcfOfflineAreaLayers[id];
  if (!l) return;
  var vis = visible ? 'visible' : 'none';
  if (_gcfOfflineMap.getLayer(l.fill))    _gcfOfflineMap.setLayoutProperty(l.fill,    'visibility', vis);
  if (_gcfOfflineMap.getLayer(l.outline)) _gcfOfflineMap.setLayoutProperty(l.outline, 'visibility', vis);
}

// ── Viewport ─────────────────────────────────────────────────────────────────

function gcfOfflineResetView() {
  if (!_gcfOfflineMap) return;

  var bounds = null;
  Object.keys(_gcfOfflineAreaLayers).forEach(function(id) {
    var cb = document.querySelector('.offline-area-check[data-area-id="' + id + '"]');
    if (cb && !cb.checked) return;
    var l = _gcfOfflineAreaLayers[id];
    if (!bounds) {
      bounds = new maplibregl.LngLatBounds([l.minLon, l.minLat], [l.maxLon, l.maxLat]);
    } else {
      bounds.extend([l.minLon, l.minLat]);
      bounds.extend([l.maxLon, l.maxLat]);
    }
  });

  if (!bounds) {
    _gcfOfflineMap.jumpTo({ center: [10, 51], zoom: 4 });
    return;
  }

  // Expand bounds by 10% on each side
  var sw = bounds.getSouthWest();
  var ne = bounds.getNorthEast();
  var dLon = (ne.lng - sw.lng) * 0.1;
  var dLat = (ne.lat - sw.lat) * 0.1;
  var expanded = new maplibregl.LngLatBounds(
    [sw.lng - dLon, sw.lat - dLat],
    [ne.lng + dLon, ne.lat + dLat]
  );
  _gcfOfflineMap.fitBounds(expanded, { padding: 20, maxZoom: 16, duration: 300 });
}

function gcfOfflineZoomToArea(minLon, minLat, maxLon, maxLat) {
  if (!_gcfOfflineMap) return;
  _gcfOfflineMap.fitBounds([[minLon, minLat], [maxLon, maxLat]], { padding: 40, maxZoom: 18, duration: 500 });
}

// ── Select-all helper ─────────────────────────────────────────────────────────

function _gcfOfflineUpdateSelectAll() {
  var all  = document.getElementById('offline-check-all');
  if (!all) return;
  var cbs     = document.querySelectorAll('.offline-area-check');
  var checked = document.querySelectorAll('.offline-area-check:checked');
  all.indeterminate = checked.length > 0 && checked.length < cbs.length;
  all.checked = cbs.length > 0 && checked.length === cbs.length;
}

// ── Layer switcher ────────────────────────────────────────────────────────────

function gcfOfflineSetLayer(name, btn) {
  if (!GCF_STYLES[name] || !_gcfOfflineMap) return;
  _gcfOfflineStyleId = name;
  localStorage.setItem('gcforge_map_style', name);
  // setStyle() wipes all custom sources/layers; clear tracking so refresh re-adds them.
  _gcfOfflineAreaLayers = {};
  _gcfOfflineMap.setStyle(GCF_STYLES[name]);
  // 'idle' fires once the new style is fully loaded — more reliable than 'styledata'
  // in MapLibre v5 where isStyleLoaded() returns false inside styledata callbacks.
  _gcfOfflineMap.once('idle', gcfOfflineRefreshMapLayers);
  document.querySelectorAll('#offline-layer-switcher .btn').forEach(function(b) {
    b.classList.remove('active');
  });
  if (btn) btn.classList.add('active');
}

function _gcfOfflineHighlightLayerBtn() {
  document.querySelectorAll('#offline-layer-switcher .btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.layer === _gcfOfflineStyleId);
  });
}

// ── Add-area form toggle ──────────────────────────────────────────────────────

function gcfOfflineToggleForm() {
  var section = document.getElementById('offline-add-section');
  var btn = document.getElementById('offline-add-btn');
  var open = section.style.display !== 'none' && section.style.display !== '';
  if (!open) {
    section.style.display = 'block';
    btn.textContent = gettext('− Cancel');
  } else {
    section.style.display = 'none';
    btn.textContent = gettext('+ Add Area');
    // Clear drawn bbox preview
    if (_gcfOfflineMap && _gcfOfflineMap.getSource('offline-bbox')) {
      if (_gcfOfflineMap.getLayer('offline-bbox-fill'))    _gcfOfflineMap.removeLayer('offline-bbox-fill');
      if (_gcfOfflineMap.getLayer('offline-bbox-outline')) _gcfOfflineMap.removeLayer('offline-bbox-outline');
      _gcfOfflineMap.removeSource('offline-bbox');
    }
    document.getElementById('offline-min-lon').value = '';
    document.getElementById('offline-min-lat').value = '';
    document.getElementById('offline-max-lon').value = '';
    document.getElementById('offline-max-lat').value = '';
    var drawBtn = document.getElementById('offline-draw-btn');
    if (drawBtn) { drawBtn.textContent = gettext('Draw area'); drawBtn.disabled = false; }
  }
}

// ── Draw mode ────────────────────────────────────────────────────────────────

function gcfOfflineStartDraw() {
  if (!_gcfOfflineMap) return;

  var mapContainer = _gcfOfflineMap.getContainer();
  var old = document.getElementById('offline-draw-overlay');
  if (old) old.parentNode.removeChild(old);

  var btn = document.getElementById('offline-draw-btn');
  if (btn) { btn.textContent = gettext('Drawing… (click & drag)'); btn.disabled = true; }

  var overlay = document.createElement('div');
  overlay.id = 'offline-draw-overlay';
  overlay.style.cssText = 'position:absolute;inset:0;cursor:crosshair;z-index:10;user-select:none;';

  var selBox = document.createElement('div');
  selBox.style.cssText = 'position:absolute;border:2px solid #0d6efd;background:rgba(13,110,253,0.12);'
                        + 'pointer-events:none;display:none;box-sizing:border-box;';
  overlay.appendChild(selBox);

  mapContainer.style.position = 'relative';
  mapContainer.appendChild(overlay);

  var startPx = null;

  overlay.addEventListener('mousedown', function(e) {
    e.preventDefault();
    var rect = overlay.getBoundingClientRect();
    startPx = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    selBox.style.display = 'block';
    selBox.style.left = startPx.x + 'px';
    selBox.style.top  = startPx.y + 'px';
    selBox.style.width  = '0';
    selBox.style.height = '0';
  });

  overlay.addEventListener('mousemove', function(e) {
    if (!startPx) return;
    var rect = overlay.getBoundingClientRect();
    var cx = e.clientX - rect.left;
    var cy = e.clientY - rect.top;
    selBox.style.left   = Math.min(startPx.x, cx) + 'px';
    selBox.style.top    = Math.min(startPx.y, cy) + 'px';
    selBox.style.width  = Math.abs(cx - startPx.x) + 'px';
    selBox.style.height = Math.abs(cy - startPx.y) + 'px';
  });

  overlay.addEventListener('mouseup', function(e) {
    if (!startPx) return;
    var rect = overlay.getBoundingClientRect();
    var ex = e.clientX - rect.left;
    var ey = e.clientY - rect.top;

    var sw = _gcfOfflineMap.unproject([Math.min(startPx.x, ex), Math.max(startPx.y, ey)]);
    var ne = _gcfOfflineMap.unproject([Math.max(startPx.x, ex), Math.min(startPx.y, ey)]);

    document.getElementById('offline-min-lon').value = sw.lng.toFixed(6);
    document.getElementById('offline-min-lat').value = sw.lat.toFixed(6);
    document.getElementById('offline-max-lon').value = ne.lng.toFixed(6);
    document.getElementById('offline-max-lat').value = ne.lat.toFixed(6);

    _gcfOfflineDrawBboxPreview(sw.lng, sw.lat, ne.lng, ne.lat);
    gcfOfflineUpdateEstimate();

    mapContainer.removeChild(overlay);
    startPx = null;
    if (btn) { btn.textContent = gettext('Redraw'); btn.disabled = false; }
  });
}

function _gcfOfflineDrawBboxPreview(minLon, minLat, maxLon, maxLat) {
  var geojson = { type: 'Feature', geometry: { type: 'Polygon', coordinates: [[
    [minLon, minLat], [maxLon, minLat], [maxLon, maxLat], [minLon, maxLat], [minLon, minLat]
  ]]}};

  if (_gcfOfflineMap.getSource('offline-bbox')) {
    _gcfOfflineMap.getSource('offline-bbox').setData(geojson);
  } else {
    _gcfOfflineMap.addSource('offline-bbox', { type: 'geojson', data: geojson });
    _gcfOfflineMap.addLayer({ id: 'offline-bbox-fill', type: 'fill', source: 'offline-bbox',
      paint: { 'fill-color': '#198754', 'fill-opacity': 0.15 } });
    _gcfOfflineMap.addLayer({ id: 'offline-bbox-outline', type: 'line', source: 'offline-bbox',
      paint: { 'line-color': '#198754', 'line-width': 2, 'line-dasharray': [4, 2] } });
  }
}

// ── Estimate ─────────────────────────────────────────────────────────────────

function gcfOfflineUpdateEstimate() {
  var minLon  = parseFloat(document.getElementById('offline-min-lon').value);
  var minLat  = parseFloat(document.getElementById('offline-min-lat').value);
  var maxLon  = parseFloat(document.getElementById('offline-max-lon').value);
  var maxLat  = parseFloat(document.getElementById('offline-max-lat').value);
  var minZoom = parseInt(document.getElementById('offline-min-zoom').value, 10);
  var maxZoom = parseInt(document.getElementById('offline-max-zoom').value, 10);

  if (isNaN(minLon) || isNaN(minLat) || isNaN(maxLon) || isNaN(maxLat) ||
      isNaN(minZoom) || isNaN(maxZoom) || minZoom > maxZoom) {
    document.getElementById('offline-estimate').textContent = '—';
    return;
  }

  var totalTiles = 0;
  for (var z = minZoom; z <= maxZoom; z++) {
    var n = Math.pow(2, z);
    var xMin = Math.floor((minLon + 180) / 360 * n);
    var xMax = Math.floor((maxLon + 180) / 360 * n);
    var yMin = Math.floor((1 - Math.log(Math.tan(maxLat * Math.PI / 180) + 1 / Math.cos(maxLat * Math.PI / 180)) / Math.PI) / 2 * n);
    var yMax = Math.floor((1 - Math.log(Math.tan(minLat * Math.PI / 180) + 1 / Math.cos(minLat * Math.PI / 180)) / Math.PI) / 2 * n);
    totalTiles += (xMax - xMin + 1) * (yMax - yMin + 1);
  }
  var estimatedMb = (totalTiles * 8192 / 1024 / 1024).toFixed(1);

  var warnEl = document.getElementById('offline-estimate-warn');
  if (maxZoom > 15) {
    warnEl.textContent = interpolate(gettext('Warning: zoom >%s can produce millions of tiles.'), [15]);
    warnEl.style.display = '';
  } else {
    warnEl.style.display = 'none';
  }

  document.getElementById('offline-estimate').textContent = interpolate(
    gettext('%(tiles)s tiles ≈ %(mb)s MB'),
    { tiles: totalTiles.toLocaleString(), mb: estimatedMb }, true);
}

function gcfOfflineSetZoomPreset(minZ, maxZ) {
  document.getElementById('offline-min-zoom').value = minZ;
  document.getElementById('offline-max-zoom').value = maxZ;
  gcfOfflineUpdateEstimate();
}
