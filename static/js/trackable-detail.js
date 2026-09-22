// Trackable detail page (templates/geocaches/trackable_detail.html):
// refresh button, action modal (move to inventory/collection, mark missing),
// coordinates editor, and wiring the movement map's row hover/click
// highlighting. Endpoint + static-script URLs come from the #tb-detail-config
// data attributes.

var _tbdCfg = document.getElementById('tb-detail-config');
function _tbdUrl(name) { return _tbdCfg ? _tbdCfg.dataset[name] : ''; }

var _tbdPendingAction = '';

// ── Refresh ──────────────────────────────────────────────────────────────────
function gcfTbDetailRefresh(e) {
  e.preventDefault();
  var btn = document.getElementById('refresh-btn');
  var status = document.getElementById('refresh-status');
  btn.disabled = true;
  btn.textContent = gettext("Refreshing…");
  status.className = 'text-muted small';
  status.textContent = '';
  fetch(_tbdUrl('refreshUrl'), {
    method: 'POST',
    headers: {'X-CSRFToken': _gcfTbCsrf(), 'HX-Request': '1'},
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    btn.disabled = false;
    btn.textContent = '↻ ' + gettext("Refresh");
    if (d.ok) {
      var msg = gettext("Refreshed");
      if (d.new_logs) msg += ' — ' + d.new_logs + ' ' + gettext("new log(s)");
      status.textContent = msg;
      setTimeout(function() { window.location.reload(); }, 800);
    } else {
      status.className = 'text-danger small';
      status.textContent = d.error || gettext("Refresh failed");
    }
  })
  .catch(function(err) {
    btn.disabled = false;
    btn.textContent = '↻ ' + gettext("Refresh");
    status.className = 'text-danger small';
    status.textContent = gettext("Network error:") + ' ' + err;
  });
}

// ── Action modal ─────────────────────────────────────────────────────────────
function gcfTbDetailActionModalOpen() {
  _tbdPendingAction = this.dataset.action;
  document.getElementById('actionModalLabel').textContent = this.dataset.label;
  document.getElementById('action-text').value = '';
  document.getElementById('action-modal-error').classList.add('d-none');
  document.getElementById('action-modal-hint').classList.add('d-none');
}

function gcfTbDetailActionSubmit() {
  if (!_tbdPendingAction) return;
  var btn = this;
  btn.disabled = true;
  var body = new FormData();
  body.append('action', _tbdPendingAction);
  body.append('text', document.getElementById('action-text').value);
  body.append('csrfmiddlewaretoken', _gcfTbCsrf());
  fetch(_tbdUrl('actionUrl'), { method: 'POST', body: body })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    btn.disabled = false;
    if (d.ok) {
      var modal = bootstrap.Modal.getInstance(document.getElementById('actionModal'));
      if (modal) modal.hide();
      document.getElementById('action-result').innerHTML =
        '<span class="text-success">' + gettext("Done. Reloading…") + '</span>';
      setTimeout(function() { window.location.reload(); }, 800);
    } else {
      var errEl = document.getElementById('action-modal-error');
      errEl.textContent = d.error || gettext("Action failed");
      errEl.classList.remove('d-none');
      if (d.hint) {
        var hintEl = document.getElementById('action-modal-hint');
        hintEl.innerHTML = gettext("To fix:") + ' <a href="' + d.hint + '" target="_blank" rel="noopener">' +
          gettext("open the TB edit page") + ' &#8599;</a> ' + gettext('and enable "Is Collectible".');
        hintEl.classList.remove('d-none');
      }
    }
  })
  .catch(function(err) {
    btn.disabled = false;
    document.getElementById('action-modal-error').textContent = gettext("Network error:") + ' ' + err;
    document.getElementById('action-modal-error').classList.remove('d-none');
  });
}

// ── Coords editor ────────────────────────────────────────────────────────────
function gcfTbDetailCoordsSubmit(e) {
  e.preventDefault();
  var status = document.getElementById('coords-status');
  var body = new FormData(this);
  fetch(_tbdUrl('coordsUrl'), { method: 'POST', body: body })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.ok) {
      status.textContent = gettext("Saved.");
      status.className = 'text-success small mt-1';
      setTimeout(function() { window.location.reload(); }, 600);
    } else {
      status.textContent = d.error || gettext("Save failed");
      status.className = 'text-danger small mt-1';
    }
  });
}

function gcfTbDetailCoordsClear() {
  document.getElementById('coords-lat').value = '';
  document.getElementById('coords-lon').value = '';
  document.getElementById('coords-form').dispatchEvent(new Event('submit'));
}

// ── Movement map row wiring ──────────────────────────────────────────────────
function gcfTbDetailMapReady() {
  if (typeof gcfTrackableMapInit === 'function') gcfTrackableMapInit();
  document.querySelectorAll('tr.tb-log-row').forEach(function(tr) {
    var id = parseInt(tr.dataset.logId, 10);
    tr.addEventListener('mouseenter', function() { if (typeof gcfTbHighlightLog === 'function') gcfTbHighlightLog(id); });
    tr.addEventListener('mouseleave', function() { if (typeof gcfTbHighlightLog === 'function') gcfTbHighlightLog(null); });
    tr.addEventListener('click', function(e) {
      if (e.target.tagName === 'A') return;
      if (typeof gcfTbFocusLog === 'function') gcfTbFocusLog(id);
    });
  });
}

(function() {
  var refreshForm = document.getElementById('refresh-form');
  if (refreshForm) refreshForm.addEventListener('submit', gcfTbDetailRefresh);

  document.querySelectorAll('[data-bs-target="#actionModal"]').forEach(function(btn) {
    btn.addEventListener('click', gcfTbDetailActionModalOpen);
  });

  var submitBtn = document.getElementById('action-submit-btn');
  if (submitBtn) submitBtn.addEventListener('click', gcfTbDetailActionSubmit);

  var coordsForm = document.getElementById('coords-form');
  if (coordsForm) coordsForm.addEventListener('submit', gcfTbDetailCoordsSubmit);

  var clearBtn = document.getElementById('coords-clear-btn');
  if (clearBtn) clearBtn.addEventListener('click', gcfTbDetailCoordsClear);

  if (typeof gcfLoadMapLibre === 'function') {
    gcfLoadMapLibre({
      scripts: [_tbdUrl('mapStylesUrl'), _tbdUrl('mapScriptUrl')],
      onReady: gcfTbDetailMapReady
    });
  }
})();
