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
  var TABS = ['general', 'cache-detail-view', 'enrichment', 'columns', 'platforms', 'accounts', 'reference-points', 'map', 'database'];
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
    wrapper.style.maxWidth = (hash === '#reference-points') ? 'none' : '900px';
  }

  applyWrapperWidth(window.location.hash);

  document.querySelectorAll('#settingsTabs a[data-bs-toggle="tab"]').forEach(function (link) {
    link.addEventListener('shown.bs.tab', function () {
      var href = link.getAttribute('href');
      history.replaceState(null, '', href);
      applyWrapperWidth(href);
      if (href === '#reference-points' && rpMap) rpMap.invalidateSize();
    });
  });

  /* ---- Reference-point map ---- */
  var mapEl = document.getElementById('rp-map');
  if (mapEl) {

  var cfg = document.getElementById('settings-config');
  var rpData = [];
  try { rpData = JSON.parse(cfg ? cfg.dataset.rpList : '[]'); } catch(e) {}

  var normalIcon = L.divIcon({
    className: '',
    html: '<div style="width:12px;height:12px;border-radius:50%;background:#0d6efd;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.5)"></div>',
    iconSize: [12, 12], iconAnchor: [6, 6], tooltipAnchor: [6, -6]
  });
  var editIcon = L.divIcon({
    className: '',
    html: '<div style="width:16px;height:16px;border-radius:50%;background:#fd7e14;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.6)"></div>',
    iconSize: [16, 16], iconAnchor: [8, 8], tooltipAnchor: [8, -8]
  });

  var rpMap = L.map('rp-map');
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© <a href="https://openstreetmap.org">OpenStreetMap</a> contributors',
    maxZoom: 19
  }).addTo(rpMap);

  var markers = {};        // id -> L.marker
  var activeEditId = null;
  var originalLatLng = {};

  /* Persist checkbox selection across page reloads (e.g. after edit form submit) */
  var STORAGE_KEY = 'gcforge_rp_checks';
  function loadCheckState() {
    try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || 'null') || {}; }
    catch(e) { return {}; }
  }
  function saveCheckState() {
    var state = {};
    document.querySelectorAll('.rp-check').forEach(function (cb) {
      state[cb.dataset.rpId] = cb.checked;
    });
    try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch(e) {}
  }

  /* Build markers; restore saved selection, fall back to home=checked */
  var savedState = loadCheckState();
  rpData.forEach(function (rp) {
    var marker = L.marker([rp.lat, rp.lon], {icon: normalIcon, draggable: false});
    marker.bindTooltip(rp.name);
    markers[rp.id] = marker;
    var cb = document.getElementById('rp-cb-' + rp.id);
    var isChecked = (String(rp.id) in savedState) ? savedState[String(rp.id)] : rp.is_home;
    if (cb) cb.checked = isChecked;
    if (isChecked) marker.addTo(rpMap);
  });

  updateSelectAll();
  fitVisible();

  function fitVisible() {
    var pts = [];
    Object.keys(markers).forEach(function (id) {
      if (rpMap.hasLayer(markers[id])) pts.push(markers[id].getLatLng());
    });
    if (pts.length === 0) {
      rpMap.setView([51, 10], 4);
    } else if (pts.length === 1) {
      rpMap.setView(pts[0], 14);
    } else {
      rpMap.fitBounds(L.latLngBounds(pts).pad(0.2), {maxZoom: 16});
    }
  }

  /* Checkbox interactions */
  document.querySelectorAll('.rp-check').forEach(function (cb) {
    cb.addEventListener('change', function () {
      var id = parseInt(this.dataset.rpId);
      if (this.checked) { markers[id].addTo(rpMap); }
      else              { markers[id].remove(); }
      updateSelectAll();
      saveCheckState();
      fitVisible();
    });
  });

  var checkAll = document.getElementById('rp-check-all');
  if (checkAll) {
    checkAll.addEventListener('change', function () {
      document.querySelectorAll('.rp-check').forEach(function (cb) {
        cb.checked = checkAll.checked;
        var id = parseInt(cb.dataset.rpId);
        if (checkAll.checked) markers[id].addTo(rpMap);
        else                  markers[id].remove();
      });
      saveCheckState();
      fitVisible();
    });
  }

  function updateSelectAll() {
    if (!checkAll) return;
    var cbs = document.querySelectorAll('.rp-check');
    var checked = document.querySelectorAll('.rp-check:checked');
    checkAll.indeterminate = checked.length > 0 && checked.length < cbs.length;
    checkAll.checked = checked.length === cbs.length;
  }

  /* Map interaction — activates automatically when edit collapse opens */
  document.querySelectorAll('[id^="edit-rp-"]').forEach(function (collapseEl) {
    var rpId = parseInt(collapseEl.id.replace('edit-rp-', ''));

    collapseEl.addEventListener('shown.bs.collapse', function () {
      activateFineTune(rpId);
    });

    collapseEl.addEventListener('hidden.bs.collapse', function () {
      if (activeEditId === rpId) deactivateFineTune(rpId, true);
    });
  });

  function activateFineTune(rpId) {
    if (activeEditId && activeEditId !== rpId) deactivateFineTune(activeEditId, true);
    activeEditId = rpId;

    var marker = markers[rpId];
    if (!marker) return;

    originalLatLng[rpId] = marker.getLatLng();

    /* Ensure marker is visible */
    var cb = document.getElementById('rp-cb-' + rpId);
    if (cb && !cb.checked) { cb.checked = true; marker.addTo(rpMap); updateSelectAll(); }

    marker.setIcon(editIcon);
    marker.dragging.enable();
    marker.on('drag dragend', function () { syncFields(rpId, marker.getLatLng()); });

    rpMap.getContainer().style.cursor = 'crosshair';
    rpMap.panTo(marker.getLatLng());

    var hint = document.getElementById('finetune-hint-' + rpId);
    if (hint) hint.classList.remove('d-none');

    setStatus('Fine tune active — drag the marker or click on the map to reposition.');
  }

  function deactivateFineTune(rpId, restore) {
    if (activeEditId !== rpId) return;
    activeEditId = null;

    var marker = markers[rpId];
    if (marker) {
      marker.dragging.disable();
      marker.off('drag dragend');
      marker.setIcon(normalIcon);
      if (restore && originalLatLng[rpId]) marker.setLatLng(originalLatLng[rpId]);
    }

    rpMap.getContainer().style.cursor = (addDetails && addDetails.open) ? 'crosshair' : '';

    var hint = document.getElementById('finetune-hint-' + rpId);
    if (hint) hint.classList.add('d-none');

    setStatus('');
  }

  function mapClickHandler(e) {
    if (activeEditId) {
      markers[activeEditId].setLatLng(e.latlng);
      syncFields(activeEditId, e.latlng);
    } else if (addDetails && addDetails.open) {
      addPickHandler(e.latlng);
    }
  }

  /* Add-location map picking */
  var addDetails = document.getElementById('add-location-details');
  var addMarker = null;
  var addIcon = L.divIcon({
    className: '',
    html: '<div style="width:12px;height:12px;border-radius:50%;background:#198754;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.5)"></div>',
    iconSize: [12, 12], iconAnchor: [6, 6], tooltipAnchor: [6, -6]
  });

  function addPickHandler(latlng) {
    var latEl = document.getElementById('rp-lat-new');
    var lonEl = document.getElementById('rp-lon-new');
    if (latEl) latEl.value = latlng.lat.toFixed(6);
    if (lonEl) lonEl.value = latlng.lng.toFixed(6);
    if (addMarker) {
      addMarker.setLatLng(latlng);
    } else {
      addMarker = L.marker(latlng, {icon: addIcon}).addTo(rpMap);
      addMarker.bindTooltip('New location');
    }
  }

  /* Register click handler once globally */
  rpMap.on('click', mapClickHandler);

  if (addDetails) {
    addDetails.addEventListener('toggle', function () {
      if (addDetails.open) {
        rpMap.getContainer().style.cursor = 'crosshair';
        setStatus('Click on the map to set the new location position.');
      } else {
        if (!activeEditId) {
          rpMap.getContainer().style.cursor = '';
          setStatus('');
        }
        if (addMarker) { addMarker.remove(); addMarker = null; }
        var latEl = document.getElementById('rp-lat-new');
        var lonEl = document.getElementById('rp-lon-new');
        if (latEl) latEl.value = '';
        if (lonEl) lonEl.value = '';
      }
    });
  }

  function syncFields(rpId, latlng) {
    var latEl = document.getElementById('rp-lat-' + rpId);
    var lonEl = document.getElementById('rp-lon-' + rpId);
    if (latEl) latEl.value = latlng.lat.toFixed(6);
    if (lonEl) lonEl.value = latlng.lng.toFixed(6);
  }

  function setStatus(msg) {
    var el = document.getElementById('rp-map-status');
    if (el) el.textContent = msg;
  }

  /* Initialise map size when tab is shown */
  document.querySelector('#settingsTabs a[href="#reference-points"]').addEventListener('shown.bs.tab', function () {
    rpMap.invalidateSize();
    fitVisible();
  });

  } // end if (mapEl)

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
