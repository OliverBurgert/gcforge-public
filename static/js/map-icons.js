/**
 * GCForge map icon system — loads c:geo SVG icons and generates
 * colored canvas icon images for MapLibre GL markers.
 *
 * Convention: GC caches = circle background, OC caches = rounded rectangle.
 * Status overlays: archived stripes, found dot, mine border.
 */

// Map type short codes (from views_map.py) to SVG filenames and c:geo colors
var GCF_TYPE_ICON_MAP = {
  'T':  { svg: 'traditional', color: '#388E3C' },
  'M':  { svg: 'multi',       color: '#F57C00' },
  'U':  { svg: 'mystery',     color: '#303f9f' },
  'V':  { svg: 'virtual',     color: '#0288d1' },
  'E':  { svg: 'earth',       color: '#0288d1' },
  'Ev': { svg: 'event',       color: '#d32f2f' },
  'CI': { svg: 'cito',        color: '#d32f2f' },
  'W':  { svg: 'webcam',      color: '#0288d1' },
  'Wh': { svg: 'wherigo',     color: '#303f9f' },
  'L':  { svg: 'advlab',      color: '#7b1fa2' },
  'B':  { svg: 'letterbox',   color: '#303f9f' },
  'ME': { svg: 'mega',        color: '#d32f2f' },
  'GE': { svg: 'giga',        color: '#d32f2f' },
  'Lo': { svg: 'locationless', color: '#616161' },
  'GA': { svg: 'maze',        color: '#afb42b' },
  'CC': { svg: 'specialevent', color: '#d32f2f' },
  'HQ': { svg: 'hq',          color: '#afb42b' },
  'PA': { svg: 'ape',         color: '#afb42b' },
  'BM': { svg: 'benchmark',   color: '#616161' },
  'DI': { svg: 'drivein',     color: '#616161' },
  'MP': { svg: 'mathphysics', color: '#303f9f' },
  'Mo': { svg: 'moving',      color: '#616161' },
  'O':  { svg: 'own',         color: '#f7991d' },
  'Po': { svg: 'podcast',     color: '#616161' },
  '?':  { svg: 'unknown',     color: '#616161' }
};

// Status overrides — archived/disabled replace type color, others darken border
var GCF_STATUS_OVERRIDES = {
  'X':  { fill: '#6c757d', border: '#495057' },   // archived: override to grey
  'D':  { fill: '#90a4ae', border: '#6c757d' }     // disabled: override to pale
};

var _gcfIconImages = {};     // loaded SVG Image objects keyed by filename
var _gcfIconCache = {};      // generated MapLibre images keyed by image ID
var _gcfIconsLoaded = false;
var _gcfIconBasePath = '';    // set from Django static path

/**
 * Compute the visual status key for a marker.
 */
function _gcfStatusKey(m) {
  if (m.s === 'X') return 'X';
  if (m.s === 'D') return 'D';
  if (m.m) return 'M';
  if (m.f) return 'F';
  return 'U';
}

/**
 * Build the image ID for a given marker.
 * Format: "i-{typeShort}-{gc|oc}-{statusKey}"
 */
function gcfMapIconId(typeShort, platform, statusKey) {
  return 'i-' + typeShort + '-' + platform + '-' + statusKey;
}

/**
 * Determine platform string from marker properties.
 */
function _gcfPlatform(props) {
  return (props.gcCode && props.gcCode !== 'null') ? 'gc' : 'oc';
}

/**
 * Load all c:geo type SVGs as Image objects, then call onReady.
 */
function gcfLoadMapIcons(basePath, onReady) {
  _gcfIconBasePath = basePath;

  // Collect all unique SVG names from type and waypoint maps
  var unique = [];
  var seen = {};
  var entries = Object.values(GCF_TYPE_ICON_MAP);
  for (var i = 0; i < entries.length; i++) {
    if (!seen[entries[i].svg]) { unique.push({ name: entries[i].svg, dir: 'types' }); seen[entries[i].svg] = true; }
  }
  var wpEntries = Object.values(GCF_WP_ICON_MAP);
  for (var w = 0; w < wpEntries.length; w++) {
    if (!seen[wpEntries[w].svg]) { unique.push({ name: wpEntries[w].svg, dir: 'waypoints' }); seen[wpEntries[w].svg] = true; }
  }

  var loaded = 0;
  var total = unique.length;

  function checkDone() {
    if (loaded >= total) {
      _gcfIconsLoaded = true;
      onReady();
    }
  }

  // basePath is e.g. '/static/icons/cgeo/types/' — derive root from it
  var iconRoot = basePath.replace(/types\/$/, '');

  for (var j = 0; j < unique.length; j++) {
    (function(item) {
      var img = new Image();
      img.onload = function() {
        _gcfIconImages[item.name] = img;
        loaded++;
        checkDone();
      };
      img.onerror = function() {
        console.warn('GCForge: failed to load icon', item.name);
        loaded++;
        checkDone();
      };
      img.src = iconRoot + item.dir + '/' + item.name + '.svg';
    })(unique[j]);
  }

  if (total === 0) onReady();
}

/**
 * Draw a rounded rectangle path on a canvas context.
 */
function _gcfRoundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

/**
 * Generate a single map icon image and register it with the map.
 * Returns the imageData object for map.addImage().
 */
function _gcfGenerateIcon(typeShort, platform, statusKey, size) {
  size = size || 32;
  var canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  var ctx = canvas.getContext('2d');

  // Use type-specific color, with status overrides for archived/disabled
  var typeInfo = GCF_TYPE_ICON_MAP[typeShort] || GCF_TYPE_ICON_MAP['?'];
  var override = GCF_STATUS_OVERRIDES[statusKey];
  var fillColor = override ? override.fill : typeInfo.color;
  var borderColor = override ? override.border : '#000';
  var pad = 2;

  // Draw background shape
  if (platform === 'oc') {
    // Rounded rectangle for OC
    _gcfRoundRect(ctx, pad, pad, size - 2 * pad, size - 2 * pad, 5);
    ctx.fillStyle = fillColor;
    ctx.fill();
    _gcfRoundRect(ctx, pad, pad, size - 2 * pad, size - 2 * pad, 5);
    ctx.strokeStyle = borderColor;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  } else {
    // Circle for GC (default)
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2 - pad, 0, Math.PI * 2);
    ctx.fillStyle = fillColor;
    ctx.fill();
    ctx.strokeStyle = borderColor;
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  // Archived overlay: red diagonal stripes
  if (statusKey === 'X') {
    ctx.save();
    if (platform === 'oc') {
      _gcfRoundRect(ctx, pad, pad, size - 2 * pad, size - 2 * pad, 5);
    } else {
      ctx.beginPath();
      ctx.arc(size / 2, size / 2, size / 2 - pad, 0, Math.PI * 2);
    }
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

  // Draw SVG type icon in the center.
  // Guard against SVGs with no intrinsic size (viewBox-only) — drawImage
  // throws "source width is 0" in some browsers for those.
  var iconEntry = GCF_TYPE_ICON_MAP[typeShort] || GCF_TYPE_ICON_MAP['?'];
  var svgImg = iconEntry ? _gcfIconImages[iconEntry.svg] : null;
  var svgDrawn = false;
  if (svgImg && svgImg.naturalWidth > 0 && svgImg.naturalHeight > 0) {
    try {
      var iconSize = Math.round(size * 1.3);
      var iconOffset = Math.round((size - iconSize) / 2);
      ctx.drawImage(svgImg, iconOffset, iconOffset, iconSize, iconSize);
      svgDrawn = true;
    } catch (e) { /* fall through to letter fallback */ }
  }
  if (!svgDrawn) {
    // Fallback: draw the letter code (same as text mode)
    var letter = typeShort || '?';
    ctx.fillStyle = '#fff';
    ctx.font = 'bold ' + (letter.length > 1 ? '9' : '11') + 'px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(letter, size / 2, size / 2 + 0.5);
  }

  return { width: size, height: size, data: ctx.getImageData(0, 0, size, size).data };
}

/**
 * Ensure an icon image is registered for the given combo.
 * Returns the image ID string.
 */
function gcfEnsureMapIcon(map, typeShort, platform, statusKey) {
  var id = gcfMapIconId(typeShort, platform, statusKey);
  if (!_gcfIconCache[id]) {
    var imgData = _gcfGenerateIcon(typeShort, platform, statusKey);
    map.addImage(id, imgData);
    _gcfIconCache[id] = true;
  }
  return id;
}

/**
 * Pre-generate all icon images needed for the given marker set.
 * Call this before adding the symbol layer.
 */
function gcfPrepareMapIcons(map, markers) {
  var needed = {};
  for (var i = 0; i < markers.length; i++) {
    var m = markers[i];
    var sk = _gcfStatusKey(m);
    var plat = (m.oc && !m.gc) ? 'oc' : 'gc';
    var key = m.t + '|' + plat + '|' + sk;
    if (!needed[key]) {
      needed[key] = { type: m.t, platform: plat, statusKey: sk };
    }
  }
  var keys = Object.keys(needed);
  for (var j = 0; j < keys.length; j++) {
    var n = needed[keys[j]];
    gcfEnsureMapIcon(map, n.type, n.platform, n.statusKey);
  }
}

// Waypoint type short codes → SVG filenames and colors
var GCF_WP_ICON_MAP = {
  'P': { svg: 'pkg',       color: '#0d6efd' },
  'S': { svg: 'stage',     color: '#fd7e14' },
  'F': { svg: 'flag',      color: '#198754' },
  'Q': { svg: 'puzzle',    color: '#6f42c1' },
  'T': { svg: 'trailhead', color: '#795548' },
  'R': { svg: 'waypoint',  color: '#6c757d' },
  'O': { svg: 'waypoint',  color: '#6c757d' }
};

/**
 * Generate a waypoint icon (smaller, no status overlays).
 */
function _gcfGenerateWpIcon(wpType, size) {
  size = size || 24;
  var canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  var ctx = canvas.getContext('2d');

  var info = GCF_WP_ICON_MAP[wpType] || GCF_WP_ICON_MAP['O'];
  var pad = 2;

  // Draw white circle with thin black outline
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size / 2 - pad, 0, Math.PI * 2);
  ctx.fillStyle = '#fff';
  ctx.fill();
  ctx.strokeStyle = '#333';
  ctx.lineWidth = 1;
  ctx.stroke();

  // Draw SVG icon large (guard against no-intrinsic-size SVGs)
  var svgImg = _gcfIconImages[info.svg];
  var wpDrawn = false;
  if (svgImg && svgImg.naturalWidth > 0 && svgImg.naturalHeight > 0) {
    try {
      var iconSize = Math.round(size * 1.4);
      var iconOffset = Math.round((size - iconSize) / 2);
      ctx.drawImage(svgImg, iconOffset, iconOffset, iconSize, iconSize);
      wpDrawn = true;
    } catch (e) { /* fall through to letter fallback */ }
  }
  if (!wpDrawn) {
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 9px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(wpType || '?', size / 2, size / 2 + 0.5);
  }

  return { width: size, height: size, data: ctx.getImageData(0, 0, size, size).data };
}

/**
 * Ensure a waypoint icon is registered. Returns the image ID.
 */
function gcfEnsureWpIcon(map, wpType) {
  var id = 'wp-' + wpType;
  if (!_gcfIconCache[id]) {
    var imgData = _gcfGenerateWpIcon(wpType);
    map.addImage(id, imgData);
    _gcfIconCache[id] = true;
  }
  return id;
}

/**
 * Pre-generate all waypoint icon types.
 */
function gcfPrepareWpIcons(map) {
  var types = Object.keys(GCF_WP_ICON_MAP);
  for (var i = 0; i < types.length; i++) {
    gcfEnsureWpIcon(map, types[i]);
  }
}

/**
 * Remove all registered icon images from the map (for style changes).
 */
function gcfClearMapIcons(map) {
  var ids = Object.keys(_gcfIconCache);
  for (var i = 0; i < ids.length; i++) {
    if (map.hasImage(ids[i])) map.removeImage(ids[i]);
  }
  _gcfIconCache = {};
}
