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
    _gcfCtxMenuEl.appendChild(_gcfMenuItem('Preview API caches', function() {
      gcfOpenFetchDialog();
    }));
  }

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Delete shape', function() {
    if (_gcfDrawCtrl) _gcfDrawCtrl.trash();
  }));

  // --- Separator ---
  _gcfCtxMenuEl.appendChild(_gcfMenuSeparator());

  // --- Background items ---
  var lat = lngLat.lat.toFixed(6);
  var lon = lngLat.lng.toFixed(6);

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Copy coordinates', function() {
    _gcfCopyText(lat + ', ' + lon);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Range circle\u2026', function() {
    _gcfPromptRangeCircle(lngLat.lng, lngLat.lat);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Set as center point', function() {
    _gcfSaveLocationFromMap(lngLat.lat, lngLat.lng);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Street View', function() {
    _gcfOpenStreetView(lngLat.lat, lngLat.lng);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Zoom in', function() {
    gcfMap.easeTo({ center: lngLat, zoom: gcfMap.getZoom() + 1 });
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Zoom out', function() {
    gcfMap.easeTo({ center: lngLat, zoom: gcfMap.getZoom() - 1 });
  }));

  _gcfPositionMenu(e.point);
}

// ── Background context menu ──────────────────────────────────────────────────

function _gcfShowBackgroundMenu(e) {
  var lngLat = e.lngLat;
  var lat = lngLat.lat.toFixed(6);
  var lon = lngLat.lng.toFixed(6);

  _gcfCtxMenuEl.innerHTML = '';

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Copy coordinates', function() {
    _gcfCopyText(lat + ', ' + lon);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Range circle\u2026', function() {
    _gcfPromptRangeCircle(lngLat.lng, lngLat.lat);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Nearest cache', function() {
    _gcfFindNearestPoint(lngLat.lat, lngLat.lng);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Search nearby', function() {
    _gcfReverseGeocode(lngLat.lat, lngLat.lng);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Set as center point', function() {
    _gcfSaveLocationFromMap(lngLat.lat, lngLat.lng);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Street View', function() {
    _gcfOpenStreetView(lngLat.lat, lngLat.lng);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Zoom in', function() {
    gcfMap.easeTo({ center: lngLat, zoom: gcfMap.getZoom() + 1 });
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Zoom out', function() {
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
  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Open detail', function() {
    window.location.href = '/' + code + '/';
  }));

  // Show external links: GC and/or OC
  var gcCode = props.gcCode || (code.substring(0, 2).toUpperCase() === 'GC' ? code : null);
  var ocCode = props.ocCode || (code.substring(0, 2).toUpperCase() === 'OC' ? code : null);

  if (gcCode) {
    _gcfCtxMenuEl.appendChild(_gcfMenuItem('Open on geocaching.com', function() {
      window.open('https://coord.info/' + gcCode, '_blank');
    }));
  }
  if (ocCode) {
    _gcfCtxMenuEl.appendChild(_gcfMenuItem('Open on opencaching.de', function() {
      window.open('https://www.opencaching.de/viewcache.php?wp=' + ocCode, '_blank');
    }));
  }

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Set as center point', function() {
    _gcfSaveCacheAsLocation(code, markerData ? markerData.n : code, mLat, mLon);
  }));

  // --- Separator ---
  _gcfCtxMenuEl.appendChild(_gcfMenuSeparator());

  // --- Map items ---
  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Copy coordinates', function() {
    _gcfCopyText(mLat.toFixed(6) + ', ' + mLon.toFixed(6));
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Range circle\u2026', function() {
    _gcfPromptRangeCircle(mLon, mLat);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Nearest cache', function() {
    _gcfFindNearest(code, mLat, mLon);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Search nearby', function() {
    _gcfReverseGeocode(mLat, mLon);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Street View', function() {
    _gcfOpenStreetView(mLat, mLon);
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Zoom in', function() {
    gcfMap.easeTo({ center: [mLon, mLat], zoom: gcfMap.getZoom() + 1 });
  }));

  _gcfCtxMenuEl.appendChild(_gcfMenuItem('Zoom out', function() {
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
  var input = window.prompt('Radius in km (use "." as decimal separator):', '2');
  if (input === null) return;
  input = input.trim();
  var km = parseFloat(input);
  if (isNaN(km) || km <= 0) {
    _gcfFlashMessage('Invalid radius: "' + input + '". Use a number like 2 or 0.5 (dot as decimal separator).');
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
      item.title = 'Remove this circle';
      item.onclick = function() { _gcfRemoveRangeCircleByIndex(idx); };
      _gcfRangeCircleMenuEl.appendChild(item);
    })(i);
  }

  var removeAll = document.createElement('button');
  removeAll.className = 'map-range-circle-item map-range-remove-all';
  removeAll.textContent = 'Remove all';
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
      var name = data.display_name || 'No results';
      new maplibregl.Popup({ maxWidth: '300px' })
        .setLngLat([lng, lat])
        .setHTML('<div style="font-size:0.8rem">' +
          '<strong>Nearby</strong><br>' +
          _gcfEsc(name) + '</div>')
        .addTo(gcfMap);
    })
    .catch(function() {
      new maplibregl.Popup({ maxWidth: '300px' })
        .setLngLat([lng, lat])
        .setHTML('<div style="font-size:0.8rem">Reverse geocode failed</div>')
        .addTo(gcfMap);
    });
}

// ── Find nearest cache (from a specific cache marker) ─────────────────────────

function _gcfFindNearest(code, lat, lon) {
  if (!_gcfMarkersData || _gcfMarkersData.length < 2) {
    _gcfFlashMessage('Not enough caches loaded to find nearest.');
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
    _gcfFlashMessage('No other caches found.');
    return;
  }

  new maplibregl.Popup({ maxWidth: '300px' })
    .setLngLat([lon, lat])
    .setHTML('<div style="font-size:0.8rem">' +
      '<strong>Nearest cache</strong><br>' +
      '<a href="/' + _gcfEsc(bestCache.c) + '/" style="color:inherit">' +
      _gcfEsc(bestCache.n) + '</a> (' + _gcfEsc(bestCache.c) + ')<br>' +
      _gcfFmtDist(bestDist) + '</div>')
    .addTo(gcfMap);
}

// ── Find nearest cache (from an arbitrary map point) ──────────────────────────

function _gcfFindNearestPoint(lat, lon) {
  if (!_gcfMarkersData || _gcfMarkersData.length === 0) {
    _gcfFlashMessage('No caches loaded.');
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
    _gcfFlashMessage('No caches found.');
    return;
  }

  new maplibregl.Popup({ maxWidth: '300px' })
    .setLngLat([lon, lat])
    .setHTML('<div style="font-size:0.8rem">' +
      '<strong>Nearest cache</strong><br>' +
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
    _gcfFlashMessage('CSRF token not found.');
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
        _gcfFlashMessage('Failed to save location: ' + (data.error || ''));
      }
    })
    .catch(function() {
      _gcfFlashMessage('Failed to save location');
    });
}

function _gcfSaveCacheAsLocation(code, cacheName, lat, lon) {
  _gcfSaveLocation(cacheName, lat, lon, 'From cache ' + code);
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
      if (!name) name = 'Mapped location';
      _gcfSaveLocation(name, lat, lng);
    })
    .catch(function() {
      _gcfSaveLocation('Mapped location', lat, lng);
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
