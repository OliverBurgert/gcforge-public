// Image gallery page (templates/geocaches/tools/image_gallery_page.html):
// polls the build task's status until it finishes (then reloads), and once
// the gallery itself is rendered, draws a small static MapLibre map into
// every [data-gallery-map] placeholder. Each half is guarded by the
// presence of its own DOM elements, so this file works unchanged regardless
// of which of the two mutually-exclusive template branches rendered.

function gcfGalleryPollTask() {
  var pollEl = document.getElementById('gallery-poll');
  if (!pollEl) return;
  var statusUrl = pollEl.dataset.statusUrl;

  function poll() {
    fetch(statusUrl)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.state === 'completed' || data.state === 'failed' || data.state === 'cancelled') {
          location.reload();
        } else {
          setTimeout(poll, 2000);
        }
      })
      .catch(function () { setTimeout(poll, 3000); });
  }
  setTimeout(poll, 1500);
}

function gcfGalleryInitMaps() {
  var mapEls = document.querySelectorAll('[data-gallery-map]');
  if (!mapEls.length || typeof gcfLoadMapLibre !== 'function') return;
  gcfLoadMapLibre({
    onReady: function() {
      mapEls.forEach(function(el) {
        var lat = parseFloat(el.dataset.lat);
        var lon = parseFloat(el.dataset.lon);
        var zoom = parseFloat(el.dataset.zoom);
        if (!isFinite(zoom)) zoom = 13;
        if (!isFinite(lat) || !isFinite(lon)) return;
        var map = new maplibregl.Map({
          container: el,
          style: {
            version: 8,
            sources: { osm: {
              type: 'raster',
              tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
              tileSize: 256,
              attribution: '© OpenStreetMap contributors'
            }},
            layers: [{ id: 'osm', type: 'raster', source: 'osm' }]
          },
          center: [lon, lat],
          zoom: zoom,
          interactive: true
        });
        new maplibregl.Marker().setLngLat([lon, lat]).addTo(map);
      });
    }
  });
}

gcfGalleryPollTask();
gcfGalleryInitMaps();
