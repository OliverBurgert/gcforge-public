// ── GCForge MapLibre loader ─────────────────────────────────────────────────
//
// Lazy-loads MapLibre GL JS (and optionally mapbox-gl-draw), then chain-loads
// a list of app scripts, then calls onReady.
//
// Shared by the list-view map (map-layout.js), the cache detail map, and
// the locations / reference-point map in settings.
//
// Usage:
//   gcfLoadMapLibre({
//     draw: true,
//     scripts: [
//       '/static/js/map-styles.js',
//       '/static/js/map-icons.js',
//       '/static/js/cache-map.js',
//       '/static/js/map-draw.js',
//       ...
//     ],
//     onReady: function() { ... }
//   });
//
// Idempotent: second and subsequent calls skip the CDN fetch and only load
// any scripts not yet requested before invoking onReady.

(function() {
  var PMTILES_JS   = '/static/vendor/js/pmtiles.js';
  var MAPLIBRE_CSS = '/static/vendor/css/maplibre-gl.css';
  var MAPLIBRE_JS  = '/static/vendor/js/maplibre-gl.js';
  var DRAW_CSS     = '/static/vendor/css/mapbox-gl-draw.css';
  var DRAW_JS      = '/static/vendor/js/mapbox-gl-draw.js';

  var _state = {
    maplibreLoaded: false,
    drawLoaded: false,
    scripts: {}           // src → 'loading' | 'loaded'
  };
  var _maplibreWaiters = [];
  var _drawWaiters = [];

  function _loadCss(href) {
    var existing = document.querySelector('link[href="' + href + '"]');
    if (existing) return;
    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    document.head.appendChild(link);
  }

  function _loadScript(src, onLoad) {
    var status = _state.scripts[src];
    if (status === 'loaded') { onLoad(); return; }
    if (status === 'loading') {
      // Another caller is loading it — poll until done
      var tick = setInterval(function() {
        if (_state.scripts[src] === 'loaded') {
          clearInterval(tick);
          onLoad();
        }
      }, 20);
      return;
    }
    _state.scripts[src] = 'loading';
    var s = document.createElement('script');
    s.src = src;
    s.onload = function() {
      _state.scripts[src] = 'loaded';
      onLoad();
    };
    s.onerror = function() {
      console.error('GCForge: failed to load script', src);
      _state.scripts[src] = 'loaded';  // don't hang the chain
      onLoad();
    };
    document.head.appendChild(s);
  }

  function _chain(scripts, idx, onDone) {
    if (idx >= scripts.length) { onDone(); return; }
    _loadScript(scripts[idx], function() {
      _chain(scripts, idx + 1, onDone);
    });
  }

  function _ensureMapLibre(onReady) {
    if (_state.maplibreLoaded) { onReady(); return; }
    _maplibreWaiters.push(onReady);
    if (_maplibreWaiters.length > 1) return;  // another call is already loading

    _loadScript(PMTILES_JS, function() {
      _loadCss(MAPLIBRE_CSS);
      _loadScript(MAPLIBRE_JS, function() {
        // Register PMTiles protocol once
        if (window.pmtiles && window.maplibregl && !window._gcfPmtilesRegistered) {
          var protocol = new pmtiles.Protocol();
          maplibregl.addProtocol('pmtiles', protocol.tile.bind(protocol));
          window._gcfPmtilesRegistered = true;
        }
        _state.maplibreLoaded = true;
        var waiters = _maplibreWaiters;
        _maplibreWaiters = [];
        for (var i = 0; i < waiters.length; i++) waiters[i]();
      });
    });
  }

  function _ensureDraw(onReady) {
    if (_state.drawLoaded) { onReady(); return; }
    _drawWaiters.push(onReady);
    if (_drawWaiters.length > 1) return;

    _loadCss(DRAW_CSS);
    _loadScript(DRAW_JS, function() {
      _state.drawLoaded = true;
      var waiters = _drawWaiters;
      _drawWaiters = [];
      for (var i = 0; i < waiters.length; i++) waiters[i]();
    });
  }

  window.gcfLoadMapLibre = function(opts) {
    opts = opts || {};
    var scripts = opts.scripts || [];
    var onReady = opts.onReady || function() {};

    _ensureMapLibre(function() {
      function afterDraw() {
        _chain(scripts, 0, onReady);
      }
      if (opts.draw) _ensureDraw(afterDraw);
      else afterDraw();
    });
  };
})();
