// Trackable action sections inside the log compose dialog
// (templates/geocaches/partials/_log_tb_sections.html): per-cache
// Discover/Retrieve, my-inventory Drop/Visit, Discover-by-code, and
// Grab-by-code rows, plus the tb_* hidden-field serialisation the log
// forms (_log_form_fields.html) submit. Endpoint URLs come from the
// #tb-sections-config data attribute.

var _tbSecCfg = document.getElementById('tb-sections-config');
function _tbSecUrl(name) { return _tbSecCfg ? _tbSecCfg.dataset[name] : ''; }

function gcfTbToggle(radio) {
  var row = radio.closest('.tb-row');
  if (!row) return;
  var mini = row.querySelector('.tb-mini');
  if (radio.value === '') { mini.style.display = 'none'; }
  else { mini.style.display = ''; }
  _gcfTbSummaryGlobal();
}

function gcfTbInvToggle(cb) {
  var row = cb.closest('.tb-row');
  if (!row) return;
  var dropCb  = row.querySelector('input[data-tb-drop]');
  var visitCb = row.querySelector('input[data-tb-visit]');
  var mini = row.querySelector('.tb-mini');
  var anyChecked = (dropCb && dropCb.checked) || (visitCb && visitCb.checked);
  mini.style.display = anyChecked ? '' : 'none';
  _gcfTbSummaryGlobal();
}

function _gcfTbSummaryGlobal() {
  var rows = document.querySelectorAll('#tb-sections .tb-row, #tb-sections .tb-extra-row, #tb-sections .tb-grab-row');
  var picked = 0;
  rows.forEach(function (r) {
    var radio = r.querySelector('input[type=radio]:checked');
    if (radio && radio.value !== '') picked++;
    var drop  = r.querySelector('input[type=checkbox][data-tb-drop]:checked');
    var visit = r.querySelector('input[type=checkbox][data-tb-visit]:checked');
    if (drop)  picked++;
    if (visit) picked++;
    if (r.classList.contains('tb-extra-row')) picked++;       // implicit discover
    if (r.classList.contains('tb-grab-row'))  picked++;       // implicit grab
  });
  var s = document.getElementById('tb-summary');
  if (s) s.textContent = picked ? '(' + picked + ' selected)' : '';
}

function _gcfTbVerify(btn) {
  var row = btn.closest('.tb-row, .tb-extra-row, .tb-grab-row');
  if (!row) return;
  var input  = row.querySelector('[data-tb-tracking]');
  var status = row.querySelector('[data-tb-verify-status]');
  var holder = row.querySelector('[data-tb-holder]');
  var code = (input && input.value || '').trim().toUpperCase();
  if (!code) {
    if (status) { status.textContent = gettext("enter a code"); status.className = 'small text-danger'; }
    return;
  }
  btn.disabled = true;
  if (status) { status.textContent = gettext("checking…"); status.className = 'small text-muted'; }
  fetch(_tbSecUrl('urlVerify'), {
    method: 'POST',
    headers: { 'X-CSRFToken': _gcfTbCsrf(), 'Content-Type': 'application/x-www-form-urlencoded' },
    credentials: 'same-origin',
    body: 'tracking_code=' + encodeURIComponent(code),
  })
    .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
    .then(function (res) {
      if (!res.ok || !res.body.ok) {
        var msg = (res.body && res.body.error) || gettext("invalid");
        if (status) { status.textContent = msg; status.className = 'small text-danger'; }
        row.removeAttribute('data-tb-verified');
        if (holder) holder.innerHTML = '';
        return;
      }
      var name = res.body.name || '';
      var ref  = res.body.ref_code || '';
      if (status) {
        status.textContent = '✓ ' + ref + (name ? ' — ' + name : '');
        status.className = 'small text-success';
      }
      row.setAttribute('data-tb-verified', '1');
      if (ref && !row.getAttribute('data-tb-ref')) row.setAttribute('data-tb-ref', ref);
      // Render holder/cache context into the grab row.
      // A TB is either held by a user or sitting in a cache (never both).
      // We surface whichever is true so the user can reach out before grabbing.
      if (holder) {
        var h = res.body.holder;
        var gc = res.body.current_geocache_code || '';
        if (h && h.username) {
          holder.innerHTML =
            '<div class="mt-1 small">' +
              gettext("Currently held by") + ' <strong>' + _gcfEsc(h.username) + '</strong>. ' +
              '<a href="' + _gcfEsc(h.message_url || h.profile_url) + '" target="_blank" rel="noopener">' + gettext("Send message") + '</a>' +
              (h.profile_url ? ' · <a href="' + _gcfEsc(h.profile_url) + '" target="_blank" rel="noopener">' + gettext("Profile") + '</a>' : '') +
            '</div>';
        } else if (gc) {
          holder.innerHTML =
            '<div class="mt-1 small">' +
              gettext("Currently listed in cache") + ' ' +
              '<a href="https://coord.info/' + _gcfEsc(gc) + '" target="_blank" rel="noopener" class="font-monospace">' + _gcfEsc(gc) + '</a>.' +
            '</div>';
        } else {
          holder.innerHTML = '<div class="mt-1 small text-muted">' + gettext("No current holder or cache reported.") + '</div>';
        }
      }
    })
    .catch(function (exc) {
      if (status) { status.textContent = String(exc); status.className = 'small text-danger'; }
    })
    .finally(function () { btn.disabled = false; });
}

function _gcfTbInvLoad() {
  var rows = document.querySelector('[data-tb-inv-rows]');
  if (!rows || rows.getAttribute('data-loaded') === '1') return;
  rows.setAttribute('data-loaded', '1');
  var url = rows.getAttribute('data-url');
  var statusEl = rows.querySelector('[data-tb-inv-status]');
  fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
    .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
    .then(function (res) {
      if (!res.ok || !res.body.ok) {
        rows.innerHTML = '<div class="text-danger small">' + _gcfEsc((res.body && res.body.error) || gettext("failed")) + '</div>';
        return;
      }
      var items = res.body.items || [];
      var countEl = document.querySelector('[data-tb-inv-count]');
      if (countEl) countEl.textContent = '(' + items.length + ')';
      if (!items.length) {
        rows.innerHTML = '<div class="text-muted small">' + gettext("No trackables in your inventory.") + '</div>';
        return;
      }
      rows.innerHTML = '';
      items.forEach(function (t, idx) {
        var rowId = 'inv-' + idx;
        var ref   = _gcfEsc(t.reference_code || '');
        var name  = _gcfEsc(t.name || '');
        var auto  = !!t.auto_visit_enabled;
        var autoText = t.auto_visit_text || '';
        var div = document.createElement('div');
        div.className = 'tb-row border rounded p-2 mb-2';
        div.setAttribute('data-tb-row', 'inv');
        div.setAttribute('data-tb-ref', t.reference_code || '');
        div.setAttribute('data-tb-inv-tracking', t.tracking_number || '');
        div.innerHTML =
          '<div class="d-flex align-items-center gap-2 small flex-wrap">' +
            '<span class="font-monospace">' + ref + '</span>' +
            '<span class="flex-grow-1" data-tb-name>' + name + '</span>' +
            '<div class="form-check">' +
              '<input class="form-check-input" type="checkbox" data-tb-drop ' +
                     'id="tb-drop-' + rowId + '" onchange="gcfTbInvToggle(this)">' +
              '<label class="form-check-label small" for="tb-drop-' + rowId + '">' + gettext("Drop here") + '</label>' +
            '</div>' +
            '<div class="form-check">' +
              '<input class="form-check-input" type="checkbox" data-tb-visit ' +
                     'id="tb-visit-' + rowId + '"' + (auto ? ' checked' : '') +
                     ' onchange="gcfTbInvToggle(this)">' +
              '<label class="form-check-label small" for="tb-visit-' + rowId + '">' +
                gettext("Visit here") + (auto ? ' <span class="text-muted">' + gettext("(auto)") + '</span>' : '') +
              '</label>' +
            '</div>' +
          '</div>' +
          '<div class="tb-mini mt-2"' + (auto ? '' : ' style="display:none"') + '>' +
            '<textarea class="form-control form-control-sm" rows="2" ' +
                      'data-tb-text placeholder="' + gettext("Optional log text") + '"></textarea>' +
          '</div>';
        rows.appendChild(div);
        // Populate per-TB auto-visit text (escaped via textarea value)
        if (autoText) {
          var ta = div.querySelector('[data-tb-text]');
          if (ta) ta.value = autoText;
        }
      });
      _gcfTbSummaryGlobal();
    })
    .catch(function (exc) {
      rows.innerHTML = '<div class="text-danger small">' + _gcfEsc(String(exc)) + '</div>';
    });
}

function _gcfTbAddDiscover(btn) {
  var idx = document.querySelectorAll('#tb-sections .tb-extra-row').length;
  var div = document.createElement('div');
  div.className = 'tb-extra-row border rounded p-2 mb-2';
  div.setAttribute('data-tb-row', 'extra');
  div.innerHTML =
    '<div class="d-flex align-items-center gap-1 small mb-1">' +
      '<label class="mb-0">' + gettext("Tracking code") + '</label>' +
      '<input type="text" class="form-control form-control-sm font-monospace" ' +
             'style="max-width:8em" maxlength="20" data-tb-tracking autocomplete="off">' +
      '<button type="button" class="btn btn-sm btn-outline-secondary" data-tb-verify>' + gettext("Verify") + '</button>' +
      '<span class="small" data-tb-verify-status></span>' +
      '<button type="button" class="btn btn-sm btn-outline-danger py-0 px-1 ms-auto" ' +
              'onclick="this.closest(\'.tb-extra-row\').remove();window._gcfTbSummaryGlobal();">×</button>' +
    '</div>' +
    '<textarea class="form-control form-control-sm" rows="2" ' +
              'data-tb-text placeholder="' + gettext("Optional log text") + '"></textarea>';
  btn.parentNode.insertBefore(div, btn);
}

function _gcfTbAddGrab(btn) {
  var div = document.createElement('div');
  div.className = 'tb-grab-row border rounded p-2 mb-2';
  div.setAttribute('data-tb-row', 'grab');
  div.innerHTML =
    '<div class="d-flex align-items-center gap-1 small mb-1">' +
      '<label class="mb-0">' + gettext("Tracking code") + '</label>' +
      '<input type="text" class="form-control form-control-sm font-monospace" ' +
             'style="max-width:8em" maxlength="20" data-tb-tracking autocomplete="off">' +
      '<button type="button" class="btn btn-sm btn-outline-secondary" data-tb-verify>' + gettext("Verify") + '</button>' +
      '<span class="small" data-tb-verify-status></span>' +
      '<button type="button" class="btn btn-sm btn-outline-danger py-0 px-1 ms-auto" ' +
              'onclick="this.closest(\'.tb-grab-row\').remove();window._gcfTbSummaryGlobal();">×</button>' +
    '</div>' +
    '<div data-tb-holder></div>' +
    '<div class="form-check mt-1">' +
      '<input class="form-check-input" type="checkbox" data-tb-grab-chain checked>' +
      '<label class="form-check-label small">' + gettext("Also drop & retrieve here") + ' ' +
        '<span class="text-muted">' + gettext("(records this cache in the TB history)") + '</span>' +
      '</label>' +
    '</div>' +
    '<textarea class="form-control form-control-sm mt-1" rows="2" ' +
              'data-tb-text placeholder="' + gettext("Optional log text (used for all three logs in the chain)") + '"></textarea>';
  btn.parentNode.insertBefore(div, btn);
}

function _gcfTbSerialiseGlobal(form) {
  form.querySelectorAll('input[data-tb-serialised]').forEach(function (n) { n.remove(); });
  // Clear any stale name attributes assigned to file inputs from a prior submit
  document.querySelectorAll('#tb-sections [data-tb-images]').forEach(function(inp) {
    inp.removeAttribute('name');
  });
  var idx = 0;
  function _add(name, value) {
    var inp = document.createElement('input');
    inp.type = 'hidden'; inp.name = name; inp.value = value;
    inp.setAttribute('data-tb-serialised', '1');
    form.appendChild(inp);
  }
  function _emit(action, ref, tracking, text) {
    _add('tb_action_' + idx, action);
    _add('tb_ref_' + idx, ref || '');
    if (tracking) _add('tb_tracking_' + idx, tracking);
    _add('tb_text_' + idx, text || '');
    idx++;
  }

  document.querySelectorAll('#tb-sections .tb-row, #tb-sections .tb-extra-row, #tb-sections .tb-grab-row').forEach(function (row) {
    var kind = row.getAttribute('data-tb-row') || '';
    var ref  = row.getAttribute('data-tb-ref') || '';
    var trackingInput = row.querySelector('[data-tb-tracking]');
    var tracking = trackingInput ? (trackingInput.value || '').trim().toUpperCase() : '';
    var textInput = row.querySelector('[data-tb-text]');
    var text = textInput ? textInput.value : '';

    var firstIdxForRow = -1;
    function _emitRow(action) {
      if (firstIdxForRow < 0) firstIdxForRow = idx;
      _emit(action, ref, tracking, text);
    }

    if (kind === 'cache') {
      var radio = row.querySelector('input[type=radio]:checked');
      var action = radio ? radio.value : '';
      if (action) _emitRow(action);
    } else if (kind === 'inv') {
      var dropCb  = row.querySelector('input[type=checkbox][data-tb-drop]');
      var visitCb = row.querySelector('input[type=checkbox][data-tb-visit]');
      // For inventory rows the tracking code comes from data-tb-inv-tracking
      // (returned by the inventory API), not from a visible input. GC requires
      // it even for drops to authenticate the holder relationship.
      var invTracking = (row.getAttribute('data-tb-inv-tracking') || '').toUpperCase();
      if (dropCb  && dropCb.checked)  _emit('drop',  ref, invTracking, text);
      if (visitCb && visitCb.checked) _emit('visit', ref, invTracking, text);
    } else if (kind === 'extra') {
      _emitRow('discover');
    } else if (kind === 'grab') {
      var chain = row.querySelector('[data-tb-grab-chain]');
      _emitRow((chain && chain.checked) ? 'grab_chain' : 'grab');
    }

    // Attach the row's image file input (if any files chosen) to the first
    // action emitted for this row. Multi-action rows (drop + visit) share
    // their images via the first emitted index.
    var fileInput = row.querySelector('[data-tb-images]');
    if (fileInput && fileInput.files && fileInput.files.length && firstIdxForRow >= 0) {
      fileInput.name = 'tb_image_' + firstIdxForRow;
    }
  });
}

document.addEventListener('DOMContentLoaded', function () {
  var section = document.getElementById('tb-sections');
  if (!section) return;
  section.addEventListener('click', function (ev) {
    var t = ev.target;
    if (t.matches('[data-tb-verify]'))        _gcfTbVerify(t);
    else if (t.matches('[data-tb-discover-add]')) _gcfTbAddDiscover(t);
    else if (t.matches('[data-tb-grab-add]'))     _gcfTbAddGrab(t);
  });
  var invSect = document.getElementById('tb-inv-section');
  if (invSect) invSect.addEventListener('toggle', function () { if (invSect.open) _gcfTbInvLoad(); });
  var form = section.closest('form');
  if (form) form.addEventListener('submit', function () { _gcfTbSerialiseGlobal(form); });

  // Auto-load inventory eagerly so auto-visit pre-checks are visible without
  // needing the user to expand the section.
  _gcfTbInvLoad();
});
