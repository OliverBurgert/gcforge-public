/* ---- Log truncate toggle (enrichment tab) ---- */
  var logTruncateCb = document.getElementById('log_truncate');
  if (logTruncateCb) {
    logTruncateCb.addEventListener('change', function() {
      var row = document.getElementById('log-truncate-length-row');
      if (row) { row.style.opacity = this.checked ? '' : '.5'; row.style.pointerEvents = this.checked ? '' : 'none'; }
    });
  }

  var enrichLogAutoShow = document.getElementById('enrich_log_auto_show');
  if (enrichLogAutoShow) {
    enrichLogAutoShow.addEventListener('change', function() {
      var newTab = document.getElementById('enrich_log_new_tab');
      newTab.disabled = !this.checked;
      if (!this.checked) newTab.checked = false;
    });
  }

  /* ---- Tab activation ---- */
  var TABS = ['general', 'list-view', 'cache-detail-view', 'logging', 'enrichment', 'platforms', 'accounts', 'reference-points', 'map', 'dashboard', 'gpx-export', 'database', 'offline', 'images'];
  var DEFAULT = 'general';

  function activateTab(id) {
    var link = document.querySelector('#settingsTabs a[href="#' + id + '"]');
    if (link) bootstrap.Tab.getOrCreateInstance(link).show();
  }

  var hash = (window.location.hash || '').replace('#', '');
  activateTab(TABS.indexOf(hash) !== -1 ? hash : DEFAULT);

  var wrapper = document.getElementById('settings-wrapper');

  function applyWrapperWidth(hash) {
    if (!wrapper) return;
    wrapper.style.maxWidth = (hash === '#reference-points' || hash === '#offline') ? 'none' : '900px';
  }

  applyWrapperWidth(window.location.hash);

  document.querySelectorAll('#settingsTabs a[data-bs-toggle="tab"]').forEach(function (link) {
    link.addEventListener('shown.bs.tab', function () {
      var href = link.getAttribute('href');
      history.replaceState(null, '', href);
      applyWrapperWidth(href);
      if (href === '#reference-points' && typeof window.gcfRpMapResize === 'function') {
        window.gcfRpMapResize();
      }
      if (href === '#offline' && typeof gcfOfflineTabShown === 'function') {
        gcfOfflineTabShown();
      }
    });
  });

  /* ---- Locations map is owned by settings-map.js (MapLibre) ---- */

  // ── Coordinate auto-split for lat/lon fields ──────────────────────────
  // If a full "lat lon" pair is pasted or typed into a lat field with
  // data-lon-target, the lon part is automatically moved to the lon field.

  (function() {
    var fields = document.querySelectorAll('[data-lon-target]');
    for (var i = 0; i < fields.length; i++) {
      (function(latEl) {
        var lonEl = document.getElementById(latEl.dataset.lonTarget);
        if (!lonEl) return;
        latEl.addEventListener('input', function() {
          var val = latEl.value.trim();
          if (!val) return;
          var parts = _trySplitCoordPair(val);
          if (parts) {
            latEl.value = parts[0];
            lonEl.value = parts[1];
            lonEl.focus();
          }
        });
      })(fields[i]);
    }

    function _trySplitCoordPair(s) {
      s = s.trim();
      // DD pair: "48.303150, 8.981267" or "48.303150 8.981267"
      var dd = s.match(/^(-?\d+\.?\d*)\s*[,;\s]\s*(-?\d+\.?\d*)$/);
      if (dd) {
        var a = parseFloat(dd[1]), b = parseFloat(dd[2]);
        if (a >= -90 && a <= 90 && b >= -180 && b <= 180) return [dd[1].trim(), dd[2].trim()];
      }
      // Hemisphere-prefixed: "N 48° 18.189' E 008° 58.876'"
      var hp = s.match(/^([NS][\s\S]+?)\s+([EW][\s\S]+)$/i);
      if (hp) return [hp[1].trim(), hp[2].trim()];
      // Hemisphere-suffixed: "48° 18.189' N 008° 58.876' E"
      var hs = s.match(/^([\s\S]+?[NS])\s+([\s\S]+?[EW])\s*$/i);
      if (hs) return [hs[1].trim(), hs[2].trim()];
      return null;
    }
  })();

function gcfFetchPublicGuid() {
  var btn = document.getElementById('fetch-guid-btn');
  var status = document.getElementById('fetch-guid-status');
  if (btn) btn.disabled = true;
  if (status) status.textContent = gettext('Looking up…');
  var csrf = (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
  fetch('/settings/fetch-gc-public-guid/', {
    method: 'POST',
    headers: {'X-CSRFToken': csrf},
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.guid) {
        var input = document.getElementById('gc_public_guid');
        if (input) input.value = data.guid;
        if (status) status.textContent = gettext('Found and saved.');
      } else {
        if (status) status.textContent = interpolate(gettext('Error: %s'), [data.error || gettext('unknown')]);
      }
    })
    .catch(function(err) {
      if (status) status.textContent = interpolate(gettext('Error: %s'), [err]);
    })
    .finally(function() {
      if (btn) btn.disabled = false;
    });
}
