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
    link.textContent = expanded ? gettext('less') : gettext('more');
  };

  // --- Map setup: handled by cache-detail-map.js (MapLibre) ---
  var cfg = document.getElementById('cache-detail-config');
  if (cfg) {

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
      btn.textContent = isEncrypted ? gettext('encode') : gettext('decode');
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

  // Detect initial format from the server-rendered text, then sync all spans
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
    _gcfUpdateAllCoordSpans();
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

  var VALID_WP_PREFIXES = ['PK', 'ST', 'QA', 'FL', 'TH', 'RP', 'WP'];

  window.openWaypointModal = function(wpId, wpType, wpName, wpNote, wpLat, wpLon, wpPrefix) {
    var modal = document.getElementById('waypointModal');
    var form = document.getElementById('waypointForm');
    var isEdit = typeof wpId === 'number';

    document.getElementById('waypointModalTitle').textContent = isEdit ? gettext('Edit waypoint') : gettext('Add waypoint');
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
    var prefixEl = document.getElementById('wpPrefix');
    if (prefixEl) {
      prefixEl.value = wpPrefix || '';
      prefixEl.classList.remove('is-invalid');
    }
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
    document.getElementById('wpProjResult').textContent = gettext('Enter bearing and distance above.');

    // Init tooltip on prefix help icon
    var prefixTip = modal.querySelector('[data-bs-toggle="tooltip"]');
    if (prefixTip && !prefixTip._bsTooltip) {
      prefixTip._bsTooltip = new bootstrap.Tooltip(prefixTip);
    }

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
    document.getElementById('wpCoordProject').classList.toggle('d-none', method.value !== 'project');
  };

  window.wpProjectUpdate = function() {
    var basisSel = document.getElementById('wpProjBasis');
    var bearingVal = parseFloat(document.getElementById('wpProjBearing').value);
    var distVal = parseFloat(document.getElementById('wpProjDist').value);
    var unit = document.getElementById('wpProjUnit').value;
    var resultEl = document.getElementById('wpProjResult');

    if (!basisSel.value || isNaN(bearingVal) || isNaN(distVal) || distVal < 0) {
      resultEl.textContent = gettext('Enter bearing and distance above.');
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
      resultEl.textContent = gettext('Invalid basis coordinates.');
      return;
    }

    var distM = _toMetres(distVal, unit);
    var dest = _destPoint(baseLat, baseLon, bearingVal, distM);
    var latStr = dest[0].toFixed(6);
    var lonStr = dest[1].toFixed(6);

    document.getElementById('wpLat').value = latStr;
    document.getElementById('wpLon').value = lonStr;
    resultEl.textContent = interpolate(gettext('Result: %(lat)s, %(lon)s'), {lat: latStr, lon: lonStr}, true);
  };

  // Validate prefix on submit
  document.getElementById('waypointForm').addEventListener('submit', function(e) {
    var prefixEl = document.getElementById('wpPrefix');
    if (prefixEl) {
      var val = prefixEl.value.trim().toUpperCase();
      if (val !== '' && VALID_WP_PREFIXES.indexOf(val) === -1) {
        prefixEl.classList.add('is-invalid');
        e.preventDefault();
        return;
      }
      prefixEl.classList.remove('is-invalid');
      prefixEl.value = val;
    }
  });
})();
