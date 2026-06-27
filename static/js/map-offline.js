// ── GCForge Offline Maps — PMTiles layer management ───────────────────────────
//
// Loaded as part of the main map script chain (after map-styles.js).
//
// Public API
//   gcfOfflineLoadAreas(map, onDone) — fetch ready areas list, update UI button
//   gcfBuildOfflineStyle()           — return a complete MapLibre style object
//                                      for all ready offline areas
//
// Phase 6: offline is a style mode, not an overlay.  When the user selects
// "Offline" in the layer switcher, gcfMapSetStyle calls gcfBuildOfflineStyle()
// and passes the result to map.setStyle().  The existing styledata handler in
// cache-map.js then re-adds GCForge cache markers on top as normal.

var _gcfOfflineAreas = [];

// ── Area list fetch ──────────────────────────────────────────────────────────

function gcfOfflineLoadAreas(map, onDone) {
  fetch('/offline-maps/areas.json')
    .then(function(r) { return r.json(); })
    .then(function(areas) {
      _gcfOfflineAreas = areas || [];
      _gcfOfflineUpdateButton();
      // If offline was the last-used style, apply it now that we have the areas
      if (_gcfOfflineAreas.length &&
          localStorage.getItem('gcforge_map_style') === 'offline') {
        if (typeof window.setLayer === 'function') {
          window.setLayer('offline', null);
        } else if (typeof gcfMapSetStyle === 'function') {
          gcfMapSetStyle('offline');
        }
      }
      if (onDone) onDone();
    })
    .catch(function(e) {
      console.warn('[offline-map] could not load offline areas', e);
      if (onDone) onDone();
    });
}

function _gcfOfflineUpdateButton() {
  var show = _gcfOfflineAreas.length > 0;
  // List-view radio label
  var label = document.getElementById('map-offline-label');
  if (label) label.style.display = show ? '' : 'none';
  // Detail-view button
  var btn = document.getElementById('detail-offline-btn');
  if (btn) btn.style.display = show ? '' : 'none';
}

// ── Style builder ────────────────────────────────────────────────────────────

function gcfBuildOfflineStyle() {
  var sources = {};
  var layers = [];

  _gcfOfflineAreas.forEach(function(area) {
    var srcId = 'gcf-offline-' + area.id;
    sources[srcId] = {
      type: 'vector',
      url: 'pmtiles://' + window.location.origin
           + '/offline-maps/' + area.id + '/tiles.pmtiles',
      attribution: 'Offline (Protomaps)'
    };
    _gcfOfflineLayersForSource(srcId).forEach(function(l) { layers.push(l); });
  });

  return {
    version: 8,
    glyphs: window.location.origin + '/fonts/{fontstack}/{range}.pbf',
    sources: sources,
    layers: layers
  };
}

// ── Layer definitions (Protomaps basemap schema) ─────────────────────────────
//
// Protomaps build.protomaps.com source layers and key properties:
//   earth                       — land background polygon
//   natural   kind              — landcover: forest, wood, park, grass, scrub …
//   land      kind              — landuse: residential, commercial, industrial …
//   water     (poly + line)     — water bodies and waterways
//   roads     kind, is_tunnel   — highway, major_road, minor_road, path, rail …
//   transit                     — transit lines
//   buildings                   — building footprints
//   places    kind, population_rank — country, region, locality, neighbourhood

function _gcfOfflineLayersForSource(srcId) {
  return [

    // ── Background ───────────────────────────────────────────────────────────
    { id: srcId + '-earth',
      type: 'fill', source: srcId, 'source-layer': 'earth',
      paint: { 'fill-color': '#e2dfda' } },

    // ── Landcover (natural) ──────────────────────────────────────────────────
    { id: srcId + '-natural',
      type: 'fill', source: srcId, 'source-layer': 'natural',
      paint: {
        'fill-color': [
          'match', ['get', 'kind'],
          ['forest', 'wood', 'national_park', 'protected_area'], '#c8dfc8',
          ['park', 'recreation_ground', 'garden', 'village_green'], '#d4e8cc',
          ['grass', 'meadow', 'farmland'], '#ddeedd',
          ['scrub', 'heath'], '#d4e0c0',
          ['beach', 'sand'], '#f0e4c8',
          '#d0dcc8'
        ],
        'fill-opacity': 0.9
      }
    },

    // ── Landuse (land) ───────────────────────────────────────────────────────
    { id: srcId + '-land',
      type: 'fill', source: srcId, 'source-layer': 'land',
      paint: {
        'fill-color': [
          'match', ['get', 'kind'],
          ['residential', 'neighbourhood'], '#ede8e4',
          ['commercial', 'retail'],          '#f0ecea',
          ['industrial', 'railway'],         '#e4dcd0',
          '#e8e4e0'
        ],
        'fill-opacity': 0.6
      }
    },

    // ── Water ────────────────────────────────────────────────────────────────
    { id: srcId + '-water',
      type: 'fill', source: srcId, 'source-layer': 'water',
      paint: { 'fill-color': '#7ec8e3' } },

    { id: srcId + '-water-line',
      type: 'line', source: srcId, 'source-layer': 'water',
      filter: ['==', ['geometry-type'], 'LineString'],
      paint: {
        'line-color': '#7ec8e3',
        'line-width': ['interpolate', ['exponential', 1.4], ['zoom'],
          8, 0.5,  12, 1.5,  16, 3]
      }
    },

    // ── Roads — tunnels (dashed, drawn below surface roads) ──────────────────
    { id: srcId + '-roads-tunnels',
      type: 'line', source: srcId, 'source-layer': 'roads',
      filter: ['==', ['get', 'is_tunnel'], true],
      layout: { 'line-join': 'round', 'line-cap': 'butt' },
      paint: {
        'line-color': '#c8c0b8',
        'line-dasharray': [3, 2],
        'line-width': ['interpolate', ['exponential', 1.5], ['zoom'],
          8, 0.5,  12, 1.5,  16, 3]
      }
    },

    // ── Roads — casing (drawn under fill, gives outline) ─────────────────────
    { id: srcId + '-roads-casing',
      type: 'line', source: srcId, 'source-layer': 'roads',
      filter: ['all',
        ['!=', ['get', 'is_tunnel'], true],
        ['in', ['get', 'kind'], ['literal', ['highway', 'major_road']]]
      ],
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: {
        'line-color': '#d4c888',
        'line-width': ['interpolate', ['exponential', 1.5], ['zoom'],
          6, ['match', ['get', 'kind'], 'highway', 3, 2],
          12, ['match', ['get', 'kind'], 'highway', 8, 5],
          16, ['match', ['get', 'kind'], 'highway', 14, 9]
        ]
      }
    },

    // ── Roads — surface fill ──────────────────────────────────────────────────
    { id: srcId + '-roads-fill',
      type: 'line', source: srcId, 'source-layer': 'roads',
      filter: ['!=', ['get', 'is_tunnel'], true],
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: {
        'line-color': [
          'match', ['get', 'kind'],
          'highway',    '#f4d35e',
          'major_road', '#ffffff',
          'minor_road', '#ffffff',
          'path',       '#d0c8b8',
          'rail',       '#c0b8c0',
          '#e8e4e0'
        ],
        'line-width': ['interpolate', ['exponential', 1.5], ['zoom'],
          6, ['match', ['get', 'kind'],
              'highway', 1.5, 'major_road', 0.8, 0.5],
          12, ['match', ['get', 'kind'],
               'highway', 5, 'major_road', 3, 'minor_road', 1.5, 1],
          16, ['match', ['get', 'kind'],
               'highway', 10, 'major_road', 7, 'minor_road', 3.5, 1.5]
        ]
      }
    },

    // ── Transit lines ─────────────────────────────────────────────────────────
    { id: srcId + '-transit',
      type: 'line', source: srcId, 'source-layer': 'transit',
      minzoom: 11,
      layout: { 'line-join': 'round' },
      paint: {
        'line-color': '#a090a0',
        'line-opacity': 0.6,
        'line-dasharray': [2, 2],
        'line-width': 1.5
      }
    },

    // ── Buildings ─────────────────────────────────────────────────────────────
    { id: srcId + '-buildings',
      type: 'fill', source: srcId, 'source-layer': 'buildings',
      minzoom: 14,
      paint: {
        'fill-color': '#d4ccc4',
        'fill-opacity': 0.7,
        'fill-outline-color': '#b8b0a8'
      }
    },

    // ── Road labels ───────────────────────────────────────────────────────────
    { id: srcId + '-road-labels',
      type: 'symbol', source: srcId, 'source-layer': 'roads',
      minzoom: 13,
      filter: ['in', ['get', 'kind'], ['literal', ['highway', 'major_road', 'minor_road']]],
      layout: {
        'text-field': ['coalesce', ['get', 'name:en'], ['get', 'name']],
        'text-font': ['Open Sans Regular'],
        'symbol-placement': 'line',
        'text-size': 11,
        'text-max-angle': 30
      },
      paint: {
        'text-color': '#555',
        'text-halo-color': '#fff',
        'text-halo-width': 1.5
      }
    },

    // ── Place labels ──────────────────────────────────────────────────────────
    { id: srcId + '-places',
      type: 'symbol', source: srcId, 'source-layer': 'places',
      layout: {
        'text-field': ['coalesce', ['get', 'name:en'], ['get', 'name']],
        'text-font': ['Open Sans Regular'],
        'text-size': ['interpolate', ['linear'], ['zoom'],
          2,  ['match', ['get', 'kind'], 'country', 10, 0],
          4,  ['match', ['get', 'kind'], 'country', 13, 'region', 10, 8],
          7,  ['match', ['get', 'kind'], 'country', 15, 'region', 12, 'locality', 11, 9],
          12, ['match', ['get', 'kind'],
               'country', 16, 'region', 13, 'locality', 14, 'neighbourhood', 11, 10]
        ],
        'text-anchor': 'center',
        'text-max-width': 8,
        'symbol-sort-key': ['-', ['coalesce', ['get', 'population_rank'], 0]]
      },
      paint: {
        'text-color': [
          'match', ['get', 'kind'],
          'country', '#333',
          'region', '#555',
          '#666'
        ],
        'text-halo-color': '#fff',
        'text-halo-width': 1.5
      }
    }

  ];
}
