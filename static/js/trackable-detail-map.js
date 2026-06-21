// ── GCForge Trackable Detail Map — MapLibre ───────────────────────────────────
//
// Renders a movement track for a trackable: one pin per cache log with known
// coords, a polyline connecting them chronologically, popups on click.
//
// Data source: <script id="tb-movements" type="application/json"> injected by
// the Django template via {{ movements|json_script:"tb-movements" }}.
//
// Fallback: if no movements have coords, centres on the home reference point
// (data-home-lat / data-home-lon on the map div), zoom 9.
//
// Depends on: maplibre-gl, map-styles.js (GCF_STYLES, gcfMapTransformRequest,
//             gcfSuppressMissingImages)

var _gcfTbMap = null;
var _gcfTbStyleId = 'street';
var _gcfTbPinById = {};
var _gcfTbFeatureById = {};

function gcfTrackableMapInit() {
  var mapEl = document.getElementById('tb-movement-map');
  if (!mapEl || _gcfTbMap) return;

  if (typeof maplibregl === 'undefined' || typeof GCF_STYLES === 'undefined') return;

  var movements = [];
  try {
    var el = document.getElementById('tb-movements');
    if (el) movements = JSON.parse(el.textContent || '[]');
  } catch (e) {}

  var savedStyle = localStorage.getItem('gcforge_map_style') || 'street';
  if (!GCF_STYLES[savedStyle]) savedStyle = 'street';
  _gcfTbStyleId = savedStyle;

  var currentLat  = parseFloat(mapEl.dataset.currentLat)  || null;
  var currentLon  = parseFloat(mapEl.dataset.currentLon)  || null;
  var currentCode = mapEl.dataset.currentCode || '';
  var currentName = mapEl.dataset.currentName || '';
  var currentPos  = (currentLat && currentLon) ? {lat: currentLat, lon: currentLon} : null;

  var initCenter, initZoom;
  if (movements.length > 0) {
    // Start somewhere visible; fitBounds will correct after load.
    initCenter = [movements[0].lon, movements[0].lat];
    initZoom = 5;
  } else if (currentPos) {
    initCenter = [currentPos.lon, currentPos.lat];
    initZoom = 10;
  } else {
    var homeLat = parseFloat(mapEl.dataset.homeLat) || 51;
    var homeLon = parseFloat(mapEl.dataset.homeLon) || 10;
    initCenter = [homeLon, homeLat];
    initZoom = 9;
  }

  _gcfTbMap = new maplibregl.Map({
    container: 'tb-movement-map',
    style: GCF_STYLES[savedStyle],
    center: initCenter,
    zoom: initZoom,
    attributionControl: true,
    transformRequest: gcfMapTransformRequest
  });

  _gcfTbMap.addControl(new maplibregl.NavigationControl(), 'top-left');
  _gcfTbMap.addControl(new maplibregl.ScaleControl({ maxWidth: 100, unit: 'metric' }), 'bottom-left');

  if (typeof gcfSuppressMissingImages === 'function') gcfSuppressMissingImages(_gcfTbMap);

  _gcfTbHighlightLayerButton();

  _gcfTbMap.on('load', function() {
    _gcfTbMap.resize();
    if (movements.length > 0) {
      _gcfTbAddTrack(movements, currentPos);
    }
    if (currentPos) {
      _gcfTbAddCurrentPos(currentPos.lat, currentPos.lon, currentCode, currentName);
      if (movements.length === 0) {
        _gcfTbMap.flyTo({ center: [currentPos.lon, currentPos.lat], zoom: 10 });
      }
    }
  });

  if (window.ResizeObserver) {
    new ResizeObserver(function() { if (_gcfTbMap) _gcfTbMap.resize(); }).observe(mapEl);
  }
  window.addEventListener('resize', function() { if (_gcfTbMap) _gcfTbMap.resize(); });

  // Force resize after CSS settles
  requestAnimationFrame(function() { if (_gcfTbMap) _gcfTbMap.resize(); });
  setTimeout(function() { if (_gcfTbMap) _gcfTbMap.resize(); }, 300);
}

function _gcfTbAddTrack(movements, currentPos) {
  if (!_gcfTbMap || !movements.length) return;

  // Build GeoJSON features
  var coords = movements.map(function(m) { return [m.lon, m.lat]; });

  // Polyline
  _gcfTbMap.addSource('tb-track', {
    type: 'geojson',
    data: {
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: coords }
    }
  });
  _gcfTbMap.addLayer({
    id: 'tb-track-line',
    type: 'line',
    source: 'tb-track',
    paint: {
      'line-color': '#0d6efd',
      'line-width': 2,
      'line-opacity': 0.7
    }
  });

  // Base pins — all movement points, plain blue.
  _gcfTbPinById = {};
  _gcfTbFeatureById = {};
  var pinFeatures = movements.map(function(m, i) {
    _gcfTbPinById[m.id] = [m.lon, m.lat];
    var feat = {
      type: 'Feature',
      id: m.id,
      geometry: { type: 'Point', coordinates: [m.lon, m.lat] },
      properties: {
        date:    m.date,
        type:    m.type,
        code:    m.code,
        snippet: m.text_snippet,
        idx:     i + 1
      }
    };
    _gcfTbFeatureById[m.id] = feat;
    return feat;
  });
  _gcfTbMap.addSource('tb-pins', {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: pinFeatures }
  });
  _gcfTbMap.addLayer({
    id: 'tb-pins-circle',
    type: 'circle',
    source: 'tb-pins',
    paint: {
      'circle-radius': 6,
      'circle-color': '#0d6efd',
      'circle-stroke-color': '#fff',
      'circle-stroke-width': 1.5
    }
  });

  // Highlight overlay — single feature, rendered after tb-pins-circle so it
  // always paints on top of the base pins. Driven by gcfTbHighlightLog().
  _gcfTbMap.addSource('tb-pin-highlight', {
    type: 'geojson',
    data: { type: 'FeatureCollection', features: [] }
  });
  _gcfTbMap.addLayer({
    id: 'tb-pin-highlight-circle',
    type: 'circle',
    source: 'tb-pin-highlight',
    paint: {
      'circle-radius': 9,
      'circle-color': '#198754',
      'circle-stroke-color': '#fff',
      'circle-stroke-width': 2
    }
  });

  // Popups + cursor on both layers (highlight layer sits on top, so its
  // click/mouseenter intercept events before the base layer would).
  ['tb-pins-circle', 'tb-pin-highlight-circle'].forEach(function(layerId) {
    _gcfTbMap.on('click', layerId, function(e) {
      var props = e.features[0].properties;
      var html = '<strong>' + _gcfEsc(props.code) + '</strong><br>'
               + '<span class="text-muted">' + _gcfEsc(props.date) + ' — ' + _gcfEsc(props.type) + '</span>';
      if (props.snippet) html += '<br><small>' + _gcfEsc(props.snippet) + '</small>';
      new maplibregl.Popup({ closeButton: false, maxWidth: '280px' })
        .setLngLat(e.lngLat)
        .setHTML(html)
        .addTo(_gcfTbMap);
    });
    _gcfTbMap.on('mouseenter', layerId, function() {
      _gcfTbMap.getCanvas().style.cursor = 'pointer';
    });
    _gcfTbMap.on('mouseleave', layerId, function() {
      _gcfTbMap.getCanvas().style.cursor = '';
    });
  });

  // Fit to all pins, including current position if provided
  var allLats = movements.map(function(m) { return m.lat; });
  var allLons = movements.map(function(m) { return m.lon; });
  if (currentPos) { allLats.push(currentPos.lat); allLons.push(currentPos.lon); }

  if (allLats.length === 1) {
    _gcfTbMap.flyTo({ center: [allLons[0], allLats[0]], zoom: 10 });
  } else {
    _gcfTbMap.fitBounds(
      [[Math.min.apply(null, allLons), Math.min.apply(null, allLats)],
       [Math.max.apply(null, allLons), Math.max.apply(null, allLats)]],
      { padding: 40, maxZoom: 14 }
    );
  }
}

function _gcfTbAddCurrentPos(lat, lon, code, name) {
  if (!_gcfTbMap) return;
  _gcfTbMap.addSource('tb-current-pos', {
    type: 'geojson',
    data: {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [lon, lat] },
      properties: { code: code, name: name }
    }
  });
  _gcfTbMap.addLayer({
    id: 'tb-current-pos-circle',
    type: 'circle',
    source: 'tb-current-pos',
    paint: {
      'circle-radius': 10,
      'circle-color': '#ffc107',
      'circle-stroke-color': '#fff',
      'circle-stroke-width': 2
    }
  });
  _gcfTbMap.on('click', 'tb-current-pos-circle', function(e) {
    var props = e.features[0].properties;
    var label = props.name || props.code || gettext('Unknown');
    var html = '<strong>' + gettext('Current location') + '</strong><br>'
             + '<span class="text-muted">' + _gcfEsc(label) + '</span>';
    if (props.name && props.code) html += '<br><small class="text-muted">' + _gcfEsc(props.code) + '</small>';
    new maplibregl.Popup({ closeButton: false, maxWidth: '240px' })
      .setLngLat(e.lngLat)
      .setHTML(html)
      .addTo(_gcfTbMap);
  });
  _gcfTbMap.on('mouseenter', 'tb-current-pos-circle', function() {
    _gcfTbMap.getCanvas().style.cursor = 'pointer';
  });
  _gcfTbMap.on('mouseleave', 'tb-current-pos-circle', function() {
    _gcfTbMap.getCanvas().style.cursor = '';
  });
}

function _gcfEsc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Log-row interaction ──────────────────────────────────────────────────────
// Called from the template's log-row mouseenter/leave/click handlers. id is a
// TrackableLog primary key.

function gcfTbHighlightLog(id) {
  if (!_gcfTbMap) return;
  var src = _gcfTbMap.getSource && _gcfTbMap.getSource('tb-pin-highlight');
  if (!src) return;

  if (id === null || !_gcfTbFeatureById[id]) {
    src.setData({ type: 'FeatureCollection', features: [] });
    return;
  }

  // Render the highlight pin (carrying the same properties so click popups
  // work from either layer).
  src.setData({ type: 'FeatureCollection', features: [_gcfTbFeatureById[id]] });

  // If the highlighted pin is outside the current viewport, pan/zoom so both
  // the previous map center and the new pin are visible with a ~10% margin.
  // maxZoom=current means we'll zoom out as needed but never zoom further in
  // on hover (zooming in is reserved for click → gcfTbFocusLog).
  var coord = _gcfTbPinById[id];
  var bounds = _gcfTbMap.getBounds();
  var lng = coord[0], lat = coord[1];
  var inside = lng >= bounds.getWest() && lng <= bounds.getEast()
            && lat >= bounds.getSouth() && lat <= bounds.getNorth();
  if (inside) return;

  var center = _gcfTbMap.getCenter();
  var newBounds = new maplibregl.LngLatBounds(
    [Math.min(lng, center.lng), Math.min(lat, center.lat)],
    [Math.max(lng, center.lng), Math.max(lat, center.lat)]
  );
  var rect = _gcfTbMap.getContainer().getBoundingClientRect();
  var pad = Math.round(Math.min(rect.width, rect.height) * 0.1);
  _gcfTbMap.fitBounds(newBounds, {
    padding: pad,
    duration: 300,
    maxZoom: _gcfTbMap.getZoom()
  });
}

function gcfTbFocusLog(id) {
  if (!_gcfTbMap) return;
  var coord = _gcfTbPinById[id];
  if (!coord) return;
  _gcfTbMap.flyTo({ center: coord, zoom: 14 });
}

// ── Layer switcher ────────────────────────────────────────────────────────────

function gcfTbSetLayer(styleId, btn) {
  if (!_gcfTbMap || !GCF_STYLES[styleId]) return;
  _gcfTbStyleId = styleId;
  localStorage.setItem('gcforge_map_style', styleId);
  _gcfTbMap.setStyle(GCF_STYLES[styleId]);
  _gcfTbHighlightLayerButton();
}

function _gcfTbHighlightLayerButton() {
  document.querySelectorAll('#tb-layer-switcher button[data-layer]').forEach(function(b) {
    b.classList.toggle('active', b.dataset.layer === _gcfTbStyleId);
  });
}
