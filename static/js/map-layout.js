// ── GCForge Map Layout — toggle, split divider, map↔list sync ────────────────
//
// Loaded eagerly (small file). MapLibre + cache-map.js are lazy-loaded
// when the user first switches to split or map layout.

(function() {
  var _mapScriptsLoaded = false;
  var _mapScriptsLoading = false;
  var _currentLayout = 'list';

  // ── Initialization ─────────────────────────────────────────────────────────

  function init() {
    // Restore saved layout
    var serverDefault = (typeof _gcfMapPrefs !== 'undefined' && _gcfMapPrefs.layout) || 'list';
    var saved = localStorage.getItem('gcforge_map_layout') || serverDefault;
    _setLayout(saved, true);

    // Setup divider drag for both map and detail panels
    _initDivider(document.getElementById('map-divider'), function() {
      if (typeof gcfMap !== 'undefined' && gcfMap) gcfMap.resize();
    });
    _initDivider(document.getElementById('detail-divider'), null);

    // Setup list row click → map/detail sync
    _setupListSync();
  }

  // ── Layout switching ───────────────────────────────────────────────────────

  function _setLayout(layout, skipSave) {
    _currentLayout = layout;
    document.body.classList.remove('layout-split', 'layout-map', 'layout-split-detail');

    // Only apply split/map layout on the list page (where #list-panel exists).
    // On other pages (detail, settings, etc.) keep the body class-free so
    // scrolling and normal layout work.
    var isListPage = !!document.getElementById('list-panel');

    if (!isListPage) {
      // Remember the preference but don't apply the layout
      _currentLayout = 'list';
    } else if (layout === 'split') {
      document.body.classList.add('layout-split');
      _applySplitPct();
      _ensureMapLoaded();
    } else if (layout === 'map') {
      document.body.classList.add('layout-map');
      _ensureMapLoaded();
    } else if (layout === 'split-detail') {
      document.body.classList.add('layout-split-detail');
      _applySplitPct();
    } else {
      // list mode: clear inline width left by split divider
      var lp = document.getElementById('list-panel');
      if (lp) lp.style.width = '';
    }

    // Clear detail state when leaving split-detail
    if (layout !== 'split-detail') {
      var panel = document.getElementById('detail-panel');
      if (panel && panel.src !== 'about:blank') panel.src = 'about:blank';
      document.querySelectorAll('tr[data-code].detail-active').forEach(function(r) {
        r.classList.remove('detail-active');
      });
    }

    // Update toggle buttons
    document.querySelectorAll('.layout-toggle-btn').forEach(function(btn) {
      btn.classList.toggle('active', btn.dataset.layout === layout);
    });

    if (!skipSave) {
      localStorage.setItem('gcforge_map_layout', layout);
      _saveLayoutPref(layout);
    }

    // Resize map if visible
    if ((layout === 'split' || layout === 'map') && typeof gcfMap !== 'undefined' && gcfMap) {
      setTimeout(function() { gcfMap.resize(); }, 50);
    }
  }

  // Cycle: list → split → split-detail → map → list
  window.gcfLayoutCycle = function() {
    var next = { list: 'split', split: 'split-detail', 'split-detail': 'map', map: 'list' };
    _setLayout(next[_currentLayout] || 'list');
  };

  window.gcfLayoutSet = function(layout) {
    // If not on the list page, navigate there (the layout will apply on load)
    if (!document.getElementById('list-panel')) {
      localStorage.setItem('gcforge_map_layout', layout);
      _saveLayoutPref(layout);
      var listUrl = sessionStorage.getItem('gcforge_list_url') || '/';
      window.location.href = listUrl;
      return;
    }
    _setLayout(layout);
  };

  // ── Save layout preference via AJAX ────────────────────────────────────────

  function _saveLayoutPref(layout) {
    var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
    if (!csrfToken) return;
    var body = new URLSearchParams();
    body.set('map_layout', layout);
    fetch('/settings/save-map-state/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-CSRFToken': csrfToken.value
      },
      body: body.toString()
    }).catch(function() {});
  }

  // ── Lazy-load MapLibre + mapbox-gl-draw + map scripts ─────────────────────

  function _ensureMapLoaded() {
    if (_mapScriptsLoaded) {
      if (typeof gcfMapInit === 'function') gcfMapInit();
      if (typeof gcfDrawInit === 'function') gcfDrawInit();
      if (typeof gcfSearchInit === 'function') gcfSearchInit();
      if (typeof gcfContextMenuInit === 'function') gcfContextMenuInit();
      return;
    }
    if (_mapScriptsLoading) return;
    _mapScriptsLoading = true;

    gcfLoadMapLibre({
      draw: true,
      scripts: [
        '/static/js/map-styles.js',
        '/static/js/map-offline.js',
        '/static/js/map-icons.js',
        '/static/js/cache-map.js',
        '/static/js/map-draw.js',
        '/static/js/map-search.js',
        '/static/js/map-layers.js',
        '/static/js/map-context-menu.js',
        '/static/js/map-fetch.js',
        '/static/js/map-route.js'
      ],
      onReady: function() {
        _mapScriptsLoaded = true;
        _mapScriptsLoading = false;
        if (typeof gcfMapInit === 'function') gcfMapInit();
        if (typeof gcfDrawInit === 'function') gcfDrawInit();
        if (typeof gcfSearchInit === 'function') gcfSearchInit();
        if (typeof gcfContextMenuInit === 'function') gcfContextMenuInit();
        if (typeof gcfRouteInit === 'function') gcfRouteInit();
      }
    });
  }

  // ── Split view divider ─────────────────────────────────────────────────────

  function _applySplitPct() {
    var pct = parseInt(localStorage.getItem('gcforge_map_split_pct') || '40', 10);
    pct = Math.max(20, Math.min(80, pct));
    var listPanel = document.getElementById('list-panel');
    if (listPanel) {
      listPanel.style.width = pct + '%';
    }
  }

  function _initDivider(divider, onResize) {
    if (!divider) return;

    var isDragging = false;

    divider.addEventListener('mousedown', function(e) {
      e.preventDefault();
      isDragging = true;
      divider.classList.add('dragging');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    });

    document.addEventListener('mousemove', function(e) {
      if (!isDragging) return;
      var content = document.getElementById('content');
      if (!content) return;
      var rect = content.getBoundingClientRect();
      var pct = ((e.clientX - rect.left) / rect.width) * 100;
      pct = Math.max(20, Math.min(80, pct));
      var listPanel = document.getElementById('list-panel');
      if (listPanel) {
        listPanel.style.width = pct + '%';
      }
      if (onResize) onResize();
    });

    document.addEventListener('mouseup', function() {
      if (!isDragging) return;
      isDragging = false;
      divider.classList.remove('dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';

      // Persist split percentage
      var listPanel = document.getElementById('list-panel');
      if (listPanel) {
        var content = document.getElementById('content');
        var pct = Math.round((listPanel.offsetWidth / content.offsetWidth) * 100);
        localStorage.setItem('gcforge_map_split_pct', pct);
        _saveSplitPct(pct);
      }
    });
  }

  function _saveSplitPct(pct) {
    var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
    if (!csrfToken) return;
    var body = new URLSearchParams();
    body.set('map_split_pct', pct);
    fetch('/settings/save-map-state/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-CSRFToken': csrfToken.value
      },
      body: body.toString()
    }).catch(function() {});
  }

  // ── Detail panel loading ───────────────────────────────────────────────────

  function _loadDetailPanel(href) {
    var panel = document.getElementById('detail-panel');
    if (!panel) return;
    var sep = href.indexOf('?') >= 0 ? '&' : '?';
    var url = href + sep + 'embed=1';
    if (panel.src !== url) {
      panel.src = url;
    }
  }

  // ── List ↔ Map/Detail sync ─────────────────────────────────────────────────

  function _setupListSync() {
    document.addEventListener('click', function(e) {
      var tr = e.target.closest('tr[data-code]');
      if (!tr) return;
      var code = tr.dataset.code;

      if (_currentLayout === 'split') {
        if (code && typeof gcfMapPanTo === 'function') {
          gcfMapPanTo(code);
        }
      } else if (_currentLayout === 'split-detail') {
        // Allow external links (target=_blank) to open normally
        var anchor = e.target.closest('a');
        if (anchor && anchor.target === '_blank') return;
        // Prevent internal link navigation
        if (anchor) e.preventDefault();

        var nameLink = tr.querySelector('a.cache-name-link');
        if (nameLink) {
          _loadDetailPanel(nameLink.href);
        }

        // Highlight selected row
        document.querySelectorAll('tr[data-code].detail-active').forEach(function(r) {
          r.classList.remove('detail-active');
        });
        tr.classList.add('detail-active');
      }
    });

    // Also handle HTMX partial reloads — re-attach after table swap
    document.body.addEventListener('htmx:afterSwap', function(evt) {
      if (evt.detail.target.id === 'cache-table-container') {
        // Refresh map markers when list filters change.  Pass the just-completed
        // request's query string: at afterSwap, HTMX has not yet pushed the new
        // URL, so window.location.search is stale and would drop the filter that
        // was just applied (e.g. an fx tag clause → map shows the old, broader set).
        if (typeof gcfMapRefresh === 'function' && (_currentLayout === 'split' || _currentLayout === 'map')) {
          var listSearch;
          try { listSearch = new URL(evt.detail.xhr.responseURL).search; } catch (e) { listSearch = undefined; }
          gcfMapRefresh(listSearch);
        }
        // Clear detail panel selection on filter change
        if (_currentLayout === 'split-detail') {
          var panel = document.getElementById('detail-panel');
          if (panel) panel.src = 'about:blank';
        }
      }
    });
  }

  // Map marker clicked → scroll list to that row and highlight
  window.gcfMapMarkerClicked = function(code) {
    if (_currentLayout !== 'split') return;
    var row = document.querySelector('tr[data-code="' + code + '"]');
    if (!row) return;
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    row.classList.add('map-highlight');
    setTimeout(function() { row.classList.remove('map-highlight'); }, 2000);
  };

  // ── Boot ───────────────────────────────────────────────────────────────────

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
