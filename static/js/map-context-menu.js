// ── GCForge Map Context Menu ──────────────────────────────────────────────────
//
// Right-click context menus for the map background and cache markers.
// Depends on: cache-map.js (gcfMap, _gcfMarkersData)
//             map-draw.js (_gcfHaversineM, _gcfCircleCoords, _gcfFmtDist)

var _gcfCtxMenuEl = null;

// ── Range circle state (multiple, color-coded) ─────────────────────────────
var _gcfRangeCircles = [];   // [{ sourceId, fillId, lineId, color, radiusKm }]
var _gcfRangeCircleColors = [
  '#0d6efd', '#dc3545', '#198754', '#fd7e14', '#6f42c1',
  '#20c997', '#e83e8c', '#6610f2', '#795548', '#17a2b8'
];
var _gcfRangeCircleMenuEl = null;

// ── Initialise ───────────────────────────────────────────────────────────────

function gcfContextMenuInit() {
  if (!gcfMap) return;

  // Create the menu element once
  _gcfCtxMenuEl = document.createElement('div');
  _gcfCtxMenuEl.id = 'map-context-menu';
  _gcfCtxMenuEl.className = 'map-draw-context-menu';   // reuse existing CSS
  document.getElementById('map-container').appendChild(_gcfCtxMenuEl);

  // Create range circle management menu
  _gcfRangeCircleMenuEl = document.createElement('div');
  _gcfRangeCircleMenuEl.id = 'map-range-circle-menu';
  _gcfRangeCircleMenuEl.className = 'map-range-circle-menu';
  document.getElementById('map-container').appendChild(_gcfRangeCircleMenuEl);

  // Right-click handler
  gcfMap.on('contextmenu', function(e) {
    e.preventDefault();

    // Check if a draw shape was clicked (highest priority)
    var shapeFeatureId = _gcfQueryDrawShape(e.point);
    if (shapeFeatureId !== null) {
      _gcfShowShapeMenu(e, shapeFeatureId);
      return;
    }

    // Check if a marker was clicked
    var features = gcfMap.queryRenderedFeatures(e.point, { layers: ['gcf-unclustered'] });
    if (features && features.length > 0) {
      _gcfShowMarkerMenu(e, features[0].properties);
    } else {
      _gcfShowBackgroundMenu(e);
    }
  });

  // Close on any click, scroll or map move
  document.addEventListener('click', _gcfCloseCtxMenu);
  document.addEventListener('wheel', _gcfCloseCtxMenu);
  gcfMap.on('movestart', _gcfCloseCtxMenu);
}

// ── Close menu ───────────────────────────────────────────────────────────────

function _gcfCloseCtxMenu() {
  if (_gcfCtxMenuEl) _gcfCtxMenuEl.classList.remove('open');
}

// ── Position + show menu ─────────────────────────────────────────────────────

function _gcfPositionMenu(point) {
  var container = document.getElementById('map-container');
  var rect = container.getBoundingClientRect();
  var x = point.x;
  var y = point.y;

  // Flip if menu would overflow the container
  _gcfCtxMenuEl.style.left = x + 'px';
  _gcfCtxMenuEl.style.top = y + 'px';
  _gcfCtxMenuEl.classList.add('open');

  // Adjust after rendering so we can measure
  var menuRect = _gcfCtxMenuEl.getBoundingClientRect();
  if (x + menuRect.width > rect.width) {
    _gcfCtxMenuEl.style.left = (x - menuRect.width) + 'px';
  }
  if (y + menuRect.height > rect.height) {
    _gcfCtxMenuEl.style.top = (y - menuRect.height) + 'px';
  }
}

// ── Draw shape detection ──────────────────────────────────────────────────────

function _gcfQueryDrawShape(point) {
  if (typeof _gcfDrawCtrl === 'undefined' || !_gcfDrawCtrl) return null;
  var queryLayers = [
    'gcf-draw-polygon-stroke-active.cold', 'gcf-draw-polygon-stroke-active.hot',
    'gcf-draw-polygon-fill.cold', 'gcf-draw-polygon-fill.hot',
  ].filter(function(id) { return gcfMap.getLayer(id); });
  if (!queryLayers.length) return null;
  var features = gcfMap.queryRenderedFeatures(point, { layers: queryLayers });
  if (!features.length) return null;
  return features[0].properties.id || null;
}

// ── Shape context menu (fused: shape actions + separator + background actions) ─

function _gcfShowShapeMenu(e, featureId) {
  var lngLat = e.lngLat;

  // Select the shape so trash() targets it
  if (featureId) _gcfDrawCtrl.changeMode('simple_select', { featureIds: [featureId] });

  _gcfCtxMenuEl.innerHTML = '';

  // --- Shape-specific items ---
  if (typeof gcfOpenFetchDialog === 'function') {
    _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Preview API caches'), function() {
      gcfOpenFetchDialog();
    }));
  }

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Delete shape'), function() {
    if (_gcfDrawCtrl) _gcfDrawCtrl.trash();
  }));

  // --- Separator ---
  _gcfCtxMenuEl.appendChild(_gcfMenuSeparator());

  // --- Background items ---
  _gcfCtxMenuEl.appendChild(_gcfMakeCopyCoordItem(lngLat.lat, lngLat.lng));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Range circle\u2026'), function() {
    _gcfPromptRangeCircle(lngLat.lng, lngLat.lat);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Set as center point'), function() {
    _gcfSaveLocationFromMap(lngLat.lat, lngLat.lng);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Street View'), function() {
    _gcfOpenStreetView(lngLat.lat, lngLat.lng);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Zoom in'), function() {
    gcfMap.easeTo({ center: lngLat, zoom: gcfMap.getZoom() + 1 });
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Zoom out'), function() {
    gcfMap.easeTo({ center: lngLat, zoom: gcfMap.getZoom() - 1 });
  }));

  _gcfPositionMenu(e.point);
}

// ── Background context menu ──────────────────────────────────────────────────

function _gcfShowBackgroundMenu(e) {
  var lngLat = e.lngLat;

  _gcfCtxMenuEl.innerHTML = '';

  _gcfCtxMenuEl.appendChild(_gcfMakeCopyCoordItem(lngLat.lat, lngLat.lng));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Range circle\u2026'), function() {
    _gcfPromptRangeCircle(lngLat.lng, lngLat.lat);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Nearest cache'), function() {
    _gcfFindNearestPoint(lngLat.lat, lngLat.lng);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Search nearby'), function() {
    _gcfReverseGeocode(lngLat.lat, lngLat.lng);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Set as center point'), function() {
    _gcfSaveLocationFromMap(lngLat.lat, lngLat.lng);
  }));

  if (typeof gcfRouteAddWaypoint === 'function') {
    _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Add to route'), function() {
      gcfRouteAddWaypoint(lngLat.lat, lngLat.lng);
    }));
  }

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Street View'), function() {
    _gcfOpenStreetView(lngLat.lat, lngLat.lng);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Zoom in'), function() {
    gcfMap.easeTo({ center: lngLat, zoom: gcfMap.getZoom() + 1 });
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Zoom out'), function() {
    gcfMap.easeTo({ center: lngLat, zoom: gcfMap.getZoom() - 1 });
  }));

  _gcfPositionMenu(e.point);
}

// ── Marker context menu ──────────────────────────────────────────────────────

function _gcfShowMarkerMenu(e, props) {
  var code = props.code;
  var lngLat = e.lngLat;

  // Use the marker's actual coordinates (not the click point)
  var markerData = _gcfMarkersData
    ? _gcfMarkersData.find(function(m) { return m.c === code; })
    : null;
  var mLat = markerData ? markerData.la : lngLat.lat;
  var mLon = markerData ? markerData.lo : lngLat.lng;

  _gcfCtxMenuEl.innerHTML = '';

  // --- Cache-specific items ---
  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Open detail'), function() {
    window.location.href = '/' + code + '/';
  }));

  // Show external links: GC and/or OC
  var gcCode = props.gcCode || (code.substring(0, 2).toUpperCase() === 'GC' ? code : null);
  var ocCode = props.ocCode || (code.substring(0, 2).toUpperCase() === 'OC' ? code : null);

  if (gcCode) {
    _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Open on geocaching.com'), function() {
      window.open('https://coord.info/' + gcCode, '_blank');
    }));
  }
  if (ocCode) {
    _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Open on opencaching.de'), function() {
      window.open('https://www.opencaching.de/viewcache.php?wp=' + ocCode, '_blank');
    }));
  }

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Set as center point'), function() {
    _gcfSaveCacheAsLocation(code, markerData ? markerData.n : code, mLat, mLon);
  }));

  if (typeof gcfRouteAddWaypoint === 'function') {
    _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Add to route'), function() {
      gcfRouteAddWaypoint(mLat, mLon, markerData ? markerData.n : code, 'cache', code);
    }));
  }

  // --- Map visibility (only "hide" branches reachable from the map: hidden
  // caches don't render markers, so the marker is always currently visible.) ---
  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Hide on map (this session)'), function() {
    _gcfPostMapVisibility(code, 'session');
  }));
  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Hide on map (always)'), function() {
    _gcfPostMapVisibility(code, 'always');
  }));

  // --- Separator ---
  _gcfCtxMenuEl.appendChild(_gcfMenuSeparator());

  // --- Map items ---
  _gcfCtxMenuEl.appendChild(_gcfMakeCopyCoordItem(mLat, mLon));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Range circle\u2026'), function() {
    _gcfPromptRangeCircle(mLon, mLat);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Nearest cache'), function() {
    _gcfFindNearest(code, mLat, mLon);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Search nearby'), function() {
    _gcfReverseGeocode(mLat, mLon);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Street View'), function() {
    _gcfOpenStreetView(mLat, mLon);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Zoom in'), function() {
    gcfMap.easeTo({ center: [mLon, mLat], zoom: gcfMap.getZoom() + 1 });
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem(gettext('Zoom out'), function() {
    gcfMap.easeTo({ center: [mLon, mLat], zoom: gcfMap.getZoom() - 1 });
  }));

  _gcfPositionMenu(e.point);
}

// ── Build a menu item button ─────────────────────────────────────────────────

function _gcfMenuItem(label, handler) {
  var btn = document.createElement('button');
  btn.className = 'map-draw-context-item';   // reuse existing CSS
  btn.textContent = label;
  btn.onclick = function(ev) {
    ev.stopPropagation();
    _gcfCloseCtxMenu();
    handler();
  };
  return btn;
}

function _gcfMenuSeparator() {
  var sep = document.createElement('div');
  sep.className = 'map-ctx-separator';
  return sep;
}

// ── Map visibility POST + marker refetch ─────────────────────────────────────

function _gcfPostMapVisibility(code, state) {
  var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
  csrfToken = csrfToken ? csrfToken.value : '';
  var body = new URLSearchParams();
  body.set('state', state);

  // Check whether this is an Adventure Lab parent BEFORE the POST — server
  // cascades the hide to every stage, so a local one-row splice would leave
  // stage markers visible until reload. Parent identification: aid set + sn
  // not set in the marker data.
  var markerData = (typeof _gcfMarkersData !== 'undefined' && _gcfMarkersData)
    ? _gcfMarkersData.find(function(m) { return m.c === code; })
    : null;
  var isAlParent = !!(markerData && markerData.aid && !markerData.sn);

  fetch('/cache/' + code + '/map-visibility/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-CSRFToken': csrfToken,
      'HX-Request': 'true',
    },
    body: body.toString(),
  }).then(function(r) {
    if (!r.ok) return;
    if (isAlParent && typeof _gcfFetchMarkers === 'function') {
      // Cascade hides every stage too — refetch the whole marker set.
      _gcfFetchMarkers();
    } else if (_gcfMarkersData) {
      // Single-cache hide: splice locally and rebuild the source.
      var idx = _gcfMarkersData.findIndex(function(m) { return m.c === code; });
      if (idx >= 0) {
        _gcfMarkersData.splice(idx, 1);
        if (typeof _gcfApplyTypeFilter === 'function') {
          _gcfApplyTypeFilter();
        }
      }
    }
    // Notify the list view (in split layouts) to refresh its badges.
    document.body.dispatchEvent(new CustomEvent('gcf-map-visibility-changed'));
  });
}

// ── Coordinate formatters ────────────────────────────────────────────────────

function _gcfCtxFmtDMM(lat, lon) {
  function fmt(deg, pos, neg) {
    var h = deg >= 0 ? pos : neg;
    var d = Math.abs(deg);
    var m = (d - Math.floor(d)) * 60;
    return h + ' ' + String(Math.floor(d)).padStart(2, '0') + '° ' +
           m.toFixed(3).padStart(6, '0') + "'";
  }
  return fmt(lat, 'N', 'S') + ' ' + fmt(lon, 'E', 'W');
}

function _gcfCtxFmtDMS(lat, lon) {
  function fmt(deg, pos, neg) {
    var h = deg >= 0 ? pos : neg;
    var d = Math.abs(deg);
    var mTotal = (d - Math.floor(d)) * 60;
    var m = Math.floor(mTotal);
    var s = (mTotal - m) * 60;
    return h + ' ' + String(Math.floor(d)).padStart(2, '0') + '° ' +
           String(m).padStart(2, '0') + "' " + s.toFixed(1).padStart(4, '0') + '"';
  }
  return fmt(lat, 'N', 'S') + ' ' + fmt(lon, 'E', 'W');
}

function _gcfCtxFmtDD(lat, lon) {
  return lat.toFixed(6) + ', ' + lon.toFixed(6);
}

// ── Copy-coordinates submenu item ────────────────────────────────────────────

function _gcfMakeCopyCoordItem(lat, lon) {
  var wrapper = document.createElement('div');

  var trigger = document.createElement('button');
  trigger.className = 'map-draw-context-item map-ctx-coord-trigger';
  trigger.innerHTML = gettext('Copy coordinates') + ' <span class="map-ctx-arrow">▸</span>';

  var sub = document.createElement('div');
  sub.className = 'map-ctx-coord-sub';

  var formats = [
    _gcfCtxFmtDMM(lat, lon),
    _gcfCtxFmtDMS(lat, lon),
    _gcfCtxFmtDD(lat, lon),
  ];

  formats.forEach(function(text) {
    var btn = document.createElement('button');
    btn.className = 'map-draw-context-item map-ctx-coord-opt';
    btn.textContent = text;
    btn.onclick = function(ev) {
      ev.stopPropagation();
      _gcfCloseCtxMenu();
      _gcfCopyText(text);
    };
    sub.appendChild(btn);
  });

  trigger.onclick = function(ev) {
    ev.stopPropagation();
    var open = sub.classList.toggle('open');
    trigger.querySelector('.map-ctx-arrow').textContent = open ? '▾' : '▸';
  };

  wrapper.appendChild(trigger);
  wrapper.appendChild(sub);
  return wrapper;
}

// ── Copy to clipboard ────────────────────────────────────────────────────────

function _gcfCopyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).catch(function() {
      _gcfCopyFallback(text);
    });
  } else {
    _gcfCopyFallback(text);
  }
}

function _gcfCopyFallback(text) {
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch(e) {}
  document.body.removeChild(ta);
}

// ── Range circles (multiple, color-coded) ─────────────────────────────────────

function _gcfPromptRangeCircle(lng, lat) {
  var input = window.prompt(gettext('Radius in km (use "." as decimal separator):'), '2');
  if (input === null) return;
  input = input.trim();
  var km = parseFloat(input);
  if (isNaN(km) || km <= 0) {
    _gcfFlashMessage(interpolate(gettext('Invalid radius: "%s". Use a number like 2 or 0.5 (dot as decimal separator).'), [input]));
    return;
  }

  var colorIdx = _gcfRangeCircles.length % _gcfRangeCircleColors.length;
  var color = _gcfRangeCircleColors[colorIdx];

  var radiusM = km * 1000;
  var coords = _gcfCircleCoords(lng, lat, radiusM);
  var geojson = {
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [coords] }
  };

  var id = _gcfRangeCircles.length;
  var srcId = 'gcf-range-circle-src-' + id;
  var fillId = 'gcf-range-circle-fill-' + id;
  var lineId = 'gcf-range-circle-line-' + id;

  gcfMap.addSource(srcId, { type: 'geojson', data: geojson });

  gcfMap.addLayer({
    id: fillId,
    type: 'fill',
    source: srcId,
    paint: {
      'fill-color': color,
      'fill-opacity': 0.08
    }
  });

  gcfMap.addLayer({
    id: lineId,
    type: 'line',
    source: srcId,
    paint: {
      'line-color': color,
      'line-width': 2,
      'line-opacity': 0.6
    }
  });

  _gcfRangeCircles.push({
    sourceId: srcId, fillId: fillId, lineId: lineId,
    color: color, radiusKm: km
  });

  _gcfUpdateRangeCircleMenu();
}

function _gcfRemoveRangeCircleByIndex(idx) {
  if (!gcfMap || idx < 0 || idx >= _gcfRangeCircles.length) return;
  var rc = _gcfRangeCircles[idx];
  if (gcfMap.getLayer(rc.fillId)) gcfMap.removeLayer(rc.fillId);
  if (gcfMap.getLayer(rc.lineId)) gcfMap.removeLayer(rc.lineId);
  if (gcfMap.getSource(rc.sourceId)) gcfMap.removeSource(rc.sourceId);
  _gcfRangeCircles.splice(idx, 1);
  _gcfUpdateRangeCircleMenu();
}

function gcfRemoveAllRangeCircles() {
  if (!gcfMap) return;
  for (var i = _gcfRangeCircles.length - 1; i >= 0; i--) {
    var rc = _gcfRangeCircles[i];
    if (gcfMap.getLayer(rc.fillId)) gcfMap.removeLayer(rc.fillId);
    if (gcfMap.getLayer(rc.lineId)) gcfMap.removeLayer(rc.lineId);
    if (gcfMap.getSource(rc.sourceId)) gcfMap.removeSource(rc.sourceId);
  }
  _gcfRangeCircles = [];
  _gcfUpdateRangeCircleMenu();
}

// Legacy compat — old code may call this
function gcfRemoveRangeCircle() {
  gcfRemoveAllRangeCircles();
}

function _gcfUpdateRangeCircleMenu() {
  if (!_gcfRangeCircleMenuEl) return;
  if (_gcfRangeCircles.length === 0) {
    _gcfRangeCircleMenuEl.style.display = 'none';
    return;
  }

  _gcfRangeCircleMenuEl.innerHTML = '';
  _gcfRangeCircleMenuEl.style.display = 'block';

  for (var i = 0; i < _gcfRangeCircles.length; i++) {
    (function(idx) {
      var rc = _gcfRangeCircles[idx];
      var item = document.createElement('button');
      item.className = 'map-range-circle-item';
      item.innerHTML = '<span class="map-range-dot" style="background:' + rc.color + '"></span>' +
        rc.radiusKm + ' km';
      item.title = gettext('Remove this circle');
      item.onclick = function() { _gcfRemoveRangeCircleByIndex(idx); };
      _gcfRangeCircleMenuEl.appendChild(item);
    })(i);
  }

  var removeAll = document.createElement('button');
  removeAll.className = 'map-range-circle-item map-range-remove-all';
  removeAll.textContent = gettext('Remove all');
  removeAll.onclick = gcfRemoveAllRangeCircles;
  _gcfRangeCircleMenuEl.appendChild(removeAll);
}

// ── Reverse geocode (Nominatim) ──────────────────────────────────────────────

function _gcfReverseGeocode(lat, lng) {
  var url = 'https://nominatim.openstreetmap.org/reverse?format=json' +
    '&lat=' + lat + '&lon=' + lng + '&zoom=14';

  fetch(url, { headers: { 'Accept-Language': 'en' } })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var name = data.display_name || gettext('No results');
      new maplibregl.Popup({ maxWidth: '300px' })
        .setLngLat([lng, lat])
        .setHTML('<div style="font-size:0.8rem">' +
          '<strong>' + gettext('Nearby') + '</strong><br>' +
          _gcfEsc(name) + '</div>')
        .addTo(gcfMap);
    })
    .catch(function() {
      new maplibregl.Popup({ maxWidth: '300px' })
        .setLngLat([lng, lat])
        .setHTML('<div style="font-size:0.8rem">' + gettext('Reverse geocode failed') + '</div>')
        .addTo(gcfMap);
    });
}

// ── Find nearest cache (from a specific cache marker) ─────────────────────────

function _gcfFindNearest(code, lat, lon) {
  if (!_gcfMarkersData || _gcfMarkersData.length < 2) {
    _gcfFlashMessage(gettext('Not enough caches loaded to find nearest.'));
    return;
  }

  var bestDist = Infinity;
  var bestCache = null;

  for (var i = 0; i < _gcfMarkersData.length; i++) {
    var m = _gcfMarkersData[i];
    if (m.c === code) continue;
    var d = _gcfHaversineM(lat, lon, m.la, m.lo);
    if (d < bestDist) {
      bestDist = d;
      bestCache = m;
    }
  }

  if (!bestCache) {
    _gcfFlashMessage(gettext('No other caches found.'));
    return;
  }

  new maplibregl.Popup({ maxWidth: '300px' })
    .setLngLat([lon, lat])
    .setHTML('<div style="font-size:0.8rem">' +
      '<strong>' + gettext('Nearest cache') + '</strong><br>' +
      '<a href="/' + _gcfEsc(bestCache.c) + '/" style="color:inherit">' +
      _gcfEsc(bestCache.n) + '</a> (' + _gcfEsc(bestCache.c) + ')<br>' +
      _gcfFmtDist(bestDist) + '</div>')
    .addTo(gcfMap);
}

// ── Find nearest cache (from an arbitrary map point) ──────────────────────────

function _gcfFindNearestPoint(lat, lon) {
  if (!_gcfMarkersData || _gcfMarkersData.length === 0) {
    _gcfFlashMessage(gettext('No caches loaded.'));
    return;
  }

  var bestDist = Infinity;
  var bestCache = null;

  for (var i = 0; i < _gcfMarkersData.length; i++) {
    var m = _gcfMarkersData[i];
    var d = _gcfHaversineM(lat, lon, m.la, m.lo);
    if (d < bestDist) {
      bestDist = d;
      bestCache = m;
    }
  }

  if (!bestCache) {
    _gcfFlashMessage(gettext('No caches found.'));
    return;
  }

  new maplibregl.Popup({ maxWidth: '300px' })
    .setLngLat([lon, lat])
    .setHTML('<div style="font-size:0.8rem">' +
      '<strong>' + gettext('Nearest cache') + '</strong><br>' +
      '<a href="/' + _gcfEsc(bestCache.c) + '/" style="color:inherit">' +
      _gcfEsc(bestCache.n) + '</a> (' + _gcfEsc(bestCache.c) + ')<br>' +
      _gcfFmtDist(bestDist) + '</div>')
    .addTo(gcfMap);
}

// ── Set as center point (save location) ─────────────────────────────────────

function _gcfGetCsrf() {
  var el = document.querySelector('[name=csrfmiddlewaretoken]');
  if (el) return el.value;
  var m = document.cookie.match(/csrftoken=([^;]+)/);
  return m ? m[1] : '';
}

function _gcfSaveLocation(name, lat, lon, note) {
  var csrf = _gcfGetCsrf();
  if (!csrf) {
    _gcfFlashMessage(gettext('CSRF token not found.'));
    return;
  }

  fetch('/location/save/', {
    method: 'POST',
    headers: {
      'X-CSRFToken': csrf,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({name: name, latitude: lat, longitude: lon, note: note || ''})
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.ok) {
        // Reload with the new location as the active ref point
        var url = new URL(window.location.href);
        url.searchParams.set('ref', data.id);
        window.location.href = url.toString();
      } else {
        _gcfFlashMessage(interpolate(gettext('Failed to save location: %s'), [data.error || '']));
      }
    })
    .catch(function() {
      _gcfFlashMessage(gettext('Failed to save location'));
    });
}

function _gcfSaveCacheAsLocation(code, cacheName, lat, lon) {
  _gcfSaveLocation(cacheName, lat, lon, interpolate(gettext('From cache %s'), [code]));
}

function _gcfSaveLocationFromMap(lat, lng) {
  // Reverse geocode to get a name, then save
  var url = 'https://nominatim.openstreetmap.org/reverse?format=json' +
    '&lat=' + lat + '&lon=' + lng + '&zoom=14';

  fetch(url, { headers: { 'Accept-Language': 'en' } })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var addr = data.address || {};
      // Use the most specific useful name: village/town/city + suburb
      var name = addr.village || addr.town || addr.city || addr.municipality || '';
      var sub = addr.suburb || addr.neighbourhood || '';
      if (name && sub) name = name + ' ' + sub;
      if (!name) name = data.display_name ? data.display_name.split(',')[0] : '';
      if (!name) name = gettext('Mapped location');
      _gcfSaveLocation(name, lat, lng);
    })
    .catch(function() {
      _gcfSaveLocation(gettext('Mapped location'), lat, lng);
    });
}

// ── Refresh locations dropdown after adding a reference point ──────────────

function _gcfRefreshLocations(callback) {
  fetch('/settings/locations-json/')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (typeof _gcfLocations !== 'undefined') {
        _gcfLocations = data;
      }
      if (typeof _gcfBuildLocationsDropdown === 'function') {
        _gcfBuildLocationsDropdown();
      }
      if (callback) callback();
    })
    .catch(function() {});
}

// ── Google Street View ─────────────────────────────────────────────────────

function _gcfOpenStreetView(lat, lng) {
  window.open('https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=' +
    lat.toFixed(6) + ',' + lng.toFixed(6), '_blank');
}

// ── Brief toast-style message ────────────────────────────────────────────────

function _gcfFlashMessage(text) {
  var el = document.createElement('div');
  el.textContent = text;
  el.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);' +
    'background:rgba(0,0,0,0.8);color:#fff;padding:6px 16px;border-radius:4px;' +
    'font-size:0.85rem;z-index:9999;pointer-events:none;';
  document.body.appendChild(el);
  setTimeout(function() {
    el.style.transition = 'opacity 0.4s';
    el.style.opacity = '0';
    setTimeout(function() { document.body.removeChild(el); }, 400);
  }, 2000);
}
