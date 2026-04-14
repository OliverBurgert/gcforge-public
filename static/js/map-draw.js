// ── GCForge Map Draw Tools ────────────────────────────────────────────────────
//
// Depends on: cache-map.js (gcfMap, _gcfMarkersData), MapboxDraw (CDN)
// Loaded after MapboxDraw and cache-map.js.

// ── Geometry helpers ──────────────────────────────────────────────────────────

function _gcfRectCoords(start, end) {
  // start/end are [lng, lat]; returns closed polygon ring
  return [
    [start[0], start[1]],
    [end[0],   start[1]],
    [end[0],   end[1]],
    [start[0], end[1]],
    [start[0], start[1]]
  ];
}

function _gcfCircleCoords(centerLng, centerLat, radius_m) {
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

function _gcfHaversineM(lat1, lon1, lat2, lon2) {
  var R = 6371000;
  var dLat = (lat2 - lat1) * Math.PI / 180;
  var dLon = (lon2 - lon1) * Math.PI / 180;
  var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
          Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
          Math.sin(dLon / 2) * Math.sin(dLon / 2);
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function _gcfPointInPolygon(lat, lon, ring) {
  // Ray-casting. ring is [[lng, lat], ...] closed.
  var inside = false;
  var n = ring.length - 1;
  for (var i = 0, j = n - 1; i < n; j = i++) {
    var xi = ring[i][0], yi = ring[i][1];
    var xj = ring[j][0], yj = ring[j][1];
    if (((yi > lat) !== (yj > lat)) && (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi)) {
      inside = !inside;
    }
  }
  return inside;
}

function _gcfDistToSegmentM(lat, lon, lat1, lon1, lat2, lon2) {
  // Minimum distance in metres from point to segment (flat projection for t).
  var dx = lon2 - lon1, dy = lat2 - lat1;
  var len2 = dx * dx + dy * dy;
  if (len2 === 0) return _gcfHaversineM(lat, lon, lat1, lon1);
  var t = Math.max(0, Math.min(1, ((lon - lon1) * dx + (lat - lat1) * dy) / len2));
  return _gcfHaversineM(lat, lon, lat1 + t * dy, lon1 + t * dx);
}

function _gcfPerpendicularOffset(lng, lat, dlng, dlat, dist_m) {
  // Returns [leftPoint, rightPoint] each offset dist_m perpendicular to (dlng, dlat).
  var cosLat = Math.cos(lat * Math.PI / 180);
  var dxm = dlng * 111320 * cosLat;
  var dym = dlat * 110540;
  var len = Math.sqrt(dxm * dxm + dym * dym);
  if (len < 1e-10) return [[lng, lat], [lng, lat]];
  // Left perpendicular in metres: rotate 90° CCW
  var pxm = -dym / len * dist_m;
  var pym =  dxm / len * dist_m;
  var leftLng  = lng + pxm / (111320 * cosLat);
  var leftLat  = lat + pym / 110540;
  var rightLng = lng - pxm / (111320 * cosLat);
  var rightLat = lat - pym / 110540;
  return [[leftLng, leftLat], [rightLng, rightLat]];
}

function _gcfSemicircle(lng, lat, startAngle, dist_m, steps) {
  // Returns arc points sweeping 180° clockwise from startAngle.
  // startAngle: angle in radians where the arc begins.
  var cosLat = Math.cos(lat * Math.PI / 180);
  var pts = [];
  for (var i = 0; i <= steps; i++) {
    var a = startAngle - Math.PI * i / steps; // clockwise sweep
    pts.push([lng + Math.cos(a) * dist_m / (111320 * cosLat),
              lat + Math.sin(a) * dist_m / 110540]);
  }
  return pts;
}

function _gcfCorridorBuffer(path, width_m, steps) {
  // Compute a buffered polygon around a polyline with rounded end caps.
  // path: [[lng, lat], ...], width_m: half-width, steps: arc resolution (default 8).
  if (!steps) steps = 16;
  var n = path.length;
  if (n < 2) return null;

  var leftSide = [], rightSide = [];

  for (var i = 0; i < n; i++) {
    var lng = path[i][0], lat = path[i][1];
    var dlng, dlat;

    if (i === 0) {
      dlng = path[1][0] - path[0][0];
      dlat = path[1][1] - path[0][1];
    } else if (i === n - 1) {
      dlng = path[n - 1][0] - path[n - 2][0];
      dlat = path[n - 1][1] - path[n - 2][1];
    } else {
      // Average normalised direction of adjacent segments
      var cosLat = Math.cos(lat * Math.PI / 180);
      var d1lng = path[i][0] - path[i-1][0], d1lat = path[i][1] - path[i-1][1];
      var d2lng = path[i+1][0] - path[i][0], d2lat = path[i+1][1] - path[i][1];
      var l1 = Math.sqrt((d1lng*cosLat)*(d1lng*cosLat) + d1lat*d1lat);
      var l2 = Math.sqrt((d2lng*cosLat)*(d2lng*cosLat) + d2lat*d2lat);
      dlng = (l1 > 0 ? d1lng/l1 : 0) + (l2 > 0 ? d2lng/l2 : 0);
      dlat = (l1 > 0 ? d1lat/l1 : 0) + (l2 > 0 ? d2lat/l2 : 0);
    }

    var offsets = _gcfPerpendicularOffset(lng, lat, dlng, dlat, width_m);
    leftSide.push(offsets[0]);
    rightSide.push(offsets[1]);
  }

  // Build ring: start cap → left side → end cap → right side reversed.
  // Both caps sweep clockwise; start cap begins at right-side angle, end cap at left-side angle.
  var p0 = path[0], d0lng = path[1][0]-path[0][0], d0lat = path[1][1]-path[0][1];
  var pN = path[n-1], dNlng = path[n-1][0]-path[n-2][0], dNlat = path[n-1][1]-path[n-2][1];

  var c0 = Math.cos(p0[1] * Math.PI / 180);
  var d0angle = Math.atan2(d0lat * 110540, d0lng * 111320 * c0);
  var cN = Math.cos(pN[1] * Math.PI / 180);
  var dNangle = Math.atan2(dNlat * 110540, dNlng * 111320 * cN);

  // Start cap: clockwise from right side (angle - π/2) around the start endpoint
  var startCap = _gcfSemicircle(p0[0], p0[1], d0angle - Math.PI / 2, width_m, steps);
  // End cap: clockwise from left side (angle + π/2) around the end endpoint
  var endCap   = _gcfSemicircle(pN[0], pN[1], dNangle + Math.PI / 2, width_m, steps);

  var ring = startCap.concat(leftSide).concat(endCap).concat(rightSide.reverse());
  ring.push(ring[0]);
  return ring;
}

function _gcfUpdateCorridorBuffer() {
  if (!gcfMap || !gcfMap.getSource('gcf-corridor-buffer')) return;
  var features = [];
  _gcfDrawRegions.forEach(function(r) {
    if (r.type !== 'corridor') return;
    var ring = _gcfCorridorBuffer(r.path, r.width_m);
    if (ring) features.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: {} });
  });
  gcfMap.getSource('gcf-corridor-buffer').setData({ type: 'FeatureCollection', features: features });
}

// Simplify a path by skipping points closer than minSpacingM to the previous kept point.
// Always keeps the first and last point. O(n).
function _gcfSimplifyPath(path, minSpacingM) {
  if (path.length < 2) return path;
  var result = [path[0]];
  for (var i = 1; i < path.length - 1; i++) {
    var last = result[result.length - 1];
    if (_gcfHaversineM(last[1], last[0], path[i][1], path[i][0]) >= minSpacingM) {
      result.push(path[i]);
    }
  }
  result.push(path[path.length - 1]);
  return result;
}

// Compute per-segment API search shapes for a corridor.
// Path is first simplified so no segment is shorter than width_m, preventing
// redundant overlapping search shapes from dense GPS tracks.
// Each remaining segment is subdivided so the shorter box dimension ≤ 2×width_m.
// For each sub-piece, the smaller of bbox vs circumscribed circle is chosen.
// Returns [{type:'rect', s,w,n,e} | {type:'circle', lat,lon,radius_m}, ...].
function _gcfCorridorBoxes(path, width_m) {
  var simplified = _gcfSimplifyPath(path, width_m);
  var maxMinorM = 2 * width_m;
  var searches = [];
  for (var i = 0; i < simplified.length - 1; i++) {
    var p0 = simplified[i], p1 = simplified[i + 1];
    var avgLat = (p0[1] + p1[1]) / 2;
    var cosLat = Math.cos(avgLat * Math.PI / 180);
    var dxM = (p1[0] - p0[0]) * 111320 * cosLat;
    var dyM = (p1[1] - p0[1]) * 110540;
    var minor = Math.min(Math.abs(dxM), Math.abs(dyM));
    var n = Math.max(1, Math.ceil(minor / maxMinorM));
    for (var j = 0; j < n; j++) {
      var t0 = j / n, t1 = (j + 1) / n;
      var lat0 = p0[1] + t0 * (p1[1] - p0[1]);
      var lng0 = p0[0] + t0 * (p1[0] - p0[0]);
      var lat1 = p0[1] + t1 * (p1[1] - p0[1]);
      var lng1 = p0[0] + t1 * (p1[0] - p0[0]);
      var midLat = (lat0 + lat1) / 2;
      var midLng = (lng0 + lng1) / 2;
      var padLat = width_m / 110540;
      var padLng = width_m / (111320 * Math.cos(midLat * Math.PI / 180));
      // Bounding box
      var s = Math.min(lat0, lat1) - padLat;
      var w = Math.min(lng0, lng1) - padLng;
      var nN = Math.max(lat0, lat1) + padLat;
      var e = Math.max(lng0, lng1) + padLng;
      var hM = (nN - s) * 110540;
      var wM = (e - w) * 111320 * Math.cos(midLat * Math.PI / 180);
      var bboxArea = hM * wM;
      // Circumscribed circle: center = midpoint, r = sqrt(half_seg² + width_m²)
      var halfLenM = _gcfHaversineM(midLat, midLng, lat0, lng0);
      var rM = Math.sqrt(halfLenM * halfLenM + width_m * width_m);
      var circleArea = Math.PI * rM * rM;
      if (circleArea < bboxArea) {
        searches.push({ type: 'circle', lat: midLat, lon: midLng, radius_m: Math.ceil(rM) });
      } else {
        searches.push({ type: 'rect', s: s, w: w, n: nN, e: e });
      }
    }
  }
  return searches;
}

// Given a closed polygon ring [[lng, lat], ...], return the best API search shape.
// Compares circumscribed circle (centroid + max vertex distance) vs bbox.
function _gcfBestSearchForPolygon(ring) {
  var end = ring.length;
  if (end > 1 && ring[0][0] === ring[end-1][0] && ring[0][1] === ring[end-1][1]) end--;
  var sumLat = 0, sumLng = 0;
  for (var i = 0; i < end; i++) { sumLng += ring[i][0]; sumLat += ring[i][1]; }
  var cLat = sumLat / end, cLng = sumLng / end;
  var rM = 0;
  for (var i = 0; i < end; i++) {
    var d = _gcfHaversineM(cLat, cLng, ring[i][1], ring[i][0]);
    if (d > rM) rM = d;
  }
  var circleArea = Math.PI * rM * rM;
  var lats = ring.slice(0, end).map(function(v) { return v[1]; });
  var lngs = ring.slice(0, end).map(function(v) { return v[0]; });
  var s = Math.min.apply(null, lats), n = Math.max.apply(null, lats);
  var w = Math.min.apply(null, lngs), eV = Math.max.apply(null, lngs);
  var avgLat = (s + n) / 2;
  var hM = (n - s) * 110540;
  var wM = (eV - w) * 111320 * Math.cos(avgLat * Math.PI / 180);
  var bboxArea = hM * wM;
  if (circleArea < bboxArea) {
    return { type: 'circle', lat: cLat, lon: cLng, radius_m: Math.ceil(rM) };
  }
  return { type: 'rect', s: s, w: w, n: n, e: eV };
}

function _gcfUpdateCorridorBoxes() {
  if (!gcfMap || !gcfMap.getSource('gcf-corridor-boxes')) return;
  var features = [];
  _gcfDrawRegions.forEach(function(r) {
    if (r.type !== 'corridor') return;
    var searches = _gcfCorridorBoxes(r.path, r.width_m);
    searches.forEach(function(sh) {
      var coords;
      if (sh.type === 'circle') {
        coords = [_gcfCircleCoords(sh.lon, sh.lat, sh.radius_m)];
      } else {
        coords = [[[sh.w, sh.s],[sh.e, sh.s],[sh.e, sh.n],[sh.w, sh.n],[sh.w, sh.s]]];
      }
      features.push({
        type: 'Feature',
        geometry: { type: 'Polygon', coordinates: coords },
        properties: { search_type: sh.type }
      });
    });
  });
  gcfMap.getSource('gcf-corridor-boxes').setData({ type: 'FeatureCollection', features: features });
}

// ── Tooltip ───────────────────────────────────────────────────────────────────

var _gcfTooltipEl = null;

function _gcfFmtDist(m) {
  return m >= 1000 ? (m / 1000).toFixed(2) + ' km' : Math.round(m) + ' m';
}

// _gcfShowTooltip(point, w_m)          — circle: shows radius
// _gcfShowTooltip(point, w_m, h_m)     — rect: shows "W × H"
function _gcfShowTooltip(point, w_m, h_m) {
  if (!_gcfTooltipEl) {
    _gcfTooltipEl = document.createElement('div');
    _gcfTooltipEl.className = 'map-draw-tooltip';
    var container = document.getElementById('map-container');
    if (container) container.appendChild(_gcfTooltipEl);
  }
  _gcfTooltipEl.textContent = h_m !== undefined
    ? _gcfFmtDist(w_m) + ' \u00d7 ' + _gcfFmtDist(h_m)
    : _gcfFmtDist(w_m);
  _gcfTooltipEl.style.left = (point.x + 14) + 'px';
  _gcfTooltipEl.style.top  = (point.y + 6) + 'px';
  _gcfTooltipEl.style.display = 'block';
}

function _gcfRemoveTooltip() {
  if (_gcfTooltipEl) _gcfTooltipEl.style.display = 'none';
}

// ── Custom Draw Mode: Rectangle ───────────────────────────────────────────────
//
// Supports two input styles:
//   Drag mode:      mousedown → drag → mouseup  (onDrag fires during drag)
//   Two-click mode: click to anchor → hover → click to finish  (onMouseMove + onClick)
// Both modes share the same anchored/committed state machine.

var GcfDrawRectangle = {};

GcfDrawRectangle.onSetup = function(opts) {
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

GcfDrawRectangle.onMouseDown = function(state, e) {
  if (state.anchored) return;  // two-click mode: wait for onClick
  state.start = [e.lngLat.lng, e.lngLat.lat];
  this.map.dragPan.disable();
};

// Fires repeatedly while mouse button is held and cursor moves (drag mode preview).
GcfDrawRectangle.onDrag = function(state, e) {
  if (!state.start || state.anchored) return;
  var cur = [e.lngLat.lng, e.lngLat.lat];
  state.rect.incomingCoords([_gcfRectCoords(state.start, cur)]);
  var w_m = _gcfHaversineM(state.start[1], state.start[0], state.start[1], cur[0]);
  var h_m = _gcfHaversineM(state.start[1], state.start[0], cur[1],          state.start[0]);
  _gcfShowTooltip(e.point, w_m, h_m);
};

// Fires when cursor moves without button held (two-click hover preview).
GcfDrawRectangle.onMouseMove = function(state, e) {
  if (!state.anchored || !state.start) return;
  var cur = [e.lngLat.lng, e.lngLat.lat];
  state.rect.incomingCoords([_gcfRectCoords(state.start, cur)]);
  var w_m = _gcfHaversineM(state.start[1], state.start[0], state.start[1], cur[0]);
  var h_m = _gcfHaversineM(state.start[1], state.start[0], cur[1],          state.start[0]);
  _gcfShowTooltip(e.point, w_m, h_m);
};

// Fires after a real drag (not after a quick click).
GcfDrawRectangle.onMouseUp = function(state, e) {
  this.map.dragPan.enable();
  _gcfRemoveTooltip();
  if (!state.start || state.anchored) return;

  var end = [e.lngLat.lng, e.lngLat.lat];
  if (Math.abs(end[0] - state.start[0]) < 0.0001 &&
      Math.abs(end[1] - state.start[1]) < 0.0001) {
    // Drag was too small — fall into two-click mode instead of cancelling.
    state.anchored = true;
    return;
  }
  _gcfFinalizeRect(state, end, this);
};

// Fires on a quick click without drag (both the anchor click and the finish click).
GcfDrawRectangle.onClick = function(state, e) {
  if (!state.anchored) {
    // First click: record anchor point, switch to two-click hover mode.
    state.start = [e.lngLat.lng, e.lngLat.lat];
    state.anchored = true;
    this.map.dragPan.enable();  // dragPan was disabled in onMouseDown; restore it
  } else {
    // Second click: finalize the rectangle.
    _gcfFinalizeRect(state, [e.lngLat.lng, e.lngLat.lat], this);
  }
};

GcfDrawRectangle.onStop = function(state) {
  this.updateUIClasses({ mouse: 'none' });
  this.map.dragPan.enable();
  _gcfRemoveTooltip();
  // Delete the feature if it was never committed (e.g. Escape pressed).
  if (!state.committed) {
    this.deleteFeature([state.rect.id], { silent: true });
  }
};

GcfDrawRectangle.onTrash = function(state) {
  this.deleteFeature([state.rect.id], { silent: true });
  this.changeMode('simple_select');
};

GcfDrawRectangle.toDisplayFeatures = function(state, geojson, display) {
  if (state.rect && state.rect.id === geojson.properties.id) {
    geojson.properties.active = 'true';
  }
  display(geojson);
};

function _gcfFinalizeRect(state, end, ctx) {
  var bbox = [
    Math.min(state.start[1], end[1]),  // south
    Math.min(state.start[0], end[0]),  // west
    Math.max(state.start[1], end[1]),  // north
    Math.max(state.start[0], end[0])   // east
  ];
  state.rect.incomingCoords([_gcfRectCoords(state.start, end)]);
  state.rect.setProperty('bbox', bbox);
  state.committed = true;
  _gcfRemoveTooltip();
  // mapbox-gl-draw does not auto-fire draw.create for custom modes.
  // Notify our region tracker directly before the mode transition.
  _gcfOnDrawCreate({ features: [state.rect.toGeoJSON()] });
  // No featureIds — shape goes straight to the cold layer, avoiding hot-layer
  // flicker when drawing the next shape.
  ctx.changeMode('simple_select');
}

// ── Custom Draw Mode: Circle ──────────────────────────────────────────────────
//
// Same anchored/committed state machine as GcfDrawRectangle.

var GcfDrawCircle = {};

GcfDrawCircle.onSetup = function(opts) {
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

GcfDrawCircle.onMouseDown = function(state, e) {
  if (state.anchored) return;
  state.center = [e.lngLat.lng, e.lngLat.lat];
  this.map.dragPan.disable();
};

// Drag mode preview — fires while button is held and cursor moves.
GcfDrawCircle.onDrag = function(state, e) {
  if (!state.center || state.anchored) return;
  var r_m = _gcfHaversineM(state.center[1], state.center[0], e.lngLat.lat, e.lngLat.lng);
  if (r_m > 0) {
    state.circle.incomingCoords([_gcfCircleCoords(state.center[0], state.center[1], r_m)]);
  }
  _gcfShowTooltip(e.point, r_m);
};

// Two-click hover preview — fires when cursor moves without button held.
GcfDrawCircle.onMouseMove = function(state, e) {
  if (!state.anchored || !state.center) return;
  var r_m = _gcfHaversineM(state.center[1], state.center[0], e.lngLat.lat, e.lngLat.lng);
  if (r_m > 0) {
    state.circle.incomingCoords([_gcfCircleCoords(state.center[0], state.center[1], r_m)]);
  }
  _gcfShowTooltip(e.point, r_m);
};

GcfDrawCircle.onMouseUp = function(state, e) {
  this.map.dragPan.enable();
  _gcfRemoveTooltip();
  if (!state.center || state.anchored) return;

  var r_m = _gcfHaversineM(state.center[1], state.center[0], e.lngLat.lat, e.lngLat.lng);
  if (r_m < 10) {
    // Too small — fall into two-click mode.
    state.anchored = true;
    return;
  }
  _gcfFinalizeCircle(state, e.lngLat, this);
};

GcfDrawCircle.onClick = function(state, e) {
  if (!state.anchored) {
    state.center = [e.lngLat.lng, e.lngLat.lat];
    state.anchored = true;
    this.map.dragPan.enable();
  } else {
    var r_m = _gcfHaversineM(state.center[1], state.center[0], e.lngLat.lat, e.lngLat.lng);
    if (r_m < 10) return;  // second click too close to centre — ignore
    _gcfFinalizeCircle(state, e.lngLat, this);
  }
};

GcfDrawCircle.onStop = function(state) {
  this.updateUIClasses({ mouse: 'none' });
  this.map.dragPan.enable();
  _gcfRemoveTooltip();
  // Delete the feature if it was never committed (e.g. Escape pressed).
  if (!state.committed) {
    this.deleteFeature([state.circle.id], { silent: true });
  }
};

GcfDrawCircle.onTrash = function(state) {
  this.deleteFeature([state.circle.id], { silent: true });
  this.changeMode('simple_select');
};

GcfDrawCircle.toDisplayFeatures = function(state, geojson, display) {
  if (state.circle && state.circle.id === geojson.properties.id) {
    geojson.properties.active = 'true';
  }
  display(geojson);
};

function _gcfFinalizeCircle(state, lngLat, ctx) {
  var r_m = _gcfHaversineM(state.center[1], state.center[0], lngLat.lat, lngLat.lng);
  state.circle.incomingCoords([_gcfCircleCoords(state.center[0], state.center[1], r_m)]);
  // center stored as [lat, lon] to match URL format circle:lat,lon,radius_m
  state.circle.setProperty('center', [state.center[1], state.center[0]]);
  state.circle.setProperty('radius_m', Math.round(r_m));
  state.committed = true;
  _gcfRemoveTooltip();
  // mapbox-gl-draw does not auto-fire draw.create for custom modes.
  _gcfOnDrawCreate({ features: [state.circle.toGeoJSON()] });
  // No featureIds — straight to cold layer, no hot-layer flicker on next draw.
  ctx.changeMode('simple_select');
}

// ── Draw state ────────────────────────────────────────────────────────────────

var _gcfDrawCtrl = null;
var _gcfDrawRegions = [];  // [{type:'rect', bbox:[s,w,n,e], id}, ...]
var _gcfCorridorWidthM = 1000;  // default corridor half-width in metres (1 km)
var _gcfGpxParsed = null;       // cached result from last GPX file parse

// ── Initialise ────────────────────────────────────────────────────────────────

function gcfDrawInit() {
  if (!window.MapboxDraw || !gcfMap) return;

  var modes = Object.assign({}, MapboxDraw.modes, {
    gcf_rectangle: GcfDrawRectangle,
    gcf_circle:    GcfDrawCircle
  });

  // Include the default MapboxDraw theme first so built-in draw modes
  // (draw_polygon, draw_line_string) render their in-progress feedback correctly.
  // MapLibre GL JS v4+ requires bare number arrays in line-dasharray to be wrapped
  // as ["literal", [...]].  Sanitize the default theme to fix this.
  var defaultTheme = ((MapboxDraw.lib && MapboxDraw.lib.theme) || []).map(function(layer) {
    var da = layer.paint && layer.paint['line-dasharray'];
    if (Array.isArray(da) && da.length > 0 && typeof da[0] === 'number') {
      layer = Object.assign({}, layer, {
        paint: Object.assign({}, layer.paint, { 'line-dasharray': ['literal', da] })
      });
    }
    return layer;
  });
  _gcfDrawCtrl = new MapboxDraw({
    displayControlsDefault: false,
    modes: modes,
    styles: defaultTheme.concat([
      {
        id: 'gcf-draw-polygon-fill',
        type: 'fill',
        filter: ['all', ['==', '$type', 'Polygon'], ['!=', 'mode', 'static']],
        paint: {
          'fill-color': ['case',
            ['==', ['get', 'shape'], 'polygon'], '#198754',
            ['==', ['get', 'active'], 'true'], '#0d6efd',
            '#6610f2'
          ],
          'fill-opacity': 0.12
        }
      },
      {
        id: 'gcf-draw-polygon-stroke',
        type: 'line',
        filter: ['all', ['==', '$type', 'Polygon'], ['!=', 'mode', 'static']],
        paint: {
          'line-color': ['case',
            ['==', ['get', 'shape'], 'polygon'], '#198754',
            ['==', ['get', 'active'], 'true'], '#0d6efd',
            '#6610f2'
          ],
          'line-width': 2
        }
      },
      {
        id: 'gcf-draw-line',
        type: 'line',
        filter: ['==', '$type', 'LineString'],
        paint: { 'line-color': '#e05000', 'line-width': 2.5 }
      },
      {
        id: 'gcf-draw-vertex',
        type: 'circle',
        filter: ['all', ['==', '$type', 'Point'], ['==', 'meta', 'vertex']],
        paint: { 'circle-radius': 4, 'circle-color': '#0d6efd' }
      }
    ])
  });

  gcfMap.addControl(_gcfDrawCtrl, 'top-left');

  // Add corridor buffer source + layers once the style is ready.
  // Must defer: addSource/addLayer fail if called before the style has loaded.
  function _gcfAddCorridorBufferLayers() {
    if (gcfMap.getSource('gcf-corridor-buffer')) return; // already added (style reload)
    gcfMap.addSource('gcf-corridor-buffer', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] }
    });
    gcfMap.addLayer({
      id: 'gcf-corridor-buffer-fill',
      type: 'fill',
      source: 'gcf-corridor-buffer',
      paint: { 'fill-color': '#e05000', 'fill-opacity': 0.15 }
    });
    gcfMap.addLayer({
      id: 'gcf-corridor-buffer-stroke',
      type: 'line',
      source: 'gcf-corridor-buffer',
      paint: { 'line-color': '#e05000', 'line-width': 1, 'line-dasharray': [4, 3] }
    });
    gcfMap.addSource('gcf-corridor-boxes', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] }
    });
    gcfMap.addLayer({
      id: 'gcf-corridor-boxes-stroke',
      type: 'line',
      source: 'gcf-corridor-boxes',
      paint: {
        'line-color': ['case', ['==', ['get', 'search_type'], 'circle'], '#198754', '#0d6efd'],
        'line-width': 1,
        'line-dasharray': [3, 3]
      }
    });
    // Replay any corridor regions already drawn (e.g. restored from URL)
    _gcfUpdateCorridorBuffer();
    _gcfUpdateCorridorBoxes();
  }
  if (gcfMap.isStyleLoaded()) {
    _gcfAddCorridorBufferLayers();
  } else {
    gcfMap.once('load', _gcfAddCorridorBufferLayers);
  }

  // Suppress mapbox-gl-draw's own button UI — we render our own toolbar
  if (_gcfDrawCtrl._container) {
    _gcfDrawCtrl._container.style.display = 'none';
  }

  gcfMap.on('draw.create', _gcfOnDrawCreate);
  gcfMap.on('draw.delete', _gcfOnDrawDelete);
  gcfMap.on('draw.update', _gcfOnDrawUpdate);

  // Show toolbar
  var tb = document.getElementById('map-draw-toolbar');
  if (tb) tb.style.display = 'flex';

  // Restore shapes from ?geo= URL param so they remain visible after page nav
  _gcfRestoreGeoShapes();

  // Prevent vertex dragging on circles — redirect direct_select to simple_select
  gcfMap.on('draw.modechange', function(e) {
    if (e.mode !== 'direct_select') return;
    // Check if the selected feature is a circle — circles shouldn't have
    // draggable vertices since it distorts the visual without changing the filter.
    var selected = _gcfDrawCtrl.getSelectedIds();
    if (!selected.length) return;
    var feat = _gcfDrawCtrl.get(selected[0]);
    if (feat && feat.properties && feat.properties.shape === 'circle') {
      // Defer to avoid interfering with the mode transition in progress
      setTimeout(function() {
        _gcfDrawCtrl.changeMode('simple_select', { featureIds: selected });
      }, 0);
    }
  });

  // Wire saved-areas dropdown toggle
  document.addEventListener('click', function(e) {
    var areasCtrl = document.getElementById('map-areas-control');
    var areasBtn  = document.getElementById('map-areas-toggle');
    if (areasCtrl && areasBtn && areasBtn.contains(e.target)) {
      areasCtrl.classList.toggle('open');
      if (areasCtrl.classList.contains('open')) _gcfLoadSavedAreas();
      e.stopPropagation();
      return;
    }
    if (areasCtrl && !areasCtrl.contains(e.target)) {
      areasCtrl.classList.remove('open');
    }

  });

  // Keyboard Delete / Backspace — remove selected draw shapes
  document.addEventListener('keydown', function(e) {
    if (e.key !== 'Delete' && e.key !== 'Backspace') return;
    if (!_gcfDrawCtrl) return;
    // Don't steal keystrokes from inputs
    var tag = document.activeElement && document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    var selected = _gcfDrawCtrl.getSelectedIds();
    if (selected.length > 0) {
      e.preventDefault();
      _gcfDrawCtrl.trash();
    }
  });


  // Initialise reference-radius circle toggle visibility
  _gcfRefRadiusInit();
}

// ── Draw event handlers ───────────────────────────────────────────────────────

function _gcfOnDrawCreate(e) {
  (e.features || []).forEach(function(f) {
    var p = f.properties;
    var alreadyTracked = _gcfDrawRegions.some(function(r) { return r.id === f.id; });
    if (alreadyTracked) return;
    if (p.shape === 'rect' && p.bbox) {
      var bbox = Array.isArray(p.bbox) ? p.bbox : JSON.parse(p.bbox);
      _gcfDrawRegions.push({ type: 'rect', bbox: bbox, id: f.id });
    } else if (p.shape === 'circle' && p.center != null && p.radius_m != null) {
      var center = Array.isArray(p.center) ? p.center : JSON.parse(p.center);
      _gcfDrawRegions.push({ type: 'circle', center: center, radius_m: Number(p.radius_m), id: f.id });
    } else if (f.geometry.type === 'Polygon' && !p.shape) {
      // Built-in draw_polygon mode — tag and track
      var ring = f.geometry.coordinates[0];
      if (_gcfDrawCtrl) _gcfDrawCtrl.setFeatureProperty(f.id, 'shape', 'polygon');
      _gcfDrawRegions.push({ type: 'polygon', coordinates: ring, id: f.id });
    } else if (f.geometry.type === 'LineString' && !p.shape) {
      // Built-in draw_line_string mode — tag and track as corridor
      var path = f.geometry.coordinates;
      if (_gcfDrawCtrl) _gcfDrawCtrl.setFeatureProperty(f.id, 'shape', 'corridor');
      if (_gcfDrawCtrl) _gcfDrawCtrl.setFeatureProperty(f.id, 'width_m', _gcfCorridorWidthM);
      _gcfDrawRegions.push({ type: 'corridor', path: path, width_m: _gcfCorridorWidthM, id: f.id });
    }
  });
  _gcfUpdateDrawStatus();
  _gcfUpdateCorridorBuffer();
  _gcfUpdateCorridorBoxes();
}

function _gcfOnDrawDelete(e) {
  var deletedIds = (e.features || []).map(function(f) { return f.id; });
  _gcfDrawRegions = _gcfDrawRegions.filter(function(r) {
    return deletedIds.indexOf(r.id) === -1;
  });
  _gcfUpdateDrawStatus();
  _gcfUpdateCorridorBuffer();
  _gcfUpdateCorridorBoxes();
}

function _gcfOnDrawUpdate(e) {
  // When a shape is moved or resized (direct_select), update _gcfDrawRegions
  (e.features || []).forEach(function(f) {
    var region = null;
    for (var i = 0; i < _gcfDrawRegions.length; i++) {
      if (_gcfDrawRegions[i].id === f.id) { region = _gcfDrawRegions[i]; break; }
    }
    if (!region) return;

    if (region.type === 'rect') {
      // Recompute bbox from the polygon geometry
      var coords = f.geometry.coordinates[0];
      var lats = coords.map(function(c) { return c[1]; });
      var lngs = coords.map(function(c) { return c[0]; });
      region.bbox = [
        Math.min.apply(null, lats),
        Math.min.apply(null, lngs),
        Math.max.apply(null, lats),
        Math.max.apply(null, lngs)
      ];
      // Update the feature's stored property too
      if (_gcfDrawCtrl) {
        _gcfDrawCtrl.setFeatureProperty(f.id, 'bbox', region.bbox);
      }
    } else if (region.type === 'circle') {
      // Recompute center from the polygon centroid and recalculate radius
      var coords = f.geometry.coordinates[0];
      var sumLat = 0, sumLng = 0;
      // Exclude closing vertex (duplicate of first)
      var n = coords.length - 1;
      for (var j = 0; j < n; j++) {
        sumLng += coords[j][0];
        sumLat += coords[j][1];
      }
      var cLat = sumLat / n, cLng = sumLng / n;
      // Radius = average distance from center to polygon vertices
      var totalDist = 0;
      for (var j = 0; j < n; j++) {
        totalDist += _gcfHaversineM(cLat, cLng, coords[j][1], coords[j][0]);
      }
      region.center = [cLat, cLng];
      region.radius_m = Math.round(totalDist / n);
      if (_gcfDrawCtrl) {
        _gcfDrawCtrl.setFeatureProperty(f.id, 'center', region.center);
        _gcfDrawCtrl.setFeatureProperty(f.id, 'radius_m', region.radius_m);
      }
    } else if (region.type === 'polygon') {
      region.coordinates = f.geometry.coordinates[0];
    } else if (region.type === 'corridor') {
      region.path = f.geometry.coordinates;
    }
  });
  _gcfUpdateDrawStatus();
  _gcfUpdateCorridorBuffer();
  _gcfUpdateCorridorBoxes();
}

// ── Selection count badge ─────────────────────────────────────────────────────

function _gcfUpdateDrawStatus() {
  var statusEl = document.getElementById('map-draw-status');
  if (!statusEl) return;

  if (_gcfDrawRegions.length === 0) {
    statusEl.style.display = 'none';
    return;
  }
  statusEl.style.display = 'flex';

  var count = 0;
  if (typeof _gcfMarkersData !== 'undefined' && _gcfMarkersData) {
    _gcfMarkersData.forEach(function(m) {
      if (_gcfIsInAnyRegion(m.la, m.lo)) count++;
    });
  }
  var badge = document.getElementById('map-draw-count');
  if (badge) badge.textContent = count + ' caches selected';

  // Update fetch Preview/Sync button visibility (map-fetch.js)
  if (typeof gcfUpdateFetchButtons === 'function') gcfUpdateFetchButtons();
}

function _gcfIsInAnyRegion(lat, lon) {
  for (var i = 0; i < _gcfDrawRegions.length; i++) {
    var r = _gcfDrawRegions[i];
    if (r.type === 'rect') {
      var s = r.bbox[0], w = r.bbox[1], n = r.bbox[2], e = r.bbox[3];
      if (lat >= s && lat <= n && lon >= w && lon <= e) return true;
    } else if (r.type === 'circle') {
      if (_gcfHaversineM(r.center[0], r.center[1], lat, lon) <= r.radius_m) return true;
    } else if (r.type === 'polygon') {
      if (_gcfPointInPolygon(lat, lon, r.coordinates)) return true;
    } else if (r.type === 'corridor') {
      var path = r.path;
      for (var j = 0; j < path.length - 1; j++) {
        if (_gcfDistToSegmentM(lat, lon, path[j][1], path[j][0], path[j+1][1], path[j+1][0]) <= r.width_m) return true;
      }
    }
  }
  return false;
}

// ── Toolbar button actions ────────────────────────────────────────────────────

function gcfDrawRect() {
  if (!_gcfDrawCtrl) return;
  if (_gcfDrawCtrl.getMode() === 'gcf_rectangle') {
    _gcfDrawCtrl.changeMode('simple_select');
    _gcfSetActiveDrawBtn(null);
  } else {
    _gcfDrawCtrl.changeMode('gcf_rectangle');
    _gcfSetActiveDrawBtn('map-draw-rect-btn');
  }
}

function gcfDrawCircle() {
  if (!_gcfDrawCtrl) return;
  if (_gcfDrawCtrl.getMode() === 'gcf_circle') {
    _gcfDrawCtrl.changeMode('simple_select');
    _gcfSetActiveDrawBtn(null);
  } else {
    _gcfDrawCtrl.changeMode('gcf_circle');
    _gcfSetActiveDrawBtn('map-draw-circle-btn');
  }
}

function gcfDrawClearAll() {
  if (!_gcfDrawCtrl) return;
  _gcfDrawCtrl.deleteAll();
  _gcfDrawRegions = [];
  _gcfUpdateDrawStatus();
  _gcfUpdateCorridorBuffer();
  _gcfUpdateCorridorBoxes();
}

function gcfDrawPolygon() {
  if (!_gcfDrawCtrl) return;
  if (_gcfDrawCtrl.getMode() === 'draw_polygon') {
    _gcfDrawCtrl.changeMode('simple_select');
    _gcfSetActiveDrawBtn(null);
  } else {
    _gcfDrawCtrl.changeMode('draw_polygon');
    _gcfSetActiveDrawBtn('map-draw-polygon-btn');
  }
}

function gcfDrawCorridor() {
  if (!_gcfDrawCtrl) return;
  var panel = document.getElementById('map-corridor-width-panel');
  if (_gcfDrawCtrl.getMode() === 'draw_line_string') {
    _gcfDrawCtrl.changeMode('simple_select');
    _gcfSetActiveDrawBtn(null);
    if (panel) panel.style.display = 'none';
  } else {
    _gcfDrawCtrl.changeMode('draw_line_string');
    _gcfSetActiveDrawBtn('map-draw-corridor-btn');
    if (panel) panel.style.display = 'flex';
    gcfMap.once('draw.modechange', function() {
      if (panel) panel.style.display = 'none';
    });
  }
}

function gcfSetCorridorWidth(value) {
  var w = parseFloat(value);
  if (!isNaN(w) && w > 0) {
    _gcfCorridorWidthM = Math.round(w * 1000);
    _gcfDrawRegions.forEach(function(r) {
      if (r.type === 'corridor') {
        r.width_m = _gcfCorridorWidthM;
        if (_gcfDrawCtrl) _gcfDrawCtrl.setFeatureProperty(r.id, 'width_m', _gcfCorridorWidthM);
      }
    });
    _gcfUpdateDrawStatus();
    _gcfUpdateCorridorBuffer();
    _gcfUpdateCorridorBoxes();
  }
}

function _gcfSetActiveDrawBtn(activeId) {
  ['map-draw-rect-btn', 'map-draw-circle-btn', 'map-draw-polygon-btn', 'map-draw-corridor-btn'].forEach(function(id) {
    var btn = document.getElementById(id);
    if (btn) btn.classList.toggle('active', id === activeId);
  });
  // Deactivate buttons when mode changes back to simple_select
  if (gcfMap) {
    gcfMap.once('draw.modechange', function() {
      ['map-draw-rect-btn', 'map-draw-circle-btn', 'map-draw-polygon-btn', 'map-draw-corridor-btn'].forEach(function(id) {
        var btn = document.getElementById(id);
        if (btn) btn.classList.remove('active');
      });
    });
  }
}

// ── Restore geo shapes from URL ───────────────────────────────────────────────

function _gcfRestoreGeoShapes() {
  if (!_gcfDrawCtrl) return;
  var geoParam = new URLSearchParams(window.location.search).get('geo');
  if (!geoParam) return;

  var parts = geoParam.split('|');
  for (var i = 0; i < parts.length; i++) {
    var part = parts[i];
    if (part.indexOf('rect:') === 0) {
      var vals = part.substring(5).split(',').map(Number);
      if (vals.length === 4) {
        var s = vals[0], w = vals[1], n = vals[2], e = vals[3];
        var coords = [[w, s], [e, s], [e, n], [w, n], [w, s]];
        var ids = _gcfDrawCtrl.add({
          type: 'Feature',
          properties: { shape: 'rect', bbox: [s, w, n, e] },
          geometry: { type: 'Polygon', coordinates: [coords] }
        });
        if (ids && ids.length) {
          _gcfDrawRegions.push({ type: 'rect', bbox: [s, w, n, e], id: ids[0] });
        }
      }
    } else if (part.indexOf('circle:') === 0) {
      var vals = part.substring(7).split(',').map(Number);
      if (vals.length === 3) {
        var lat = vals[0], lon = vals[1], radius_m = vals[2];
        var circleCoords = _gcfCircleCoords(lon, lat, radius_m);
        var ids = _gcfDrawCtrl.add({
          type: 'Feature',
          properties: { shape: 'circle', center: [lat, lon], radius_m: radius_m },
          geometry: { type: 'Polygon', coordinates: [circleCoords] }
        });
        if (ids && ids.length) {
          _gcfDrawRegions.push({ type: 'circle', center: [lat, lon], radius_m: radius_m, id: ids[0] });
        }
      }
    } else if (part.indexOf('polygon:') === 0) {
      var vals = part.substring(8).split(',').map(Number);
      if (vals.length >= 6 && vals.length % 2 === 0) {
        var ring = [];
        for (var j = 0; j < vals.length; j += 2) ring.push([vals[j], vals[j + 1]]);
        if (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1]) ring.push(ring[0]);
        var ids = _gcfDrawCtrl.add({
          type: 'Feature',
          properties: { shape: 'polygon' },
          geometry: { type: 'Polygon', coordinates: [ring] }
        });
        if (ids && ids.length) _gcfDrawRegions.push({ type: 'polygon', coordinates: ring, id: ids[0] });
      }
    } else if (part.indexOf('corridor:') === 0) {
      var rest = part.substring(9);
      var sep = rest.indexOf(':');
      if (sep >= 0) {
        var width_m = Number(rest.substring(0, sep));
        var vals = rest.substring(sep + 1).split(',').map(Number);
        if (vals.length >= 4 && vals.length % 2 === 0) {
          var path = [];
          for (var j = 0; j < vals.length; j += 2) path.push([vals[j], vals[j + 1]]);
          var ids = _gcfDrawCtrl.add({
            type: 'Feature',
            properties: { shape: 'corridor', width_m: width_m },
            geometry: { type: 'LineString', coordinates: path }
          });
          if (ids && ids.length) {
            _gcfDrawRegions.push({ type: 'corridor', path: path, width_m: width_m, id: ids[0] });
            _gcfCorridorWidthM = width_m;
            var inp = document.getElementById('map-corridor-width');
            if (inp) inp.value = (width_m / 1000).toFixed(1);
          }
        }
      }
    }
  }
  _gcfUpdateDrawStatus();
  _gcfUpdateCorridorBuffer();
  _gcfUpdateCorridorBoxes();
}

// ── Filter actions ────────────────────────────────────────────────────────────

function gcfPinAsFilter() {
  if (!_gcfDrawRegions.length) return;
  var parts = _gcfDrawRegions.map(function(r) {
    if (r.type === 'rect') {
      return 'rect:' + r.bbox.map(function(v) { return Number(v).toFixed(6); }).join(',');
    }
    if (r.type === 'circle') {
      return 'circle:' + Number(r.center[0]).toFixed(6) + ',' +
             Number(r.center[1]).toFixed(6) + ',' + Math.round(r.radius_m);
    }
    if (r.type === 'polygon') {
      var ring = r.coordinates;
      var last = ring.length - 1;
      var isClose = ring[0][0] === ring[last][0] && ring[0][1] === ring[last][1];
      var end = isClose ? last : ring.length;
      var flat = [];
      for (var j = 0; j < end; j++) flat.push(Number(ring[j][0]).toFixed(6), Number(ring[j][1]).toFixed(6));
      return 'polygon:' + flat.join(',');
    }
    if (r.type === 'corridor') {
      var flat = [];
      r.path.forEach(function(p) { flat.push(Number(p[0]).toFixed(6), Number(p[1]).toFixed(6)); });
      return 'corridor:' + Math.round(r.width_m) + ':' + flat.join(',');
    }
    return null;
  }).filter(Boolean);
  if (!parts.length) return;

  var params = new URLSearchParams(window.location.search);
  params.set('geo', parts.join('|'));
  params.delete('page');
  window.location.search = params.toString();
}

// ── Saved area filters ────────────────────────────────────────────────────────

function gcfSaveArea() {
  if (!_gcfDrawRegions.length) {
    alert('Draw shapes on the map first.');
    return;
  }
  var name = prompt('Save area filter as:');
  if (!name || !name.trim()) return;

  var regions = _gcfDrawRegions.map(function(r) {
    if (r.type === 'rect')     return { type: 'rect',     bbox: r.bbox };
    if (r.type === 'circle')   return { type: 'circle',   center: r.center, radius_m: r.radius_m };
    if (r.type === 'polygon')  return { type: 'polygon',  coordinates: r.coordinates };
    if (r.type === 'corridor') return { type: 'corridor', path: r.path, width_m: r.width_m };
    return null;
  }).filter(Boolean);

  var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
  fetch('/map/areas/save/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrfToken ? csrfToken.value : ''
    },
    body: JSON.stringify({ name: name.trim(), regions: regions })
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    if (data.error) { alert('Error: ' + data.error); return; }
    _gcfLoadSavedAreas();
  })
  .catch(function(e) { alert('Save failed: ' + e); });
}

function _gcfLoadSavedAreas() {
  fetch('/map/areas/')
    .then(function(r) { return r.json(); })
    .then(function(data) { _gcfRenderSavedAreas(data.areas); })
    .catch(function() {});
}

function _gcfRenderSavedAreas(areas) {
  var list = document.getElementById('map-areas-list');
  if (!list) return;
  list.innerHTML = '';

  if (!areas || !areas.length) {
    var empty = document.createElement('div');
    empty.className = 'map-layer-option';
    empty.style.cssText = 'color:#888;font-style:italic';
    empty.textContent = 'No saved areas';
    list.appendChild(empty);
    return;
  }

  areas.forEach(function(area) {
    var row = document.createElement('div');
    row.className = 'map-area-item';

    var nameSpan = document.createElement('span');
    nameSpan.textContent = area.name;
    nameSpan.title = 'Load ' + area.name;
    nameSpan.onclick = function() { gcfLoadSavedArea(area); };

    var delBtn = document.createElement('button');
    delBtn.textContent = '\u00d7';
    delBtn.className = 'map-area-delete-btn';
    delBtn.title = 'Delete';
    delBtn.onclick = function(ev) { ev.stopPropagation(); gcfDeleteSavedArea(area.id, area.name); };

    row.appendChild(nameSpan);
    row.appendChild(delBtn);
    list.appendChild(row);
  });
}

function gcfLoadSavedArea(area) {
  var parts = (area.regions || []).map(function(r) {
    if (r.type === 'rect') {
      return 'rect:' + r.bbox.map(function(v) { return Number(v).toFixed(6); }).join(',');
    }
    if (r.type === 'circle') {
      return 'circle:' + Number(r.center[0]).toFixed(6) + ',' +
             Number(r.center[1]).toFixed(6) + ',' + Math.round(r.radius_m);
    }
    if (r.type === 'polygon') {
      var ring = r.coordinates;
      var last = ring.length - 1;
      var isClose = ring[0][0] === ring[last][0] && ring[0][1] === ring[last][1];
      var end = isClose ? last : ring.length;
      var flat = [];
      for (var j = 0; j < end; j++) flat.push(Number(ring[j][0]).toFixed(6), Number(ring[j][1]).toFixed(6));
      return 'polygon:' + flat.join(',');
    }
    if (r.type === 'corridor') {
      var flat = [];
      r.path.forEach(function(p) { flat.push(Number(p[0]).toFixed(6), Number(p[1]).toFixed(6)); });
      return 'corridor:' + Math.round(r.width_m) + ':' + flat.join(',');
    }
    return null;
  }).filter(Boolean);
  if (!parts.length) return;

  var params = new URLSearchParams(window.location.search);
  params.set('geo', parts.join('|'));
  params.delete('page');
  window.location.search = params.toString();
}

function gcfDeleteSavedArea(id, name) {
  if (!confirm('Delete saved area "' + name + '"?')) return;
  var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
  fetch('/map/areas/' + id + '/delete/', {
    method: 'DELETE',
    headers: { 'X-CSRFToken': csrfToken ? csrfToken.value : '' }
  })
  .then(function(r) { return r.json(); })
  .then(function() { _gcfLoadSavedAreas(); })
  .catch(function(e) { alert('Delete failed: ' + e); });
}


// ── Reference-point radius circle ────────────────────────────────────────────
//
// Reads ?radius= and ?ref= from the current URL, finds the matching location
// from _gcfLocations, and draws a dashed circle on the map.

var _gcfRefRadiusVisible = false;
var _gcfRefRadiusShaded  = false;
var _gcfRefRadiusDefaultsApplied = false;

function _gcfGetRefCircle() {
  var params  = new URLSearchParams(window.location.search);
  var radiusStr = params.get('radius');
  if (!radiusStr) return null;
  var radius_km = parseFloat(radiusStr);
  if (isNaN(radius_km) || radius_km <= 0) return null;

  var refId = params.get('ref');
  var loc = null;
  if (typeof _gcfLocations !== 'undefined' && _gcfLocations.length) {
    if (refId) {
      loc = _gcfLocations.filter(function(l) { return String(l.id) === refId; })[0] || null;
    }
    if (!loc) {
      loc = _gcfLocations.filter(function(l) { return l.home; })[0] || _gcfLocations[0];
    }
  }
  if (!loc) return null;
  return { lat: loc.lat, lon: loc.lon, radius_km: radius_km };
}

function _gcfRefRadiusInit() {
  var rr = _gcfGetRefCircle();
  var section = document.getElementById('radius-circle-section');
  if (section) section.style.display = rr ? '' : 'none';

  // On first call when radius is active, apply server defaults
  if (rr && !_gcfRefRadiusDefaultsApplied) {
    _gcfRefRadiusDefaultsApplied = true;
    var prefs = (typeof _gcfMapPrefs !== 'undefined') ? _gcfMapPrefs : {};
    if (prefs.radius_circle) {
      _gcfRefRadiusVisible = true;
      var cb = document.getElementById('radius-circle-toggle');
      if (cb) cb.checked = true;
      var shadeOpt = document.getElementById('radius-circle-shade-opt');
      if (shadeOpt) shadeOpt.style.display = '';
      if (prefs.radius_shade) {
        _gcfRefRadiusShaded = true;
        var shadeCb = document.getElementById('radius-circle-shade');
        if (shadeCb) shadeCb.checked = true;
      }
    }
  }

  // If radius circle is visible, draw it (or defer until map style is loaded)
  if (_gcfRefRadiusVisible) {
    if (gcfMap && gcfMap.isStyleLoaded()) {
      _gcfApplyRefRadius();
    } else if (gcfMap) {
      gcfMap.once('load', function() { _gcfApplyRefRadius(); });
    }
  }
}

function _gcfApplyRefRadius() {
  if (!gcfMap || !gcfMap.isStyleLoaded()) return;
  // Remove existing layers/source
  ['gcf-ref-radius-fill', 'gcf-ref-radius-line'].forEach(function(id) {
    if (gcfMap.getLayer(id)) gcfMap.removeLayer(id);
  });
  if (gcfMap.getSource('gcf-ref-radius')) gcfMap.removeSource('gcf-ref-radius');

  if (!_gcfRefRadiusVisible) return;
  var rr = _gcfGetRefCircle();
  if (!rr) return;

  // Build circle polygon (128 points for smoothness)
  var n = 128;
  var coords = [];
  var lat_r = rr.lat * Math.PI / 180;
  var radius_m = rr.radius_km * 1000;
  for (var i = 0; i < n; i++) {
    var angle = (i / n) * 2 * Math.PI;
    var dx = radius_m * Math.cos(angle) / (111320 * Math.cos(lat_r));
    var dy = radius_m * Math.sin(angle) / 110540;
    coords.push([rr.lon + dx, rr.lat + dy]);
  }
  coords.push(coords[0]);

  gcfMap.addSource('gcf-ref-radius', {
    type: 'geojson',
    data: { type: 'Feature', geometry: { type: 'Polygon', coordinates: [coords] } }
  });

  gcfMap.addLayer({
    id: 'gcf-ref-radius-line',
    type: 'line',
    source: 'gcf-ref-radius',
    paint: { 'line-color': '#e74c3c', 'line-width': 2, 'line-dasharray': [5, 3] }
  });

  if (_gcfRefRadiusShaded) {
    gcfMap.addLayer({
      id: 'gcf-ref-radius-fill',
      type: 'fill',
      source: 'gcf-ref-radius',
      paint: { 'fill-color': '#e74c3c', 'fill-opacity': 0.08 }
    }, 'gcf-ref-radius-line');
  }
}

function gcfToggleRadiusCircle(visible) {
  _gcfRefRadiusVisible = visible;
  var shadeOpt = document.getElementById('radius-circle-shade-opt');
  if (shadeOpt) shadeOpt.style.display = visible ? '' : 'none';
  _gcfApplyRefRadius();
}

function gcfToggleRadiusShade(shaded) {
  _gcfRefRadiusShaded = shaded;
  _gcfApplyRefRadius();
}

// ── GPX track import ──────────────────────────────────────────────────────────

function gcfOpenGpxImport() {
  var modalEl = document.getElementById('gpxImportDialog');
  if (!modalEl) return;
  // Reset state
  _gcfGpxParsed = null;
  var fileInput = document.getElementById('gpx-file-input');
  if (fileInput) fileInput.value = '';
  var infoEl = document.getElementById('gpx-track-info');
  if (infoEl) infoEl.style.display = 'none';
  var importBtn = document.getElementById('gpx-import-btn');
  if (importBtn) importBtn.disabled = true;
  // Sync width with current corridor width setting
  var widthInput = document.getElementById('gpx-width-input');
  if (widthInput) widthInput.value = (_gcfCorridorWidthM / 1000).toFixed(1);

  var modal = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
  modal.show();
}

function gcfGpxFileChanged(input) {
  var infoEl = document.getElementById('gpx-track-info');
  var importBtn = document.getElementById('gpx-import-btn');
  _gcfGpxParsed = null;
  if (importBtn) importBtn.disabled = true;

  var file = input.files && input.files[0];
  if (!file) {
    if (infoEl) infoEl.style.display = 'none';
    return;
  }

  var reader = new FileReader();
  reader.onload = function(ev) {
    try {
      _gcfGpxParsed = _gcfParseGpx(ev.target.result);
      var info = _gcfGpxParsed;
      var lines = [
        info.segmentCount + ' segment' + (info.segmentCount !== 1 ? 's' : '') +
        ', ' + info.originalCount.toLocaleString() + ' points' +
        ', ~' + info.lengthKm.toFixed(1) + '\u202fkm'
      ];
      if (info.originalCount > 5000) {
        lines.push('<span class="text-warning">Large track \u2014 rendering may be slow</span>');
      }
      if (infoEl) { infoEl.innerHTML = lines.join('<br>'); infoEl.style.display = ''; }
      if (importBtn) importBtn.disabled = false;
    } catch (err) {
      if (infoEl) {
        infoEl.textContent = 'Error: ' + err.message;
        infoEl.className = 'small text-danger mb-2';
        infoEl.style.display = '';
      }
    }
  };
  reader.readAsText(file);
}

function _gcfParseGpx(text) {
  var parser = new DOMParser();
  var doc = parser.parseFromString(text, 'application/xml');
  if (doc.querySelector('parsererror')) throw new Error('Invalid GPX file');

  var allPoints = [];
  var segmentCount = 0;

  // <trk><trkseg><trkpt> — standard GPS tracks
  doc.querySelectorAll('trk').forEach(function(trk) {
    trk.querySelectorAll('trkseg').forEach(function(seg) {
      var pts = [];
      seg.querySelectorAll('trkpt').forEach(function(pt) {
        var lat = parseFloat(pt.getAttribute('lat'));
        var lon = parseFloat(pt.getAttribute('lon'));
        if (!isNaN(lat) && !isNaN(lon)) pts.push([lon, lat]);
      });
      if (pts.length >= 2) { allPoints = allPoints.concat(pts); segmentCount++; }
    });
  });

  // <rte><rtept> — route waypoints, used as fallback if no track segments
  if (!allPoints.length) {
    doc.querySelectorAll('rte').forEach(function(rte) {
      var pts = [];
      rte.querySelectorAll('rtept').forEach(function(pt) {
        var lat = parseFloat(pt.getAttribute('lat'));
        var lon = parseFloat(pt.getAttribute('lon'));
        if (!isNaN(lat) && !isNaN(lon)) pts.push([lon, lat]);
      });
      if (pts.length >= 2) { allPoints = allPoints.concat(pts); segmentCount++; }
    });
  }

  if (allPoints.length < 2) throw new Error('No track data found in GPX');

  // Estimate total length
  var lengthM = 0;
  for (var i = 1; i < allPoints.length; i++) {
    lengthM += _gcfHaversineM(allPoints[i-1][1], allPoints[i-1][0], allPoints[i][1], allPoints[i][0]);
  }

  return {
    points: allPoints,
    segmentCount: segmentCount,
    originalCount: allPoints.length,
    lengthKm: lengthM / 1000
  };
}

function gcfImportGpxConfirm() {
  if (!_gcfGpxParsed || !_gcfDrawCtrl) return;

  var widthKm = parseFloat(document.getElementById('gpx-width-input').value) || 1;
  var widthM = Math.round(widthKm * 1000);
  var path = _gcfGpxParsed.points;

  // Close modal before modifying map state
  var modalEl = document.getElementById('gpxImportDialog');
  var modal = bootstrap.Modal.getInstance(modalEl);
  if (modal) modal.hide();

  // Add to MapboxDraw as a corridor LineString (same as restored corridors)
  var ids = _gcfDrawCtrl.add({
    type: 'Feature',
    properties: { shape: 'corridor', width_m: widthM },
    geometry: { type: 'LineString', coordinates: path }
  });
  if (ids && ids.length) {
    _gcfDrawRegions.push({ type: 'corridor', path: path, width_m: widthM, id: ids[0] });
    _gcfCorridorWidthM = widthM;
    var corridorInput = document.getElementById('map-corridor-width');
    if (corridorInput) corridorInput.value = widthKm.toFixed(1);
  }

  _gcfUpdateDrawStatus();
  _gcfUpdateCorridorBuffer();
  _gcfUpdateCorridorBoxes();

  // Fit map to the imported track
  if (gcfMap && path.length) {
    var lats = path.map(function(p) { return p[1]; });
    var lngs = path.map(function(p) { return p[0]; });
    gcfMap.fitBounds(
      [[Math.min.apply(null, lngs), Math.min.apply(null, lats)],
       [Math.max.apply(null, lngs), Math.max.apply(null, lats)]],
      { padding: 40, maxZoom: 14 }
    );
  }
}
