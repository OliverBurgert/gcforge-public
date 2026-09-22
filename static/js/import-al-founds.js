// Adventure Lab founds import page (templates/geocaches/import/import_al_founds.html):
// tag-suggestion chips, the htmx:afterSwap handling for the preview partial
// (templates/geocaches/partials/_al_founds_preview.html, htmx-swapped into
// #preview-area — its own former inline script, which just synced the map
// markers after each swap, is folded into this delegated listener instead of
// an external <script src> inside the swapped fragment) and after an import
// completes, and the fetch-based "Import Selected" submit. Endpoint URLs and
// the AL icon path come from the #al-founds-config data-* element.

var _alFoundsCfg = document.getElementById('al-founds-config');
function _alFoundsUrl(name) { return _alFoundsCfg ? _alFoundsCfg.dataset[name] : ''; }

function gcfAlFoundsSubmitImport() {
  var guids = Array.from(document.querySelectorAll('.al-item-check:checked'))
    .map(function(cb) { return cb.value; });
  if (!guids.length) { alert('No Adventures selected.'); return; }

  // Remember which guids were submitted so we can drop them on success
  window._gcfAlPendingImport = guids;

  var tags = document.getElementById('al_tags').value;
  var foundAccuracy = document.getElementById('al_found_accuracy').value;
  var csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;

  var params = new URLSearchParams();
  params.append('csrfmiddlewaretoken', csrf);
  params.append('tags', tags);
  params.append('found_accuracy', foundAccuracy);
  guids.forEach(function(g) { params.append('guids', g); });

  var statusArea = document.getElementById('import-status-area');
  statusArea.innerHTML = '<div class="d-flex align-items-center gap-2 mt-1"><div class="spinner-border spinner-border-sm text-success" role="status"></div><small class="text-success">Starting import…</small></div>';

  fetch(_alFoundsUrl('importUrl'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: params.toString(),
  })
  .then(function(r) { return r.text(); })
  .then(function(html) { statusArea.innerHTML = html; htmx.process(statusArea); })
  .catch(function(e) { statusArea.innerHTML = '<div class="alert alert-danger py-1 small mt-1">Request failed: ' + e + '</div>'; });
}

function gcfAlFoundsAfterSwap(evt) {
  // Preview partial loaded → reveal import controls, refresh count, sync the
  // map markers (formerly _al_founds_preview.html's own inline script).
  if (evt.detail.target && evt.detail.target.id === 'preview-area') {
    var hasItems = document.querySelectorAll('.al-item-check').length > 0;
    document.getElementById('import-controls').style.display = hasItems ? '' : 'none';
    if (typeof gcfAlFoundsUpdateCount === 'function') gcfAlFoundsUpdateCount();
    if (typeof gcfAlFoundsSyncMarkers === 'function') {
      if (window._gcfAlMap && window._gcfAlMap.loaded()) {
        gcfAlFoundsSyncMarkers();
      } else if (window._gcfAlMap) {
        window._gcfAlMap.once('load', gcfAlFoundsSyncMarkers);
      }
    }
    return;
  }
  // Import status updated → if success alert is present, drop the imported
  // items from the list & map (no re-fetch). On failure, just clear pending.
  if (window._gcfAlPendingImport) {
    var area = document.getElementById('import-status-area');
    if (!area) return;
    if (area.querySelector('.alert-success')) {
      if (typeof gcfAlFoundsMoveImportedToConfirmed === 'function') {
        gcfAlFoundsMoveImportedToConfirmed(window._gcfAlPendingImport);
      }
      window._gcfAlPendingImport = null;
    } else if (area.querySelector('.alert-danger')) {
      window._gcfAlPendingImport = null;
    }
  }
}

(function() {
  // Tag suggestions
  fetch(_alFoundsUrl('tagsUrl'))
    .then(function(r) { return r.json(); })
    .then(function(names) {
      var box = document.getElementById('al-tag-suggestions');
      var input = document.getElementById('al_tags');
      if (!box || !names.length) return;
      names.forEach(function(name) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm btn-outline-secondary';
        btn.textContent = name;
        btn.addEventListener('click', function() {
          var cur = input.value.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
          if (cur.indexOf(name) === -1) cur.push(name);
          input.value = cur.join(', ');
        });
        box.appendChild(btn);
      });
    });

  document.addEventListener('htmx:afterSwap', gcfAlFoundsAfterSwap);

  gcfLoadMapLibre({
    scripts: [_alFoundsUrl('mapStylesUrl'), _alFoundsUrl('mapScriptUrl')],
    onReady: function() {
      if (typeof gcfAlFoundsMapInit === 'function') gcfAlFoundsMapInit();
    }
  });
})();
