// Trackable map (index) page (templates/geocaches/trackable_map.html):
// debounced free-text search auto-submit, "Resolve" for pins without a
// known location, and the MapLibre init for the pins map. Endpoint URLs and
// the two dynamically-loaded map script paths come from the #tb-map-config
// data-* element.

var _tbMapCfg = document.getElementById('tb-map-config');
function _tbMapUrl(name) { return _tbMapCfg ? _tbMapCfg.dataset[name] : ''; }

function gcfTbMapResolveLocations() {
  var btn    = document.getElementById('resolve-loc-btn');
  var status = document.getElementById('resolve-loc-status');
  if (btn) btn.disabled = true;
  if (status) status.textContent = gettext("Resolving…");
  var csrf = _gcfTbCsrf();
  var body = new URLSearchParams(window.location.search).toString();
  fetch(_tbMapUrl('resolveUrl'), {
    method: 'POST',
    headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/x-www-form-urlencoded'},
    body: body,
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (btn) btn.disabled = false;
    if (d.ok) {
      if (status) status.textContent = gettext("Resolved") + ' ' + d.resolved + ' ' + gettext("of") + ' ' + d.total_missing + '.';
      if (d.resolved > 0) setTimeout(function() { window.location.reload(); }, 800);
    } else {
      if (status) status.textContent = gettext("Error:") + ' ' + (d.error || gettext("unknown"));
    }
  })
  .catch(function(e) {
    if (btn) btn.disabled = false;
    if (status) status.textContent = gettext("Failed:") + ' ' + e;
  });
}

(function() {
  var inp = document.getElementById('tb-map-q');
  if (inp) {
    var timer;
    inp.addEventListener('input', function() {
      clearTimeout(timer);
      timer = setTimeout(function() { document.getElementById('tb-map-filter-form').submit(); }, 400);
    });
  }

  gcfLoadMapLibre({
    scripts: [_tbMapUrl('mapStylesUrl'), _tbMapUrl('mapScriptUrl')],
    onReady: function() { if (typeof gcfTbIndexMapInit === 'function') gcfTbIndexMapInit(); }
  });
})();
