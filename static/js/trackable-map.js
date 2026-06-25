// ── GCForge Trackable Index Map ───────────────────────────────────────────────
//
// Pin map for /trackables/map/. Uses HTML markers so each TB can show its
// own icon (from geocaching.com — won't load through MapLibre's image API
// because the CDN doesn't send CORS headers, but works fine as a plain
// <img> inside a Marker element).
//
// Depends on: maplibre-gl, map-styles.js (GCF_STYLES, gcfMapTransformRequest,
//             gcfSuppressMissingImages)

var _gcfTbIndexMap = null;
var _gcfTbIndexStyleId = 'street';
var _gcfTbIndexMarkers = [];

function gcfTbIndexMapInit() {
  var mapEl = document.getElementById('tb-index-map');
  if (!mapEl || _gcfTbIndexMap) return;
  if (typeof maplibregl === 'undefined' || typeof GCF_STYLES === 'undefined') return;

  var savedStyle = localStorage.getItem('gcforge_map_style') || 'street';
  if (!GCF_STYLES[savedStyle]) savedStyle = 'street';
  _gcfTbIndexStyleId = savedStyle;

  var homeLat = parseFloat(mapEl.dataset.homeLat) || 51;
  var homeLon = parseFloat(mapEl.dataset.homeLon) || 10;

  _gcfTbIndexMap = new maplibregl.Map({
    container: 'tb-index-map',
    style: GCF_STYLES[savedStyle],
    center: [homeLon, homeLat],
    zoom: 4,
    attributionControl: true,
    transformRequest: gcfMapTransformRequest
  });

  _gcfTbIndexMap.addControl(new maplibregl.NavigationControl(), 'top-left');
  _gcfTbIndexMap.addControl(new maplibregl.ScaleControl({ maxWidth: 100, unit: 'metric' }), 'bottom-left');

  if (typeof gcfSuppressMissingImages === 'function') gcfSuppressMissingImages(_gcfTbIndexMap);

  _gcfTbIndexHighlightLayerBtn();

  var pinsUrl = mapEl.dataset.pinsUrl;
  _gcfTbIndexMap.on('load', function() {
    _gcfTbIndexMap.resize();
    fetch(pinsUrl)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.ok && data.pins) _gcfTbIndexAddPins(data.pins);
      })
      .catch(function(err) { console.error('trackable-map pins fetch:', err); });
  });

  if (window.ResizeObserver) {
    new ResizeObserver(function() { if (_gcfTbIndexMap) _gcfTbIndexMap.resize(); }).observe(mapEl);
  }
  window.addEventListener('resize', function() { if (_gcfTbIndexMap) _gcfTbIndexMap.resize(); });
  requestAnimationFrame(function() { if (_gcfTbIndexMap) _gcfTbIndexMap.resize(); });
  setTimeout(function() { if (_gcfTbIndexMap) _gcfTbIndexMap.resize(); }, 300);
}

function _gcfTbIndexAddPins(pins) {
  if (!_gcfTbIndexMap || !pins.length) return;

  _gcfTbIndexClearMarkers();

  pins.forEach(function(p) {
    var el = document.createElement('div');
    el.className = 'tb-marker';

    var img = document.createElement('img');
    img.src = p.icon_url || '';
    img.alt = '';
    img.referrerPolicy = 'no-referrer';
    img.className = 'tb-marker-icon';
    el.appendChild(img);

    var label = document.createElement('div');
    label.className = 'tb-marker-label';
    label.textContent = p.name || p.ref;
    label.title = p.name || '';
    el.appendChild(label);

    var marker = new maplibregl.Marker({ element: el, anchor: 'bottom' })
      .setLngLat([p.lon, p.lat])
      .addTo(_gcfTbIndexMap);

    el.addEventListener('click', function(e) {
      e.stopPropagation();
      _gcfTbIndexOpenPopup(p);
    });

    _gcfTbIndexMarkers.push(marker);
  });

  // Fit to pin bounds
  var lats = pins.map(function(p) { return p.lat; });
  var lons = pins.map(function(p) { return p.lon; });
  if (pins.length === 1) {
    _gcfTbIndexMap.flyTo({ center: [lons[0], lats[0]], zoom: 10 });
  } else {
    var minLat = Math.min.apply(null, lats);
    var maxLat = Math.max.apply(null, lats);
    var minLon = Math.min.apply(null, lons);
    var maxLon = Math.max.apply(null, lons);
    _gcfTbIndexMap.fitBounds([[minLon, minLat], [maxLon, maxLat]], { padding: 60, maxZoom: 14 });
  }
}

function _gcfTbIndexClearMarkers() {
  _gcfTbIndexMarkers.forEach(function(m) { m.remove(); });
  _gcfTbIndexMarkers = [];
}

function _gcfTbIndexOpenPopup(p) {
  var html = '<strong>' + _gcfTbIdxEsc(p.name) + '</strong>'
           + ' <span class="font-monospace small">(<a target="_blank" href="https://coord.info/' + _gcfTbIdxEsc(p.ref) + '">' + _gcfTbIdxEsc(p.ref) + '</a>)</span><br>'
           + '<span class="badge bg-secondary">' + _gcfTbIdxEsc(p.state_label) + '</span>';
  if (p.series) html += ' <span class="text-muted small">' + _gcfTbIdxEsc(p.series) + '</span>';
  html += '<br>';
  if (p.state === 'in_cache' && p.cache_code) {
    html += gettext('In:') + ' <a href="https://coord.info/' + _gcfTbIdxEsc(p.cache_code) + '" target="_blank" rel="noopener">'
          + _gcfTbIdxEsc(p.cache_code)
          + (p.cache_name ? ' — ' + _gcfTbIdxEsc(p.cache_name) : '')
          + '</a><br>';
  }
  if (p.state === 'held_by_other' && p.holder_name) {
    html += gettext('Holder:') + ' <a href="https://www.geocaching.com/p/?u=' + encodeURIComponent(p.holder_name) + '" target="_blank" rel="noopener">'
          + _gcfTbIdxEsc(p.holder_name) + '</a><br>';
  }
  if (p.distance_km) {
    html += interpolate(gettext('Travelled: %s km'), [parseFloat(p.distance_km).toFixed(0)]) + '<br>';
  }
  html += '<a href="' + _gcfTbIdxEsc(p.detail_url) + '">' + gettext('Open detail page &rarr;') + '</a>';

  new maplibregl.Popup({ closeButton: true, maxWidth: '300px' })
    .setLngLat([p.lon, p.lat])
    .setHTML(html)
    .addTo(_gcfTbIndexMap);
}

function _gcfTbIdxEsc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function gcfTbIndexSetLayer(styleId, btn) {
  if (!_gcfTbIndexMap || !GCF_STYLES[styleId]) return;
  _gcfTbIndexStyleId = styleId;
  localStorage.setItem('gcforge_map_style', styleId);
  _gcfTbIndexMap.setStyle(GCF_STYLES[styleId]);
  _gcfTbIndexHighlightLayerBtn();
}

function _gcfTbIndexHighlightLayerBtn() {
  document.querySelectorAll('#tb-index-layer-switcher button[data-layer]').forEach(function(b) {
    b.classList.toggle('active', b.dataset.layer === _gcfTbIndexStyleId);
  });
}
