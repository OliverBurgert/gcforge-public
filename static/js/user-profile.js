// User profile page (templates/preferences/user_profile.html): per-trackable
// Auto-visit toggle + text save for the GC inventory list. Wires up every
// [data-tb-auto-save-url] container found on the page (normally at most one,
// since only one GC platform card has trackables) rather than relying on
// <script>'s own position in the DOM, so the logic works regardless of how
// many such blocks the server renders.

function _gcfUserProfileCsrf() {
  return (document.querySelector('input[name=csrfmiddlewaretoken]') || {}).value || '';
}

function _gcfUserProfileSaveAutoVisit(root, row, statusEl) {
  var url = root.getAttribute('data-tb-auto-save-url');
  var ref = row.getAttribute('data-ref');
  var enabled = row.querySelector('[data-tb-auto-enabled]').checked ? '1' : '0';
  var text = row.querySelector('[data-tb-auto-text]').value;
  statusEl.textContent = 'saving…';
  statusEl.className = 'small text-muted';
  fetch(url, {
    method: 'POST',
    headers: { 'X-CSRFToken': _gcfUserProfileCsrf(), 'Content-Type': 'application/x-www-form-urlencoded' },
    credentials: 'same-origin',
    body: 'ref_code=' + encodeURIComponent(ref)
        + '&enabled=' + enabled
        + '&text=' + encodeURIComponent(text),
  })
    .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
    .then(function (res) {
      if (!res.ok || !res.body.ok) {
        statusEl.textContent = (res.body && res.body.error) || 'save failed';
        statusEl.className = 'small text-danger';
        return;
      }
      statusEl.textContent = 'saved';
      statusEl.className = 'small text-success';
      setTimeout(function () { statusEl.textContent = ''; }, 1500);
    })
    .catch(function (exc) {
      statusEl.textContent = String(exc);
      statusEl.className = 'small text-danger';
    });
}

function gcfUserProfileInitAutoVisit() {
  document.querySelectorAll('[data-tb-auto-save-url]').forEach(function (root) {
    root.querySelectorAll('.tb-inv-edit').forEach(function (row) {
      var cb = row.querySelector('[data-tb-auto-enabled]');
      var wrap = row.querySelector('[data-tb-auto-text-wrap]');
      var statusEl = row.querySelector('[data-tb-auto-status]');
      cb.addEventListener('change', function () {
        wrap.style.display = cb.checked ? '' : 'none';
        _gcfUserProfileSaveAutoVisit(root, row, statusEl);
      });
      var btn = row.querySelector('[data-tb-auto-save]');
      btn.addEventListener('click', function () { _gcfUserProfileSaveAutoVisit(root, row, statusEl); });
    });
  });
}

gcfUserProfileInitAutoVisit();
