// ── GCForge Map Layers — separation circles, corrected coords, waypoints, labels
//
// Depends on: cache-map.js (gcfMap, _gcfMarkersData, _gcfMarkerColor)
//             map-draw.js (_gcfCircleCoords, _gcfHaversineM)

// ── State tracking ──────────────────────────────────────────────────────────

var _gcfSepCirclesEnabled = false;
var _gcfSepCirclesTimer = null;
var _gcfSepCirclesMoveHandler = null;

var _gcfCorrectedEnabled = false;

var _gcfWaypointsEnabled = false;
var _gcfWaypointCache = {};       // keyed by cache code
var _gcfWaypointsTimer = null;
var _gcfWaypointsMoveHandler = null;

var _gcfLabelsMode = null;        // 'name', 'code', or null
var _gcfLayerDefaultsApplied = false;


// ── Persist / restore layer state via localStorage ──────────────────────────

function _gcfSaveLayerState() {
  var state = {
    sep_circles: _gcfSepCirclesEnabled,
    corrected: _gcfCorrectedEnabled,
    waypoints: _gcfWaypointsEnabled,
    labels: _gcfLabelsMode
  };
  localStorage.setItem('gcforge_map_layers', JSON.stringify(state));
}

function gcfLayersInit() {
  // Apply saved session state or server defaults (once per session).
  if (_gcfLayerDefaultsApplied) return;
  _gcfLayerDefaultsApplied = true;

  var prefs = (typeof _gcfMapPrefs !== 'undefined') ? _gcfMapPrefs : {};
  var saved = null;
  try {
    var raw = localStorage.getItem('gcforge_map_layers');
    if (raw) saved = JSON.parse(raw);
  } catch(e) {}

  // Determine effective state: localStorage overrides server defaults
  var sepCircles = saved ? !!saved.sep_circles : !!prefs.layer_sep_circles;
  var corrected = saved ? !!saved.corrected : !!prefs.layer_corrected;
  var waypoints = saved ? !!saved.waypoints : !!prefs.layer_waypoints;
  var labels = saved ? (saved.labels || null) : (prefs.layer_labels || null);

  // Apply state — set checkboxes and activate layers
  if (sepCircles) {
    var cb = document.getElementById('layer-sep-circles');
    if (cb) cb.checked = true;
    gcfToggleSepCircles(true);
  }
  if (corrected) {
    var cb2 = document.getElementById('layer-corrected');
    if (cb2) cb2.checked = true;
    gcfToggleCorrectedCoords(true);
  }
  if (waypoints) {
    var cb3 = document.getElementById('layer-waypoints');
    if (cb3) cb3.checked = true;
    gcfToggleWaypoints(true);
  }
  if (labels) {
    var radio = document.querySelector('input[name="map-labels"][value="' + labels + '"]');
    if (radio) radio.checked = true;
    gcfToggleLabels(labels);
  }

  // LOD panel: localStorage overrides server default
  var lodSaved = null;
  try {
    var lodRaw = localStorage.getItem('gcforge_lod');
    if (lodRaw) lodSaved = JSON.parse(lodRaw);
  } catch(e) {}
  var showLod = lodSaved && lodSaved.visible != null ? !!lodSaved.visible : !!prefs.layer_lod;
  if (showLod) {
    gcfToggleLodPanel(true);
  }
}


// ═══════════════════════════════════════════════════════════════════════════
// 1. Separation Circles (161m)
// ═══════════════════════════════════════════════════════════════════════════

var _GCF_NO_CONTAINER_TYPES = { V: 1, E: 1, W: 1, Ev: 1, CI: 1, ME: 1, GE: 1, CC: 1, Lo: 1, L: 1 };

function _gcfBuildSepCirclesGeoJSON() {
  if (!gcfMap || !_gcfMarkersData) return { type: 'FeatureCollection', features: [] };

  var bounds = gcfMap.getBounds();
  var sw = bounds.getSouthWest();
  var ne = bounds.getNorthEast();
  var features = [];

  function addCircle(lat, lon, code) {
    if (lat < sw.lat || lat > ne.lat || lon < sw.lng || lon > ne.lng) return;
    var ring = _gcfCircleCoords(lon, lat, 161.0);
    features.push({
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: [ring] },
      properties: { code: code }
    });
  }

  for (var i = 0; i < _gcfMarkersData.length; i++) {
    var m = _gcfMarkersData[i];
    if (_gcfHiddenTypes[m.t]) continue;

    var wps = _gcfWaypointCache[m.c] || [];
    var hasFinal = false;

    // No-container types (Virtual, Earthcache, etc.) — never get circles, period.
    if (_GCF_NO_CONTAINER_TYPES[m.t]) continue;

    // Final and Stage waypoint circles.
    // Rule 3: If a Final Location waypoint exists, draw a circle there.
    // Rule 1 (multi): Also draw circles at physical Stage waypoints.
    for (var w = 0; w < wps.length; w++) {
      var wp = wps[w];
      if (wp.t === 'F') {
        hasFinal = true;
        addCircle(wp.la, wp.lo, m.c);
      } else if (wp.t === 'S' && m.t === 'M') {
        addCircle(wp.la, wp.lo, m.c);
      }
    }

    // Rule 1: Multi-caches — only Final and Stage waypoints get circles (handled above).
    if (m.t === 'M') continue;

    // All other cache types: draw circle at main position (corrected coords
    // are already promoted to m.la/m.lo by cache-map.js).
    if (!hasFinal) {
      addCircle(m.la, m.lo, m.c);
    }
  }

  return { type: 'FeatureCollection', features: features };
}

function _gcfAddSepCircleLayers() {
  if (!gcfMap) return;

  _gcfEnsureSepCircleWaypoints(function() {
    var geojson = _gcfBuildSepCirclesGeoJSON();

    if (gcfMap.getSource('gcf-sep-circles')) {
      gcfMap.getSource('gcf-sep-circles').setData(geojson);
      return;
    }

    gcfMap.addSource('gcf-sep-circles', { type: 'geojson', data: geojson });

    gcfMap.addLayer({
      id: 'gcf-sep-circles-fill',
      type: 'fill',
      source: 'gcf-sep-circles',
      paint: {
        'fill-color': '#ff0000',
        'fill-opacity': 0.125
      }
    });

    gcfMap.addLayer({
      id: 'gcf-sep-circles-line',
      type: 'line',
      source: 'gcf-sep-circles',
      paint: {
        'line-color': '#ff0000',
        'line-opacity': 0.5,
        'line-width': 1
      }
    });
  });
}

function _gcfEnsureSepCircleWaypoints(callback) {
  var codes = _gcfVisibleCacheCodes();
  var uncached = [];
  for (var i = 0; i < codes.length; i++) {
    if (_gcfWaypointCache[codes[i]] === undefined) uncached.push(codes[i]);
  }
  if (uncached.length === 0) { callback(); return; }

  var batchSize = 50;
  var pending = 0;

  function processBatch(batch) {
    pending++;
    fetch('/map/waypoints/?codes=' + batch.join(','))
      .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function(data) {
        var fetched = data.waypoints || [];
        var fetchedCodes = {};
        for (var k = 0; k < fetched.length; k++) {
          fetchedCodes[fetched[k].code] = true;
          _gcfWaypointCache[fetched[k].code] = fetched[k].wp || [];
        }
        for (var b = 0; b < batch.length; b++) {
          if (!fetchedCodes[batch[b]]) _gcfWaypointCache[batch[b]] = [];
        }
      })
      .catch(function(err) {
        console.error('GCForge map: failed to fetch waypoints for sep circles', err);
      })
      .finally(function() {
        pending--;
        if (pending === 0) callback();
      });
  }

  for (var b = 0; b < uncached.length; b += batchSize) {
    processBatch(uncached.slice(b, b + batchSize));
  }
}

function _gcfRemoveSepCircleLayers() {
  if (!gcfMap) return;
  if (gcfMap.getLayer('gcf-sep-circles-fill')) gcfMap.removeLayer('gcf-sep-circles-fill');
  if (gcfMap.getLayer('gcf-sep-circles-line')) gcfMap.removeLayer('gcf-sep-circles-line');
  if (gcfMap.getSource('gcf-sep-circles')) gcfMap.removeSource('gcf-sep-circles');
}

function _gcfSepCirclesMoveEnd() {
  if (_gcfSepCirclesTimer) clearTimeout(_gcfSepCirclesTimer);
  _gcfSepCirclesTimer = setTimeout(function() {
    if (_gcfSepCirclesEnabled) _gcfAddSepCircleLayers();
  }, 300);
}

function gcfToggleSepCircles(enabled) {
  _gcfSepCirclesEnabled = !!enabled;
  _gcfSaveLayerState();

  if (_gcfSepCirclesEnabled) {
    _gcfAddSepCircleLayers();
    if (!_gcfSepCirclesMoveHandler) {
      _gcfSepCirclesMoveHandler = _gcfSepCirclesMoveEnd;
      gcfMap.on('moveend', _gcfSepCirclesMoveHandler);
    }
  } else {
    _gcfRemoveSepCircleLayers();
    if (_gcfSepCirclesMoveHandler) {
      gcfMap.off('moveend', _gcfSepCirclesMoveHandler);
      _gcfSepCirclesMoveHandler = null;
    }
    if (_gcfSepCirclesTimer) {
      clearTimeout(_gcfSepCirclesTimer);
      _gcfSepCirclesTimer = null;
    }
  }
}

function gcfRefreshSepCircles() {
  if (_gcfSepCirclesEnabled) _gcfAddSepCircleLayers();
}


// ═══════════════════════════════════════════════════════════════════════════
// 2. Original Waypoint Display (for caches with corrected coordinates)
//
// Primary markers ALWAYS use corrected coords when available (swapped in
// cache-map.js on fetch).  This toggle shows the original listing coords
// as a hollow "waypoint" marker with a connecting line — similar to how
// child waypoints are displayed.
// ═══════════════════════════════════════════════════════════════════════════

function _gcfBuildOrigWpGeoJSON() {
  if (!_gcfMarkersData) return { points: null, lines: null };

  var pointFeatures = [];
  var lineFeatures = [];

  for (var i = 0; i < _gcfMarkersData.length; i++) {
    var m = _gcfMarkersData[i];
    if (_gcfHiddenTypes[m.t]) continue;
    // ola/olo are only set when corrected coords were promoted to la/lo
    if (m.ola == null || m.olo == null) continue;

    var color = _gcfMarkerColor(m);

    // Hollow marker at original position
    pointFeatures.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [m.olo, m.ola] },
      properties: {
        code: m.c,
        color: color
      }
    });

    // Connecting line from original to corrected (primary) position
    lineFeatures.push({
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[m.olo, m.ola], [m.lo, m.la]]
      },
      properties: { color: color }
    });
  }

  return {
    points: { type: 'FeatureCollection', features: pointFeatures },
    lines: { type: 'FeatureCollection', features: lineFeatures }
  };
}

function _gcfAddOrigWpLayers() {
  if (!gcfMap) return;
  var data = _gcfBuildOrigWpGeoJSON();
  if (!data.points) return;

  // If sources already exist, just update data
  if (gcfMap.getSource('gcf-orig-wp')) {
    gcfMap.getSource('gcf-orig-wp').setData(data.points);
    gcfMap.getSource('gcf-orig-wp-lines').setData(data.lines);
    return;
  }

  // Connecting lines
  gcfMap.addSource('gcf-orig-wp-lines', { type: 'geojson', data: data.lines });
  var _before = gcfMap.getLayer('gcf-unclustered') ? 'gcf-unclustered' : undefined;
  gcfMap.addLayer({
    id: 'gcf-orig-wp-lines',
    type: 'line',
    source: 'gcf-orig-wp-lines',
    paint: {
      'line-color': ['get', 'color'],
      'line-width': 1.5,
      'line-opacity': 0.5,
      'line-dasharray': [4, 3]
    }
  }, _before);

  // Original position hollow markers
  gcfMap.addSource('gcf-orig-wp', { type: 'geojson', data: data.points });
  gcfMap.addLayer({
    id: 'gcf-orig-wp-circles',
    type: 'circle',
    source: 'gcf-orig-wp',
    paint: {
      'circle-radius': 7,
      'circle-color': 'transparent',
      'circle-stroke-width': 1.5,
      'circle-stroke-color': ['get', 'color']
    }
  });
}

function _gcfRemoveOrigWpLayers() {
  if (!gcfMap) return;
  var layers = ['gcf-orig-wp-circles', 'gcf-orig-wp-lines'];
  var sources = ['gcf-orig-wp', 'gcf-orig-wp-lines'];
  for (var i = 0; i < layers.length; i++) {
    if (gcfMap.getLayer(layers[i])) gcfMap.removeLayer(layers[i]);
  }
  for (var j = 0; j < sources.length; j++) {
    if (gcfMap.getSource(sources[j])) gcfMap.removeSource(sources[j]);
  }
}

function gcfToggleCorrectedCoords(enabled) {
  _gcfCorrectedEnabled = !!enabled;
  _gcfSaveLayerState();

  if (_gcfCorrectedEnabled) {
    _gcfAddOrigWpLayers();
  } else {
    _gcfRemoveOrigWpLayers();
  }
}

function gcfRefreshOrigWaypoints() {
  if (_gcfCorrectedEnabled) _gcfAddOrigWpLayers();
}


// ═══════════════════════════════════════════════════════════════════════════
// 3. Child Waypoints
// ═══════════════════════════════════════════════════════════════════════════

var _GCF_WP_COLORS = {
  P: '#0d6efd',   // Parking
  S: '#fd7e14',   // Stage
  F: '#198754',   // Final
  Q: '#6f42c1',   // Question
  T: '#795548',   // Trailhead
  R: '#6c757d',   // Reference
  O: '#6c757d'    // Other
};

var _GCF_WP_TYPE_NAMES = {
  P: 'Parking',
  S: 'Stage',
  F: 'Final',
  Q: 'Question',
  T: 'Trailhead',
  R: 'Reference',
  O: 'Other'
};

function _gcfVisibleCacheCodes() {
  if (!gcfMap || !_gcfMarkersData) return [];

  var bounds = gcfMap.getBounds();
  var sw = bounds.getSouthWest();
  var ne = bounds.getNorthEast();
  var codes = [];

  for (var i = 0; i < _gcfMarkersData.length; i++) {
    var m = _gcfMarkersData[i];
    if (_gcfHiddenTypes[m.t]) continue;
    if (m.la >= sw.lat && m.la <= ne.lat && m.lo >= sw.lng && m.lo <= ne.lng) {
      codes.push(m.c);
    }
  }
  return codes;
}

function _gcfFetchAndRenderWaypoints() {
  if (!gcfMap || !_gcfWaypointsEnabled) return;

  // Only show waypoints above the configurable lines zoom threshold
  if (gcfMap.getZoom() <= GCF_LINES_MIN_ZOOM) {
    _gcfRenderWaypointLayers([]);
    return;
  }

  var codes = _gcfVisibleCacheCodes();
  if (codes.length === 0) {
    _gcfRenderWaypointLayers([]);
    return;
  }

  // Split into cached and uncached
  var uncached = [];
  var allWaypoints = [];

  for (var i = 0; i < codes.length; i++) {
    var code = codes[i];
    if (_gcfWaypointCache[code] !== undefined) {
      var cached = _gcfWaypointCache[code];
      for (var j = 0; j < cached.length; j++) {
        allWaypoints.push({ cache: code, wp: cached[j] });
      }
    } else {
      uncached.push(code);
    }
  }

  if (uncached.length === 0) {
    _gcfRenderWaypointLayers(allWaypoints);
    return;
  }

  // Fetch uncached waypoints (batch in groups of 50 to avoid URL length issues)
  var batchSize = 50;
  var pending = 0;

  function processBatch(batch) {
    pending++;
    fetch('/map/waypoints/?codes=' + batch.join(','))
      .then(function(r) {
        if (!r.ok) throw new Error('Server returned ' + r.status);
        return r.json();
      })
      .then(function(data) {
        var fetched = data.waypoints || [];
        // Index fetched codes so we can mark empty ones as cached too
        var fetchedCodes = {};
        for (var k = 0; k < fetched.length; k++) {
          var entry = fetched[k];
          fetchedCodes[entry.code] = true;
          _gcfWaypointCache[entry.code] = entry.wp || [];
          var wps = entry.wp || [];
          for (var w = 0; w < wps.length; w++) {
            allWaypoints.push({ cache: entry.code, wp: wps[w] });
          }
        }
        // Mark codes with no waypoints so we don't re-fetch
        for (var b = 0; b < batch.length; b++) {
          if (!fetchedCodes[batch[b]]) {
            _gcfWaypointCache[batch[b]] = [];
          }
        }
      })
      .catch(function(err) {
        console.error('GCForge map: failed to fetch waypoints', err);
      })
      .finally(function() {
        pending--;
        if (pending === 0 && _gcfWaypointsEnabled) {
          _gcfRenderWaypointLayers(allWaypoints);
        }
      });
  }

  for (var b = 0; b < uncached.length; b += batchSize) {
    processBatch(uncached.slice(b, b + batchSize));
  }

  // Render what we have from cache immediately
  if (allWaypoints.length > 0) {
    _gcfRenderWaypointLayers(allWaypoints);
  }
}

function _gcfBuildWaypointGeoJSON(waypointList) {
  var pointFeatures = [];
  var lineFeatures = [];

  // Build a lookup of parent cache coordinates from _gcfMarkersData
  var cacheCoords = {};
  if (_gcfMarkersData) {
    for (var c = 0; c < _gcfMarkersData.length; c++) {
      var mk = _gcfMarkersData[c];
      cacheCoords[mk.c] = [mk.lo, mk.la];
    }
  }

  for (var i = 0; i < waypointList.length; i++) {
    var item = waypointList[i];
    var wp = item.wp;
    var color = _GCF_WP_COLORS[wp.t] || '#6c757d';

    pointFeatures.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [wp.lo, wp.la] },
      properties: {
        cacheCode: item.cache,
        wpType: wp.t,
        wpName: wp.n || '',
        wpLat: wp.la,
        wpLon: wp.lo,
        color: color,
        typeName: _GCF_WP_TYPE_NAMES[wp.t] || 'Other',
        letter: wp.t || '?'
      }
    });

    // Connecting line from parent cache to waypoint
    var parentCoords = cacheCoords[item.cache];
    if (parentCoords) {
      lineFeatures.push({
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: [parentCoords, [wp.lo, wp.la]]
        },
        properties: { color: color }
      });
    }
  }

  return {
    points: { type: 'FeatureCollection', features: pointFeatures },
    lines: { type: 'FeatureCollection', features: lineFeatures }
  };
}

function _gcfRenderWaypointLayers(waypointList) {
  if (!gcfMap) return;
  var data = _gcfBuildWaypointGeoJSON(waypointList);

  if (gcfMap.getSource('gcf-waypoints')) {
    gcfMap.getSource('gcf-waypoints').setData(data.points);
    gcfMap.getSource('gcf-waypoint-lines').setData(data.lines);
    return;
  }

  gcfMap.addSource('gcf-waypoint-lines', { type: 'geojson', data: data.lines });
  var _before = gcfMap.getLayer('gcf-unclustered') ? 'gcf-unclustered' : undefined;
  gcfMap.addLayer({
    id: 'gcf-waypoint-lines',
    type: 'line',
    source: 'gcf-waypoint-lines',
    paint: {
      'line-color': ['get', 'color'],
      'line-width': 1.5,
      'line-opacity': 0.5,
      'line-dasharray': [4, 3]
    }
  }, _before);

  gcfMap.addSource('gcf-waypoints', { type: 'geojson', data: data.points });

  var useIcons = (typeof _gcfMapPrefs !== 'undefined' && _gcfMapPrefs.icon_set === 'cgeo'
                  && typeof _gcfIconsLoaded !== 'undefined' && _gcfIconsLoaded);

  if (useIcons) {
    if (typeof gcfPrepareWpIcons === 'function') gcfPrepareWpIcons(gcfMap);
    gcfMap.addLayer({
      id: 'gcf-waypoints-circles',
      type: 'symbol',
      source: 'gcf-waypoints',
      layout: {
        'icon-image': ['concat', 'wp-', ['get', 'letter']],
        'icon-size': 0.75,
        'icon-allow-overlap': true
      }
    });
    gcfMap.addLayer({
      id: 'gcf-waypoints-labels',
      type: 'symbol',
      source: 'gcf-waypoints',
      filter: ['boolean', false],
      layout: { 'text-field': '' }
    });
  } else {
    gcfMap.addLayer({
      id: 'gcf-waypoints-circles',
      type: 'circle',
      source: 'gcf-waypoints',
      paint: {
        'circle-color': ['get', 'color'],
        'circle-radius': 6,
        'circle-stroke-width': 1,
        'circle-stroke-color': '#fff'
      }
    });
    gcfMap.addLayer({
      id: 'gcf-waypoints-labels',
      type: 'symbol',
      source: 'gcf-waypoints',
      layout: {
        'text-field': ['get', 'letter'],
        'text-font': ['Open Sans Bold'],
        'text-size': 8,
        'text-allow-overlap': true
      },
      paint: {
        'text-color': '#fff'
      }
    });
  }

  // Click handler for waypoint popups
  gcfMap.on('click', 'gcf-waypoints-circles', function(e) {
    var f = e.features[0];
    var p = f.properties;
    var coords = f.geometry.coordinates.slice();
    var html = '<div style="min-width:150px;font-size:0.8rem">' +
      '<strong>' + _gcfEsc(p.typeName) + '</strong><br>' +
      _gcfEsc(p.wpName) + '<br>' +
      '<span class="font-monospace">' + Number(p.wpLat).toFixed(5) + ', ' + Number(p.wpLon).toFixed(5) + '</span><br>' +
      '<span class="text-muted">' + _gcfEsc(p.cacheCode) + '</span>' +
      '</div>';
    new maplibregl.Popup({ offset: 8, maxWidth: '250px' })
      .setLngLat(coords)
      .setHTML(html)
      .addTo(gcfMap);
  });

  gcfMap.on('mouseenter', 'gcf-waypoints-circles', function() {
    gcfMap.getCanvas().style.cursor = 'pointer';
  });
  gcfMap.on('mouseleave', 'gcf-waypoints-circles', function() {
    gcfMap.getCanvas().style.cursor = '';
  });
}

function _gcfRemoveWaypointLayers() {
  if (!gcfMap) return;
  if (gcfMap.getLayer('gcf-waypoints-labels')) gcfMap.removeLayer('gcf-waypoints-labels');
  if (gcfMap.getLayer('gcf-waypoints-circles')) gcfMap.removeLayer('gcf-waypoints-circles');
  if (gcfMap.getSource('gcf-waypoints')) gcfMap.removeSource('gcf-waypoints');
  if (gcfMap.getLayer('gcf-waypoint-lines')) gcfMap.removeLayer('gcf-waypoint-lines');
  if (gcfMap.getSource('gcf-waypoint-lines')) gcfMap.removeSource('gcf-waypoint-lines');
}

function _gcfWaypointsMoveEnd() {
  if (_gcfWaypointsTimer) clearTimeout(_gcfWaypointsTimer);
  _gcfWaypointsTimer = setTimeout(function() {
    if (_gcfWaypointsEnabled) _gcfFetchAndRenderWaypoints();
  }, 500);
}

function gcfToggleWaypoints(enabled) {
  _gcfWaypointsEnabled = !!enabled;
  _gcfSaveLayerState();

  if (_gcfWaypointsEnabled) {
    _gcfFetchAndRenderWaypoints();
    if (!_gcfWaypointsMoveHandler) {
      _gcfWaypointsMoveHandler = _gcfWaypointsMoveEnd;
      gcfMap.on('moveend', _gcfWaypointsMoveHandler);
    }
  } else {
    _gcfRemoveWaypointLayers();
    if (_gcfWaypointsMoveHandler) {
      gcfMap.off('moveend', _gcfWaypointsMoveHandler);
      _gcfWaypointsMoveHandler = null;
    }
    if (_gcfWaypointsTimer) {
      clearTimeout(_gcfWaypointsTimer);
      _gcfWaypointsTimer = null;
    }
  }
}

function gcfRefreshWaypoints() {
  // Clear cache so we re-fetch with new marker data
  _gcfWaypointCache = {};
  if (_gcfWaypointsEnabled) _gcfFetchAndRenderWaypoints();
}


// ═══════════════════════════════════════════════════════════════════════════
// 4. Adventure Lab Stage Lines (connect stages to parent)
// ═══════════════════════════════════════════════════════════════════════════

function _gcfBuildALStageLineGeoJSON() {
  if (!_gcfMarkersData) return { type: 'FeatureCollection', features: [] };

  // Build lookup: adventure_id → parent marker coords (parent has aid but no sn)
  var parentCoords = {};
  for (var i = 0; i < _gcfMarkersData.length; i++) {
    var m = _gcfMarkersData[i];
    if (m.aid != null && m.sn == null) {
      parentCoords[m.aid] = [m.lo, m.la];
    }
  }

  var features = [];
  for (var j = 0; j < _gcfMarkersData.length; j++) {
    var s = _gcfMarkersData[j];
    if (_gcfHiddenTypes[s.t]) continue;
    if (s.aid == null || s.sn == null) continue;
    var parent = parentCoords[s.aid];
    if (!parent) continue;

    features.push({
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [parent, [s.lo, s.la]]
      },
      properties: {}
    });
  }

  return { type: 'FeatureCollection', features: features };
}

function _gcfAddALStageLayers() {
  if (!gcfMap) return;

  // Hide AL stage lines below the lines zoom threshold
  if (gcfMap.getZoom() <= GCF_LINES_MIN_ZOOM) {
    // Clear existing data if source exists
    if (gcfMap.getSource('gcf-al-stage-lines')) {
      gcfMap.getSource('gcf-al-stage-lines').setData({ type: 'FeatureCollection', features: [] });
    }
    return;
  }

  var geojson = _gcfBuildALStageLineGeoJSON();

  if (gcfMap.getSource('gcf-al-stage-lines')) {
    gcfMap.getSource('gcf-al-stage-lines').setData(geojson);
    return;
  }

  gcfMap.addSource('gcf-al-stage-lines', { type: 'geojson', data: geojson });
  var _before = gcfMap.getLayer('gcf-unclustered') ? 'gcf-unclustered' : undefined;
  gcfMap.addLayer({
    id: 'gcf-al-stage-lines',
    type: 'line',
    source: 'gcf-al-stage-lines',
    paint: {
      'line-color': '#20c997',
      'line-width': 1.5,
      'line-opacity': 0.5,
      'line-dasharray': [4, 3]
    }
  }, _before);
}

function _gcfRemoveALStageLayers() {
  if (!gcfMap) return;
  if (gcfMap.getLayer('gcf-al-stage-lines')) gcfMap.removeLayer('gcf-al-stage-lines');
  if (gcfMap.getSource('gcf-al-stage-lines')) gcfMap.removeSource('gcf-al-stage-lines');
}

function gcfRefreshALStageLines() {
  _gcfAddALStageLayers();
}


// ═══════════════════════════════════════════════════════════════════════════
// 5. Text Labels (Name / Code)
// ═══════════════════════════════════════════════════════════════════════════

function _gcfAddLabelLayer(mode) {
  if (!gcfMap) return;
  var field = mode === 'name' ? 'name' : 'code';

  // If layer exists, remove and re-add with new field
  if (gcfMap.getLayer('gcf-text-labels')) gcfMap.removeLayer('gcf-text-labels');

  // Requires the gcf-markers source to exist
  if (!gcfMap.getSource('gcf-markers')) return;

  gcfMap.addLayer({
    id: 'gcf-text-labels',
    type: 'symbol',
    source: 'gcf-markers',
    filter: ['!', ['has', 'point_count']],
    layout: {
      'text-field': ['get', field],
      'text-font': ['Open Sans Regular'],
      'text-size': 11,
      'text-offset': [0, 1.5],
      'text-allow-overlap': false
    },
    paint: {
      'text-color': '#333',
      'text-halo-color': '#fff',
      'text-halo-width': 1.5
    }
  });
}

function _gcfRemoveLabelLayer() {
  if (!gcfMap) return;
  if (gcfMap.getLayer('gcf-text-labels')) gcfMap.removeLayer('gcf-text-labels');
}

function gcfToggleLabels(mode) {
  // mode: 'name', 'code', or null (off)
  _gcfLabelsMode = mode || null;
  _gcfSaveLayerState();

  if (_gcfLabelsMode) {
    _gcfAddLabelLayer(_gcfLabelsMode);
  } else {
    _gcfRemoveLabelLayer();
  }
}

function gcfRefreshLabels() {
  if (_gcfLabelsMode) _gcfAddLabelLayer(_gcfLabelsMode);
}


// ═══════════════════════════════════════════════════════════════════════════
// Style change restoration
// ═══════════════════════════════════════════════════════════════════════════

function gcfRestoreLayers() {
  if (_gcfSepCirclesEnabled) {
    _gcfAddSepCircleLayers();
  }
  if (_gcfCorrectedEnabled) {
    _gcfAddOrigWpLayers();
  }
  if (_gcfWaypointsEnabled) {
    _gcfFetchAndRenderWaypoints();
  }
  _gcfAddALStageLayers();
  if (_gcfLabelsMode) {
    _gcfAddLabelLayer(_gcfLabelsMode);
  }
}
