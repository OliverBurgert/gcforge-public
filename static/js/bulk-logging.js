// Bulk logging tool (templates/geocaches/tools/bulk_logging.html): pending
// prev/next navigation, fetch-based "Submit now" (keeps the editor open with
// the typed text/passphrase/attached images intact on a failed submit instead
// of navigating away), reloading the editor when the cache-detail preview
// iframe navigates, and live-updating the active list row's log-type text.
// Config (pending note ids, selected id, active tab) comes from the
// #bulk-config data-* element.

var _bulkCfg = document.getElementById('bulk-config');
function _bulkCfgVal(name) { return _bulkCfg ? _bulkCfg.dataset[name] : ''; }

var _bulkPending = _bulkCfgVal('pendingIds') ? _bulkCfgVal('pendingIds').split(',').map(Number) : [];
var _bulkSelectedId = _bulkCfgVal('selectedId') ? Number(_bulkCfgVal('selectedId')) : null;
var _bulkTab = _bulkCfgVal('tab') || 'pending';
var _bulkIframeInitialLoad = true;

function _bulkNoteUrl(id) {
  return '?tab=' + _bulkTab + '&note=' + id;
}

function _bulkShowSubmitError(msg) {
  var errBox = document.getElementById('bulk-submit-error');
  if (!errBox) return;
  errBox.textContent = msg;
  errBox.classList.remove('d-none');
  var ed = document.getElementById('bulk-editor');
  if (ed) ed.scrollTop = 0;
}

// Reload editor when the detail iframe navigates (e.g. after cache refresh)
// so the passphrase field appears if req_passwd changed.
function gcfBulkDetailFrameLoad() {
  if (_bulkIframeInitialLoad) { _bulkIframeInitialLoad = false; return; }
  window.location.reload();
}

// Submit-now goes through fetch so a failed submit (e.g. a wrong OC
// passphrase) keeps the editor open with the typed text, passphrase and the
// attached images intact instead of navigating to the next note. Save draft,
// Remove from queue and prev/next stay plain links/submits.
function gcfBulkSubmitNow(ev) {
  var editorForm = ev.currentTarget;
  var submitter = ev.submitter;
  if (!submitter || submitter.value !== 'submit_now') return;
  ev.preventDefault();
  // The TB-sections serialiser normally runs from its own 'submit' listener
  // (registered on DOMContentLoaded, i.e. after this one), which would append
  // tb_action_* hidden inputs too late for the FormData snapshot below. Run
  // it explicitly first so trackable actions aren't silently dropped from
  // the fetch-based submit.
  if (window._gcfTbSerialiseGlobal) window._gcfTbSerialiseGlobal(editorForm);
  var fd = new FormData(editorForm);
  fd.set('action', 'submit_now');
  var csrfEl = editorForm.querySelector('[name=csrfmiddlewaretoken]');
  var csrf = csrfEl ? csrfEl.value : '';
  // NB: the form has <button name="action">, which shadows the form's
  // built-in .action property — read the attribute string explicitly.
  var actionUrl = editorForm.getAttribute('action');
  submitter.disabled = true;
  var errBox = document.getElementById('bulk-submit-error');
  if (errBox) { errBox.classList.add('d-none'); errBox.textContent = ''; }
  fetch(actionUrl, {
    method: 'POST',
    headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-CSRFToken': csrf },
    body: fd,
    credentials: 'same-origin',
  })
    .then(function (r) {
      if (!r.ok) { throw new Error('HTTP ' + r.status); }
      return r.json();
    })
    .then(function (res) {
      if (res.success) { window.location.href = res.next_url; return; }
      submitter.disabled = false;
      _bulkShowSubmitError(res.error || gettext("Submit failed."));
    })
    .catch(function (e) {
      submitter.disabled = false;
      _bulkShowSubmitError(gettext("Submit failed.") + ' ' + e);
    });
}

// Live-update log type text in the active list item when the select changes.
function gcfBulkLogTypeChange() {
  var activeRow = document.querySelector('.note-row.active');
  if (!activeRow) return;
  var logTypeSpan = activeRow.querySelector('[data-role="log-type-text"]');
  if (logTypeSpan) {
    var current = logTypeSpan.textContent;
    var datePart = current.indexOf(' · ') >= 0 ? current.substring(current.indexOf(' · ')) : '';
    logTypeSpan.textContent = this.value + datePart;
  }
}

(function () {
  var idx = _bulkPending.indexOf(_bulkSelectedId);

  var btnPrev = document.getElementById('btn-prev');
  var btnNext = document.getElementById('btn-next');
  if (btnPrev) {
    if (idx > 0) { btnPrev.href = _bulkNoteUrl(_bulkPending[idx - 1]); }
    else { btnPrev.classList.add('disabled'); btnPrev.setAttribute('aria-disabled', 'true'); }
  }
  if (btnNext) {
    if (idx >= 0 && idx < _bulkPending.length - 1) { btnNext.href = _bulkNoteUrl(_bulkPending[idx + 1]); }
    else { btnNext.classList.add('disabled'); btnNext.setAttribute('aria-disabled', 'true'); }
  }

  var detailFrame = document.getElementById('detail-frame');
  if (detailFrame) detailFrame.addEventListener('load', gcfBulkDetailFrameLoad);

  var editorForm = document.querySelector('#bulk-editor form');
  if (editorForm) editorForm.addEventListener('submit', gcfBulkSubmitNow);

  var logTypeSelect = document.querySelector('select[name="log_type"]');
  if (logTypeSelect) logTypeSelect.addEventListener('change', gcfBulkLogTypeChange);
})();
