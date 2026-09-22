// Log form fields partial (templates/geocaches/partials/_log_form_fields.html,
// included once per page via _log_editor_pane.html — never htmx-swapped, only
// ever rendered as part of a full page load, so a plain <script src> in place
// is safe): Favourite/Recommend checkbox visibility, "Refresh from platforms"
// Find # lookup, and the field-note / passphrase quick-insert buttons.

(function () {
  // Favourite / Recommend visibility logic
  var _FAV_TYPES = ['Found it', 'Attended', 'Webcam Photo Taken'];
  var _REC_TYPES = ['Found it'];

  function _gcfLogFieldsUpdateFavRec() {
    var form = document.querySelector('#fav-rec-section') && document.querySelector('#fav-rec-section').closest('form');
    if (!form) return;
    var logTypeSel = form.querySelector('select[name="log_type"]');
    var logType = logTypeSel ? logTypeSel.value : '';
    var gcChecked = !!form.querySelector('input[name="platforms"][value="gc"]:checked');
    var ocChecked = Array.prototype.some.call(
      form.querySelectorAll('input[name="platforms"]'),
      function (el) { return el.value.indexOf('oc_') === 0 && el.checked; }
    );
    var favWrap = document.getElementById('fav-check-wrap');
    var recWrap = document.getElementById('rec-check-wrap');
    if (favWrap) {
      var show = gcChecked && _FAV_TYPES.indexOf(logType) >= 0;
      favWrap.style.setProperty('display', show ? 'block' : 'none', 'important');
      if (!show) { var cb = favWrap.querySelector('input'); if (cb) cb.checked = false; }
    }
    if (recWrap) {
      var showR = ocChecked && _REC_TYPES.indexOf(logType) >= 0;
      recWrap.style.setProperty('display', showR ? 'block' : 'none', 'important');
      if (!showR) { var cbR = recWrap.querySelector('input'); if (cbR) cbR.checked = false; }
    }
  }

  // Refresh Find # from live platform APIs.
  function _gcfLogFieldsRefreshFinds(btn) {
    var input = document.getElementById('logFormSequenceNumber');
    var status = document.getElementById('logFormRefreshFindsStatus');
    if (!btn || !input) return;
    var url = btn.getAttribute('data-url');
    if (!url) return;
    var csrf = (document.querySelector('input[name=csrfmiddlewaretoken]') || {}).value || '';
    btn.disabled = true;
    if (status) { status.textContent = '…'; status.className = 'text-muted small'; }
    fetch(url, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrf, 'Accept': 'application/json' },
      credentials: 'same-origin',
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok || !res.body.ok) {
          var msg = (res.body.errors && res.body.errors.length) ? res.body.errors.join('; ') : 'no platform returned a count';
          if (status) { status.textContent = msg; status.className = 'text-danger small'; }
          return;
        }
        input.value = String(res.body.total + 1);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        if (status) {
          var msg = 'updated (total ' + res.body.total + ')';
          if (res.body.errors && res.body.errors.length) {
            msg += '; partial: ' + res.body.errors.join('; ');
            status.className = 'text-warning small';
          } else {
            status.className = 'text-success small';
          }
          status.textContent = msg;
        }
      })
      .catch(function (exc) {
        if (status) { status.textContent = String(exc); status.className = 'text-danger small'; }
      })
      .finally(function () { btn.disabled = false; });
  }

  document.addEventListener('DOMContentLoaded', function () {
    _gcfLogFieldsUpdateFavRec();
    document.querySelectorAll('select[name="log_type"], input[name="platforms"]').forEach(function (el) {
      el.addEventListener('change', _gcfLogFieldsUpdateFavRec);
    });
    var refreshBtn = document.getElementById('logFormRefreshFinds');
    if (refreshBtn) refreshBtn.addEventListener('click', function () { _gcfLogFieldsRefreshFinds(refreshBtn); });
  });
})();

function gcfInsertPassphrase(text) {
  var inp = document.getElementById('logFormPassphrase');
  if (inp) { inp.value = text; inp.focus(); }
}
function gcfInsertFieldNote(text, logType, localDt) {
  var ta = document.getElementById('logFormText');
  var start = (typeof ta.selectionStart === 'number') ? ta.selectionStart : 0;
  var end   = (typeof ta.selectionEnd   === 'number') ? ta.selectionEnd   : 0;
  ta.value = ta.value.substring(0, start) + text + ta.value.substring(end);
  ta.selectionStart = ta.selectionEnd = start + text.length;
  ta.focus();
  if (logType) {
    var sel = ta.closest('form').querySelector('select[name="log_type"]');
    if (sel) {
      for (var i = 0; i < sel.options.length; i++) {
        if (sel.options[i].value === logType) { sel.selectedIndex = i; break; }
      }
    }
  }
  if (localDt) {
    var dtInput = ta.closest('form').querySelector('input[name="logged_at"]');
    if (dtInput) dtInput.value = localDt;
  }
}
