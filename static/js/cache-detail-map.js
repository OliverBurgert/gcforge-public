// ── GCForge Cache Detail Map — MapLibre ─────────────────────────────────────
//
// Single-cache map: cache marker, corrected coords, AL stages, additional
// waypoints, layer switcher, left-click-to-add-waypoint, freehand drawings
// (rectangle / circle / polygon / line) persisted per-cache in localStorage.
//
// Depends on: maplibre-gl, @mapbox/mapbox-gl-draw, map-styles.js, map-icons.js

var _gcfDetailMap = null;
var _gcfDetailDraw = null;
var _gcfDetailState = null;   // parsed config
var _gcfDetailSaveTimer = null;
var _gcfDetailStyleId = 'street';
var _gcfDetailStagesVisible = true;
var _gcfDetailWaypointsVisible = true;
var _gcfDetailDrawEndedAt = 0;   // ms timestamp — suppresses map-click right after draw finishes
var _gcfDetailTipEl = null;      // floating measurement tooltip (lazy-created)

// ── Initialise ──────────────────────────────────────────────────────────────

function gcfDetailMapInit() {
  var cfg = document.getElementById('cache-detail-config');
  if (!cfg || _gcfDetailMap) return;

  var cacheLat = parseFloat(cfg.dataset.cacheLat);
  var cacheLon = parseFloat(cfg.dataset.cacheLon);
  var corrLat = cfg.dataset.corrLat !== undefined ? parseFloat(cfg.dataset.corrLat) : null;
  var corrLon = cfg.dataset.corrLon !== undefined ? parseFloat(cfg.dataset.corrLon) : null;
  var mapState = null;
  try { if (cfg.dataset.mapState) mapState = JSON.parse(cfg.dataset.mapState); } catch(e) {}
  var stages = [];
  try { stages = JSON.parse(cfg.dataset.stages || '[]'); } catch(e) {}
  var waypoints = [];
  try {
    var wpEl = document.getElementById('cache-map-waypoints');
    if (wpEl) waypoints = JSON.parse(wpEl.textContent);
  } catch(e) {}

  _gcfDetailState = {
    cacheLat: cacheLat,
    cacheLon: cacheLon,
    corrLat: corrLat,
    corrLon: corrLon,
    cacheCode: cfg.dataset.cacheCode,
    cacheName: cfg.dataset.cacheName,
    cacheTypeShort: cfg.dataset.cacheTypeShort || '?',
    platform: cfg.dataset.platform || 'gc',
    iconSet: cfg.dataset.iconSet || 'text',
    stages: stages,
    waypoints: waypoints,
    saveMapUrl: cfg.dataset.saveMapUrl,
    resetMapUrl: cfg.dataset.resetMapUrl,
    csrfToken: cfg.dataset.csrfToken,
    initialMapState: mapState
  };

  // Restore saved style (per-cache not needed — use same as main map)
  var savedStyle = localStorage.getItem('gcforge_map_style') || 'street';
  if (!GCF_STYLES[savedStyle] && savedStyle !== 'offline') savedStyle = 'street';
  _gcfDetailStyleId = savedStyle;

  var focusLat = corrLat !== null ? corrLat : cacheLat;
  var focusLon = corrLon !== null ? corrLon : cacheLon;

  var initZoom = 14;
  var initCenter = [focusLon, focusLat];
  if (mapState) {
    initZoom = mapState.zoom;
    initCenter = [mapState.lon, mapState.lat];
  }

  // 'offline' style is built dynamically after areas are fetched; start with street
  var initStyle = GCF_STYLES[savedStyle] || GCF_STYLES.street;

  _gcfDetailMap = new maplibregl.Map({
    container: 'cache-map',
    style: initStyle,
    center: initCenter,
    zoom: initZoom,
    attributionControl: true,
    transformRequest: gcfMapTransformRequest
  });

  _gcfDetailMap.addControl(new maplibregl.NavigationControl(), 'top-left');
  _gcfDetailMap.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-left');
  if (typeof gcfSuppressMissingImages === 'function') gcfSuppressMissingImages(_gcfDetailMap);

  var fitMapParam = new URLSearchParams(window.location.search).get('fit_map') === '1';
  if (fitMapParam) history.replaceState(null, '', window.location.pathname);

  _gcfDetailMap.on('load', function() {
    _gcfDetailSetupLayers();
    _gcfDetailSetupDraw();
    _gcfDetailSetupDividers();
    if (!mapState || fitMapParam) _gcfDetailFitBounds();
    _gcfDetailHighlightLayerButton();
    if (typeof gcfOfflineLoadAreas === 'function') gcfOfflineLoadAreas(_gcfDetailMap);
  });


  // Persist viewport (debounced) via per-cache save endpoint
  _gcfDetailMap.on('moveend', _gcfDetailSaveViewport);
  _gcfDetailMap.on('zoomend', _gcfDetailSaveViewport);
}

// ── Markers: cache, corrected, stages, waypoints ────────────────────────────

function _gcfDetailSetupLayers() {
  var s = _gcfDetailState;
  var iconSet = s.iconSet;

  // Prepare icons — load c:geo SVGs if using cgeo icon set
  function _onIconsReady() {
    _gcfDetailAddCacheLayer();
    _gcfDetailAddCorrectedLayer();
    _gcfDetailAddStagesLayer();
    _gcfDetailAddWaypointsLayer();
    _gcfDetailLoadDrawings();
    _gcfDetailSetupContextMenu();
  }

  if (iconSet === 'cgeo' && typeof gcfLoadMapIcons === 'function') {
    gcfLoadMapIcons('/static/icons/cgeo/types/', _onIconsReady);
  } else {
    _onIconsReady();
  }
}

function _gcfDetailAddCacheLayer() {
  var s = _gcfDetailState;
  var iconId;
  if (s.iconSet === 'cgeo' && typeof gcfEnsureMapIcon === 'function') {
    iconId = gcfEnsureMapIcon(_gcfDetailMap, s.cacheTypeShort, s.platform, 'U');
  } else {
    iconId = _gcfDetailEnsurePinIcon('cache-pin', '#dc3545');
  }

  _gcfDetailMap.addSource('detail-cache', {
    type: 'geojson',
    data: {
      type: 'Feature',
      properties: {
        code: s.cacheCode,
        name: s.cacheName,
        popup: '<strong>' + _gcfEsc(s.cacheCode) + '</strong><br>' + _gcfEsc(s.cacheName)
      },
      geometry: { type: 'Point', coordinates: [s.cacheLon, s.cacheLat] }
    }
  });
  _gcfDetailMap.addLayer({
    id: 'detail-cache-layer',
    type: 'symbol',
    source: 'detail-cache',
    layout: {
      'icon-image': iconId,
      'icon-allow-overlap': true,
      'icon-size': 1
    }
  });

  _gcfDetailMap.on('click', 'detail-cache-layer', function(e) {
    var f = e.features[0];
    new maplibregl.Popup({ offset: 14 })
      .setLngLat(f.geometry.coordinates)
      .setHTML(f.properties.popup)
      .addTo(_gcfDetailMap);
    e.originalEvent._gcfHandled = true;
  });
  _gcfDetailMap.on('mouseenter', 'detail-cache-layer', function() {
    _gcfDetailMap.getCanvas().style.cursor = 'pointer';
  });
  _gcfDetailMap.on('mouseleave', 'detail-cache-layer', function() {
    _gcfDetailMap.getCanvas().style.cursor = '';
  });

  // Background click — "Add waypoint here" popup (but not during a draw mode,
  // and not on the click that just finalised a draw).
  _gcfDetailMap.on('click', function(e) {
    if (e.originalEvent && e.originalEvent._gcfHandled) return;
    if (_gcfDetailDraw && _gcfDetailDraw.getMode() !== 'simple_select') return;
    if (Date.now() - _gcfDetailDrawEndedAt < 350) return;
    var feats = _gcfDetailMap.queryRenderedFeatures(e.point, {
      layers: ['detail-cache-layer', 'detail-corrected-layer', 'detail-stages-layer', 'detail-waypoints-layer'].filter(function(id) {
        return !!_gcfDetailMap.getLayer(id);
      })
    });
    if (feats && feats.length) return;
    _gcfDetailShowAddWaypointPopup(e.lngLat.lat, e.lngLat.lng);
  });
}

function _gcfDetailAddCorrectedLayer() {
  var s = _gcfDetailState;
  if (s.corrLat === null || s.corrLat === undefined || isNaN(s.corrLat)) return;

  var iconId = _gcfDetailEnsurePinIcon('corrected-pin', '#198754');
  _gcfDetailMap.addSource('detail-corrected', {
    type: 'geojson',
    data: {
      type: 'Feature',
      properties: { popup: 'Corrected coordinates' },
      geometry: { type: 'Point', coordinates: [s.corrLon, s.corrLat] }
    }
  });
  _gcfDetailMap.addLayer({
    id: 'detail-corrected-layer',
    type: 'symbol',
    source: 'detail-corrected',
    layout: {
      'icon-image': iconId,
      'icon-allow-overlap': true,
      'icon-size': 1
    }
  });
  _gcfDetailMap.on('click', 'detail-corrected-layer', function(e) {
    new maplibregl.Popup({ offset: 12 })
      .setLngLat(e.features[0].geometry.coordinates)
      .setHTML(e.features[0].properties.popup)
      .addTo(_gcfDetailMap);
    e.originalEvent._gcfHandled = true;
  });
}

function _gcfDetailAddStagesLayer() {
  var s = _gcfDetailState;
  if (!s.stages.length) return;

  var features = s.stages.map(function(st) {
    var iconId = _gcfDetailEnsureStageIcon(st.num, st.found);
    return {
      type: 'Feature',
      properties: {
        num: st.num,
        name: st.name,
        popup: '<strong>Stage ' + st.num + '</strong><br>' + _gcfEsc(st.name),
        iconId: iconId
      },
      geometry: { type: 'Point', coordinates: [st.lon, st.lat] }
    };
  });
  _gcfDetailMap.addSource('detail-stages', {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: features }
  });
  _gcfDetailMap.addLayer({
    id: 'detail-stages-layer',
    type: 'symbol',
    source: 'detail-stages',
    layout: {
      'icon-image': ['get', 'iconId'],
      'icon-allow-overlap': true,
      'icon-size': 1
    }
  });
  _gcfDetailMap.on('click', 'detail-stages-layer', function(e) {
    var f = e.features[0];
    new maplibregl.Popup({ offset: 12 })
      .setLngLat(f.geometry.coordinates)
      .setHTML(f.properties.popup)
      .addTo(_gcfDetailMap);
    e.originalEvent._gcfHandled = true;
  });
}

function _gcfDetailAddWaypointsLayer() {
  var s = _gcfDetailState;
  if (!s.waypoints.length) return;

  // Ensure waypoint type icons exist (short-code based)
  var features = s.waypoints.map(function(w) {
    var wpShort = w.t || 'O';
    var iconId;
    if (typeof gcfEnsureWpIcon === 'function') {
      iconId = gcfEnsureWpIcon(_gcfDetailMap, wpShort);
    } else {
      iconId = _gcfDetailEnsurePinIcon('wp-fallback', '#0d6efd');
    }
    return {
      type: 'Feature',
      properties: {
        popup: _gcfEsc(w.type) + ': ' + _gcfEsc(w.name || ''),
        iconId: iconId
      },
      geometry: { type: 'Point', coordinates: [w.lon, w.lat] }
    };
  });
  _gcfDetailMap.addSource('detail-waypoints', {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: features }
  });
  _gcfDetailMap.addLayer({
    id: 'detail-waypoints-layer',
    type: 'symbol',
    source: 'detail-waypoints',
    layout: {
      'icon-image': ['get', 'iconId'],
      'icon-allow-overlap': true,
      'icon-size': 1
    }
  });
  _gcfDetailMap.on('click', 'detail-waypoints-layer', function(e) {
    var f = e.features[0];
    new maplibregl.Popup({ offset: 12 })
      .setLngLat(f.geometry.coordinates)
      .setHTML(f.properties.popup)
      .addTo(_gcfDetailMap);
    e.originalEvent._gcfHandled = true;
  });
}

// ── Icon generators ─────────────────────────────────────────────────────────

function _gcfDetailEnsurePinIcon(id, color) {
  if (_gcfDetailMap.hasImage(id)) return id;
  var size = 22;
  var canvas = document.createElement('canvas');
  canvas.width = size; canvas.height = size;
  var ctx = canvas.getContext('2d');
  ctx.beginPath();
  ctx.arc(size/2, size/2, size/2 - 2, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = '#fff';
  ctx.stroke();
  _gcfDetailMap.addImage(id, { width: size, height: size, data: ctx.getImageData(0, 0, size, size).data });
  return id;
}

function _gcfDetailEnsureStageIcon(num, found) {
  var id = 'stage-' + (found ? 'f' : 'u') + '-' + num;
  if (_gcfDetailMap.hasImage(id)) return id;
  var size = 26;
  var canvas = document.createElement('canvas');
  canvas.width = size; canvas.height = size;
  var ctx = canvas.getContext('2d');
  ctx.beginPath();
  ctx.arc(size/2, size/2, size/2 - 2, 0, Math.PI * 2);
  ctx.fillStyle = found ? '#198754' : '#6f42c1';
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = '#fff';
  ctx.stroke();
  ctx.fillStyle = '#fff';
  ctx.font = 'bold 12px sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(String(num), size/2, size/2 + 1);
  _gcfDetailMap.addImage(id, { width: size, height: size, data: ctx.getImageData(0, 0, size, size).data });
  return id;
}

// ── Toggles / reset view ────────────────────────────────────────────────────

window.toggleStages = function(btn) {
  _gcfDetailStagesVisible = !_gcfDetailStagesVisible;
  if (_gcfDetailMap.getLayer('detail-stages-layer')) {
    _gcfDetailMap.setLayoutProperty('detail-stages-layer', 'visibility',
      _gcfDetailStagesVisible ? 'visible' : 'none');
  }
  btn.classList.toggle('btn-primary', _gcfDetailStagesVisible);
  btn.classList.toggle('btn-outline-secondary', !_gcfDetailStagesVisible);
};

window.toggleWaypoints = function(btn) {
  _gcfDetailWaypointsVisible = !_gcfDetailWaypointsVisible;
  if (_gcfDetailMap.getLayer('detail-waypoints-layer')) {
    _gcfDetailMap.setLayoutProperty('detail-waypoints-layer', 'visibility',
      _gcfDetailWaypointsVisible ? 'visible' : 'none');
  }
  btn.classList.toggle('btn-primary', _gcfDetailWaypointsVisible);
  btn.classList.toggle('btn-outline-secondary', !_gcfDetailWaypointsVisible);
};

window.resetMapView = function() {
  var s = _gcfDetailState;
  fetch(s.resetMapUrl, {
    method: 'POST',
    headers: {'X-CSRFToken': s.csrfToken},
  }).then(function() { _gcfDetailFitBounds(); });
};

function _gcfDetailFitBounds() {
  var s = _gcfDetailState;
  var pts = [[s.cacheLon, s.cacheLat]];
  if (s.corrLat !== null && s.corrLat !== undefined && !isNaN(s.corrLat)) {
    pts.push([s.corrLon, s.corrLat]);
  }
  s.stages.forEach(function(st) { pts.push([st.lon, st.lat]); });
  s.waypoints.forEach(function(w) { pts.push([w.lon, w.lat]); });

  if (pts.length === 1) {
    _gcfDetailMap.easeTo({ center: pts[0], zoom: 14 });
    return;
  }
  var bounds = new maplibregl.LngLatBounds();
  pts.forEach(function(p) { bounds.extend(p); });
  _gcfDetailMap.fitBounds(bounds, { padding: 40, maxZoom: 16, duration: 300 });
}

function _gcfDetailSaveViewport() {
  if (_gcfDetailSaveTimer) clearTimeout(_gcfDetailSaveTimer);
  _gcfDetailSaveTimer = setTimeout(function() {
    var s = _gcfDetailState;
    var c = _gcfDetailMap.getCenter();
    fetch(s.saveMapUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-CSRFToken': s.csrfToken
      },
      body: 'zoom=' + _gcfDetailMap.getZoom() + '&lat=' + c.lat + '&lon=' + c.lng
    }).catch(function() {});
  }, 800);
}

// ── Layer switcher ──────────────────────────────────────────────────────────

window.setLayer = function(name, btn) {
  var style;
  if (name === 'offline') {
    if (typeof gcfBuildOfflineStyle !== 'function' || !_gcfOfflineAreas || !_gcfOfflineAreas.length) return;
    style = gcfBuildOfflineStyle();
  } else {
    if (!GCF_STYLES[name]) return;
    style = GCF_STYLES[name];
  }
  _gcfDetailStyleId = name;
  localStorage.setItem('gcforge_map_style', name);
  _gcfDetailMap.setStyle(style);
  _gcfDetailMap.once('idle', function() {
    if (!_gcfDetailMap.getSource('detail-cache')) {
      _gcfDetailSetupLayers();
    }
  });
  _gcfDetailHighlightLayerButton();
};

function _gcfDetailHighlightLayerButton() {
  document.querySelectorAll('#layer-switcher .btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.layer === _gcfDetailStyleId);
  });
}

// ── Add-waypoint popup ──────────────────────────────────────────────────────

function _gcfDetailShowAddWaypointPopup(lat, lng) {
  var latStr = lat.toFixed(6);
  var lonStr = lng.toFixed(6);

  // Build content as real DOM so we can attach a proper listener (more robust
  // than an inline onclick — no string-parse hazards, no global needed).
  var root = document.createElement('div');
  root.className = 'small';

  var coords = document.createElement('div');
  coords.className = 'font-monospace mb-1';
  coords.textContent = latStr + ', ' + lonStr;
  root.appendChild(coords);

  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn btn-sm btn-primary';
  btn.textContent = gettext('Add waypoint here');
  root.appendChild(btn);

  var popup = new maplibregl.Popup({ offset: 6, maxWidth: '240px' })
    .setLngLat([lng, lat])
    .setDOMContent(root)
    .addTo(_gcfDetailMap);

  btn.addEventListener('click', function() {
    popup.remove();
    if (typeof window.openWaypointModal === 'function') {
      window.openWaypointModal(null, 'Other', '', '', latStr, lonStr, '');
    } else {
      console.warn('GCForge: openWaypointModal not available');
    }
  });
}

// ── Draw tools ──────────────────────────────────────────────────────────────

function _gcfDetailSetupDraw() {
  if (!window.MapboxDraw) return;

  var modes = Object.assign({}, MapboxDraw.modes, {
    gcf_rectangle: _GcfDetailDrawRectangle(),
    gcf_circle: _GcfDetailDrawCircle()
  });

  var defaultTheme = ((MapboxDraw.lib && MapboxDraw.lib.theme) || []).map(function(layer) {
    var da = layer.paint && layer.paint['line-dasharray'];
    if (Array.isArray(da) && da.length > 0 && typeof da[0] === 'number') {
      layer = Object.assign({}, layer, {
        paint: Object.assign({}, layer.paint, { 'line-dasharray': ['literal', da] })
      });
    }
    return layer;
  });

  // Explicit high-contrast styles overlaid on the default theme. The default
  // theme alone is thin and pale; on busy tiles the polygons are nearly
  // invisible and line strings don't render at all for some versions.
  var DRAW_COLOR = '#e05000';
  var customStyles = [
    {
      id: 'gcf-det-polygon-fill',
      type: 'fill',
      filter: ['all', ['==', '$type', 'Polygon'], ['!=', 'mode', 'static']],
      paint: { 'fill-color': DRAW_COLOR, 'fill-opacity': 0.18 }
    },
    {
      id: 'gcf-det-polygon-stroke',
      type: 'line',
      filter: ['all', ['==', '$type', 'Polygon'], ['!=', 'mode', 'static']],
      paint: { 'line-color': DRAW_COLOR, 'line-width': 3 }
    },
    {
      id: 'gcf-det-line',
      type: 'line',
      filter: ['all', ['==', '$type', 'LineString'], ['!=', 'mode', 'static']],
      paint: { 'line-color': DRAW_COLOR, 'line-width': 3 }
    },
    {
      id: 'gcf-det-vertex-halo',
      type: 'circle',
      filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'vertex']],
      paint: { 'circle-radius': 6, 'circle-color': '#fff' }
    },
    {
      id: 'gcf-det-vertex',
      type: 'circle',
      filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'vertex']],
      paint: { 'circle-radius': 4, 'circle-color': DRAW_COLOR }
    },
    {
      id: 'gcf-det-midpoint',
      type: 'circle',
      filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'midpoint']],
      paint: { 'circle-radius': 3, 'circle-color': DRAW_COLOR, 'circle-opacity': 0.6 }
    }
  ];

  _gcfDetailDraw = new MapboxDraw({
    displayControlsDefault: false,
    modes: modes,
    styles: defaultTheme.concat(customStyles)
  });
  _gcfDetailMap.addControl(_gcfDetailDraw, 'top-left');

  _gcfDetailMap.on('draw.create', function(e) {
    _gcfDetailDrawEndedAt = Date.now();
    _gcfDetailSaveDrawings();
    _gcfDetailClearDrawButtons();
    _gcfDetailTipHide();
  });
  _gcfDetailMap.on('draw.update', _gcfDetailSaveDrawings);
  _gcfDetailMap.on('draw.delete', _gcfDetailSaveDrawings);

  // When the draw mode returns to simple_select (after finishing a shape, or
  // after the user presses Escape), deactivate the draw-tool buttons so the
  // UI reflects reality.
  _gcfDetailMap.on('draw.modechange', function(e) {
    if (e.mode === 'simple_select') {
      _gcfDetailClearDrawButtons();
      _gcfDetailTipHide();
    }
  });

  // Live measurement tooltip for built-in line / polygon modes.
  // (Custom rect / circle modes handle their own tooltip inside onDrag/onMouseMove.)
  _gcfDetailMap.on('mousemove', function(e) {
    if (!_gcfDetailDraw) return;
    var mode = _gcfDetailDraw.getMode();
    if (mode !== 'draw_line_string' && mode !== 'draw_polygon') return;
    var fc = _gcfDetailDraw.getAll();
    var target = mode === 'draw_polygon' ? 'Polygon' : 'LineString';
    var feat = null;
    for (var i = fc.features.length - 1; i >= 0; i--) {
      var gt = fc.features[i].geometry && fc.features[i].geometry.type;
      if (gt === target) { feat = fc.features[i]; break; }
    }
    if (!feat) { _gcfDetailTipHide(); return; }
    var ring = target === 'Polygon' ? feat.geometry.coordinates[0] : feat.geometry.coordinates;
    if (!ring || ring.length === 0) { _gcfDetailTipHide(); return; }
    // Polygon rings close — drop the trailing duplicate for our math.
    var pts = ring.slice();
    if (target === 'Polygon' && pts.length >= 2) {
      var a = pts[0], b = pts[pts.length - 1];
      if (a[0] === b[0] && a[1] === b[1]) pts.pop();
    }
    if (pts.length === 0) { _gcfDetailTipHide(); return; }

    // Last visible segment length (last-to-previous, which is what the user
    // sees as the "current" rubber-band segment — MapboxDraw tracks the cursor
    // in the last coord while drawing).
    var seg = 0;
    if (pts.length >= 2) {
      var p1 = pts[pts.length - 2];
      var p2 = pts[pts.length - 1];
      seg = _gcfDetailHaversineM(p1[1], p1[0], p2[1], p2[0]);
    }
    var total = _gcfDetailLineLength(pts);
    var label = (mode === 'draw_polygon' ? 'perimeter' : 'length');
    _gcfDetailTipShow(e.point, 'segment ' + _gcfFmtDist(seg) + ' · ' + label + ' ' + _gcfFmtDist(total));
  });

  // Hide tooltip when cursor leaves the map entirely.
  _gcfDetailMap.getContainer().addEventListener('mouseleave', _gcfDetailTipHide);

  // Delete selected with Del key
  document.addEventListener('keydown', function(e) {
    if (e.key !== 'Delete' && e.key !== 'Backspace') return;
    if (!_gcfDetailDraw) return;
    var t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    var selected = _gcfDetailDraw.getSelectedIds();
    if (selected && selected.length) {
      _gcfDetailDraw.delete(selected);
      _gcfDetailSaveDrawings();
    }
  });
}

function _gcfDetailClearDrawButtons() {
  document.querySelectorAll('#draw-tools .btn').forEach(function(b) {
    b.classList.remove('active');
  });
}

function _gcfDetailDrawKey() {
  return 'gcforge_drawings_' + (_gcfDetailState.cacheCode || 'unknown');
}

function _gcfDetailSaveDrawings() {
  if (!_gcfDetailDraw) return;
  try {
    var fc = _gcfDetailDraw.getAll();
    localStorage.setItem(_gcfDetailDrawKey(), JSON.stringify(fc));
  } catch(e) {}
}

function _gcfDetailLoadDrawings() {
  if (!_gcfDetailDraw) {
    // Draw may not be ready yet (e.g. after style reload); retry once
    setTimeout(_gcfDetailLoadDrawings, 100);
    return;
  }
  try {
    var raw = localStorage.getItem(_gcfDetailDrawKey());
    if (!raw) return;
    var fc = JSON.parse(raw);
    if (fc && fc.features) _gcfDetailDraw.set(fc);
  } catch(e) {}
}

window.gcfDetailDrawMode = function(mode, btn) {
  if (!_gcfDetailDraw) return;
  _gcfDetailDraw.changeMode(mode);
  document.querySelectorAll('#draw-tools .btn').forEach(function(b) {
    b.classList.remove('active');
  });
  if (btn) btn.classList.add('active');
};

window.gcfDetailDrawClearAll = function() {
  if (!_gcfDetailDraw) return;
  _gcfDetailDraw.deleteAll();
  try { localStorage.removeItem(_gcfDetailDrawKey()); } catch(e) {}
};

window.gcfDetailDrawDeleteSelected = function() {
  if (!_gcfDetailDraw) return;
  var ids = _gcfDetailDraw.getSelectedIds();
  if (ids && ids.length) {
    _gcfDetailDraw.delete(ids);
    _gcfDetailSaveDrawings();
  }
};

// ── Custom draw modes: rectangle + circle (self-contained) ──────────────────

function _gcfDetailRectCoords(start, end) {
  return [
    [start[0], start[1]], [end[0], start[1]],
    [end[0], end[1]], [start[0], end[1]],
    [start[0], start[1]]
  ];
}

function _gcfDetailCircleCoords(centerLng, centerLat, radius_m) {
  var n = 64;
  var coords = [];
  var lat_r = centerLat * Math.PI / 180;
  for (var i = 0; i < n; i++) {
    var angle = (i / n) * 2 * Math.PI;
    var dx = radius_m * Math.cos(angle) / (111320 * Math.cos(lat_r));
    var dy = radius_m * Math.sin(angle) / 110540;
    coords.push([centerLng + dx, centerLat + dy]);
  }
  coords.push(coords[0]);
  return coords;
}

function _gcfDetailHaversineM(lat1, lon1, lat2, lon2) {
  var R = 6371000;
  var dLat = (lat2 - lat1) * Math.PI / 180;
  var dLon = (lon2 - lon1) * Math.PI / 180;
  var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
          Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
          Math.sin(dLon / 2) * Math.sin(dLon / 2);
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function _GcfDetailDrawRectangle() {
  var m = {};
  m.onSetup = function() {
    var rect = this.newFeature({
      type: 'Feature',
      properties: { shape: 'rect' },
      geometry: { type: 'Polygon', coordinates: [[]] }
    });
    this.addFeature(rect);
    this.clearSelectedFeatures();
    this.updateUIClasses({ mouse: 'add' });
    this.setActionableState({ trash: false });
    return { rect: rect, start: null, anchored: false, committed: false };
  };
  m.onMouseDown = function(state, e) {
    if (state.anchored) return;
    state.start = [e.lngLat.lng, e.lngLat.lat];
    this.map.dragPan.disable();
  };
  m.onDrag = function(state, e) {
    if (!state.start || state.anchored) return;
    var cur = [e.lngLat.lng, e.lngLat.lat];
    state.rect.incomingCoords([_gcfDetailRectCoords(state.start, cur)]);
    _gcfDetailRectTip(state.start, cur, e.point);
  };
  m.onMouseMove = function(state, e) {
    if (!state.anchored || !state.start) return;
    var cur = [e.lngLat.lng, e.lngLat.lat];
    state.rect.incomingCoords([_gcfDetailRectCoords(state.start, cur)]);
    _gcfDetailRectTip(state.start, cur, e.point);
  };
  m.onMouseUp = function(state, e) {
    this.map.dragPan.enable();
    if (!state.start || state.anchored) return;
    var end = [e.lngLat.lng, e.lngLat.lat];
    if (Math.abs(end[0] - state.start[0]) < 0.0001 &&
        Math.abs(end[1] - state.start[1]) < 0.0001) {
      state.anchored = true;
      return;
    }
    _gcfDetailFinalizeRect(state, end, this);
  };
  m.onClick = function(state, e) {
    if (!state.anchored) {
      state.start = [e.lngLat.lng, e.lngLat.lat];
      state.anchored = true;
      this.map.dragPan.enable();
    } else {
      _gcfDetailFinalizeRect(state, [e.lngLat.lng, e.lngLat.lat], this);
    }
  };
  m.onStop = function(state) {
    this.updateUIClasses({ mouse: 'none' });
    this.map.dragPan.enable();
    _gcfDetailTipHide();
    if (!state.committed) this.deleteFeature([state.rect.id], { silent: true });
  };
  m.onTrash = function(state) {
    this.deleteFeature([state.rect.id], { silent: true });
    this.changeMode('simple_select');
  };
  m.toDisplayFeatures = function(state, geojson, display) {
    if (state.rect && state.rect.id === geojson.properties.id) {
      geojson.properties.active = 'true';
    }
    display(geojson);
  };
  return m;
}

function _gcfDetailRectTip(start, cur, point) {
  var w = _gcfDetailHaversineM(start[1], start[0], start[1], cur[0]);
  var h = _gcfDetailHaversineM(start[1], start[0], cur[1], start[0]);
  _gcfDetailTipShow(point, _gcfFmtDist(w) + ' × ' + _gcfFmtDist(h));
}

function _gcfDetailFinalizeRect(state, end, ctx) {
  state.rect.incomingCoords([_gcfDetailRectCoords(state.start, end)]);
  state.committed = true;
  _gcfDetailDrawEndedAt = Date.now();
  _gcfDetailTipHide();
  // Manually fire draw.create (custom modes don't auto-fire it)
  ctx.map.fire('draw.create', { features: [state.rect.toGeoJSON()] });
  ctx.changeMode('simple_select');
}

function _GcfDetailDrawCircle() {
  var m = {};
  m.onSetup = function() {
    var circle = this.newFeature({
      type: 'Feature',
      properties: { shape: 'circle' },
      geometry: { type: 'Polygon', coordinates: [[]] }
    });
    this.addFeature(circle);
    this.clearSelectedFeatures();
    this.updateUIClasses({ mouse: 'add' });
    this.setActionableState({ trash: false });
    return { circle: circle, center: null, anchored: false, committed: false };
  };
  m.onMouseDown = function(state, e) {
    if (state.anchored) return;
    state.center = [e.lngLat.lng, e.lngLat.lat];
    this.map.dragPan.disable();
  };
  m.onDrag = function(state, e) {
    if (!state.center || state.anchored) return;
    var r = _gcfDetailHaversineM(state.center[1], state.center[0], e.lngLat.lat, e.lngLat.lng);
    if (r > 0) state.circle.incomingCoords([_gcfDetailCircleCoords(state.center[0], state.center[1], r)]);
    _gcfDetailTipShow(e.point, 'radius ' + _gcfFmtDist(r));
  };
  m.onMouseMove = function(state, e) {
    if (!state.anchored || !state.center) return;
    var r = _gcfDetailHaversineM(state.center[1], state.center[0], e.lngLat.lat, e.lngLat.lng);
    if (r > 0) state.circle.incomingCoords([_gcfDetailCircleCoords(state.center[0], state.center[1], r)]);
    _gcfDetailTipShow(e.point, 'radius ' + _gcfFmtDist(r));
  };
  m.onMouseUp = function(state, e) {
    this.map.dragPan.enable();
    if (!state.center || state.anchored) return;
    var r = _gcfDetailHaversineM(state.center[1], state.center[0], e.lngLat.lat, e.lngLat.lng);
    if (r < 10) { state.anchored = true; return; }
    _gcfDetailFinalizeCircle(state, e.lngLat, this);
  };
  m.onClick = function(state, e) {
    if (!state.anchored) {
      state.center = [e.lngLat.lng, e.lngLat.lat];
      state.anchored = true;
      this.map.dragPan.enable();
    } else {
      var r = _gcfDetailHaversineM(state.center[1], state.center[0], e.lngLat.lat, e.lngLat.lng);
      if (r < 10) return;
      _gcfDetailFinalizeCircle(state, e.lngLat, this);
    }
  };
  m.onStop = function(state) {
    this.updateUIClasses({ mouse: 'none' });
    this.map.dragPan.enable();
    _gcfDetailTipHide();
    if (!state.committed) this.deleteFeature([state.circle.id], { silent: true });
  };
  m.onTrash = function(state) {
    this.deleteFeature([state.circle.id], { silent: true });
    this.changeMode('simple_select');
  };
  m.toDisplayFeatures = function(state, geojson, display) {
    if (state.circle && state.circle.id === geojson.properties.id) {
      geojson.properties.active = 'true';
    }
    display(geojson);
  };
  return m;
}

function _gcfDetailFinalizeCircle(state, lngLat, ctx) {
  var r = _gcfDetailHaversineM(state.center[1], state.center[0], lngLat.lat, lngLat.lng);
  state.circle.incomingCoords([_gcfDetailCircleCoords(state.center[0], state.center[1], r)]);
  state.circle.setProperty('center', [state.center[1], state.center[0]]);
  state.circle.setProperty('radius_m', Math.round(r));
  state.committed = true;
  _gcfDetailDrawEndedAt = Date.now();
  _gcfDetailTipHide();
  ctx.map.fire('draw.create', { features: [state.circle.toGeoJSON()] });
  ctx.changeMode('simple_select');
}

// ── Resizable dividers (session-only; not persisted) ───────────────────────

function _gcfDetailSetupDividers() {
  _gcfDetailSetupColDivider();
  _gcfDetailSetupMapResizer();
}

function _gcfDetailSetupColDivider() {
  var row = document.querySelector('.gcf-detail-row');
  var div = document.querySelector('.gcf-detail-col-divider');
  if (!row || !div) return;

  var dragging = false;
  div.addEventListener('mousedown', function(e) {
    e.preventDefault();
    dragging = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  });
  document.addEventListener('mousemove', function(e) {
    if (!dragging) return;
    var rect = row.getBoundingClientRect();
    var pct = ((e.clientX - rect.left) / rect.width) * 100;
    pct = Math.max(25, Math.min(80, pct));
    row.style.setProperty('--gcf-detail-left-w', pct + '%');
    if (_gcfDetailMap) _gcfDetailMap.resize();
  });
  document.addEventListener('mouseup', function() {
    if (!dragging) return;
    dragging = false;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    if (_gcfDetailMap) _gcfDetailMap.resize();
  });
}

function _gcfDetailSetupMapResizer() {
  var resizer = document.querySelector('.gcf-detail-map-resizer');
  var mapEl = document.getElementById('cache-map');
  if (!resizer || !mapEl) return;

  var dragging = false, startY = 0, startH = 0;
  resizer.addEventListener('mousedown', function(e) {
    e.preventDefault();
    dragging = true;
    startY = e.clientY;
    startH = mapEl.offsetHeight;
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';
  });
  document.addEventListener('mousemove', function(e) {
    if (!dragging) return;
    var h = startH + (e.clientY - startY);
    h = Math.max(160, Math.min(1400, h));
    mapEl.style.height = h + 'px';
    if (_gcfDetailMap) _gcfDetailMap.resize();
  });
  document.addEventListener('mouseup', function() {
    if (!dragging) return;
    dragging = false;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    if (_gcfDetailMap) _gcfDetailMap.resize();
  });
}

// ── Live measurement tooltip (during drawing) ───────────────────────────────

function _gcfDetailTipEnsure() {
  if (_gcfDetailTipEl) return _gcfDetailTipEl;
  var el = document.createElement('div');
  el.className = 'gcf-draw-measure-tip';
  el.style.display = 'none';
  document.body.appendChild(el);
  _gcfDetailTipEl = el;
  return el;
}

function _gcfDetailTipShow(pt, text) {
  var el = _gcfDetailTipEnsure();
  el.textContent = text;
  el.style.display = 'block';
  var rect = _gcfDetailMap.getContainer().getBoundingClientRect();
  el.style.left = (rect.left + pt.x + 12) + 'px';
  el.style.top = (rect.top + pt.y - 18) + 'px';
}

function _gcfDetailTipHide() {
  if (_gcfDetailTipEl) _gcfDetailTipEl.style.display = 'none';
}

function _gcfFmtDist(m) {
  if (!isFinite(m)) return '—';
  if (m < 1000) return m.toFixed(0) + ' m';
  return (m / 1000).toFixed(2) + ' km';
}

function _gcfDetailLineLength(coords) {
  var sum = 0;
  for (var i = 0; i < coords.length - 1; i++) {
    sum += _gcfDetailHaversineM(coords[i][1], coords[i][0], coords[i+1][1], coords[i+1][0]);
  }
  return sum;
}

// ── Right-click context menu ─────────────────────────────────────────────────

var _gcfDetailCtxMenuEl = null;
var _gcfDetailRangeCircles = [];
var _gcfDetailRangeCircleColors = [
  '#0d6efd', '#dc3545', '#198754', '#fd7e14', '#6f42c1',
  '#20c997', '#e83e8c', '#6610f2', '#795548', '#17a2b8'
];

function _gcfDetailSetupContextMenu() {
  // Create the DOM element once; re-attach layer handlers every call (layers are
  // re-added after each style reload so the old handlers are gone).
  if (!_gcfDetailCtxMenuEl) {
    _gcfDetailCtxMenuEl = document.createElement('div');
    _gcfDetailCtxMenuEl.className = 'map-draw-context-menu';
    _gcfDetailMap.getContainer().appendChild(_gcfDetailCtxMenuEl);

    document.addEventListener('click', function() {
      if (_gcfDetailCtxMenuEl) _gcfDetailCtxMenuEl.classList.remove('open');
    });
    _gcfDetailMap.on('movestart', function() {
      if (_gcfDetailCtxMenuEl) _gcfDetailCtxMenuEl.classList.remove('open');
    });
  }

  function _showCtx(e) {
    e.preventDefault();
    var lat = e.lngLat.lat;
    var lng = e.lngLat.lng;
    _gcfDetailCtxMenuEl.innerHTML = '';

    var streetViewBtn = document.createElement('button');
    streetViewBtn.className = 'map-draw-context-item';
    streetViewBtn.textContent = gettext('Street View');
    streetViewBtn.onclick = function(ev) {
      ev.stopPropagation();
      _gcfDetailCtxMenuEl.classList.remove('open');
      window.open('https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=' +
        lat.toFixed(6) + ',' + lng.toFixed(6), '_blank');
    };
    _gcfDetailCtxMenuEl.appendChild(streetViewBtn);

    var rangeBtn = document.createElement('button');
    rangeBtn.className = 'map-draw-context-item';
    rangeBtn.textContent = gettext('Range circle…');
    rangeBtn.onclick = function(ev) {
      ev.stopPropagation();
      _gcfDetailCtxMenuEl.classList.remove('open');
      _gcfDetailPromptRangeCircle(lng, lat);
    };
    _gcfDetailCtxMenuEl.appendChild(rangeBtn);

    var container = _gcfDetailMap.getContainer();
    var rect = container.getBoundingClientRect();
    var x = e.point.x;
    var y = e.point.y;
    _gcfDetailCtxMenuEl.style.left = x + 'px';
    _gcfDetailCtxMenuEl.style.top = y + 'px';
    _gcfDetailCtxMenuEl.classList.add('open');

    var menuRect = _gcfDetailCtxMenuEl.getBoundingClientRect();
    if (x + menuRect.width > rect.width) {
      _gcfDetailCtxMenuEl.style.left = (x - menuRect.width) + 'px';
    }
    if (y + menuRect.height > rect.height) {
      _gcfDetailCtxMenuEl.style.top = (y - menuRect.height) + 'px';
    }
  }

  ['detail-cache-layer', 'detail-corrected-layer',
   'detail-stages-layer', 'detail-waypoints-layer'].forEach(function(id) {
    if (_gcfDetailMap.getLayer(id)) {
      _gcfDetailMap.on('contextmenu', id, _showCtx);
    }
  });
}

function _gcfDetailPromptRangeCircle(lng, lat) {
  var input = window.prompt(gettext('Radius in km (use "." as decimal separator):'), '2');
  if (input === null) return;
  var km = parseFloat(input.trim());
  if (isNaN(km) || km <= 0) return;

  var colorIdx = _gcfDetailRangeCircles.length % _gcfDetailRangeCircleColors.length;
  var color = _gcfDetailRangeCircleColors[colorIdx];
  var coords = _gcfDetailCircleCoords(lng, lat, km * 1000);
  var id = _gcfDetailRangeCircles.length;
  var srcId = 'gcf-det-rc-src-' + id;
  var fillId = 'gcf-det-rc-fill-' + id;
  var lineId = 'gcf-det-rc-line-' + id;

  _gcfDetailMap.addSource(srcId, {
    type: 'geojson',
    data: { type: 'Feature', geometry: { type: 'Polygon', coordinates: [coords] } }
  });
  _gcfDetailMap.addLayer({
    id: fillId, type: 'fill', source: srcId,
    paint: { 'fill-color': color, 'fill-opacity': 0.08 }
  });
  _gcfDetailMap.addLayer({
    id: lineId, type: 'line', source: srcId,
    paint: { 'line-color': color, 'line-width': 2, 'line-opacity': 0.7 }
  });
  _gcfDetailRangeCircles.push({ srcId: srcId, fillId: fillId, lineId: lineId });
}

// ── Utilities ───────────────────────────────────────────────────────────────

function _gcfEsc(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
