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

    // Setup divider drag
    _setupDivider();

    // Setup list row click → map sync
    _setupListSync();
  }

  // ── Layout switching ───────────────────────────────────────────────────────

  function _setLayout(layout, skipSave) {
    _currentLayout = layout;
    document.body.classList.remove('layout-split', 'layout-map');

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
    } else {
      // list mode: clear inline width left by split divider
      var lp = document.getElementById('list-panel');
      if (lp) lp.style.width = '';
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

  // Cycle: list → split → map → list
  window.gcfLayoutCycle = function() {
    var next = { list: 'split', split: 'map', map: 'list' };
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

    // Load MapLibre CSS
    var link1 = document.createElement('link');
    link1.rel = 'stylesheet';
    link1.href = 'https://unpkg.com/maplibre-gl@latest/dist/maplibre-gl.css';
    document.head.appendChild(link1);

    // Load mapbox-gl-draw CSS (API-compatible with MapLibre)
    var link2 = document.createElement('link');
    link2.rel = 'stylesheet';
    link2.href = 'https://unpkg.com/@mapbox/mapbox-gl-draw@latest/dist/mapbox-gl-draw.css';
    document.head.appendChild(link2);

    // Load MapLibre JS → mapbox-gl-draw JS → app scripts (chained)
    var s = document.createElement('script');
    s.src = 'https://unpkg.com/maplibre-gl@latest/dist/maplibre-gl.js';
    s.onload = function() {
      var s2 = document.createElement('script');
      s2.src = 'https://unpkg.com/@mapbox/mapbox-gl-draw@latest/dist/mapbox-gl-draw.js';
      s2.onload = function() {
        var s3 = document.createElement('script');
        s3.src = '/static/js/cache-map.js';
        s3.onload = function() {
          var s3b = document.createElement('script');
          s3b.src = '/static/js/map-icons.js';
          s3b.onload = function() {
            var s4 = document.createElement('script');
            s4.src = '/static/js/map-draw.js';
            s4.onload = function() {
              var s5 = document.createElement('script');
              s5.src = '/static/js/map-search.js';
              s5.onload = function() {
                var s6 = document.createElement('script');
                s6.src = '/static/js/map-layers.js';
                s6.onload = function() {
                  var s7 = document.createElement('script');
                  s7.src = '/static/js/map-context-menu.js';
                  s7.onload = function() {
                    var s8 = document.createElement('script');
                    s8.src = '/static/js/map-fetch.js';
                    s8.onload = function() {
                      _mapScriptsLoaded = true;
                      _mapScriptsLoading = false;
                      if (typeof gcfMapInit === 'function') gcfMapInit();
                      // gcfMapInit() creates gcfMap synchronously; addControl works before map load
                      if (typeof gcfDrawInit === 'function') gcfDrawInit();
                      if (typeof gcfSearchInit === 'function') gcfSearchInit();
                      if (typeof gcfContextMenuInit === 'function') gcfContextMenuInit();
                    };
                    document.head.appendChild(s8);
                  };
                  document.head.appendChild(s7);
                };
                document.head.appendChild(s6);
              };
              document.head.appendChild(s5);
            };
            document.head.appendChild(s4);
          };
          document.head.appendChild(s3b);
        };
        document.head.appendChild(s3);
      };
      document.head.appendChild(s2);
    };
    document.head.appendChild(s);
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

  function _setupDivider() {
    var divider = document.getElementById('map-divider');
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
      if (typeof gcfMap !== 'undefined' && gcfMap) gcfMap.resize();
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

  // ── List ↔ Map sync ────────────────────────────────────────────────────────

  function _setupListSync() {
    // Click a list row → pan map to that cache
    document.addEventListener('click', function(e) {
      if (_currentLayout !== 'split') return;
      var tr = e.target.closest('tr[data-code]');
      if (!tr) return;
      var code = tr.dataset.code;
      if (code && typeof gcfMapPanTo === 'function') {
        gcfMapPanTo(code);
      }
    });

    // Also handle HTMX partial reloads — re-attach after table swap
    document.body.addEventListener('htmx:afterSwap', function(evt) {
      if (evt.detail.target.id === 'cache-table-container') {
        // Refresh map markers when list filters change
        if (typeof gcfMapRefresh === 'function' && _currentLayout !== 'list') {
          gcfMapRefresh();
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
