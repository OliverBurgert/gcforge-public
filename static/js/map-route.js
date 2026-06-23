// ── GCForge Map Route Planner ────────────────────────────────────────────────
//
// Assemble an ordered list of waypoints (saved locations, geocoded addresses,
// or right-click "Add to route" from the map / a cache), then:
//   • "Route"      → POST /map/route/ → BRouter road geometry → corridor region
//                    (reuses gcfAddCorridorRegion from map-draw.js, so the route
//                    becomes a corridor filter: "caches along route").
//   • "Export GPX" → download the routed track from BRouter (via our backend).
//   • "Fine-tune…" → open brouter-web pre-loaded with the waypoints + nearby
//                    caches as POIs; the user fine-tunes there, exports GPX, and
//                    re-imports it via the existing GPX-track importer.
//
// Depends on: cache-map.js (gcfMap, _gcfMarkersData), map-draw.js
//             (gcfAddCorridorRegion), map-search.js (_gcfParseCoordinates),
//             MapLibre GL JS (maplibregl). Loaded after those in map-layout.js.

var _gcfRouteWaypoints = [];   // [{lat, lon, label, kind, code}]
var _gcfRouteMarkers = [];     // MapLibre markers, parallel to _gcfRouteWaypoints
var _gcfRouteRegion = null;    // corridor region for the current route (so re-route replaces it)
var _gcfRouteAddrTimer = null;
var _gcfRouteBrouterWebBase = 'https://brouter.de/brouter-web/#';
var _gcfRouteMaxPois = 50;     // cap caches passed to brouter-web (URL length)
var _gcfSavedRoutes = [];      // [{id, name, waypoints, profile, width_m, path}]
var _gcfRouteCurrentName = null; // name of the loaded/last-saved route (prefills Save)
var _gcfRouteTableReversed = false;       // itinerary direction (false = outbound)
var _gcfRouteTableIncludeCaches = false;  // include in-corridor caches in the table

// Local equirectangular metre constants (same model as map-draw.js corridor math).
var _GCF_M_PER_DEG_LAT = 110540;
var _GCF_M_PER_DEG_LNG = 111320;

// Default corridor half-width (km) per travel mode.
function _gcfRouteDefaultWidthKm(profile) {
  if (profile === 'car-fast') return 1.0;       // driving
  if (profile === 'hiking-beta') return 0.2;    // walking
  return 0.5;                                    // cycling (trekking / fast / shortest)
}

function gcfRouteInit() {
  _gcfRouteBuildLocationSelect();

  // Restore last-used profile (client-only preference); width follows the mode.
  var profileSel = document.getElementById('route-profile');
  if (profileSel) {
    var saved = localStorage.getItem('gcforge_route_profile');
    if (saved) profileSel.value = saved;
    profileSel.addEventListener('change', function() {
      localStorage.setItem('gcforge_route_profile', profileSel.value);
      _gcfRouteApplyDefaultWidth();
    });
  }
  _gcfRouteApplyDefaultWidth();
  _gcfRouteLoadSavedList();

  // Address / coordinate input — debounced, mirrors map-search.js.
  var addr = document.getElementById('route-address-input');
  if (addr) {
    addr.addEventListener('input', function() {
      var val = addr.value.trim();
      _gcfRouteClearAddrResults();
      if (!val) return;
      if (typeof _gcfParseCoordinates === 'function') {
        var coords = _gcfParseCoordinates(val);
        if (coords) {
          _gcfRouteShowAddrResults([{ lat: coords.lat, lon: coords.lon, label: val }]);
          return;
        }
      }
      if (_gcfRouteAddrTimer) clearTimeout(_gcfRouteAddrTimer);
      _gcfRouteAddrTimer = setTimeout(function() { _gcfRouteGeocode(val); }, 800);
    });
  }

  _gcfRouteRender();
}

// ── Panel toggle ──────────────────────────────────────────────────────────────

function gcfToggleRoutePanel() {
  var panel = document.getElementById('map-route-panel');
  var btn = document.getElementById('map-route-btn');
  if (!panel) return;
  var open = panel.style.display === 'none' || !panel.style.display;
  panel.style.display = open ? 'block' : 'none';
  if (btn) btn.classList.toggle('active', open);
  if (open) _gcfRouteBuildLocationSelect();
}

// ── Waypoint assembly ───────────────────────────────────────────────────────

// Public entry point — called from the map context menu ("Add to route").
function gcfRouteAddWaypoint(lat, lon, label, kind, code) {
  _gcfRouteWaypoints.push({
    lat: lat, lon: lon,
    label: label || (lat.toFixed(5) + ', ' + lon.toFixed(5)),
    kind: kind || 'point',
    code: code || null,
  });
  // Make sure the panel is visible so the user sees what they added.
  var panel = document.getElementById('map-route-panel');
  if (panel && (panel.style.display === 'none' || !panel.style.display)) {
    gcfToggleRoutePanel();
  }
  _gcfRouteRender();
}

function gcfRouteAddLocation(sel) {
  if (!sel || !sel.value) return;
  var loc = (typeof _gcfLocations !== 'undefined' && _gcfLocations)
    ? _gcfLocations.find(function(l) { return String(l.id) === String(sel.value); })
    : null;
  if (loc) gcfRouteAddWaypoint(loc.lat, loc.lon, loc.name, 'location');
  sel.value = '';
}

function gcfRouteRemoveWaypoint(i) {
  if (i < 0 || i >= _gcfRouteWaypoints.length) return;
  _gcfRouteWaypoints.splice(i, 1);
  _gcfRouteRender();
}

function gcfRouteMoveWaypoint(i, dir) {
  var j = i + dir;
  if (i < 0 || i >= _gcfRouteWaypoints.length || j < 0 || j >= _gcfRouteWaypoints.length) return;
  var tmp = _gcfRouteWaypoints[i];
  _gcfRouteWaypoints[i] = _gcfRouteWaypoints[j];
  _gcfRouteWaypoints[j] = tmp;
  _gcfRouteRender();
}

function gcfRouteClear() {
  _gcfRouteWaypoints = [];
  if (typeof gcfRemoveCorridorRegion === 'function' && _gcfRouteRegion) {
    gcfRemoveCorridorRegion(_gcfRouteRegion);
  }
  _gcfRouteRegion = null;
  _gcfRouteCurrentName = null;
  var savedSel = document.getElementById('route-saved-select');
  if (savedSel) savedSel.value = '';
  var summary = document.getElementById('route-summary');
  if (summary) summary.textContent = '';
  _gcfRouteRender();
}

// ── Saved routes (persist / load / delete) ────────────────────────────────────

function _gcfRouteCsrf() {
  var el = document.querySelector('[name=csrfmiddlewaretoken]');
  return el ? el.value : '';
}

function _gcfRouteLoadSavedList() {
  fetch('/map/routes/')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      _gcfSavedRoutes = data.routes || [];
      _gcfRouteRenderSavedSelect();
    })
    .catch(function() {});
}

function _gcfRouteRenderSavedSelect() {
  var sel = document.getElementById('route-saved-select');
  if (!sel) return;
  while (sel.options.length > 1) sel.remove(1);
  _gcfSavedRoutes.forEach(function(rt) {
    var opt = document.createElement('option');
    opt.value = rt.id;
    opt.textContent = rt.name;
    sel.appendChild(opt);
  });
  // Keep the current route selected if it still exists.
  if (_gcfRouteCurrentName) {
    var match = _gcfSavedRoutes.find(function(rt) { return rt.name === _gcfRouteCurrentName; });
    sel.value = match ? String(match.id) : '';
  } else {
    sel.value = '';
  }
}

function gcfRouteSave() {
  if (!_gcfRouteWaypoints.length) {
    _gcfRouteFlash(gettext('Add at least one waypoint first.'));
    return;
  }
  var name = prompt(gettext('Save route as:'), _gcfRouteCurrentName || '');
  if (!name || !name.trim()) return;
  name = name.trim();

  fetch('/map/routes/save/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _gcfRouteCsrf() },
    body: JSON.stringify({
      name: name,
      waypoints: _gcfRouteWaypoints,
      profile: _gcfRouteProfile(),
      width_m: _gcfRouteWidthM(),
      path: _gcfRouteRegion ? _gcfRouteRegion.path : [],
    }),
  })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(res) {
      if (!res.ok) {
        _gcfRouteFlash(interpolate(gettext('Save failed: %s'), [res.data.error || '']));
        return;
      }
      _gcfRouteCurrentName = name;
      _gcfRouteLoadSavedList();
    })
    .catch(function() { _gcfRouteFlash(gettext('Save failed.')); });
}

function gcfRouteLoadSaved(sel) {
  if (!sel || !sel.value) return;
  var rt = _gcfSavedRoutes.find(function(r) { return String(r.id) === String(sel.value); });
  if (!rt) return;

  _gcfRouteWaypoints = (rt.waypoints || []).map(function(w) {
    return { lat: w.lat, lon: w.lon, label: w.label, kind: w.kind, code: w.code || null };
  });
  _gcfRouteCurrentName = rt.name;

  var profileSel = document.getElementById('route-profile');
  if (profileSel && rt.profile) profileSel.value = rt.profile;
  var widthInput = document.getElementById('route-width');
  if (widthInput && rt.width_m) widthInput.value = (rt.width_m / 1000).toFixed(1);

  // Replace any existing route corridor; redraw from the saved geometry if present.
  if (typeof gcfRemoveCorridorRegion === 'function' && _gcfRouteRegion) {
    gcfRemoveCorridorRegion(_gcfRouteRegion);
  }
  _gcfRouteRegion = null;
  if (rt.path && rt.path.length >= 2 && typeof gcfAddCorridorRegion === 'function') {
    _gcfRouteRegion = gcfAddCorridorRegion(rt.path, rt.width_m || _gcfRouteWidthM());
  } else if (gcfMap && _gcfRouteWaypoints.length) {
    var lats = _gcfRouteWaypoints.map(function(w) { return w.lat; });
    var lngs = _gcfRouteWaypoints.map(function(w) { return w.lon; });
    gcfMap.fitBounds(
      [[Math.min.apply(null, lngs), Math.min.apply(null, lats)],
       [Math.max.apply(null, lngs), Math.max.apply(null, lats)]],
      { padding: 40, maxZoom: 14 }
    );
  }
  var summary = document.getElementById('route-summary');
  if (summary) summary.textContent = '';
  _gcfRouteRender();
}

function gcfRouteDeleteSaved() {
  var sel = document.getElementById('route-saved-select');
  if (!sel || !sel.value) {
    _gcfRouteFlash(gettext('Select a saved route to delete.'));
    return;
  }
  var rt = _gcfSavedRoutes.find(function(r) { return String(r.id) === String(sel.value); });
  if (!rt) return;
  if (!confirm(interpolate(gettext('Delete saved route "%s"?'), [rt.name]))) return;

  fetch('/map/routes/' + rt.id + '/delete/', {
    method: 'DELETE',
    headers: { 'X-CSRFToken': _gcfRouteCsrf() },
  })
    .then(function() {
      if (_gcfRouteCurrentName === rt.name) _gcfRouteCurrentName = null;
      _gcfRouteLoadSavedList();
    })
    .catch(function() { _gcfRouteFlash(gettext('Delete failed.')); });
}

// ── Rendering ─────────────────────────────────────────────────────────────────

function _gcfRouteRender() {
  var list = document.getElementById('route-waypoint-list');
  var hint = document.getElementById('route-empty-hint');
  if (hint) hint.style.display = _gcfRouteWaypoints.length ? 'none' : 'block';
  if (list) {
    list.innerHTML = '';
    _gcfRouteWaypoints.forEach(function(w, i) {
      var li = document.createElement('li');
      li.className = 'map-route-item';

      var label = document.createElement('span');
      label.className = 'map-route-label';
      label.textContent = w.code ? (w.code + ' · ' + w.label) : w.label;
      label.title = label.textContent;
      li.appendChild(label);

      var ctrl = document.createElement('span');
      ctrl.className = 'map-route-item-ctrl';
      ctrl.appendChild(_gcfRouteBtn('↑', gettext('Move up'), function() { gcfRouteMoveWaypoint(i, -1); }));
      ctrl.appendChild(_gcfRouteBtn('↓', gettext('Move down'), function() { gcfRouteMoveWaypoint(i, 1); }));
      ctrl.appendChild(_gcfRouteBtn('×', gettext('Remove'), function() { gcfRouteRemoveWaypoint(i); }));
      li.appendChild(ctrl);

      list.appendChild(li);
    });
  }
  _gcfRouteRenderMarkers();
}

function _gcfRouteBtn(text, title, handler) {
  var b = document.createElement('button');
  b.type = 'button';
  b.className = 'map-route-mini-btn';
  b.textContent = text;
  b.title = title;
  b.onclick = handler;
  return b;
}

function _gcfRouteRenderMarkers() {
  _gcfRouteMarkers.forEach(function(m) { m.remove(); });
  _gcfRouteMarkers = [];
  if (!gcfMap || typeof maplibregl === 'undefined') return;

  var n = _gcfRouteWaypoints.length;
  _gcfRouteWaypoints.forEach(function(w, i) {
    var el = document.createElement('div');
    el.className = 'map-route-pin';
    el.textContent = String(i + 1);
    // Start green, end red, intermediate blue.
    el.style.background = (i === 0) ? '#198754' : (i === n - 1 ? '#dc3545' : '#0d6efd');
    var marker = new maplibregl.Marker({ element: el })
      .setLngLat([w.lon, w.lat])
      .addTo(gcfMap);
    _gcfRouteMarkers.push(marker);
  });
}

// ── Address geocoding (Nominatim, browser-direct — same as map-search.js) ──────

function _gcfRouteGeocode(query) {
  var params = new URLSearchParams({ q: query, format: 'json', limit: 5 });
  fetch('https://nominatim.openstreetmap.org/search?' + params)
    .then(function(r) { return r.json(); })
    .then(function(results) {
      _gcfRouteShowAddrResults((results || []).map(function(r) {
        return { lat: parseFloat(r.lat), lon: parseFloat(r.lon), label: r.display_name };
      }));
    })
    .catch(function() {});
}

function _gcfRouteShowAddrResults(results) {
  var box = document.getElementById('route-address-results');
  if (!box) return;
  box.innerHTML = '';
  if (!results.length) { box.style.display = 'none'; return; }
  results.forEach(function(r) {
    var item = document.createElement('button');
    item.type = 'button';
    item.className = 'map-search-result-item';
    item.textContent = r.label;
    item.title = r.label;
    item.onclick = function() {
      gcfRouteAddWaypoint(r.lat, r.lon, r.label.split(',')[0], 'address');
      var addr = document.getElementById('route-address-input');
      if (addr) addr.value = '';
      _gcfRouteClearAddrResults();
    };
    box.appendChild(item);
  });
  box.style.display = 'block';
}

function _gcfRouteClearAddrResults() {
  var box = document.getElementById('route-address-results');
  if (box) { box.innerHTML = ''; box.style.display = 'none'; }
}

// ── Compute route (BRouter via backend) ───────────────────────────────────────

function _gcfRouteProfile() {
  var sel = document.getElementById('route-profile');
  return (sel && sel.value) ? sel.value : 'hiking-beta';
}

function _gcfRouteWidthM() {
  var input = document.getElementById('route-width');
  var km = input ? parseFloat(input.value) : 1;
  if (!km || km <= 0) km = 1;
  return Math.round(km * 1000);
}

function _gcfRouteApplyDefaultWidth() {
  var input = document.getElementById('route-width');
  if (input) input.value = _gcfRouteDefaultWidthKm(_gcfRouteProfile()).toFixed(1);
}

function _gcfRouteLonLats() {
  return _gcfRouteWaypoints.map(function(w) { return [w.lon, w.lat]; });
}

function gcfRouteCompute() {
  if (_gcfRouteWaypoints.length < 2) {
    _gcfRouteFlash(gettext('Add at least two waypoints first.'));
    return;
  }
  var summary = document.getElementById('route-summary');
  if (summary) summary.textContent = gettext('Routing…');

  var csrf = document.querySelector('[name=csrfmiddlewaretoken]');
  fetch('/map/route/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrf ? csrf.value : '',
    },
    body: JSON.stringify({ lonlats: _gcfRouteLonLats(), profile: _gcfRouteProfile() }),
  })
    .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
    .then(function(res) {
      if (!res.ok || !res.data.path) {
        if (summary) summary.textContent = '';
        _gcfRouteFlash(interpolate(gettext('Routing failed: %s'), [res.data.error || '']));
        return;
      }
      localStorage.setItem('gcforge_route_profile', _gcfRouteProfile());
      // Replace the previous route's corridor instead of stacking a new one.
      if (typeof gcfRemoveCorridorRegion === 'function' && _gcfRouteRegion) {
        gcfRemoveCorridorRegion(_gcfRouteRegion);
        _gcfRouteRegion = null;
      }
      if (typeof gcfAddCorridorRegion === 'function') {
        _gcfRouteRegion = gcfAddCorridorRegion(res.data.path, _gcfRouteWidthM());
      }
      _gcfRouteShowSummary(res.data);
    })
    .catch(function() {
      if (summary) summary.textContent = '';
      _gcfRouteFlash(gettext('Routing failed.'));
    });
}

function _gcfRouteShowSummary(data) {
  var summary = document.getElementById('route-summary');
  if (!summary) return;
  var parts = [];
  if (data.distance_m != null) {
    parts.push(interpolate(gettext('%s km'), [(data.distance_m / 1000).toFixed(1)]));
  }
  if (data.duration_s != null) {
    var h = Math.floor(data.duration_s / 3600);
    var m = Math.round((data.duration_s % 3600) / 60);
    parts.push(h ? (h + ' h ' + m + ' min') : (m + ' min'));
  }
  if (data.ascend_m != null) {
    parts.push(interpolate(gettext('↑ %s m'), [data.ascend_m]));
  }
  summary.textContent = parts.join(' · ');
}

// ── Export GPX (BRouter via backend, as a file download) ──────────────────────

function gcfRouteExportGpx() {
  if (_gcfRouteWaypoints.length < 2) {
    _gcfRouteFlash(gettext('Add at least two waypoints first.'));
    return;
  }
  var pts = _gcfRouteWaypoints.map(function(w) {
    return w.lon.toFixed(6) + ',' + w.lat.toFixed(6);
  }).join('|');
  var params = new URLSearchParams({ lonlats: pts, profile: _gcfRouteProfile(), format: 'gpx' });
  window.location.href = '/map/route/?' + params.toString();
}

// ── Open in brouter-web for fine-tuning ───────────────────────────────────────

function gcfRouteOpenBrouterWeb() {
  var pts = _gcfRouteWaypoints.map(function(w) {
    return w.lon.toFixed(6) + ',' + w.lat.toFixed(6);
  }).join(';');

  // Pass currently visible caches as POIs (capped) so the user can route
  // through them in brouter-web's full UI. Names use the cache code (compact).
  var pois = '';
  if (typeof _gcfMarkersData !== 'undefined' && _gcfMarkersData) {
    pois = _gcfMarkersData.slice(0, _gcfRouteMaxPois).map(function(m) {
      return m.lo.toFixed(6) + ',' + m.la.toFixed(6) + ',' + encodeURIComponent(m.c || '');
    }).join(';');
  }

  var c = gcfMap ? gcfMap.getCenter() : { lat: 0, lng: 0 };
  var z = gcfMap ? Math.round(gcfMap.getZoom()) : 12;
  var hash = 'map=' + z + '/' + c.lat.toFixed(5) + '/' + c.lng.toFixed(5) + '/standard';
  if (pts) hash += '&lonlats=' + pts;
  if (pois) hash += '&pois=' + pois;
  hash += '&profile=' + encodeURIComponent(_gcfRouteProfile());

  window.open(_gcfRouteBrouterWebBase + hash, '_blank');
}

// ── Itinerary table (follows the route) ───────────────────────────────────────

// Cumulative along-path distance (metres) to each vertex of a [[lon,lat],…] path.
function _gcfRouteCumulative(path) {
  var cum = [0];
  for (var i = 1; i < path.length; i++) {
    cum.push(cum[i - 1] + _gcfRouteSegMetres(path[i - 1], path[i]));
  }
  return cum;
}

function _gcfRouteSegMetres(a, b) {
  var latMid = (a[1] + b[1]) / 2;
  var dx = (b[0] - a[0]) * _GCF_M_PER_DEG_LNG * Math.cos(latMid * Math.PI / 180);
  var dy = (b[1] - a[1]) * _GCF_M_PER_DEG_LAT;
  return Math.sqrt(dx * dx + dy * dy);
}

// Project [lat,lon] onto the path; return {along_m, offset_m} — distance along
// the route to the nearest point, and the perpendicular distance to the route.
function _gcfRouteProject(lat, lon, path, cum) {
  var best = { along_m: 0, offset_m: Infinity };
  for (var i = 0; i < path.length - 1; i++) {
    var a = path[i], b = path[i + 1];
    var kx = _GCF_M_PER_DEG_LNG * Math.cos(((a[1] + b[1]) / 2) * Math.PI / 180);
    var ky = _GCF_M_PER_DEG_LAT;
    var ax = a[0] * kx, ay = a[1] * ky;
    var bx = b[0] * kx, by = b[1] * ky;
    var px = lon * kx, py = lat * ky;
    var vx = bx - ax, vy = by - ay;
    var segLen2 = vx * vx + vy * vy;
    var t = segLen2 > 0 ? ((px - ax) * vx + (py - ay) * vy) / segLen2 : 0;
    if (t < 0) t = 0; else if (t > 1) t = 1;
    var dx = px - (ax + t * vx), dy = py - (ay + t * vy);
    var off = Math.sqrt(dx * dx + dy * dy);
    if (off < best.offset_m) {
      best.offset_m = off;
      best.along_m = cum[i] + t * Math.sqrt(segLen2);
    }
  }
  return best;
}

// Build the ordered itinerary: every waypoint, plus (optionally) every cache
// within the corridor, sorted by distance from the start. Reversing flips the
// "from start" distances (total − along) for the way home.
function _gcfRouteBuildItinerary(includeCaches) {
  var path = _gcfRouteRegion ? _gcfRouteRegion.path : null;
  if (!path || path.length < 2) return null;
  var cum = _gcfRouteCumulative(path);
  var total = cum[cum.length - 1];
  var widthM = _gcfRouteRegion.width_m;
  var rows = [];

  _gcfRouteWaypoints.forEach(function(w) {
    var p = _gcfRouteProject(w.lat, w.lon, path, cum);
    rows.push({ waypoint: true, label: w.label, code: w.code || null, type: null,
                along_m: p.along_m, offset_m: p.offset_m });
  });

  if (includeCaches && typeof _gcfMarkersData !== 'undefined' && _gcfMarkersData) {
    var wpCodes = {};
    _gcfRouteWaypoints.forEach(function(w) { if (w.code) wpCodes[w.code] = true; });
    _gcfMarkersData.forEach(function(m) {
      if (m.c && wpCodes[m.c]) return;  // already listed as a waypoint
      var p = _gcfRouteProject(m.la, m.lo, path, cum);
      if (p.offset_m <= widthM) {
        rows.push({ waypoint: false, label: m.n, code: m.c, type: m.t,
                    along_m: p.along_m, offset_m: p.offset_m });
      }
    });
  }

  if (_gcfRouteTableReversed) rows.forEach(function(r) { r.along_m = total - r.along_m; });
  rows.sort(function(a, b) { return a.along_m - b.along_m; });
  return { rows: rows, total_m: total };
}

function gcfRouteOpenTable() {
  if (!_gcfRouteRegion || !_gcfRouteRegion.path || _gcfRouteRegion.path.length < 2) {
    _gcfRouteFlash(gettext('Compute the route first.'));
    return;
  }
  var checkbox = document.getElementById('route-table-caches');
  if (checkbox) checkbox.checked = _gcfRouteTableIncludeCaches;
  gcfRouteTableRender();
  var el = document.getElementById('routeTableDialog');
  if (el && window.bootstrap) bootstrap.Modal.getOrCreateInstance(el).show();
}

function gcfRouteTableToggleReverse() {
  _gcfRouteTableReversed = !_gcfRouteTableReversed;
  gcfRouteTableRender();
}

function gcfRouteTableRender() {
  var checkbox = document.getElementById('route-table-caches');
  _gcfRouteTableIncludeCaches = checkbox ? checkbox.checked : false;

  var data = _gcfRouteBuildItinerary(_gcfRouteTableIncludeCaches);
  var container = document.getElementById('route-table-container');
  if (!container) return;
  container.innerHTML = '';
  if (!data) return;

  var summary = document.getElementById('route-table-summary');
  if (summary) {
    summary.textContent = interpolate(
      gettext('%(stops)s stops · %(dist)s total'),
      { stops: data.rows.length, dist: _gcfFmtDist(data.total_m) }, true,
    );
  }

  var table = document.createElement('table');
  table.className = 'table table-sm route-table mb-0';
  var thead = document.createElement('thead');
  thead.innerHTML = '<tr><th>#</th><th></th><th>' + gettext('Name') + '</th>' +
    '<th class="text-end">' + gettext('From start') + '</th>' +
    '<th class="text-end">' + gettext('Off-route') + '</th></tr>';
  table.appendChild(thead);

  var tbody = document.createElement('tbody');
  data.rows.forEach(function(r, i) {
    var tr = document.createElement('tr');
    if (r.waypoint) tr.className = 'route-table-wp';

    tr.appendChild(_gcfRouteCell(String(i + 1)));

    var marker = document.createElement('td');
    marker.textContent = r.waypoint ? '★' : (r.type || '');  // ★ for waypoints
    marker.className = 'route-table-marker';
    tr.appendChild(marker);

    var nameTd = document.createElement('td');
    if (r.code) {
      var a = document.createElement('a');
      a.href = '/' + r.code + '/';
      a.target = '_blank';
      a.textContent = r.label || r.code;
      nameTd.appendChild(a);
    } else {
      nameTd.textContent = r.label || '';
    }
    tr.appendChild(nameTd);

    tr.appendChild(_gcfRouteCell(_gcfFmtDist(r.along_m), 'text-end'));
    tr.appendChild(_gcfRouteCell(r.waypoint ? '—' : _gcfFmtDist(r.offset_m), 'text-end'));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  container.appendChild(table);
}

function _gcfRouteCell(text, cls) {
  var td = document.createElement('td');
  td.textContent = text;
  if (cls) td.className = cls;
  return td;
}

// Build the itinerary as a header + data matrix (strings), honouring the
// current "include caches" choice and reverse direction — the shared source
// for both the clipboard copy and the CSV download.
function _gcfRouteItineraryMatrix() {
  var data = _gcfRouteBuildItinerary(_gcfRouteTableIncludeCaches);
  if (!data) return null;
  var matrix = [[
    '#', gettext('Type'), gettext('Name'), gettext('Code'),
    gettext('From start (km)'), gettext('Off-route (m)'),
  ]];
  data.rows.forEach(function(r, i) {
    matrix.push([
      String(i + 1),
      r.waypoint ? gettext('Waypoint') : (r.type || gettext('Cache')),
      r.label || '',
      r.code || '',
      (r.along_m / 1000).toFixed(2),
      r.waypoint ? '' : String(Math.round(r.offset_m)),
    ]);
  });
  return matrix;
}

function _gcfCsvEscape(v) {
  v = String(v);
  return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
}

function _gcfRouteToCsv(matrix) {
  return matrix.map(function(row) { return row.map(_gcfCsvEscape).join(','); }).join('\r\n');
}

function _gcfRouteToTsv(matrix) {
  // Tab-separated pastes straight into spreadsheet columns; strip any
  // tab/newline from cells so the grid stays intact.
  return matrix.map(function(row) {
    return row.map(function(v) { return String(v).replace(/[\t\n\r]+/g, ' '); }).join('\t');
  }).join('\n');
}

function gcfRouteTableCopy() {
  var matrix = _gcfRouteItineraryMatrix();
  if (!matrix) return;
  var text = _gcfRouteToTsv(matrix);
  if (typeof _gcfCopyText === 'function') {
    _gcfCopyText(text);
  } else if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).catch(function() {});
  }
  _gcfRouteFlash(gettext('Itinerary copied to clipboard.'));
}

function gcfRouteTableDownload() {
  var matrix = _gcfRouteItineraryMatrix();
  if (!matrix) return;
  // Prepend a UTF-8 BOM so Excel reads umlauts/accents correctly.
  var csv = '﻿' + _gcfRouteToCsv(matrix);
  var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = (_gcfRouteCurrentName
    ? _gcfRouteCurrentName.replace(/[^\w\-]+/g, '_') : 'route') + '.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _gcfRouteBuildLocationSelect() {
  var sel = document.getElementById('route-location-select');
  if (!sel || typeof _gcfLocations === 'undefined' || !_gcfLocations) return;
  // Keep the placeholder (first option), rebuild the rest.
  while (sel.options.length > 1) sel.remove(1);
  _gcfLocations.forEach(function(l) {
    var opt = document.createElement('option');
    opt.value = l.id;
    opt.textContent = l.home ? ('★ ' + l.name) : l.name;
    sel.appendChild(opt);
  });
}

function _gcfRouteFlash(text) {
  if (typeof _gcfFlashMessage === 'function') { _gcfFlashMessage(text); return; }
  var summary = document.getElementById('route-summary');
  if (summary) summary.textContent = text;
}
