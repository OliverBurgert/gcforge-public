// ── GCForge Map — MapLibre initialization, markers, popups, clustering ──────
//
// Lazy-loaded when the layout switches to split or map mode.
// Depends on MapLibre GL JS being loaded first (CDN).

var gcfMap = null;
var _gcfMapInitialized = false;
var _gcfMarkersData = null;
var _gcfSaveTimer = null;
var _gcfLastMarkerParams = null;  // tracks last-fetched filter state

// Clustering config — adjustable via LOD sliders
var GCF_CLUSTER_MAX_ZOOM = 14;
var GCF_LINES_MIN_ZOOM = 13;

// ── Tile styles ──────────────────────────────────────────────────────────────
// GCF_STYLES is defined in map-styles.js (loaded before this file).

// ── Marker color logic ───────────────────────────────────────────────────────

function _gcfMarkerColor(m) {
  if (m.s === 'X') return '#6c757d';    // archived: grey
  if (m.s === 'D') return '#adb5bd';    // disabled: light grey
  if (m.m) return '#ffc107';            // mine: yellow
  if (m.f) return '#198754';            // found: green
  return '#0d6efd';                     // unfound: blue
}

function _gcfMarkerBorderColor(m) {
  if (m.s === 'X') return '#495057';
  if (m.s === 'D') return '#6c757d';
  return '#000';
}

// ── Generate circle icon images ──────────────────────────────────────────────

function _gcfCreateCircleIcon(map, id, fillColor, borderColor, letter) {
  var size = 28;
  var canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  var ctx = canvas.getContext('2d');

  // Archived striped pattern
  var isArchived = (id.indexOf('archived') !== -1);

  // Circle fill
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size / 2 - 2, 0, Math.PI * 2);
  ctx.fillStyle = fillColor;
  ctx.fill();

  if (isArchived) {
    // Red diagonal stripes over the circle
    ctx.save();
    ctx.clip();
    ctx.strokeStyle = 'rgba(220, 53, 69, 0.6)';
    ctx.lineWidth = 2;
    for (var i = -size; i < size * 2; i += 6) {
      ctx.beginPath();
      ctx.moveTo(i, 0);
      ctx.lineTo(i + size, size);
      ctx.stroke();
    }
    ctx.restore();
  }

  // Border
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size / 2 - 2, 0, Math.PI * 2);
  ctx.strokeStyle = borderColor;
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // Letter
  if (letter) {
    ctx.fillStyle = '#fff';
    ctx.font = 'bold ' + (letter.length > 1 ? '9' : '11') + 'px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(letter, size / 2, size / 2 + 0.5);
  }

  return { width: size, height: size, data: ctx.getImageData(0, 0, size, size).data };
}


// ── Initialize map ───────────────────────────────────────────────────────────

function gcfMapInit() {
  if (_gcfMapInitialized) return;
  _gcfMapInitialized = true;

  // Restore saved state
  var savedLat = null, savedLon = null, savedZoom = null;
  try {
    var raw = localStorage.getItem('gcforge_map_state');
    if (raw) {
      var parsed = JSON.parse(raw);
      savedLat = parsed.lat;
      savedLon = parsed.lon;
      savedZoom = parsed.zoom;
    }
  } catch(e) {}

  // Restore saved style — localStorage overrides server default
  var serverStyle = (typeof _gcfMapPrefs !== 'undefined' && _gcfMapPrefs.style) || 'outdoor';
  var savedStyle = localStorage.getItem('gcforge_map_style') || serverStyle;
  // 'offline' style is built dynamically after areas are fetched; start with street
  var initStyle = GCF_STYLES[savedStyle] || GCF_STYLES.street;
  // Check the correct radio button
  var radio = document.querySelector('input[name="map-style"][value="' + savedStyle + '"]');
  if (radio) radio.checked = true;

  gcfMap = new maplibregl.Map({
    container: 'map',
    style: initStyle,
    center: [savedLon || 10.0, savedLat || 48.5],
    zoom: savedZoom || 6,
    attributionControl: true,
    transformRequest: gcfMapTransformRequest
  });

  gcfMap.addControl(new maplibregl.NavigationControl(), 'top-left');
  if (typeof gcfSuppressMissingImages === 'function') gcfSuppressMissingImages(gcfMap);

  var _scaleUnit = (typeof _gcfMapPrefs !== 'undefined' && _gcfMapPrefs.distance_unit === 'mi')
    ? 'imperial' : 'metric';

  var infoEl = document.createElement('div');
  infoEl.className = 'gcf-map-info';
  var scaleEl = document.createElement('span');
  scaleEl.className = 'gcf-map-info-scale';
  var zoomEl = document.createElement('span');
  zoomEl.className = 'gcf-map-info-zoom';
  infoEl.appendChild(scaleEl);
  infoEl.appendChild(zoomEl);
  document.getElementById('map-container').appendChild(infoEl);

  function _gcfUpdateMapInfo() {
    try {
      var maxPx = 120;
      var canvas = gcfMap.getCanvas();
      var h = canvas.clientHeight / 2;
      var w = canvas.clientWidth / 2;
      if (!w || !h) { zoomEl.textContent = interpolate(gettext('Z %s'), [gcfMap.getZoom().toFixed(1)]); return; }
      var leftPt = gcfMap.unproject([w - maxPx / 2, h]);
      var rightPt = gcfMap.unproject([w + maxPx / 2, h]);
      var meters = leftPt.distanceTo(rightPt);
      var value, unit;
      if (_scaleUnit === 'imperial') {
        var feet = meters * 3.2808;
        if (feet > 5280) { value = feet / 5280; unit = 'mi'; }
        else { value = feet; unit = 'ft'; }
      } else if (meters >= 1000) {
        value = meters / 1000; unit = 'km';
      } else {
        value = meters; unit = 'm';
      }
      var pow = Math.pow(10, String(Math.floor(value)).length - 1);
      var r = value / pow;
      r = r >= 10 ? 10 : r >= 5 ? 5 : r >= 3 ? 3 : r >= 2 ? 2 : 1;
      var nice = pow * r;
      var px = Math.round(maxPx * (nice / value));
      scaleEl.textContent = nice + ' ' + unit;
      scaleEl.style.width = px + 'px';
      zoomEl.textContent = interpolate(gettext('Z %s'), [gcfMap.getZoom().toFixed(1)]);
    } catch (e) { console.warn('map-info update failed', e); }
  }
  _gcfUpdateMapInfo();
  gcfMap.on('move', _gcfUpdateMapInfo);
  gcfMap.on('load', _gcfUpdateMapInfo);

  gcfMap.on('load', function() {
    function _gcfOnMapReady() {
      _gcfFetchMarkers();
      _gcfBuildLocationsDropdown();
      _gcfApplyDefaultBoundaries();
      if (typeof _gcfApplyRefRadius === 'function') _gcfApplyRefRadius();
      if (typeof gcfLayersInit === 'function') gcfLayersInit();
      _gcfRestoreLodState();
    }

    // If icon mode, load SVG icons first, then fetch markers
    if (typeof _gcfMapPrefs !== 'undefined' && _gcfMapPrefs.icon_set === 'cgeo'
        && typeof gcfLoadMapIcons === 'function') {
      gcfLoadMapIcons('/static/icons/cgeo/types/', _gcfOnMapReady);
    } else {
      _gcfOnMapReady();
    }

    if (typeof gcfOfflineLoadAreas === 'function') {
      gcfOfflineLoadAreas(gcfMap);
    }
  });

  // Save state on move/zoom (debounced)
  gcfMap.on('moveend', _gcfDebounceSaveState);
  gcfMap.on('zoomend', _gcfDebounceSaveState);

  // Notify scope picker so it can refresh vbox when target=viewport
  gcfMap.on('moveend', function () {
    if (typeof gcfOnMapMoveForScope === 'function') gcfOnMapMoveForScope();
  });
}


// ── Save map state (debounced) ───────────────────────────────────────────────

function _gcfDebounceSaveState() {
  if (_gcfSaveTimer) clearTimeout(_gcfSaveTimer);
  _gcfSaveTimer = setTimeout(function() {
    var center = gcfMap.getCenter();
    var zoom = gcfMap.getZoom();
    var state = { lat: center.lat, lon: center.lng, zoom: zoom };
    localStorage.setItem('gcforge_map_state', JSON.stringify(state));

    // Also persist to server
    var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
    if (csrfToken) {
      var body = new URLSearchParams();
      body.set('map_center_lat', center.lat.toFixed(6));
      body.set('map_center_lon', center.lng.toFixed(6));
      body.set('map_zoom', zoom.toFixed(1));
      fetch('/settings/save-map-state/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-CSRFToken': csrfToken.value
        },
        body: body.toString()
      }).catch(function() {});
    }
  }, 2000);
}


// ── Fetch markers from server ────────────────────────────────────────────────

function _gcfSetMapLoading(on) {
  var el = document.getElementById('map-loading-overlay');
  if (el) el.classList.toggle('active', on);
}

// Params that don't affect which markers are shown — skip map refresh when
// only these change.
var _GCF_MAP_IGNORE_PARAMS = ['sort', 'order', 'page'];

// Build the filter param set for a markers fetch.
//
// `searchOverride` is the query string of the just-completed list request
// (from htmx:afterSwap's xhr.responseURL).  HTMX pushes the new URL during the
// later *settle* phase, so at afterSwap time window.location.search is still the
// pre-filter URL — reading it (or the hidden form inputs, which only update on a
// full render) would miss the filter that was just applied, and the map would
// show the previous, broader result.  When an override is given it is the
// authoritative applied-filter state: the URL wins and a stale hidden form input
// (e.g. a previous-render `fx`) must never clobber a param the URL carries.
function _gcfBuildFilterParams(searchOverride) {
  var authoritative = searchOverride != null;
  var p = new URLSearchParams(authoritative ? searchOverride : window.location.search);
  var filterForm = document.getElementById('filter-form');
  if (filterForm) {
    var formParams = new URLSearchParams(new FormData(filterForm));
    formParams.forEach(function(val, key) {
      if (authoritative) {
        // Only pull in form-only params the authoritative URL lacks.
        if (val !== '' && !p.has(key)) p.set(key, val);
      } else if (val !== '' || !p.get(key)) {
        // No override (initial load / popstate): URL and form agree; the
        // non-empty-or-absent rule guards saved-filter values a <select>
        // can't represent (e.g. flag=ftf_possible).
        p.set(key, val);
      }
    });
  }
  return p;
}

// Promote a marker's corrected coordinates (cla/clo) to its primary position
// (la/lo), stashing the originals in ola/olo. In-place; markers without
// corrected coords are left untouched. Downstream code (clustering, 161m
// circles, draw selection, distance) then transparently uses corrected coords,
// and "is corrected" checks read m.ola != null.
function _gcfPromoteCorrectedCoords(m) {
  if (m && m.cla != null && m.clo != null) {
    m.ola = m.la;   // preserve original lat
    m.olo = m.lo;   // preserve original lon
    m.la = m.cla;   // corrected becomes primary
    m.lo = m.clo;
  }
  return m;
}

function _gcfFetchMarkers(searchOverride) {
  var fetchParams = _gcfBuildFilterParams(searchOverride);
  // Map doesn't paginate — remove page param
  fetchParams.delete('page');
  var params = fetchParams.toString();

  _gcfSetMapLoading(true);

  // Abort after 30 seconds so the spinner never hangs forever
  var controller = new AbortController();
  var timeout = setTimeout(function() { controller.abort(); }, 30000);

  fetch('/map/markers/?' + params, { signal: controller.signal })
    .then(function(r) {
      if (!r.ok) throw new Error('Server returned ' + r.status);
      return r.json();
    })
    .then(function(data) {
      // Promote corrected coordinates to primary position so all
      // downstream code (clustering, 161m circles, draw selection,
      // distance, etc.) automatically uses corrected coords.
      for (var i = 0; i < data.markers.length; i++) {
        _gcfPromoteCorrectedCoords(data.markers[i]);
      }
      _gcfMarkersData = data.markers;
      _gcfRenderMarkers(data.markers);
      // Record which filter state produced these markers
      _gcfLastMarkerParams = _gcfFilterParams(searchOverride);
      // Re-apply local type filter if any types are hidden
      if (Object.keys(_gcfHiddenTypes).length > 0) _gcfApplyTypeFilter();
      // Update draw status count (shapes may have been restored before markers loaded)
      if (typeof _gcfUpdateDrawStatus === 'function') _gcfUpdateDrawStatus();
      // Refresh optional display layers after new marker data
      if (typeof gcfRefreshWaypoints === 'function') gcfRefreshWaypoints();
      if (typeof gcfRefreshSepCircles === 'function') gcfRefreshSepCircles();
      if (typeof gcfRefreshAlcCircles === 'function') gcfRefreshAlcCircles();
      if (typeof gcfRefreshOrigWaypoints === 'function') gcfRefreshOrigWaypoints();
      if (typeof gcfRefreshALStageLines === 'function') gcfRefreshALStageLines();
      if (typeof gcfRefreshLabels === 'function') gcfRefreshLabels();
    })
    .catch(function(err) {
      if (err.name === 'AbortError') {
        console.warn('GCForge map: marker fetch timed out (30s)');
      } else {
        console.error('GCForge map: failed to fetch markers', err);
      }
    })
    .finally(function() {
      clearTimeout(timeout);
      _gcfSetMapLoading(false);
    });
}


// ── Render markers as GeoJSON source + layers ────────────────────────────────

var _gcfMarkerLayerMode = null;       // 'icon' | 'text' of the currently-built layers
var _gcfMarkerHandlersBound = false;  // marker/cluster click + cursor handlers bound once

function _gcfRenderMarkers(markers, opts) {
  if (!gcfMap) return;
  opts = opts || {};

  // Determine icon mode
  var useIcons = (typeof _gcfMapPrefs !== 'undefined' && _gcfMapPrefs.icon_set === 'cgeo'
                  && typeof _gcfIconsLoaded !== 'undefined' && _gcfIconsLoaded);
  var mode = useIcons ? 'icon' : 'text';

  // Build GeoJSON
  var features = markers.map(function(m) {
    var plat = (m.oc && !m.gc) ? 'oc' : 'gc';
    var sk = (typeof _gcfStatusKey === 'function') ? _gcfStatusKey(m) : 'U';
    return {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [m.lo, m.la] },
      properties: {
        code: m.c,
        name: m.n,
        cacheType: m.t,
        size: m.sz,
        difficulty: m.d,
        terrain: m.tr,
        found: m.f,
        status: m.s,
        isMine: m.m,
        gcCode: m.gc || null,
        ocCode: m.oc || null,
        platform: plat,
        statusKey: sk,
        dnf: (m.dnf && !m.f) ? '-dnf' : '',
        corrected: m.ola != null ? '-c' : '',
        color: _gcfMarkerColor(m),
        borderColor: _gcfMarkerBorderColor(m),
        origLat: m.ola || null,
        origLon: m.olo || null
      }
    };
  });

  var geojson = { type: 'FeatureCollection', features: features };

  // Fast path: when the source + layers already exist in the same render mode,
  // update the data in place instead of tearing everything down and rebuilding
  // it. Eliminates the visible marker flash on every filter change and avoids
  // re-running the layer + event-handler setup. opts.rebuild forces the full
  // path (e.g. an LOD cluster-zoom change, which is baked into the source at
  // creation time and so can't be applied via setData).
  if (!opts.rebuild && _gcfMarkerLayerMode === mode && gcfMap.getSource('gcf-markers')) {
    if (useIcons) {
      try { gcfPrepareMapIcons(gcfMap, markers); } catch (e) { /* keep existing icons */ }
    }
    gcfMap.getSource('gcf-markers').setData(geojson);
    return;
  }

  // Full (re)build — remove ALL layers referencing gcf-markers before the source.
  ['gcf-clusters', 'gcf-cluster-count', 'gcf-unclustered', 'gcf-marker-labels', 'gcf-text-labels'].forEach(function(id) {
    if (gcfMap.getLayer(id)) gcfMap.removeLayer(id);
  });
  if (gcfMap.getSource('gcf-markers')) gcfMap.removeSource('gcf-markers');

  gcfMap.addSource('gcf-markers', {
    type: 'geojson',
    data: geojson,
    cluster: true,
    clusterMaxZoom: GCF_CLUSTER_MAX_ZOOM,
    clusterRadius: 50
  });

  // Cluster circles
  gcfMap.addLayer({
    id: 'gcf-clusters',
    type: 'circle',
    source: 'gcf-markers',
    filter: ['has', 'point_count'],
    paint: {
      'circle-color': [
        'step', ['get', 'point_count'],
        '#51bbd6', 50,
        '#f1f075', 200,
        '#f28cb1'
      ],
      'circle-radius': [
        'step', ['get', 'point_count'],
        18, 50, 24, 200, 30
      ],
      'circle-stroke-width': 1,
      'circle-stroke-color': '#fff'
    }
  });

  // Cluster count labels
  gcfMap.addLayer({
    id: 'gcf-cluster-count',
    type: 'symbol',
    source: 'gcf-markers',
    filter: ['has', 'point_count'],
    layout: {
      'text-field': '{point_count_abbreviated}',
      'text-font': ['Open Sans Bold'],
      'text-size': 12
    }
  });

  if (useIcons) {
    // Pre-generate icon images for all type/platform/status combos in the data.
    // Fall back to text mode if icon generation fails (e.g. canvas security error).
    try {
      gcfPrepareMapIcons(gcfMap, markers);
    } catch (iconErr) {
      console.warn('GCForge map: icon preparation failed, falling back to text mode:', iconErr);
      useIcons = false;
    }
  }

  if (useIcons) {
    // Individual markers as icon symbols
    gcfMap.addLayer({
      id: 'gcf-unclustered',
      type: 'symbol',
      source: 'gcf-markers',
      filter: ['!', ['has', 'point_count']],
      layout: {
        'icon-image': ['concat', 'i-', ['get', 'cacheType'], '-', ['get', 'platform'], '-', ['get', 'statusKey'], ['get', 'dnf'], ['get', 'corrected']],
        'icon-size': 1,
        'icon-allow-overlap': true
      }
    });

    // No separate marker-labels layer needed — icons are self-explanatory
    gcfMap.addLayer({
      id: 'gcf-marker-labels',
      type: 'symbol',
      source: 'gcf-markers',
      filter: ['boolean', false],
      layout: { 'text-field': '' }
    });
  } else {
    // Text mode: circles with type letter
    gcfMap.addLayer({
      id: 'gcf-unclustered',
      type: 'circle',
      source: 'gcf-markers',
      filter: ['!', ['has', 'point_count']],
      paint: {
        'circle-color': ['get', 'color'],
        'circle-radius': 10,
        'circle-stroke-width': 1.5,
        'circle-stroke-color': ['get', 'borderColor']
      }
    });

    // Type letter overlay on individual markers
    gcfMap.addLayer({
      id: 'gcf-marker-labels',
      type: 'symbol',
      source: 'gcf-markers',
      filter: ['!', ['has', 'point_count']],
      layout: {
        'text-field': ['get', 'cacheType'],
        'text-font': ['Open Sans Bold'],
        'text-size': 10,
        'text-allow-overlap': true
      },
      paint: {
        'text-color': '#fff'
      }
    });
  }

  _gcfMarkerLayerMode = mode;

  // Bind interaction handlers once. MapLibre's delegated (layer-scoped)
  // listeners survive setStyle and layer remove/re-add — they're keyed by the
  // layer id — so binding on every render would stack duplicate popups/handlers.
  if (!_gcfMarkerHandlersBound) {
    // Click cluster to zoom in
    gcfMap.on('click', 'gcf-clusters', function(e) {
      var features = gcfMap.queryRenderedFeatures(e.point, { layers: ['gcf-clusters'] });
      var clusterId = features[0].properties.cluster_id;
      gcfMap.getSource('gcf-markers').getClusterExpansionZoom(clusterId).then(function(zoom) {
        gcfMap.easeTo({ center: features[0].geometry.coordinates, zoom: zoom });
      });
    });

    // Click marker to show popup
    gcfMap.on('click', 'gcf-unclustered', function(e) {
      var f = e.features[0];
      var p = f.properties;
      _gcfShowPopup(f.geometry.coordinates.slice(), p);
    });

    // Cursor changes
    gcfMap.on('mouseenter', 'gcf-unclustered', function() { gcfMap.getCanvas().style.cursor = 'pointer'; });
    gcfMap.on('mouseleave', 'gcf-unclustered', function() { gcfMap.getCanvas().style.cursor = ''; });
    gcfMap.on('mouseenter', 'gcf-clusters', function() { gcfMap.getCanvas().style.cursor = 'pointer'; });
    gcfMap.on('mouseleave', 'gcf-clusters', function() { gcfMap.getCanvas().style.cursor = ''; });

    _gcfMarkerHandlersBound = true;
  }

  // Auto-fit bounds on first load (only if no saved state)
  if (markers.length > 0 && !localStorage.getItem('gcforge_map_state')) {
    var bounds = new maplibregl.LngLatBounds();
    markers.forEach(function(m) { bounds.extend([m.lo, m.la]); });
    gcfMap.fitBounds(bounds, { padding: 40, maxZoom: 14 });
  }
}


// ── Show popup ───────────────────────────────────────────────────────────────

function _gcfShowPopup(coords, props) {
  var statusLabels = {
    A: gettext('Active'),
    D: gettext('Disabled'),
    X: gettext('Archived'),
    U: gettext('Unpublished'),
    L: gettext('Locked')
  };
  var statusBadge = {
    A: 'bg-success', D: 'bg-warning text-dark', X: 'bg-secondary',
    U: 'bg-light text-dark', L: 'bg-dark'
  };

  var d = props.difficulty != null ? props.difficulty : '?';
  var t = props.terrain != null ? props.terrain : '?';

  var html = '<div style="min-width:200px;font-size:0.8rem">' +
    '<strong><a href="/' + props.code + '/" style="color:inherit">' +
    _gcfEsc(props.name) + '</a></strong><br>' +
    '<span class="font-monospace">' + _gcfEsc(props.code) + '</span>' +
    ' &middot; ' + _gcfEsc(props.cacheType) +
    ' &middot; D' + d + '/T' + t +
    ' &middot; ' + _gcfEsc(props.size) +
    '<br><span class="badge ' + (statusBadge[props.status] || 'bg-secondary') + '">' +
    (statusLabels[props.status] || props.status) + '</span>';

  if (props.found === true || props.found === 'true') {
    html += ' <span class="badge bg-success">' + gettext('Found') + '</span>';
  }
  if (props.isMine === true || props.isMine === 'true') {
    html += ' <span class="badge bg-warning text-dark">' + gettext('Mine') + '</span>';
  }

  html += '</div>';

  new maplibregl.Popup({ offset: 12, maxWidth: '300px' })
    .setLngLat(coords)
    .setHTML(html)
    .addTo(gcfMap);

  // Notify list panel for sync
  if (typeof gcfMapMarkerClicked === 'function') {
    gcfMapMarkerClicked(props.code);
  }
}

function _gcfEsc(s) {
  if (!s) return '';
  var div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}


// ── Style switcher ───────────────────────────────────────────────────────────

function gcfMapSetStyle(styleName) {
  if (!gcfMap) return;
  var style;
  if (styleName === 'offline') {
    if (typeof gcfBuildOfflineStyle !== 'function' || !_gcfOfflineAreas || !_gcfOfflineAreas.length) return;
    style = gcfBuildOfflineStyle();
  } else {
    style = GCF_STYLES[styleName] || GCF_STYLES.street;
  }
  gcfMap.setStyle(style);
  // Re-render markers, boundaries, radius circle and display layers after style change
  gcfMap.once('styledata', function() {
    // Clear cached icon images — new style has a fresh image store
    if (typeof gcfClearMapIcons === 'function') gcfClearMapIcons(gcfMap);
    if (_gcfMarkersData) _gcfRenderMarkers(_gcfMarkersData);
    _gcfRestoreBoundaries();
    if (typeof _gcfApplyRefRadius === 'function') _gcfApplyRefRadius();
    if (typeof gcfRestoreLayers === 'function') gcfRestoreLayers();
    // Offline tiles are part of the style when in offline mode — no overlay needed.
  });
  localStorage.setItem('gcforge_map_style', styleName);
  // Close the layer panel
  var ctrl = document.getElementById('map-layer-control');
  if (ctrl) ctrl.classList.remove('open');
}

// Layer control + locations control toggle (shared click handler)
document.addEventListener('click', function(e) {
  // Layer control
  var layerCtrl = document.getElementById('map-layer-control');
  var layerBtn = document.getElementById('map-layer-toggle');
  if (layerCtrl && layerBtn && layerBtn.contains(e.target)) {
    layerCtrl.classList.toggle('open');
    var locCtrl = document.getElementById('map-locations-control');
    if (locCtrl) locCtrl.classList.remove('open');
    e.stopPropagation();
    return;
  }
  if (layerCtrl && !layerCtrl.contains(e.target)) {
    layerCtrl.classList.remove('open');
  }

  // Locations control
  var locCtrl = document.getElementById('map-locations-control');
  var locBtn = document.getElementById('map-locations-toggle');
  if (locCtrl && locBtn && locBtn.contains(e.target)) {
    locCtrl.classList.toggle('open');
    if (layerCtrl) layerCtrl.classList.remove('open');
    e.stopPropagation();
    return;
  }
  if (locCtrl && !locCtrl.contains(e.target)) {
    locCtrl.classList.remove('open');
  }
});

// Populate locations dropdown on map init
function _gcfBuildLocationsDropdown() {
  var list = document.getElementById('map-locations-list');
  var ctrl = document.getElementById('map-locations-control');
  if (!list || typeof _gcfLocations === 'undefined' || !_gcfLocations.length) {
    if (ctrl) ctrl.style.display = 'none';
    return;
  }
  if (ctrl) ctrl.style.display = '';
  list.innerHTML = '';
  _gcfLocations.forEach(function(loc) {
    var btn = document.createElement('button');
    btn.className = 'map-location-item' + (loc.home ? ' map-location-home' : '');
    btn.textContent = (loc.home ? '\u2302 ' : '') + loc.name;
    btn.title = loc.lat.toFixed(5) + ', ' + loc.lon.toFixed(5);
    btn.onclick = function() {
      if (gcfMap) gcfMap.flyTo({ center: [loc.lon, loc.lat], zoom: 13 });
      document.getElementById('map-locations-control').classList.remove('open');
    };
    list.appendChild(btn);
  });
}


// ── Admin boundary overlays ───────────────────────────────────────────────────

var _GCF_BOUNDARY_LAYERS = {
  country: { id: 'gcf-boundary-country', adminLevel: 2, color: '#c0392b', width: 2.0 },
  state:   { id: 'gcf-boundary-state',   adminLevel: 4, color: '#2471a3', width: 2.0 },
  county:  { id: 'gcf-boundary-county',  adminLevel: 6, color: '#1e8449', width: 2.0 },
};

// Track which boundary type each layer was added for (to re-add after style switch)
var _gcfActiveBoundaries = {};

function _gcfApplyDefaultBoundaries() {
  if (typeof _gcfMapPrefs === 'undefined') return;
  var types = ['country', 'state', 'county'];
  types.forEach(function(type) {
    var key = 'boundary_' + type;
    if (_gcfMapPrefs[key]) {
      gcfToggleBoundary(type, true);
      var cb = document.getElementById('boundary-' + type);
      if (cb) cb.checked = true;
    }
  });
}

function gcfToggleBoundary(type, enabled) {
  if (!gcfMap) return;
  var cfg = _GCF_BOUNDARY_LAYERS[type];
  if (!cfg) return;

  if (enabled) {
    _gcfActiveBoundaries[type] = true;
    _gcfAddBoundaryLayer(cfg);
  } else {
    delete _gcfActiveBoundaries[type];
    if (gcfMap.getLayer(cfg.id)) gcfMap.removeLayer(cfg.id);
  }
}

function _gcfAddBoundaryLayer(cfg) {
  if (!gcfMap || gcfMap.getLayer(cfg.id)) return;
  // Boundary data is only available in the vector (street) style.
  // Detect by checking if the 'openmaptiles' source exists.
  if (!gcfMap.getSource('openmaptiles')) return;
  try {
    gcfMap.addLayer({
      id: cfg.id,
      type: 'line',
      source: 'openmaptiles',
      'source-layer': 'boundary',
      filter: ['==', ['get', 'admin_level'], cfg.adminLevel],
      paint: {
        'line-color': cfg.color,
        'line-width': cfg.width,
        'line-opacity': 0.75,
        'line-dasharray': cfg.adminLevel > 2 ? [4, 2] : [1]
      }
    });
  } catch(e) {
    // Style doesn't support vector boundary layers (raster tile mode)
  }
}

// Re-apply active boundary layers after a style change
function _gcfRestoreBoundaries() {
  Object.keys(_gcfActiveBoundaries).forEach(function(type) {
    _gcfAddBoundaryLayer(_GCF_BOUNDARY_LAYERS[type]);
  });
}


// ── Public: pan to a specific cache (for list → map sync) ────────────────────

function gcfMapPanTo(code) {
  if (!gcfMap || !_gcfMarkersData) return;
  var m = _gcfMarkersData.find(function(mk) { return mk.c === code; });
  if (!m) return;
  gcfMap.flyTo({ center: [m.lo, m.la], zoom: Math.max(gcfMap.getZoom(), 15) });
  _gcfShowPopup([m.lo, m.la], {
    code: m.c, name: m.n, cacheType: m.t, size: m.sz,
    difficulty: m.d, terrain: m.tr, found: m.f, status: m.s, isMine: m.m
  });
}


// ── Refresh markers (called when filters change) ─────────────────────────────

function _gcfFilterParams(searchOverride) {
  // Build the same param string _gcfFetchMarkers would use, minus sort/order/page.
  var p = _gcfBuildFilterParams(searchOverride);
  _GCF_MAP_IGNORE_PARAMS.forEach(function(k) { p.delete(k); });
  p.sort();
  return p.toString();
}

// ── Type quick-filter (map-local) ────────────────────────────────────────────

var _gcfHiddenTypes = {};  // { 'T': true, 'M': true, ... }

function gcfToggleTypeFilter(typeCode, visible) {
  if (visible) {
    delete _gcfHiddenTypes[typeCode];
  } else {
    _gcfHiddenTypes[typeCode] = true;
  }
  _gcfApplyTypeFilter();
}

function gcfTypeFilterClick(btn) {
  var type = btn.getAttribute('data-type');
  var isActive = btn.classList.contains('active');
  btn.classList.toggle('active', !isActive);
  gcfToggleTypeFilter(type, !isActive);
}

function gcfTypeFilterSetAll(active) {
  var btns = document.querySelectorAll('.map-type-btn[data-type]');
  _gcfHiddenTypes = {};
  for (var i = 0; i < btns.length; i++) {
    btns[i].classList.toggle('active', active);
    if (!active) {
      _gcfHiddenTypes[btns[i].getAttribute('data-type')] = true;
    }
  }
  _gcfApplyTypeFilter();
}

function _gcfApplyTypeFilter() {
  // Update the source data so clusters also reflect hidden types.
  // Layer-level setFilter only affects unclustered points, not cluster counts.
  if (!gcfMap || !gcfMap.getSource('gcf-markers') || !_gcfMarkersData) return;

  var hidden = Object.keys(_gcfHiddenTypes);
  var filtered = (hidden.length === 0)
    ? _gcfMarkersData
    : _gcfMarkersData.filter(function(m) { return !_gcfHiddenTypes[m.t]; });

  var features = filtered.map(function(m) {
    var plat = (m.oc && !m.gc) ? 'oc' : 'gc';
    var sk = (typeof _gcfStatusKey === 'function') ? _gcfStatusKey(m) : 'U';
    return {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [m.lo, m.la] },
      properties: {
        code: m.c,
        name: m.n,
        cacheType: m.t,
        size: m.sz,
        difficulty: m.d,
        terrain: m.tr,
        found: m.f,
        status: m.s,
        isMine: m.m,
        gcCode: m.gc || null,
        ocCode: m.oc || null,
        platform: plat,
        statusKey: sk,
        dnf: (m.dnf && !m.f) ? '-dnf' : '',
        corrected: m.ola != null ? '-c' : '',
        color: _gcfMarkerColor(m),
        borderColor: _gcfMarkerBorderColor(m),
        origLat: m.ola || null,
        origLon: m.olo || null
      }
    };
  });

  gcfMap.getSource('gcf-markers').setData({
    type: 'FeatureCollection',
    features: features
  });

  // Refresh display layers that depend on marker data
  if (typeof gcfRefreshWaypoints === 'function') gcfRefreshWaypoints();
  if (typeof gcfRefreshSepCircles === 'function') gcfRefreshSepCircles();
  if (typeof gcfRefreshOrigWaypoints === 'function') gcfRefreshOrigWaypoints();
  if (typeof gcfRefreshALStageLines === 'function') gcfRefreshALStageLines();
  if (typeof gcfRefreshLabels === 'function') gcfRefreshLabels();
}


function gcfMapRefresh(searchOverride) {
  if (!_gcfMapInitialized || !gcfMap) return;
  // Update radius circle visibility when URL changes (radius filter added/removed)
  if (typeof _gcfRefRadiusInit === 'function') _gcfRefRadiusInit();
  // Skip reload when only sort/order/page changed — markers are the same.
  var current = _gcfFilterParams(searchOverride);
  if (current === _gcfLastMarkerParams) return;
  _gcfFetchMarkers(searchOverride);
}

// Force a marker refresh even when the URL params are unchanged. Needed when
// server-side session state (e.g. the Now-Forging scope) alters the result set
// without changing the query string — gcfMapRefresh's skip-on-identical-params
// guard would otherwise wrongly skip the update.
window.gcfMapForceRefresh = function(searchOverride) {
  if (!_gcfMapInitialized || !gcfMap) return;
  _gcfLastMarkerParams = null;
  gcfMapRefresh(searchOverride);
};

// ── Zoom to filtered caches ─────────────────────────────────────────────────

function gcfZoomToFiltered() {
  if (!gcfMap || !_gcfMarkersData || !_gcfMarkersData.length) return;

  // Use visible markers (respect local type filter)
  var markers = _gcfMarkersData;
  if (Object.keys(_gcfHiddenTypes).length > 0) {
    markers = markers.filter(function(m) { return !_gcfHiddenTypes[m.t]; });
  }
  if (!markers.length) return;

  var minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
  markers.forEach(function(m) {
    if (m.la < minLat) minLat = m.la;
    if (m.la > maxLat) maxLat = m.la;
    if (m.lo < minLon) minLon = m.lo;
    if (m.lo > maxLon) maxLon = m.lo;
  });

  // Add 10% margin
  var dlat = (maxLat - minLat) * 0.1 || 0.005;
  var dlon = (maxLon - minLon) * 0.1 || 0.005;

  gcfMap.fitBounds(
    [[minLon - dlon, minLat - dlat], [maxLon + dlon, maxLat + dlat]],
    { animate: true, duration: 600 }
  );
}


// ── LOD controls ──────────────────────────────────────────────────────────

var _gcfLodClusterTimer = null;

function gcfToggleLodPanel(show) {
  var el = document.getElementById('map-lod-control');
  if (el) el.style.display = show ? '' : 'none';
  // Persist in localStorage
  var saved = JSON.parse(localStorage.getItem('gcforge_lod') || '{}');
  saved.visible = show;
  localStorage.setItem('gcforge_lod', JSON.stringify(saved));
}

function gcfSetClusterZoom(val) {
  GCF_CLUSTER_MAX_ZOOM = val;
  var label = document.getElementById('lod-cluster-val');
  if (label) label.textContent = val;
  // Re-render markers with new cluster zoom (debounced). Force a rebuild —
  // clusterMaxZoom is fixed at source-creation time, so setData can't apply it.
  if (_gcfLodClusterTimer) clearTimeout(_gcfLodClusterTimer);
  _gcfLodClusterTimer = setTimeout(function() {
    if (_gcfMarkersData) _gcfRenderMarkers(_gcfMarkersData, { rebuild: true });
  }, 300);
  _gcfSaveLodState();
}

function gcfSetLinesZoom(val) {
  GCF_LINES_MIN_ZOOM = val;
  var label = document.getElementById('lod-lines-val');
  if (label) label.textContent = val;
  // Re-trigger waypoint/line visibility check
  if (typeof _gcfFetchAndRenderWaypoints === 'function') _gcfFetchAndRenderWaypoints();
  if (typeof gcfRefreshALStageLines === 'function') gcfRefreshALStageLines();
  _gcfSaveLodState();
}

function _gcfSaveLodState() {
  var saved = JSON.parse(localStorage.getItem('gcforge_lod') || '{}');
  saved.cluster = GCF_CLUSTER_MAX_ZOOM;
  saved.lines = GCF_LINES_MIN_ZOOM;
  localStorage.setItem('gcforge_lod', JSON.stringify(saved));
}

function _gcfRestoreLodState() {
  // Restore slider values from localStorage (visibility is handled by gcfLayersInit)
  var saved = JSON.parse(localStorage.getItem('gcforge_lod') || '{}');
  if (saved.cluster != null) {
    GCF_CLUSTER_MAX_ZOOM = saved.cluster;
    var el = document.getElementById('lod-cluster');
    if (el) el.value = saved.cluster;
    var label = document.getElementById('lod-cluster-val');
    if (label) label.textContent = saved.cluster;
  }
  if (saved.lines != null) {
    GCF_LINES_MIN_ZOOM = saved.lines;
    var el = document.getElementById('lod-lines');
    if (el) el.value = saved.lines;
    var label = document.getElementById('lod-lines-val');
    if (label) label.textContent = saved.lines;
  }
}
