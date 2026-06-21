/**
 * map-fetch.js — Preview / Sync workflow for fetching caches from API via drawn regions.
 *
 * Depends on: cache-map.js (gcfMap, _gcfFetchMarkers)
 *             map-draw.js  (_gcfDrawRegions, gcfPinAsFilter)
 *             map-context-menu.js (_gcfFlashMessage)
 *             Bootstrap 5 (modal)
 */

/* global gcfMap, _gcfDrawRegions, _gcfFetchMarkers, gcfSetGhostMarkers, gcfClearGhostMarkers,
          gcfPinAsFilter, _gcfFlashMessage, bootstrap, htmx */

// ── State ───────────────────────────────────────────────────────────────────

// Ghost marker data from preview, grouped by platform for the sync step.
window._gcfGhostMarkers = [];
var _gcfGhostByPlatform = {};  // { "gc": ["GC1","GC2"], "oc_de": ["OC1"] }
var _gcfPollTimers = [];
var _gcfFetchModal = null;
var _gcfFetchMode = 'area';    // 'area' | 'criteria'
var _gcfProviderCaps = {};     // platform → { min_fav, found_status }

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

// Open the dialog forced into criteria mode (toolbar "Search by criteria").
function gcfOpenCriteriaSearch() {
  gcfOpenFetchDialog('criteria');
}

// Switch between "by area" and "by criteria" preview modes.
function gcfFetchSetMode(mode) {
  _gcfFetchMode = (mode === 'criteria') ? 'criteria' : 'area';
  var isCriteria = _gcfFetchMode === 'criteria';

  var radio = document.getElementById(isCriteria ? 'fetch-mode-criteria' : 'fetch-mode-area');
  if (radio) radio.checked = true;

  var critFields = document.getElementById('fetch-criteria-fields');
  if (critFields) critFields.style.display = isCriteria ? '' : 'none';
  var summary = document.getElementById('fetch-region-summary');
  if (summary) summary.style.display = isCriteria ? 'none' : '';
  var introArea = document.getElementById('fetch-intro-area');
  if (introArea) introArea.style.display = isCriteria ? 'none' : '';
  var introCrit = document.getElementById('fetch-intro-criteria');
  if (introCrit) introCrit.style.display = isCriteria ? '' : 'none';

  if (isCriteria) _gcfUpdateCriteriaGating();
}

function gcfOpenFetchDialog(mode) {
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

  // Choose mode: explicit arg, else area when shapes are drawn, else criteria.
  var resolvedMode = (mode === 'area' || mode === 'criteria')
    ? mode : (regions.length ? 'area' : 'criteria');
  gcfFetchSetMode(resolvedMode);

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
      _gcfProviderCaps = {};
      providers.forEach(function(p) {
        if (platforms.indexOf(p.platform) !== -1) return; // dedup
        platforms.push(p.platform);
        _gcfProviderCaps[p.platform] = p.capabilities || {};
        var div = document.createElement('div');
        div.className = 'form-check';
        div.innerHTML =
          '<input class="form-check-input fetch-platform-cb" type="checkbox" value="' +
          p.platform + '" id="fetch-plat-' + p.platform + '" checked>' +
          '<label class="form-check-label small" for="fetch-plat-' + p.platform + '">' +
          _escHtml(p.label) + ' (' + _escHtml(p.username) + ')</label>';
        container.appendChild(div);
        div.querySelector('input').addEventListener('change', _gcfUpdateCriteriaGating);
      });

      // Re-gate criteria fields now that capabilities are known
      _gcfUpdateCriteriaGating();

      // Load quota for these platforms
      _gcfLoadQuota(platforms, 'fetch-quota');
    })
    .catch(function() {
      container.innerHTML = '<span class="text-danger small">' + gettext('Failed to load providers') + '</span>';
    });
}

function _gcfLoadQuota(platforms, elementId) {
  var el = document.getElementById(elementId);
  el.textContent = gettext('Loading...');
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
          interpolate(
            gettext('%(platform)s: %(lightRem)s / %(lightLim)s light, %(fullRem)s / %(fullLim)s full'),
            {
              platform: p.toUpperCase(),
              lightRem: _fmtNum(light.remaining),
              lightLim: _fmtNum(light.limit),
              fullRem: _fmtNum(full.remaining),
              fullLim: _fmtNum(full.limit)
            }, true)
        );
      });
      el.innerHTML = lines.join('<br>');
    })
    .catch(function() {
      el.innerHTML = '<span class="text-danger">' + gettext('Failed to load quota') + '</span>';
    });
}

// ── Submit preview ──────────────────────────────────────────────────────────

// ── Criteria field gating + gathering ────────────────────────────────────────

function _gcfSetFieldEnabled(id, enabled, noteId) {
  var el = document.getElementById(id);
  if (el) el.disabled = !enabled;
  var note = noteId && document.getElementById(noteId);
  if (note) note.style.display = enabled ? 'none' : '';
}

// Disable criteria fields no selected provider can honour. With no selection,
// leave everything enabled (the user hasn't narrowed providers yet).
function _gcfUpdateCriteriaGating() {
  var sel = _gcfSelectedPlatforms();
  function anySupports(cap) {
    if (!sel.length) return true;
    return sel.some(function(p) { return _gcfProviderCaps[p] && _gcfProviderCaps[p][cap]; });
  }
  _gcfSetFieldEnabled('crit-min-fav', anySupports('min_fav'), 'crit-min-fav-note');
  _gcfSetFieldEnabled('crit-found-status', anySupports('found_status'), 'crit-found-status-note');
}

function _gcfGatherCriteria() {
  function val(id) { var e = document.getElementById(id); return e ? e.value.trim() : ''; }
  function num(id) { var e = document.getElementById(id); var v = e ? parseFloat(e.value) : NaN; return isNaN(v) ? null : v; }
  function multi(id) {
    var e = document.getElementById(id);
    return e ? Array.prototype.map.call(e.selectedOptions, function(o) { return o.value; }) : [];
  }
  var favEl = document.getElementById('crit-min-fav');
  var fsEl = document.getElementById('crit-found-status');
  return {
    owner: val('crit-owner'),
    types: multi('crit-types'),
    d_min: num('crit-d-min'), d_max: num('crit-d-max'),
    t_min: num('crit-t-min'), t_max: num('crit-t-max'),
    sizes: multi('crit-sizes'),
    min_fav: (favEl && !favEl.disabled) ? (parseInt(favEl.value, 10) || 0) : 0,
    name: val('crit-name'),
    found_status: (fsEl && !fsEl.disabled) ? fsEl.value : 'either',
  };
}

// True when no criterion would narrow the search (avoids fetching the planet).
function _gcfCriteriaIsEmpty(c) {
  return !c.owner && !c.name && !c.types.length && !c.sizes.length && !c.min_fav &&
    (c.found_status === 'either' || !c.found_status) &&
    (c.d_min == null || c.d_min <= 1) && (c.d_max == null || c.d_max >= 5) &&
    (c.t_min == null || c.t_min <= 1) && (c.t_max == null || c.t_max >= 5);
}

function _gcfViewportBbox() {
  if (typeof gcfMap === 'undefined' || !gcfMap || !gcfMap.getBounds) return null;
  var b = gcfMap.getBounds();
  return [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()];
}

function gcfSubmitPreview() {
  var platforms = _gcfSelectedPlatforms();
  if (!platforms.length) {
    _gcfFlashMessage(gettext('Select at least one provider'));
    return;
  }

  var regionData;
  if (_gcfFetchMode === 'criteria') {
    var criteria = _gcfGatherCriteria();
    var bbox = document.getElementById('crit-limit-view') &&
               document.getElementById('crit-limit-view').checked
      ? _gcfViewportBbox() : null;
    if (_gcfCriteriaIsEmpty(criteria) && !bbox) {
      _gcfFlashMessage(gettext('Enter at least one search criterion'));
      return;
    }
    regionData = [{ type: 'criteria', criteria: criteria, bbox: bbox }];
  } else {
    var regions = (typeof _gcfDrawRegions !== 'undefined') ? _gcfDrawRegions : [];
    if (!regions.length) {
      _gcfFlashMessage(gettext('Draw at least one region first'));
      return;
    }
    // Prepare region data (strip draw feature IDs)
    regionData = regions.map(function(r) {
      if (r.type === 'rect') return { type: 'rect', bbox: r.bbox };
      if (r.type === 'circle') return { type: 'circle', center: r.center, radius_m: r.radius_m };
      if (r.type === 'polygon') return { type: 'polygon', coordinates: r.coordinates };
      if (r.type === 'corridor') return { type: 'corridor', path: r.path, width_m: r.width_m };
      return null;
    }).filter(Boolean);
    if (!regionData.length) {
      _gcfFlashMessage(gettext('No previewable regions'));
      return;
    }
  }

  document.getElementById('fetch-preview-btn').disabled = true;
  document.getElementById('fetch-preview-progress').style.display = '';
  document.getElementById('fetch-preview-phase').textContent = gettext('Submitting...');

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
        _gcfFlashMessage(interpolate(gettext('Preview failed: %s'), [data.error]));
        document.getElementById('fetch-preview-btn').disabled = false;
        document.getElementById('fetch-preview-progress').style.display = 'none';
        return;
      }
      // Poll each task
      _gcfPollPreviewTasks(data.task_ids || []);
    })
    .catch(function(err) {
      _gcfFlashMessage(gettext('Preview request failed'));
      document.getElementById('fetch-preview-btn').disabled = false;
      document.getElementById('fetch-preview-progress').style.display = 'none';
    });
}

function _gcfPollPreviewTasks(taskIds) {
  if (!taskIds.length) {
    _gcfFlashMessage(gettext('No preview tasks created'));
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
                  allErrors.push(interpolate(gettext('Failed to fetch results for task %s'), [tid]));
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
    var noResultMsg = (_gcfFetchMode === 'criteria')
      ? gettext('No caches matched your criteria')
      : gettext('No caches found in the selected area');
    if (errors.length) {
      noResultMsg += interpolate(gettext(' — fetch error: %s'), [errors[0]]);
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

  // In criteria mode the draw-status badge would otherwise read "0 selected".
  if (_gcfFetchMode === 'criteria') {
    var badge = document.getElementById('map-draw-count');
    if (badge) {
      badge.textContent = interpolate(
        ngettext('%s cache found', '%s caches found', caches.length), [caches.length]);
    }
  }

  // Close modal + flash message
  if (_gcfFetchModal) _gcfFetchModal.hide();
  var newCount = caches.filter(function(c) { return !c.in_db; }).length;
  var existCount = caches.length - newCount;
  var flashMsg = interpolate(
    gettext('Preview: %(total)s caches (%(newCount)s new, %(existCount)s already synced)'),
    { total: caches.length, newCount: newCount, existCount: existCount }, true);
  if (errors.length) {
    flashMsg += interpolate(
      ngettext(' — %s batch error', ' — %s batch errors', errors.length),
      [errors.length]);
  }
  _gcfFlashMessage(flashMsg);

  document.getElementById('fetch-preview-btn').disabled = false;
  document.getElementById('fetch-preview-progress').style.display = 'none';
}

// ── Open sync dialog ────────────────────────────────────────────────────────

function gcfOpenSyncDialog() {
  if (!window._gcfGhostMarkers || !window._gcfGhostMarkers.length) {
    _gcfFlashMessage(gettext('Run a preview first'));
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
    lines.push(interpolate(
      gettext('<strong>%(count)s</strong> caches from %(platform)s'),
      { count: count, platform: plat.toUpperCase() }, true));
  });
  document.getElementById('fetch-sync-summary').innerHTML = lines.join(', ') + ' ' + gettext('ready to sync.');

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
  var logCount = parseInt(document.getElementById('fetch-log-count').value, 10);
  if (isNaN(logCount) || logCount < 0) logCount = 0;

  document.getElementById('fetch-sync-btn').disabled = true;
  document.getElementById('fetch-sync-progress').style.display = '';
  document.getElementById('fetch-sync-phase').textContent = gettext('Submitting...');

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
        _gcfFlashMessage(interpolate(gettext('Sync failed: %s'), [data.error]));
        document.getElementById('fetch-sync-btn').disabled = false;
        document.getElementById('fetch-sync-progress').style.display = 'none';
        return;
      }
      _gcfPollSyncTasks(data.task_ids || []);
    })
    .catch(function() {
      _gcfFlashMessage(gettext('Sync request failed'));
      document.getElementById('fetch-sync-btn').disabled = false;
      document.getElementById('fetch-sync-progress').style.display = 'none';
    });
}

function _gcfPollSyncTasks(taskIds) {
  if (!taskIds.length) {
    _gcfFlashMessage(gettext('No sync tasks created'));
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

  // Build flash summary.
  var parts = [];
  if (created) parts.push(interpolate(gettext('%s new'), [created]));
  if (updated) parts.push(interpolate(gettext('%s updated'), [updated]));
  if (failed) parts.push(interpolate(gettext('%s failed'), [failed]));
  var flashMsg = interpolate(gettext('Synced: %s'), [parts.join(', ') || gettext('0 caches')]);

  // Enrichment payload (elevation/location) for the caches just synced.
  var enrichPayload = ((created || updated) && Object.keys(_gcfGhostByPlatform).length)
    ? JSON.stringify(_gcfGhostByPlatform) : null;

  // Close dialog
  if (_gcfFetchModal) _gcfFetchModal.hide();

  // Build a geo filter from the drawn regions so the view scopes to the synced
  // area. Criteria searches have no drawn shape to pin, so skip the geo rewrite.
  var regions = (_gcfFetchMode === 'criteria' || typeof _gcfDrawRegions === 'undefined')
    ? [] : _gcfDrawRegions;
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
  var qs = params.toString();
  var url = window.location.pathname + (qs ? '?' + qs : '');

  var tableContainer = document.getElementById('cache-table-container');
  if (window.htmx && tableContainer) {
    // In-place refresh — no full page reload, so MapLibre is never torn down
    // and rebuilt. Update the address bar, drop the now-stale ghost markers,
    // then swap just the list table. The htmx:afterSwap hook (map-layout.js)
    // refreshes the map markers with the same filter, replacing the ghosts
    // with the real DB markers.
    history.replaceState({}, '', url);
    if (typeof gcfClearGhostMarkers === 'function') gcfClearGhostMarkers();
    if (typeof _gcfFlashMessage === 'function') _gcfFlashMessage(flashMsg);
    if (enrichPayload) {
      fetch('/map/auto-enrich/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _gcfCsrf() },
        body: enrichPayload,
      }).catch(function() {});  // fire-and-forget
    }
    htmx.ajax('GET', url, { target: '#cache-table-container', swap: 'innerHTML' });
  } else {
    // Fallback (htmx/table unavailable): carry the flash + enrichment across a
    // full page reload via sessionStorage (read by the IIFE at top of file).
    sessionStorage.setItem('gcf_sync_flash', flashMsg);
    if (enrichPayload) sessionStorage.setItem('gcf_pending_enrich', enrichPayload);
    window.location.search = qs;
  }
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
        'text-anchor': 'top',
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
        ? '<span class="badge bg-secondary">' + gettext('Already synced') + '</span>'
        : '<span class="badge bg-success">' + gettext('New') + '</span>';
      var html =
        '<div style="min-width:180px">' +
        '<strong>' + _escHtml(f.name) + '</strong> ' + badge + '<br>' +
        '<span class="text-muted">' + _escHtml(f.code) + '</span><br>' +
        gettext('Type:') + ' ' + _escHtml(f.type) + ' | ' + gettext('Size:') + ' ' + _escHtml(f.size) + '<br>' +
        gettext('D/T:') + ' ' + f.difficulty + '/' + f.terrain + '<br>' +
        gettext('Status:') + ' ' + _escHtml(f.status) +
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
  var nonAlcRegions = regions.filter(function(r) { return r.type !== 'al_circle'; });
  var previewBtn = document.getElementById('map-fetch-preview-btn');
  if (previewBtn) {
    previewBtn.style.display = nonAlcRegions.length > 0 ? '' : 'none';
  }

  // Any shape change invalidates ghost markers (they were fetched for old regions)
  if (window._gcfGhostMarkers && window._gcfGhostMarkers.length) {
    gcfClearGhostMarkers();
  }
}

function gcfUpdateAlcButtons() {
  var regions = (typeof _gcfDrawRegions !== 'undefined') ? _gcfDrawRegions : [];
  var alCircles = regions.filter(function(r) { return r.type === 'al_circle'; });
  var nonAlcRegions = regions.filter(function(r) { return r.type !== 'al_circle'; });
  var isRegular = (typeof _gcfDrawModeGroup !== 'undefined') && _gcfDrawModeGroup === 'regular';

  var fetchBtn = document.getElementById('map-alc-fetch-btn');
  if (fetchBtn) fetchBtn.style.display = (!isRegular && alCircles.length > 0) ? '' : 'none';

  var refreshBtn = document.getElementById('map-alc-refresh-btn');
  if (refreshBtn) refreshBtn.style.display = (!isRegular && nonAlcRegions.length > 0) ? '' : 'none';
}

function _gcfUpdateSyncButton() {
  var has = window._gcfGhostMarkers && window._gcfGhostMarkers.length;
  var syncBtn = document.getElementById('map-fetch-sync-btn');
  if (syncBtn) syncBtn.style.display = has ? '' : 'none';
  // The Sync button lives in the draw-status bar, which is hidden until there
  // is draw activity. A criteria preview has no drawn shape, so reveal the bar
  // here whenever ghost markers exist (e.g. after a criteria search).
  if (has) {
    var status = document.getElementById('map-draw-status');
    if (status && status.style.display === 'none') status.style.display = '';
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

// ── Adventure Lab fetch/refresh ──────────────────────────────────────────────

var _gcfAlcDialogMode = null; // 'fetch' | 'refresh'
var _gcfAlcRefreshBbox = null; // computed bbox for refresh

function gcfFetchAlcInArea() {
  var regions = (typeof _gcfDrawRegions !== 'undefined') ? _gcfDrawRegions : [];
  var alCircles = regions.filter(function(r) { return r.type === 'al_circle'; });
  if (!alCircles.length) {
    _gcfFlashMessage(gettext('Draw at least one ALC circle first'));
    return;
  }
  _gcfAlcDialogMode = 'fetch';
  _gcfOpenAlcDialog(gettext('Fetch ALCs in area'), gettext('Fetch'));
}

function gcfRefreshAlcInArea() {
  var regions = (typeof _gcfDrawRegions !== 'undefined') ? _gcfDrawRegions : [];
  var nonAlc = regions.filter(function(r) { return r.type !== 'al_circle'; });
  if (!nonAlc.length) {
    _gcfFlashMessage(gettext('Draw at least one area shape first'));
    return;
  }
  var south = Infinity, west = Infinity, north = -Infinity, east = -Infinity;
  nonAlc.forEach(function(r) {
    if (r.type === 'rect') {
      var b = r.bbox;
      south = Math.min(south, b[0]); west = Math.min(west, b[1]);
      north = Math.max(north, b[2]); east = Math.max(east, b[3]);
    } else if (r.type === 'circle') {
      var latDelta = r.radius_m / 111320;
      var lonDelta = r.radius_m / (111320 * Math.cos(r.center[0] * Math.PI / 180));
      south = Math.min(south, r.center[0] - latDelta);
      north = Math.max(north, r.center[0] + latDelta);
      west  = Math.min(west,  r.center[1] - lonDelta);
      east  = Math.max(east,  r.center[1] + lonDelta);
    } else if (r.type === 'polygon' || r.type === 'corridor') {
      var coords = r.coordinates || r.path || [];
      coords.forEach(function(c) {
        south = Math.min(south, c[1]); north = Math.max(north, c[1]);
        west  = Math.min(west,  c[0]); east  = Math.max(east,  c[0]);
      });
    }
  });
  _gcfAlcRefreshBbox = { south: south, west: west, north: north, east: east };
  _gcfAlcDialogMode = 'refresh';
  _gcfOpenAlcDialog(gettext('Refresh ALCs in area'), gettext('Refresh'));
}

function _gcfOpenAlcDialog(title, confirmLabel) {
  var titleEl = document.getElementById('alcFetchDialogTitle');
  var confirmBtn = document.getElementById('alc-fetch-confirm-btn');
  var tagsInput = document.getElementById('alc-fetch-tags');
  var suggestionsBox = document.getElementById('alc-fetch-tag-suggestions');
  if (titleEl) titleEl.textContent = title;
  if (confirmBtn) confirmBtn.textContent = confirmLabel;
  if (tagsInput) tagsInput.value = '';
  if (suggestionsBox) {
    suggestionsBox.innerHTML = '';
    var tagsUrl = (typeof _gcfTagsUrl !== 'undefined') ? _gcfTagsUrl : null;
    if (tagsUrl) {
      fetch(tagsUrl)
        .then(function(r) { return r.json(); })
        .then(function(names) {
          names.forEach(function(name) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btn btn-sm btn-outline-secondary me-1 mb-1';
            btn.textContent = name;
            btn.addEventListener('click', function() {
              var inp = document.getElementById('alc-fetch-tags');
              var cur = inp.value.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
              if (cur.indexOf(name) === -1) cur.push(name);
              inp.value = cur.join(', ');
            });
            suggestionsBox.appendChild(btn);
          });
        });
    }
  }
  var modalEl = document.getElementById('alcFetchDialog');
  if (modalEl) {
    var modal = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
    modal.show();
  }
}

function gcfConfirmAlcAction() {
  var tagsInput = document.getElementById('alc-fetch-tags');
  var tags = tagsInput
    ? tagsInput.value.split(',').map(function(s) { return s.trim(); }).filter(Boolean)
    : [];

  var modalEl = document.getElementById('alcFetchDialog');
  var modal = modalEl && (bootstrap.Modal.getInstance(modalEl) || null);
  if (modal) modal.hide();

  if (_gcfAlcDialogMode === 'fetch') {
    _gcfDoFetchAlcInArea(tags);
  } else if (_gcfAlcDialogMode === 'refresh') {
    _gcfDoRefreshAlcInArea(tags);
  }
}

function _gcfDoFetchAlcInArea(tags) {
  var regions = (typeof _gcfDrawRegions !== 'undefined') ? _gcfDrawRegions : [];
  var alCircles = regions.filter(function(r) { return r.type === 'al_circle'; });
  var circles = alCircles.map(function(r) {
    return { lat: r.center[0], lon: r.center[1], radius_m: r.radius_m };
  });
  var btn = document.getElementById('map-alc-fetch-btn');
  if (btn) btn.disabled = true;
  fetch('/map/al-fetch-circles/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _gcfCsrf() },
    body: JSON.stringify({ circles: circles, tags: tags }),
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) {
        _gcfFlashMessage(interpolate(gettext('ALC fetch failed: %s'), [data.error]));
      } else {
        _gcfFlashMessage(gettext('ALC fetch started — track progress in the task indicator'));
      }
    })
    .catch(function() { _gcfFlashMessage(gettext('ALC fetch request failed')); })
    .finally(function() { if (btn) btn.disabled = false; });
}

function _gcfDoRefreshAlcInArea(tags) {
  var bbox = _gcfAlcRefreshBbox;
  if (!bbox) return;
  var btn = document.getElementById('map-alc-refresh-btn');
  if (btn) btn.disabled = true;
  fetch('/map/al-refresh-bbox/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': _gcfCsrf() },
    body: JSON.stringify({ south: bbox.south, west: bbox.west, north: bbox.north, east: bbox.east, tags: tags }),
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) {
        _gcfFlashMessage(interpolate(gettext('ALC refresh failed: %s'), [data.error]));
      } else {
        _gcfFlashMessage(gettext('ALC refresh started — track progress in the task indicator'));
      }
    })
    .catch(function() { _gcfFlashMessage(gettext('ALC refresh request failed')); })
    .finally(function() { if (btn) btn.disabled = false; });
}
