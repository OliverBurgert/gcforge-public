// ── GCForge Map Search — coordinate parsing + Nominatim geocoding ─────────────
//
// Depends on: cache-map.js (gcfMap), MapLibre GL JS (maplibregl)

var _gcfSearchTimer = null;
var _gcfSearchPin = null;

function gcfSearchInit() {
  var input = document.getElementById('map-search-input');
  if (!input) return;

  input.addEventListener('input', function() {
    var val = input.value.trim();
    _gcfSearchClearResults();
    if (!val) return;

    // Try coordinate parsing immediately
    var coords = _gcfParseCoordinates(val);
    if (coords) {
      _gcfSearchFlyTo(coords.lat, coords.lon, val);
      return;
    }

    // Nominatim geocoding with 800ms debounce (respects ~1 req/s policy)
    if (_gcfSearchTimer) clearTimeout(_gcfSearchTimer);
    _gcfSearchTimer = setTimeout(function() { _gcfNominatimSearch(val); }, 800);
  });

  input.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      input.value = '';
      _gcfSearchClearResults();
    }
  });

  document.addEventListener('click', function(e) {
    var box = document.getElementById('map-search-box');
    if (box && !box.contains(e.target)) _gcfSearchClearResults();
  });
}

// ── Coordinate parsing ────────────────────────────────────────────────────────

function _gcfParseCoordinates(input) {
  input = input.trim();

  // Decimal degrees: "48.858844, 2.294351" or "48.858844 2.294351"
  var ddMatch = input.match(/^(-?\d{1,3}\.?\d*)[,\s]+(-?\d{1,3}\.?\d*)$/);
  if (ddMatch) {
    var lat = parseFloat(ddMatch[1]);
    var lon = parseFloat(ddMatch[2]);
    if (lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180 && (lat !== 0 || lon !== 0)) {
      return { lat: lat, lon: lon };
    }
  }

  // DMS: N 48° 51' 31.8" E 002° 17' 39.7"
  var dmsMatch = input.match(
    /([NS])\s*(\d+)[°\u00b0\s]+(\d+)['\u2019\u02bc\s]+(\d+\.?\d*)["'\u201d]?\s+([EW])\s*(\d+)[°\u00b0\s]+(\d+)['\u2019\u02bc\s]+(\d+\.?\d*)["'\u201d]?/i
  );
  if (dmsMatch) {
    var latD = parseInt(dmsMatch[2]) + parseInt(dmsMatch[3]) / 60 + parseFloat(dmsMatch[4]) / 3600;
    var lonD = parseInt(dmsMatch[6]) + parseInt(dmsMatch[7]) / 60 + parseFloat(dmsMatch[8]) / 3600;
    if (dmsMatch[1].toUpperCase() === 'S') latD = -latD;
    if (dmsMatch[5].toUpperCase() === 'W') lonD = -lonD;
    return { lat: latD, lon: lonD };
  }

  // DDM: N 48° 51.530 E 002° 17.661
  var ddmMatch = input.match(
    /([NS])\s*(\d+)[°\u00b0\s]+(\d+\.?\d*)'?\s+([EW])\s*(\d+)[°\u00b0\s]+(\d+\.?\d*)'?/i
  );
  if (ddmMatch) {
    var latD = parseInt(ddmMatch[2]) + parseFloat(ddmMatch[3]) / 60;
    var lonD = parseInt(ddmMatch[5]) + parseFloat(ddmMatch[6]) / 60;
    if (ddmMatch[1].toUpperCase() === 'S') latD = -latD;
    if (ddmMatch[4].toUpperCase() === 'W') lonD = -lonD;
    return { lat: latD, lon: lonD };
  }

  return null;
}

// ── Nominatim ─────────────────────────────────────────────────────────────────

function _gcfNominatimSearch(query) {
  var params = new URLSearchParams({ q: query, format: 'json', limit: 5 });
  // Browser provides a User-Agent automatically; Referer comes from the page origin
  fetch('https://nominatim.openstreetmap.org/search?' + params)
    .then(function(r) { return r.json(); })
    .then(function(results) { _gcfShowSearchResults(results); })
    .catch(function() {});
}

function _gcfShowSearchResults(results) {
  var container = document.getElementById('map-search-results');
  if (!container) return;
  container.innerHTML = '';

  if (!results || !results.length) {
    container.style.display = 'none';
    return;
  }

  results.forEach(function(r) {
    var item = document.createElement('button');
    item.className = 'map-search-result-item';
    item.textContent = r.display_name;
    item.title = r.display_name;
    item.onclick = function() {
      _gcfSearchFlyTo(parseFloat(r.lat), parseFloat(r.lon), r.display_name);
      var input = document.getElementById('map-search-input');
      if (input) input.value = r.display_name.split(',')[0];
      container.style.display = 'none';
    };
    container.appendChild(item);
  });
  container.style.display = 'block';
}

function _gcfSearchClearResults() {
  var container = document.getElementById('map-search-results');
  if (container) container.style.display = 'none';
}

function _gcfSearchFlyTo(lat, lon, label) {
  if (!gcfMap) return;
  gcfMap.flyTo({ center: [lon, lat], zoom: 14 });

  if (_gcfSearchPin) {
    _gcfSearchPin.remove();
    _gcfSearchPin = null;
  }
  _gcfSearchPin = new maplibregl.Marker({ color: '#dc3545' })
    .setLngLat([lon, lat])
    .setPopup(new maplibregl.Popup({ offset: 12 }).setText(label))
    .addTo(gcfMap);
}
