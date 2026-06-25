// ── GCForge map tile styles ─────────────────────────────────────────────────
//
// Shared MapLibre style definitions used by every map in the app
// (main list-view map, cache detail map, locations / reference-point map).
//
// The three styles map to the same three layer-switcher buttons everywhere:
//   street  — OpenFreeMap Liberty (vector)
//   outdoor — OpenTopoMap (raster)
//   aerial  — Esri World Imagery (raster)

// Redirect MapLibre glyph requests to our self-hosted fonts endpoint.
// The Liberty style's CDN (tiles.openfreemap.org/fonts) is unreliable;
// we serve the same PBF files from Django at /fonts/{fontstack}/{range}.pbf.
function gcfMapTransformRequest(url, resourceType) {
  if (resourceType === 'Glyphs') {
    var m = url.match(/\/fonts\/([^/]+)\/(\d+-\d+)\.pbf/);
    if (m) {
      return { url: window.location.origin + '/fonts/' + m[1] + '/' + m[2] + '.pbf' };
    }
  }
}

// Suppress "Image X could not be loaded" console warnings for POI sprite icons
// that are referenced by the Liberty vector style but not present in its sprite.
// A transparent 1×1 placeholder is added so MapLibre stops complaining.
function gcfSuppressMissingImages(map) {
  map.on('styleimagemissing', function(e) {
    // Skip our own generated image IDs — those are added by icon-generation code
    // and must not get a placeholder (which would block the real addImage call).
    var id = e.id;
    if (id.indexOf('i-') === 0 || id.indexOf('wp-') === 0 ||
        id.indexOf('gcf-') === 0 || id.indexOf('corrected-') === 0 ||
        id.indexOf('detail-') === 0) { return; }
    if (!map.hasImage(id)) {
      map.addImage(id, { width: 1, height: 1, data: new Uint8ClampedArray(4) });
    }
  });
}

var GCF_STYLES = {
  street: 'https://tiles.openfreemap.org/styles/liberty',
  outdoor: {
    version: 8,
    name: 'OpenTopoMap',
    sources: {
      'opentopomap': {
        type: 'raster',
        tiles: [
          'https://a.tile.opentopomap.org/{z}/{x}/{y}.png',
          'https://b.tile.opentopomap.org/{z}/{x}/{y}.png',
          'https://c.tile.opentopomap.org/{z}/{x}/{y}.png'
        ],
        tileSize: 256,
        maxzoom: 17,
        attribution: '&copy; <a href="https://opentopomap.org">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>), &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>'
      }
    },
    layers: [{
      id: 'opentopomap-layer',
      type: 'raster',
      source: 'opentopomap',
      minzoom: 0,
      maxzoom: 17
    }]
  },
  aerial: {
    version: 8,
    name: 'Esri World Imagery',
    sources: {
      'esri-imagery': {
        type: 'raster',
        tiles: [
          'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
        ],
        tileSize: 256,
        attribution: '&copy; Esri, Maxar, Earthstar Geographics',
        maxzoom: 19
      }
    },
    layers: [{
      id: 'esri-imagery-layer',
      type: 'raster',
      source: 'esri-imagery',
      minzoom: 0,
      maxzoom: 19
    }]
  }
};
