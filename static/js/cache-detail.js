// --- Back-to-list URL restore ---
  (function () {
    var saved = sessionStorage.getItem('gcforge_list_url');
    if (!saved) return;
    var back = document.getElementById('back-to-list');
    if (back) back.href = saved;
    var deleteNext = document.getElementById('delete-next');
    if (deleteNext) deleteNext.value = saved;
  })();

  // --- Log text expand/collapse ---
  window.toggleLogText = function(link) {
    var item = link.parentElement;
    var shortEl = item.querySelector('.log-text-short');
    var fullEl  = item.querySelector('.log-text-full');
    if (!shortEl || !fullEl) return;
    var expanded = fullEl.classList.contains('d-none');
    shortEl.classList.toggle('d-none', expanded);
    fullEl.classList.toggle('d-none', !expanded);
    link.textContent = expanded ? 'less' : 'more';
  };

  // --- Map setup ---
  var cfg = document.getElementById('cache-detail-config');
  if (cfg) {

  var cacheLat = parseFloat(cfg.dataset.cacheLat);
  var cacheLon = parseFloat(cfg.dataset.cacheLon);
  var corrLat = cfg.dataset.corrLat !== undefined ? parseFloat(cfg.dataset.corrLat) : null;
  var corrLon = cfg.dataset.corrLon !== undefined ? parseFloat(cfg.dataset.corrLon) : null;
  var _saveMapUrl = cfg.dataset.saveMapUrl;
  var _resetMapUrl = cfg.dataset.resetMapUrl;
  var _csrfToken = cfg.dataset.csrfToken;
  var mapState = null;
  try { if (cfg.dataset.mapState) mapState = JSON.parse(cfg.dataset.mapState); } catch(e) {}
  var stages = [];
  try { stages = JSON.parse(cfg.dataset.stages || '[]'); } catch(e) {}
  var waypoints = [];
  try {
    var _wpEl = document.getElementById('cache-map-waypoints');
    if (_wpEl) waypoints = JSON.parse(_wpEl.textContent);
  } catch(e) {}
  var cacheCode = cfg.dataset.cacheCode;
  var cacheName = cfg.dataset.cacheName;

  var layers = {
    osm: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© <a href="https://openstreetmap.org">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }),
    topo: L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
      attribution: '© OpenTopoMap contributors',
      maxZoom: 17,
      referrerPolicy: 'origin',
    }),
    satellite: L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      attribution: 'Tiles © Esri',
      maxZoom: 19,
    }),
  };

  var focusLat = corrLat !== null ? corrLat : cacheLat;
  var focusLon = corrLon !== null ? corrLon : cacheLon;

  // Collect all known marker positions for bounds fitting
  var allLatLngs = [[cacheLat, cacheLon]];
  if (corrLat !== null) allLatLngs.push([corrLat, corrLon]);
  stages.forEach(function(s) { allLatLngs.push([s.lat, s.lon]); });
  waypoints.forEach(function(w) { allLatLngs.push([w.lat, w.lon]); });

  var map = L.map('cache-map');
  if (mapState) {
    map.setView([mapState.lat, mapState.lon], mapState.zoom);
  } else {
    if (allLatLngs.length > 1) {
      map.fitBounds(L.latLngBounds(allLatLngs).pad(0.2), {maxZoom: 16});
    } else {
      map.setView([focusLat, focusLon], 14);
    }
  }
  layers.osm.addTo(map);
  var activeLayerName = 'osm';

  // Persist zoom/pan after user interaction (debounced, fire-and-forget)
  var _saveTimer = null;
  map.on('moveend zoomend', function() {
    clearTimeout(_saveTimer);
    _saveTimer = setTimeout(function() {
      var c = map.getCenter();
      fetch(_saveMapUrl, {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': _csrfToken},
        body: 'zoom=' + map.getZoom() + '&lat=' + c.lat + '&lon=' + c.lng,
      });
    }, 800);
  });

  window.resetMapView = function() {
    fetch(_resetMapUrl, {
      method: 'POST',
      headers: {'X-CSRFToken': _csrfToken},
    }).then(function() {
      if (allLatLngs.length > 1) {
        map.fitBounds(L.latLngBounds(allLatLngs).pad(0.2), {maxZoom: 16});
      } else {
        map.setView([focusLat, focusLon], 14);
      }
    });
  };

  // Cache marker — use type-colored icon in c:geo mode, pin otherwise
  var _detailTypeColors = {
    'Traditional':'#388E3C','Multi-Cache':'#F57C00','Mystery':'#303f9f',
    'Virtual':'#0288d1','Letterbox Hybrid':'#303f9f','Earthcache':'#0288d1',
    'Event':'#d32f2f','CITO':'#d32f2f','Webcam':'#0288d1','Wherigo':'#303f9f',
    'Adventure Lab':'#7b1fa2','Mega-Event':'#d32f2f','Giga-Event':'#d32f2f',
    'Locationless':'#616161','GPS Adventures Exhibit':'#afb42b',
    'Community Celebration Event':'#d32f2f','Geocaching HQ':'#afb42b',
    'Geocaching HQ Celebration':'#d32f2f','Geocaching HQ Block Party':'#d32f2f',
    'Project A.P.E.':'#afb42b','Unknown':'#616161'
  };
  var _detailTypeIcons = {
    'Traditional':'traditional','Multi-Cache':'multi','Mystery':'mystery',
    'Virtual':'virtual','Letterbox Hybrid':'letterbox','Earthcache':'earth',
    'Event':'event','CITO':'cito','Webcam':'webcam','Wherigo':'wherigo',
    'Adventure Lab':'advlab','Mega-Event':'mega','Giga-Event':'giga',
    'Locationless':'locationless','GPS Adventures Exhibit':'maze',
    'Community Celebration Event':'specialevent','Geocaching HQ':'hq',
    'Geocaching HQ Celebration':'event_hq','Geocaching HQ Block Party':'event_blockparty',
    'Project A.P.E.':'ape','NGS Benchmark':'benchmark','Drive-In':'drivein',
    'Math/Physics':'mathphysics','Moving':'moving','Own':'own','Podcast':'podcast',
    'Unknown':'unknown'
  };
  var detailCacheType = cfg.dataset.cacheType || '';
  var detailPlatform = cfg.dataset.platform || 'gc';
  var detailIconSet = cfg.dataset.iconSet || 'text';
  var typeColor = _detailTypeColors[detailCacheType] || '#dc3545';
  var cacheIcon;
  if (detailIconSet === 'cgeo' && _detailTypeIcons[detailCacheType]) {
    var svgName = _detailTypeIcons[detailCacheType];
    var borderRadius = detailPlatform === 'oc' ? '4px' : '50%';
    cacheIcon = L.divIcon({
      className: '',
      html: '<div style="width:28px;height:28px;background:' + typeColor + ';border:2px solid #fff;border-radius:' + borderRadius + ';display:flex;align-items:center;justify-content:center;box-shadow:1px 1px 3px rgba(0,0,0,.4)">'
           + '<img src="/static/icons/cgeo/types/' + svgName + '.svg" style="width:28px;height:28px">'
           + '</div>',
      iconSize: [28, 28],
      iconAnchor: [14, 14],
    });
  } else {
    cacheIcon = L.divIcon({
      className: '',
      html: '<div style="width:24px;height:24px;background:#dc3545;border:2px solid #fff;border-radius:50% 50% 50% 0;transform:rotate(-45deg);box-shadow:2px 2px 4px rgba(0,0,0,.4)"></div>',
      iconSize: [24, 24],
      iconAnchor: [12, 24],
    });
  }
  L.marker([cacheLat, cacheLon], {icon: cacheIcon})
    .addTo(map)
    .bindPopup('<strong>' + cacheCode + '</strong><br>' + cacheName);

  // Corrected coords marker
  if (corrLat !== null) {
    var corrIcon = L.divIcon({
      className: '',
      html: '<div style="width:20px;height:20px;background:#198754;border:2px solid #fff;border-radius:50% 50% 50% 0;transform:rotate(-45deg);box-shadow:2px 2px 4px rgba(0,0,0,.4)"></div>',
      iconSize: [20, 20],
      iconAnchor: [10, 20],
    });
    L.marker([corrLat, corrLon], {icon: corrIcon})
      .addTo(map)
      .bindPopup('Corrected coordinates');
  }

  // Adventure Lab stages — numbered markers
  var stageMarkers = [];
  stages.forEach(function(s) {
    var bg = s.found ? '#198754' : '#6f42c1';
    stageMarkers.push(
      L.marker([s.lat, s.lon], {
        icon: L.divIcon({
          className: '',
          html: '<div style="width:22px;height:22px;background:' + bg + ';border:2px solid #fff;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:bold;box-shadow:1px 1px 3px rgba(0,0,0,.4)">' + s.num + '</div>',
          iconSize: [22, 22],
          iconAnchor: [11, 11],
        })
      }).addTo(map).bindPopup('<strong>Stage ' + s.num + '</strong><br>' + s.name)
    );
  });

  window.toggleStages = function(btn) {
    var visible = btn.classList.contains('btn-primary');
    stageMarkers.forEach(function(m) { if (visible) map.removeLayer(m); else m.addTo(map); });
    btn.classList.toggle('btn-primary', !visible);
    btn.classList.toggle('btn-outline-secondary', visible);
  };

  // Additional waypoints — stored so they can be toggled
  var waypointMarkers = [];
  waypoints.forEach(function(w) {
    waypointMarkers.push(
      L.circleMarker([w.lat, w.lon], {
        radius: 6, color: '#0d6efd', fillColor: '#0d6efd', fillOpacity: 0.7
      }).addTo(map).bindPopup(w.type + ': ' + w.name)
    );
  });

  window.toggleWaypoints = function(btn) {
    var visible = btn.classList.contains('btn-primary');
    waypointMarkers.forEach(function(m) { if (visible) map.removeLayer(m); else m.addTo(map); });
    btn.classList.toggle('btn-primary', !visible);
    btn.classList.toggle('btn-outline-secondary', visible);
  };

  window.setLayer = function(name, btn) {
    map.removeLayer(layers[activeLayerName]);
    layers[name].addTo(map);
    activeLayerName = name;
    document.querySelectorAll('#layer-switcher .btn').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
  };

  // --- Corrected coordinates form ---
  window.toggleCorrectedForm = function() {
    var form = document.getElementById('corrected-form');
    var disp = document.getElementById('corrected-display');
    var visible = form.style.display !== 'none';
    form.style.display = visible ? 'none' : '';
    if (disp) disp.style.display = visible ? '' : 'none';
    if (!visible) { var el = document.getElementById('corr-lat'); if (el) el.focus(); }
  };

  // --- Coordinate auto-split: detect lat+lon in the lat field, move lon part ---
  // Uses data-lon-target attribute to find the paired lon field.
  _gcfSetupAllCoordAutoSplit();

  window.toggleRefPointForm = function() {
    var form = document.getElementById('refpoint-form');
    form.style.display = form.style.display === 'none' ? '' : 'none';
  };

  // --- Hint decode (encrypted mode) ---
  window.toggleHintDecode = function(btn) {
    var card = btn.closest('.card');
    var rot = card.querySelector('.hint-rot13');
    var plain = card.querySelector('.hint-plain');
    if (rot && plain) {
      var isEncrypted = !rot.classList.contains('d-none');
      rot.classList.toggle('d-none', isEncrypted);
      plain.classList.toggle('d-none', !isEncrypted);
      btn.textContent = isEncrypted ? 'encode' : 'decode';
    }
  };

  // --- Description toggle ---
  window.showRendered = function() {
    document.getElementById('desc-rendered').style.display = '';
    document.getElementById('desc-source').style.display = 'none';
    document.getElementById('btn-rendered').classList.add('active');
    document.getElementById('btn-source').classList.remove('active');
  };
  window.showSource = function() {
    document.getElementById('desc-rendered').style.display = 'none';
    document.getElementById('desc-source').style.display = '';
    document.getElementById('btn-source').classList.add('active');
    document.getElementById('btn-rendered').classList.remove('active');
  };
  window.setDescBg = function(color) {
    document.getElementById('desc-body').style.backgroundColor = color;
  };

  } // end if (cfg)

  // ── Coordinate format rotation (DD → DMM → DMS → DD) ─────────────────

  var _gcfCoordFormats = ['dd', 'dmm', 'dms'];
  var _gcfCoordFmtIdx = 0;  // current format index

  // Detect initial format from the server-rendered text
  (function() {
    var el = document.querySelector('.gcf-coords');
    if (!el) return;
    var text = el.textContent.trim();
    if (/[NS]\s*\d+.*[']\s*[\d.]+["]/i.test(text)) {
      _gcfCoordFmtIdx = 2; // DMS
    } else if (/[NS]\s*\d+/i.test(text)) {
      _gcfCoordFmtIdx = 1; // DMM
    } else {
      _gcfCoordFmtIdx = 0; // DD
    }
  })();

  function _gcfFormatDD(lat, lon) {
    return lat.toFixed(6) + '  ' + lon.toFixed(6);
  }

  function _gcfFormatDMM(lat, lon) {
    function fmt(deg, pos, neg) {
      var h = deg >= 0 ? pos : neg;
      var d = Math.abs(deg);
      var m = (d - Math.floor(d)) * 60;
      return h + ' ' + String(Math.floor(d)).padStart(2, '0') + '\u00b0 ' + m.toFixed(3).padStart(6, '0') + "'";
    }
    return fmt(lat, 'N', 'S') + '  ' + fmt(lon, 'E', 'W');
  }

  function _gcfFormatDMS(lat, lon) {
    function fmt(deg, pos, neg) {
      var h = deg >= 0 ? pos : neg;
      var d = Math.abs(deg);
      var mTotal = (d - Math.floor(d)) * 60;
      var m = Math.floor(mTotal);
      var s = (mTotal - m) * 60;
      return h + ' ' + String(Math.floor(d)).padStart(2, '0') + '\u00b0 ' +
             String(m).padStart(2, '0') + "' " + s.toFixed(1).padStart(4, '0') + '"';
    }
    return fmt(lat, 'N', 'S') + '  ' + fmt(lon, 'E', 'W');
  }

  function _gcfFormatCoordPair(lat, lon, fmtIdx) {
    switch (_gcfCoordFormats[fmtIdx]) {
      case 'dmm': return _gcfFormatDMM(lat, lon);
      case 'dms': return _gcfFormatDMS(lat, lon);
      default:    return _gcfFormatDD(lat, lon);
    }
  }

  function _gcfUpdateAllCoordSpans() {
    var spans = document.querySelectorAll('.gcf-coords');
    for (var i = 0; i < spans.length; i++) {
      var el = spans[i];
      var lat = parseFloat(el.dataset.lat);
      var lon = parseFloat(el.dataset.lon);
      if (isNaN(lat) || isNaN(lon)) continue;
      el.textContent = _gcfFormatCoordPair(lat, lon, _gcfCoordFmtIdx);
    }
  }

  // Click handler for all coord spans
  document.addEventListener('click', function(e) {
    if (!e.target.closest('.gcf-coords')) return;
    _gcfCoordFmtIdx = (_gcfCoordFmtIdx + 1) % _gcfCoordFormats.length;
    _gcfUpdateAllCoordSpans();
  });

  // Copy coords to clipboard
  window.gcfCopyCoords = function(spanId) {
    var el = document.getElementById(spanId);
    if (!el) return;
    var text = el.textContent.trim();
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function() {
        _gcfCopyFlash(el);
      }).catch(function() {});
    } else {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch(e) {}
      document.body.removeChild(ta);
      _gcfCopyFlash(el);
    }
  };

  function _gcfCopyFlash(el) {
    el.style.transition = 'background 0.2s';
    el.style.background = '#d4edda';
    setTimeout(function() { el.style.background = ''; }, 600);
  }

  // ── Coordinate auto-split ─────────────────────────────────────────────
  //
  // When the user pastes or types a full "lat lon" pair into the lat field,
  // detect the split point and move the longitude part to the lon field.
  // Works with DD, DMM, and DMS formats.

  function _gcfSetupAllCoordAutoSplit() {
    var fields = document.querySelectorAll('[data-lon-target]');
    for (var i = 0; i < fields.length; i++) {
      _gcfSetupCoordAutoSplit(fields[i]);
    }
  }

  function _gcfSetupCoordAutoSplit(latEl) {
    var lonEl = document.getElementById(latEl.dataset.lonTarget);
    if (!lonEl) return;

    latEl.addEventListener('input', function() {
      var val = latEl.value.trim();
      if (!val) return;

      var parts = _gcfTrySplitCoordPair(val);
      if (parts) {
        latEl.value = parts[0];
        lonEl.value = parts[1];
        lonEl.focus();
      }
    });
  }

  // Try to split a string into lat + lon parts.
  // Returns [latStr, lonStr] or null if it looks like a single coordinate.
  function _gcfTrySplitCoordPair(s) {
    s = s.trim();

    // Pattern 1: Two decimal numbers separated by comma or whitespace
    //   "48.303150, 8.981267"  or  "48.303150 8.981267"  or  "-48.3 -8.9"
    var ddPair = s.match(/^(-?\d+\.?\d*)\s*[,;\s]\s*(-?\d+\.?\d*)$/);
    if (ddPair) {
      var a = parseFloat(ddPair[1]);
      var b = parseFloat(ddPair[2]);
      // Only split if first looks like lat (-90..90) and second like lon
      if (a >= -90 && a <= 90 && b >= -180 && b <= 180) {
        return [ddPair[1].trim(), ddPair[2].trim()];
      }
    }

    // Pattern 2: Hemisphere-prefixed pair (DMM or DMS)
    //   "N 48° 18.189' E 008° 58.876'"
    //   "N 48° 18' 11.3\" E 8° 58' 52.6\""
    // Split at the E/W hemisphere letter that starts the longitude part.
    // Look for E/W that is preceded by whitespace and followed by a digit/space.
    var hemiSplit = s.match(/^([NS][\s\S]+?)\s+([EW][\s\S]+)$/i);
    if (hemiSplit) {
      return [hemiSplit[1].trim(), hemiSplit[2].trim()];
    }

    // Pattern 3: Two hemisphere-suffixed parts
    //   "48° 18.189' N 008° 58.876' E"
    var suffixSplit = s.match(/^([\s\S]+?[NS])\s+([\s\S]+?[EW])\s*$/i);
    if (suffixSplit) {
      return [suffixSplit[1].trim(), suffixSplit[2].trim()];
    }

    return null;
  }

// ── Waypoint add/edit modal ───────────────────────────────────────────────────

(function () {
  var cfg = document.getElementById('cache-detail-config');
  if (!cfg) return;

  var cacheLat = parseFloat(cfg.dataset.cacheLat);
  var cacheLon = parseFloat(cfg.dataset.cacheLon);
  var coordWaypoints = [];
  try {
    var _cwEl = document.getElementById('cache-coord-waypoints');
    if (_cwEl) coordWaypoints = JSON.parse(_cwEl.textContent);
  } catch(e) {}
  var wpAddUrl = cfg.dataset.wpAddUrl || '';

  var pickMap = null;
  var pickMarker = null;
  var pickedLat = null, pickedLon = null;

  // Destination point from bearing (degrees) and distance (metres)
  function _destPoint(lat, lon, bearing, distM) {
    var R = 6371000;
    var d = distM / R;
    var b = bearing * Math.PI / 180;
    var lat1 = lat * Math.PI / 180;
    var lon1 = lon * Math.PI / 180;
    var lat2 = Math.asin(Math.sin(lat1) * Math.cos(d) + Math.cos(lat1) * Math.sin(d) * Math.cos(b));
    var lon2 = lon1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(lat1), Math.cos(d) - Math.sin(lat1) * Math.sin(lat2));
    lon2 = ((lon2 * 180 / Math.PI) + 540) % 360 - 180;
    return [lat2 * 180 / Math.PI, lon2];
  }

  function _toMetres(val, unit) {
    if (unit === 'ft') return val * 0.3048;
    if (unit === 'km') return val * 1000;
    if (unit === 'mi') return val * 1609.344;
    return val; // m
  }

  window.openWaypointModal = function(wpId, wpType, wpName, wpNote, wpLat, wpLon) {
    var modal = document.getElementById('waypointModal');
    var form = document.getElementById('waypointForm');
    var isEdit = typeof wpId === 'number';

    document.getElementById('waypointModalTitle').textContent = isEdit ? 'Edit waypoint' : 'Add waypoint';
    document.getElementById('wpId').value = isEdit ? wpId : '';

    // Set form action URL
    if (isEdit) {
      form.action = wpAddUrl.replace('/add/', '/' + wpId + '/edit/');
    } else {
      form.action = wpAddUrl;
    }

    // Populate fields
    var typeEl = document.getElementById('wpType');
    if (typeEl) typeEl.value = wpType || 'Other';
    document.getElementById('wpName').value = wpName || '';
    document.getElementById('wpNote').value = wpNote || '';

    // Coords
    var latEl = document.getElementById('wpLat');
    var lonEl = document.getElementById('wpLon');
    if (wpLat !== null && wpLat !== undefined && wpLon !== null && wpLon !== undefined) {
      latEl.value = wpLat;
      lonEl.value = wpLon;
    } else {
      latEl.value = '';
      lonEl.value = '';
    }

    // Reset coord method to manual
    document.getElementById('cmManual').checked = true;
    wpCoordMethodChange();

    // Populate projection basis dropdown
    var basisSel = document.getElementById('wpProjBasis');
    while (basisSel.options.length > 1) basisSel.remove(1);
    coordWaypoints.forEach(function(w) {
      var opt = new Option(w.label, 'wp_' + w.id);
      opt.dataset.lat = w.lat;
      opt.dataset.lon = w.lon;
      basisSel.add(opt);
    });

    // Reset projection fields
    document.getElementById('wpProjBearing').value = '';
    document.getElementById('wpProjDist').value = '';
    document.getElementById('wpProjResult').textContent = 'Enter bearing and distance above.';

    var bsModal = bootstrap.Modal.getOrCreateInstance(modal);
    bsModal.show();

    // Attach coord auto-split to wpLat if not already done
    if (!latEl._wpSplitAttached) {
      latEl._wpSplitAttached = true;
      latEl.addEventListener('input', function() {
        var val = latEl.value.trim();
        if (!val) return;
        // Re-use the global _gcfTrySplitCoordPair if available
        if (typeof _gcfTrySplitCoordPair === 'function') {
          var parts = _gcfTrySplitCoordPair(val);
          if (parts) { latEl.value = parts[0]; lonEl.value = parts[1]; lonEl.focus(); }
        }
      });
    }
  };

  window.wpCoordMethodChange = function() {
    var method = document.querySelector('input[name="coord_method"]:checked');
    if (!method) return;
    document.getElementById('wpCoordManual').classList.toggle('d-none', method.value !== 'manual');
    document.getElementById('wpCoordMap').classList.toggle('d-none', method.value !== 'map');
    document.getElementById('wpCoordProject').classList.toggle('d-none', method.value !== 'project');

    if (method.value === 'map') {
      _initPickMap();
    }
  };

  function _initPickMap() {
    if (pickMap) {
      pickMap.invalidateSize();
      return;
    }
    // Build pick map centred on cache
    pickMap = L.map('wpPickMap').setView([cacheLat, cacheLon], 15);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© <a href="https://openstreetmap.org">OpenStreetMap</a>',
      maxZoom: 19,
    }).addTo(pickMap);

    // Show cache marker
    L.marker([cacheLat, cacheLon]).addTo(pickMap).bindPopup('Cache');

    pickMap.on('click', function(e) {
      pickedLat = e.latlng.lat;
      pickedLon = e.latlng.lng;
      if (pickMarker) pickMarker.setLatLng(e.latlng);
      else pickMarker = L.marker(e.latlng, {draggable: true}).addTo(pickMap);
      pickMarker.on('dragend', function(ev) {
        pickedLat = ev.target.getLatLng().lat;
        pickedLon = ev.target.getLatLng().lng;
        _syncPickedCoords();
      });
      _syncPickedCoords();
    });
  }

  function _syncPickedCoords() {
    if (pickedLat === null) return;
    document.getElementById('wpLat').value = pickedLat.toFixed(6);
    document.getElementById('wpLon').value = pickedLon.toFixed(6);
  }

  window.wpProjectUpdate = function() {
    var basisSel = document.getElementById('wpProjBasis');
    var bearingVal = parseFloat(document.getElementById('wpProjBearing').value);
    var distVal = parseFloat(document.getElementById('wpProjDist').value);
    var unit = document.getElementById('wpProjUnit').value;
    var resultEl = document.getElementById('wpProjResult');

    if (!basisSel.value || isNaN(bearingVal) || isNaN(distVal) || distVal < 0) {
      resultEl.textContent = 'Enter bearing and distance above.';
      return;
    }

    var baseLat, baseLon;
    if (basisSel.value === 'cache') {
      baseLat = cacheLat;
      baseLon = cacheLon;
    } else {
      var opt = basisSel.selectedOptions[0];
      baseLat = parseFloat(opt.dataset.lat);
      baseLon = parseFloat(opt.dataset.lon);
    }

    if (isNaN(baseLat) || isNaN(baseLon)) {
      resultEl.textContent = 'Invalid basis coordinates.';
      return;
    }

    var distM = _toMetres(distVal, unit);
    var dest = _destPoint(baseLat, baseLon, bearingVal, distM);
    var latStr = dest[0].toFixed(6);
    var lonStr = dest[1].toFixed(6);

    document.getElementById('wpLat').value = latStr;
    document.getElementById('wpLon').value = lonStr;
    resultEl.textContent = 'Result: ' + latStr + ', ' + lonStr;
  };

  // Before submit: if coord_method is map, ensure picked coords are in the form fields
  document.getElementById('waypointForm').addEventListener('submit', function() {
    var method = document.querySelector('input[name="coord_method"]:checked');
    if (method && method.value === 'map' && pickedLat !== null) {
      document.getElementById('wpLat').value = pickedLat.toFixed(6);
      document.getElementById('wpLon').value = pickedLon.toFixed(6);
    }
    // Invalidate pick map so it re-initialises next open
    if (pickMap && method && method.value === 'map') {
      pickMap.remove();
      pickMap = null;
      pickMarker = null;
      pickedLat = null;
      pickedLon = null;
    }
  });

  // Re-init pick map on modal close to avoid stale size
  var wpModal = document.getElementById('waypointModal');
  if (wpModal) {
    wpModal.addEventListener('hidden.bs.modal', function() {
      if (pickMap) { pickMap.remove(); pickMap = null; pickMarker = null; pickedLat = null; pickedLon = null; }
    });
  }
})();
