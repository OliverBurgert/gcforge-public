/**
 * map-fetch.js — Preview / Sync workflow for fetching caches from API via drawn regions.
 *
 * Depends on: cache-map.js (gcfMap, _gcfFetchMarkers)
 *             map-draw.js  (_gcfDrawRegions, gcfPinAsFilter)
 *             map-context-menu.js (_gcfFlashMessage)
 *             Bootstrap 5 (modal)
 */

/* global gcfMap, _gcfDrawRegions, _gcfFetchMarkers, gcfSetGhostMarkers, gcfClearGhostMarkers,
          gcfPinAsFilter, _gcfFlashMessage, bootstrap */

// ── State ───────────────────────────────────────────────────────────────────

// Ghost marker data from preview, grouped by platform for the sync step.
window._gcfGhostMarkers = [];
var _gcfGhostByPlatform = {};  // { "gc": ["GC1","GC2"], "oc_de": ["OC1"] }
var _gcfPollTimers = [];
var _gcfFetchModal = null;

// Show flash message that was saved before page reload (e.g. sync summary)
(function() {
  var msg = sessionStorage.getItem('gcf_sync_flash');
  if (msg) {
    sessionStorage.removeItem('gcf_sync_flash');
    // Delay so _gcfFlashMessage is defined (loaded later in map-context-menu.js)
    setTimeout(function() {
      if (typeof _gcfFlashMessage === 'function') _gcfFlashMessage(msg);
    }, 500);
  }

  // Trigger deferred enrichment for caches synced before reload.
  // This lets the map redraw immediately with synced caches while
  // enrichment (elevation, location) runs in the background.
  var enrichStr = sessionStorage.getItem('gcf_pending_enrich');
  if (enrichStr) {
    sessionStorage.removeItem('gcf_pending_enrich');
    setTimeout(function() {
      var csrf = document.querySelector('[name=csrfmiddlewaretoken]');
      fetch('/map/auto-enrich/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrf ? csrf.value : '',
        },
        body: enrichStr,
      }).catch(function() {}); // fire-and-forget
    }, 1000);
  }
})();

// ── CSRF helper ─────────────────────────────────────────────────────────────

function _gcfCsrf() {
  var el = document.querySelector('[name=csrfmiddlewaretoken]');
  return el ? el.value : '';
}

// ── Tag suggestions for sync dialog ─────────────────────────────────────────

var _gcfFetchTagsLoaded = false;

function _gcfLoadFetchTagSuggestions() {
  if (_gcfFetchTagsLoaded) return;
  _gcfFetchTagsLoaded = true;
  var input = document.getElementById('fetch-tags');
  var box = document.getElementById('fetch-tag-suggestions');
  if (!input || !box) return;
  fetch('/tags/json/')
    .then(function(r) { return r.json(); })
    .then(function(names) {
      if (!names.length) return;
      names.forEach(function(name) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm btn-outline-secondary me-1 mb-1';
        btn.textContent = name;
        btn.addEventListener('click', function() {
          var cur = input.value.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
          if (cur.indexOf(name) === -1) { cur.push(name); }
          input.value = cur.join(', ');
        });
        box.appendChild(btn);
      });
    });
}

// ── Modal page switching ────────────────────────────────────────────────────

function gcfFetchShowPage(page) {
  document.getElementById('fetch-page-preview').style.display = page === 'preview' ? '' : 'none';
  document.getElementById('fetch-page-sync').style.display = page === 'sync' ? '' : 'none';
}

// ── Open the fetch dialog ───────────────────────────────────────────────────

function gcfOpenFetchDialog() {
  gcfFetchShowPage('preview');

  // Reset progress
  document.getElementById('fetch-preview-progress').style.display = 'none';
  document.getElementById('fetch-preview-bar').style.width = '0%';
  document.getElementById('fetch-preview-btn').disabled = false;

  // Region summary
  var regions = (typeof _gcfDrawRegions !== 'undefined') ? _gcfDrawRegions : [];
  var summary = regions.length + ' region' + (regions.length !== 1 ? 's' : '') + ' drawn';
  var corridors = regions.filter(function(r) { return r.type === 'corridor'; });
  var polygons  = regions.filter(function(r) { return r.type === 'polygon'; });
  if (corridors.length && typeof _gcfCorridorBoxes === 'function') {
    var totalRects = 0, totalCircles = 0;
    corridors.forEach(function(r) {
      _gcfCorridorBoxes(r.path, r.width_m).forEach(function(sh) {
        if (sh.type === 'circle') totalCircles++; else totalRects++;
      });
    });
    var parts = [];
    if (totalRects)   parts.push(totalRects   + ' rect' + (totalRects   !== 1 ? 's' : ''));
    if (totalCircles) parts.push(totalCircles  + ' circle' + (totalCircles !== 1 ? 's' : ''));
    summary += ' (corridor: ' + parts.join(' + ') + ' API searches, results filtered to exact shape)';
  }
  if (polygons.length && typeof _gcfBestSearchForPolygon === 'function') {
    var polyCircles = 0;
    polygons.forEach(function(r) {
      if (_gcfBestSearchForPolygon(r.coordinates).type === 'circle') polyCircles++;
    });
    var polyDesc = polyCircles === polygons.length ? 'circle' :
                   polyCircles === 0               ? 'bbox'   : 'bbox/circle';
    summary += ' (polygon: ' + polyDesc + ' search, results filtered to exact shape)';
  }
  document.getElementById('fetch-region-summary').textContent = summary;

  // Load providers + quota
  _gcfLoadProviders();

  // Show modal
  if (!_gcfFetchModal) {
    _gcfFetchModal = new bootstrap.Modal(document.getElementById('mapFetchDialog'));
  }
  _gcfFetchModal.show();
}

// ── Load providers ──────────────────────────────────────────────────────────

function _gcfLoadProviders() {
  var container = document.getElementById('fetch-providers');
  var noProviders = document.getElementById('fetch-no-providers');
  container.innerHTML = '';
  noProviders.style.display = 'none';

  fetch('/map/providers/')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var providers = data.providers || [];
      if (!providers.length) {
        noProviders.style.display = '';
        document.getElementById('fetch-preview-btn').disabled = true;
        return;
      }

      // Build checkboxes
      var platforms = [];
      providers.forEach(function(p) {
        if (platforms.indexOf(p.platform) !== -1) return; // dedup
        platforms.push(p.platform);
        var div = document.createElement('div');
        div.className = 'form-check';
        div.innerHTML =
          '<input class="form-check-input fetch-platform-cb" type="checkbox" value="' +
          p.platform + '" id="fetch-plat-' + p.platform + '" checked>' +
          '<label class="form-check-label small" for="fetch-plat-' + p.platform + '">' +
          _escHtml(p.label) + ' (' + _escHtml(p.username) + ')</label>';
        container.appendChild(div);
      });

      // Load quota for these platforms
      _gcfLoadQuota(platforms, 'fetch-quota');
    })
    .catch(function() {
      container.innerHTML = '<span class="text-danger small">Failed to load providers</span>';
    });
}

function _gcfLoadQuota(platforms, elementId) {
  var el = document.getElementById(elementId);
  el.textContent = 'Loading...';
  fetch('/map/quota/?platforms=' + platforms.join(','))
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var lines = [];
      platforms.forEach(function(p) {
        var pdata = data[p];
        if (!pdata) return;
        var light = pdata.light || {};
        var full = pdata.full || {};
        lines.push(
          p.toUpperCase() + ': ' +
          _fmtNum(light.remaining) + ' / ' + _fmtNum(light.limit) + ' light, ' +
          _fmtNum(full.remaining) + ' / ' + _fmtNum(full.limit) + ' full'
        );
      });
      el.innerHTML = lines.join('<br>');
    })
    .catch(function() {
      el.innerHTML = '<span class="text-danger">Failed to load quota</span>';
    });
}

// ── Submit preview ──────────────────────────────────────────────────────────

function gcfSubmitPreview() {
  var regions = (typeof _gcfDrawRegions !== 'undefined') ? _gcfDrawRegions : [];
  if (!regions.length) {
    _gcfFlashMessage('Draw at least one region first');
    return;
  }

  var platforms = _gcfSelectedPlatforms();
  if (!platforms.length) {
    _gcfFlashMessage('Select at least one provider');
    return;
  }

  // Prepare region data (strip draw feature IDs)
  var regionData = regions.map(function(r) {
    if (r.type === 'rect') return { type: 'rect', bbox: r.bbox };
    if (r.type === 'circle') return { type: 'circle', center: r.center, radius_m: r.radius_m };
    if (r.type === 'polygon') return { type: 'polygon', coordinates: r.coordinates };
    if (r.type === 'corridor') return { type: 'corridor', path: r.path, width_m: r.width_m };
    return null;
  }).filter(Boolean);

  if (!regionData.length) {
    _gcfFlashMessage('No previewable regions');
    return;
  }

  document.getElementById('fetch-preview-btn').disabled = true;
  document.getElementById('fetch-preview-progress').style.display = '';
  document.getElementById('fetch-preview-phase').textContent = 'Submitting...';

  fetch('/map/preview/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': _gcfCsrf(),
    },
    body: JSON.stringify({ regions: regionData, platforms: platforms }),
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) {
        _gcfFlashMessage('Preview failed: ' + data.error);
        document.getElementById('fetch-preview-btn').disabled = false;
        document.getElementById('fetch-preview-progress').style.display = 'none';
        return;
      }
      // Poll each task
      _gcfPollPreviewTasks(data.task_ids || []);
    })
    .catch(function(err) {
      _gcfFlashMessage('Preview request failed');
      document.getElementById('fetch-preview-btn').disabled = false;
      document.getElementById('fetch-preview-progress').style.display = 'none';
    });
}

function _gcfPollPreviewTasks(taskIds) {
  if (!taskIds.length) {
    _gcfFlashMessage('No preview tasks created');
    document.getElementById('fetch-preview-btn').disabled = false;
    document.getElementById('fetch-preview-progress').style.display = 'none';
    return;
  }

  var allCaches = [];
  var allErrors = [];
  // Track both task completion and result fetching separately to avoid race
  var pollsDone = 0;       // tasks that finished polling (any terminal state)
  var resultsDone = 0;     // tasks whose result fetch (or skip) has resolved
  var totalTasks = taskIds.length;
  var taskProgress = {};   // tid → last known progress %
  taskIds.forEach(function(tid) { taskProgress[tid] = 0; });

  function _updateProgressBar() {
    var sum = 0;
    taskIds.forEach(function(tid) { sum += (taskProgress[tid] || 0); });
    var combinedPct = sum / totalTasks;
    document.getElementById('fetch-preview-bar').style.width = Math.round(combinedPct) + '%';
  }

  function _checkAllDone() {
    if (resultsDone === totalTasks) {
      _gcfPreviewComplete(allCaches, allErrors);
    }
  }

  // Clear previous timers
  _gcfPollTimers.forEach(clearInterval);
  _gcfPollTimers = [];

  taskIds.forEach(function(tid) {
    var timer = setInterval(function() {
      fetch('/tasks/' + tid + '/')
        .then(function(r) { return r.json(); })
        .then(function(info) {
          // Update combined progress bar
          taskProgress[tid] = info.progress_pct || 0;
          _updateProgressBar();
          document.getElementById('fetch-preview-phase').textContent = info.phase || info.state || '';

          if (info.state === 'completed' || info.state === 'failed' || info.state === 'cancelled') {
            clearInterval(timer);
            pollsDone++;
            taskProgress[tid] = 100;
            _updateProgressBar();

            if (info.state === 'completed') {
              // Fetch the actual preview results
              fetch('/map/preview/' + tid + '/')
                .then(function(r) { return r.json(); })
                .then(function(result) {
                  if (result.caches) {
                    allCaches = allCaches.concat(result.caches);
                  }
                  if (result.errors && result.errors.length) {
                    result.errors.forEach(function(e) { allErrors.push(e); });
                  }
                  resultsDone++;
                  _checkAllDone();
                })
                .catch(function() {
                  allErrors.push('Failed to fetch results for task ' + tid);
                  resultsDone++;
                  _checkAllDone();
                });
            } else {
              if (info.state === 'failed' && info.error) {
                allErrors.push(info.error);
              }
              resultsDone++;
              _checkAllDone();
            }
          }
        });
    }, 1500);
    _gcfPollTimers.push(timer);
  });
}

function _gcfPreviewComplete(caches, errors) {
  document.getElementById('fetch-preview-bar').style.width = '100%';
  errors = errors || [];

  if (!caches.length) {
    var noResultMsg = 'No caches found in the selected area';
    if (errors.length) {
      noResultMsg += ' — fetch error: ' + errors[0];
    }
    _gcfFlashMessage(noResultMsg);
    document.getElementById('fetch-preview-btn').disabled = false;
    document.getElementById('fetch-preview-progress').style.display = 'none';
    return;
  }

  // Store ghost markers
  window._gcfGhostMarkers = caches;
  _gcfGhostByPlatform = {};
  caches.forEach(function(c) {
    var plat = c.platform || 'gc';
    if (!_gcfGhostByPlatform[plat]) _gcfGhostByPlatform[plat] = [];
    _gcfGhostByPlatform[plat].push(c.code);
  });

  // Render ghost markers on map
  if (typeof gcfSetGhostMarkers === 'function') {
    gcfSetGhostMarkers(caches);
  }

  // Show sync button in toolbar
  _gcfUpdateSyncButton();

  // Close modal + flash message
  if (_gcfFetchModal) _gcfFetchModal.hide();
  var newCount = caches.filter(function(c) { return !c.in_db; }).length;
  var existCount = caches.length - newCount;
  var flashMsg = 'Preview: ' + caches.length + ' caches (' + newCount + ' new, ' + existCount + ' already synced)';
  if (errors.length) {
    flashMsg += ' — ' + errors.length + ' batch error(s)';
  }
  _gcfFlashMessage(flashMsg);

  document.getElementById('fetch-preview-btn').disabled = false;
  document.getElementById('fetch-preview-progress').style.display = 'none';
}

// ── Open sync dialog ────────────────────────────────────────────────────────

function gcfOpenSyncDialog() {
  if (!window._gcfGhostMarkers || !window._gcfGhostMarkers.length) {
    _gcfFlashMessage('Run a preview first');
    return;
  }

  gcfFetchShowPage('sync');
  _gcfLoadFetchTagSuggestions();

  // Reset progress
  document.getElementById('fetch-sync-progress').style.display = 'none';
  document.getElementById('fetch-sync-bar').style.width = '0%';
  document.getElementById('fetch-sync-btn').disabled = false;

  // Summary
  var lines = [];
  var totalCount = 0;
  Object.keys(_gcfGhostByPlatform).forEach(function(plat) {
    var count = _gcfGhostByPlatform[plat].length;
    totalCount += count;
    lines.push('<strong>' + count + '</strong> caches from ' + plat.toUpperCase());
  });
  document.getElementById('fetch-sync-summary').innerHTML = lines.join(', ') + ' ready to sync.';

  // Load full-mode quota
  var platforms = Object.keys(_gcfGhostByPlatform);
  _gcfLoadQuota(platforms, 'fetch-sync-quota');

  // Show modal
  if (!_gcfFetchModal) {
    _gcfFetchModal = new bootstrap.Modal(document.getElementById('mapFetchDialog'));
  }
  _gcfFetchModal.show();
}

// ── Submit sync ─────────────────────────────────────────────────────────────

function gcfSubmitSync() {
  if (!window._gcfGhostMarkers || !window._gcfGhostMarkers.length) return;

  var tags = document.getElementById('fetch-tags').value.trim();
  var logCount = parseInt(document.getElementById('fetch-log-count').value, 10) || 5;

  document.getElementById('fetch-sync-btn').disabled = true;
  document.getElementById('fetch-sync-progress').style.display = '';
  document.getElementById('fetch-sync-phase').textContent = 'Submitting...';

  fetch('/map/sync/', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': _gcfCsrf(),
    },
    body: JSON.stringify({
      platforms: _gcfGhostByPlatform,
      tags: tags,
      log_count: logCount,
    }),
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) {
        _gcfFlashMessage('Sync failed: ' + data.error);
        document.getElementById('fetch-sync-btn').disabled = false;
        document.getElementById('fetch-sync-progress').style.display = 'none';
        return;
      }
      _gcfPollSyncTasks(data.task_ids || []);
    })
    .catch(function() {
      _gcfFlashMessage('Sync request failed');
      document.getElementById('fetch-sync-btn').disabled = false;
      document.getElementById('fetch-sync-progress').style.display = 'none';
    });
}

function _gcfPollSyncTasks(taskIds) {
  if (!taskIds.length) {
    _gcfFlashMessage('No sync tasks created');
    document.getElementById('fetch-sync-btn').disabled = false;
    document.getElementById('fetch-sync-progress').style.display = 'none';
    return;
  }

  var completed = 0;
  var totalCreated = 0, totalUpdated = 0, totalFailed = 0;
  var syncProgress = {};
  taskIds.forEach(function(tid) { syncProgress[tid] = 0; });

  function _updateSyncBar() {
    var sum = 0;
    taskIds.forEach(function(tid) { sum += (syncProgress[tid] || 0); });
    document.getElementById('fetch-sync-bar').style.width = Math.round(sum / taskIds.length) + '%';
  }

  _gcfPollTimers.forEach(clearInterval);
  _gcfPollTimers = [];

  taskIds.forEach(function(tid) {
    var timer = setInterval(function() {
      fetch('/tasks/' + tid + '/')
        .then(function(r) { return r.json(); })
        .then(function(info) {
          syncProgress[tid] = info.progress_pct || 0;
          _updateSyncBar();
          document.getElementById('fetch-sync-phase').textContent = info.phase || info.state || '';

          if (info.state === 'completed' || info.state === 'failed' || info.state === 'cancelled') {
            clearInterval(timer);
            completed++;
            syncProgress[tid] = 100;
            _updateSyncBar();

            if (info.state === 'completed' && info.result) {
              totalCreated += info.result.created || 0;
              totalUpdated += info.result.updated || 0;
              totalFailed += info.result.failed || 0;
            }

            if (completed === taskIds.length) {
              _gcfSyncComplete(totalCreated, totalUpdated, totalFailed);
            }
          }
        });
    }, 1500);
    _gcfPollTimers.push(timer);
  });
}

function _gcfSyncComplete(created, updated, failed) {
  document.getElementById('fetch-sync-bar').style.width = '100%';

  // Build flash summary and store in sessionStorage so it survives page reload
  var parts = [];
  if (created) parts.push(created + ' new');
  if (updated) parts.push(updated + ' updated');
  if (failed) parts.push(failed + ' failed');
  var flashMsg = 'Synced: ' + (parts.join(', ') || '0 caches');
  sessionStorage.setItem('gcf_sync_flash', flashMsg);

  // Save enrichment data — triggered after page reload so the map redraws
  // immediately with synced caches while enrichment runs in background.
  if ((created || updated) && Object.keys(_gcfGhostByPlatform).length) {
    sessionStorage.setItem('gcf_pending_enrich', JSON.stringify(_gcfGhostByPlatform));
  }

  // Close dialog
  if (_gcfFetchModal) _gcfFetchModal.hide();

  // Build geo filter URL from current draw regions and reload the page.
  // This replaces ghost markers with real DB markers and applies the geo filter.
  var regions = (typeof _gcfDrawRegions !== 'undefined') ? _gcfDrawRegions : [];
  var geoParts = regions.map(function(r) {
    if (r.type === 'rect') {
      return 'rect:' + r.bbox.map(function(v) { return Number(v).toFixed(6); }).join(',');
    }
    if (r.type === 'circle') {
      return 'circle:' + Number(r.center[0]).toFixed(6) + ',' +
             Number(r.center[1]).toFixed(6) + ',' + Math.round(r.radius_m);
    }
    return null;
  }).filter(Boolean);

  var params = new URLSearchParams(window.location.search);
  if (geoParts.length) {
    params.set('geo', geoParts.join('|'));
  }
  params.delete('page');
  window.location.search = params.toString();
}

// ── Ghost marker rendering ──────────────────────────────────────────────────
// These are called from this file and from cache-map.js

// TYPE_COLORS — keys match CacheType.value (DB strings) from gc_client / oc_client
var _GHOST_TYPE_COLORS = {
  'Traditional':                    '#2d8b2d',
  'Multi-Cache':                    '#d4760a',
  'Mystery':                        '#1a6bc4',
  'Virtual':                        '#8B4513',
  'Earthcache':                     '#228B22',
  'Event':                          '#c41a8e',
  'Mega-Event':                     '#c41a8e',
  'Giga-Event':                     '#c41a8e',
  'Community Celebration Event':    '#c41a8e',
  'CITO':                           '#228B22',
  'Letterbox Hybrid':               '#ff6600',
  'Webcam':                         '#4a4a4a',
  'Wherigo':                        '#006b6b',
  'Moving':                         '#006b6b',
  'Project A.P.E.':                 '#8B0000',
  'Adventure Lab':                  '#8B0000',
  'GPS Adventures Exhibit':         '#666',
  'Geocaching HQ':                  '#666',
  'Geocaching HQ Celebration':      '#666',
  'Geocaching HQ Block Party':      '#666',
  'Locationless':                   '#666',
  'Podcast':                        '#666',
  'Own':                            '#666',
  'Drive-In':                       '#666',
  'Math/Physics':                   '#666',
};

function gcfSetGhostMarkers(caches) {
  if (!gcfMap) { console.warn('gcfSetGhostMarkers: map not ready'); return; }
  if (!gcfMap.isStyleLoaded()) {
    console.warn('gcfSetGhostMarkers: style not loaded, deferring');
    gcfMap.once('load', function() { gcfSetGhostMarkers(caches); });
    return;
  }
  var geojson = {
    type: 'FeatureCollection',
    features: caches.map(function(c) {
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [c.lon, c.lat] },
        properties: {
          code: c.code,
          name: c.name,
          type: c.type,
          size: c.size,
          difficulty: c.difficulty,
          terrain: c.terrain,
          status: c.status,
          found: c.found,
          in_db: c.in_db,
          platform: c.platform,
          color: _GHOST_TYPE_COLORS[c.type] || '#999',
        },
      };
    }),
  };

  try {
  if (gcfMap.getSource('ghost-markers')) {
    gcfMap.getSource('ghost-markers').setData(geojson);
  } else {
    gcfMap.addSource('ghost-markers', { type: 'geojson', data: geojson });

    gcfMap.addLayer({
      id: 'ghost-marker-circles',
      type: 'circle',
      source: 'ghost-markers',
      paint: {
        'circle-radius': 7,
        'circle-color': ['get', 'color'],
        'circle-opacity': 0.7,
        'circle-stroke-width': 2,
        'circle-stroke-color': ['get', 'color'],
        'circle-stroke-opacity': 0.6,
      },
    });

    gcfMap.addLayer({
      id: 'ghost-marker-labels',
      type: 'symbol',
      source: 'ghost-markers',
      layout: {
        'text-field': ['get', 'code'],
        'text-size': 9,
        'text-offset': [0, 1.5],
        'text-allow-overlap': false,
      },
      paint: {
        'text-color': '#666',
        'text-halo-color': '#fff',
        'text-halo-width': 1,
        'text-opacity': 0.7,
      },
    });

    // Click popup for ghost markers
    gcfMap.on('click', 'ghost-marker-circles', function(e) {
      if (!e.features || !e.features.length) return;
      var f = e.features[0].properties;
      var badge = f.in_db
        ? '<span class="badge bg-secondary">Already synced</span>'
        : '<span class="badge bg-success">New</span>';
      var html =
        '<div style="min-width:180px">' +
        '<strong>' + _escHtml(f.name) + '</strong> ' + badge + '<br>' +
        '<span class="text-muted">' + _escHtml(f.code) + '</span><br>' +
        'Type: ' + _escHtml(f.type) + ' | Size: ' + _escHtml(f.size) + '<br>' +
        'D/T: ' + f.difficulty + '/' + f.terrain + '<br>' +
        'Status: ' + _escHtml(f.status) +
        '</div>';
      new maplibregl.Popup({ closeButton: true, maxWidth: '260px' })
        .setLngLat(e.lngLat)
        .setHTML(html)
        .addTo(gcfMap);
    });

    gcfMap.on('mouseenter', 'ghost-marker-circles', function() {
      gcfMap.getCanvas().style.cursor = 'pointer';
    });
    gcfMap.on('mouseleave', 'ghost-marker-circles', function() {
      gcfMap.getCanvas().style.cursor = '';
    });
  }
  } catch (err) {
    console.error('gcfSetGhostMarkers error:', err);
  }
}

function gcfClearGhostMarkers() {
  window._gcfGhostMarkers = [];
  _gcfGhostByPlatform = {};
  if (gcfMap && gcfMap.getSource('ghost-markers')) {
    gcfMap.getSource('ghost-markers').setData({ type: 'FeatureCollection', features: [] });
  }
  _gcfUpdateSyncButton();
}

// ── Toolbar button visibility ───────────────────────────────────────────────

function gcfUpdateFetchButtons() {
  var regions = (typeof _gcfDrawRegions !== 'undefined') ? _gcfDrawRegions : [];
  var previewBtn = document.getElementById('map-fetch-preview-btn');
  if (previewBtn) {
    previewBtn.style.display = regions.length > 0 ? '' : 'none';
  }

  // Any shape change invalidates ghost markers (they were fetched for old regions)
  if (window._gcfGhostMarkers && window._gcfGhostMarkers.length) {
    gcfClearGhostMarkers();
  }
}

function _gcfUpdateSyncButton() {
  var syncBtn = document.getElementById('map-fetch-sync-btn');
  if (syncBtn) {
    syncBtn.style.display = (window._gcfGhostMarkers && window._gcfGhostMarkers.length) ? '' : 'none';
  }
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function _gcfSelectedPlatforms() {
  var cbs = document.querySelectorAll('.fetch-platform-cb:checked');
  var platforms = [];
  for (var i = 0; i < cbs.length; i++) {
    platforms.push(cbs[i].value);
  }
  return platforms;
}

function _escHtml(s) {
  if (!s) return '';
  var div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

function _fmtNum(n) {
  if (n == null) return '?';
  return n.toLocaleString();
}
