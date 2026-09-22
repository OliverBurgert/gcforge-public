function gcfSubmitScope() {
  // Prefer the cache-list helper that merges the toolbar ?fx= tree —
  // a plain FormData(filter-form) drops every data-fx-* widget (Type,
  // Country, Status, Tag, Flag, Found) because they carry no name=,
  // which would silently strip those filters on the redirect back.
  var next;
  if (typeof gcfBuildCurrentListUrl === 'function') {
    next = gcfBuildCurrentListUrl();
  }
  if (!next) {
    var ff = document.getElementById('filter-form');
    next = ff
      ? (window.location.pathname + '?' + new URLSearchParams(new FormData(ff)).toString())
      : (sessionStorage.getItem('gcforge_list_url') || window.location.pathname + window.location.search);
  }
  var form = document.getElementById('scope-form');
  document.getElementById('scope-next').value = next;

  // In-place path: POST the scope prefs (don't follow the 302 — we don't need
  // the redirected page), then refresh the list table without a full reload so
  // the map isn't torn down and rebuilt. The scope is a server-side session
  // pref (not a URL param), so force the map marker refresh and replace history
  // rather than pushing a new entry. Fall back to a normal submit when the
  // in-place helper or table aren't present (non-list pages).
  if (typeof gcfApplyListChange === 'function' && document.getElementById('cache-table-container')) {
    fetch(form.action, { method: 'POST', body: new FormData(form), redirect: 'manual' })
      .then(function () { gcfApplyListChange(next, { forceMap: true, replace: true }); })
      .catch(function () { form.submit(); });
  } else {
    form.submit();
  }
}

(function () {
  var logo = document.getElementById('logo-link');
  if (!logo) return;
  var saved = sessionStorage.getItem('gcforge_list_url');
  if (saved) logo.href = saved;
})();

document.body.addEventListener('gcApiValidated', function () {
  var el = document.getElementById('nav-conn-gc');
  if (el) {
    el.className = el.className.replace(/text-warning|text-info/g, 'text-success');
    el.title = 'GC: full API access (Level 3)';
  }
});

// --- Task dock auto-reload (templates/geocaches/partials/_task_status.html) ---
// The dock polls #task-status-dock every 2s via hx-get; htmx re-executes any
// <script> in the swapped HTML. That used to mean a tiny inline <script> was
// duplicated per failed/completed task card. Moved here: each such card now
// carries data-task-reload-name/-id instead, and this delegated htmx:afterSwap
// listener does the same one-time-per-task reload check.
document.body.addEventListener('htmx:afterSwap', function (evt) {
  if (evt.detail.target.id !== 'task-status-dock') return;
  evt.detail.target.querySelectorAll('[data-task-reload-id]').forEach(function (card) {
    var name = card.dataset.taskReloadName || '';
    if (name.indexOf('Preview ') === 0 || name.indexOf('Sync ') === 0) return;
    var key = 'gcf_reloaded_' + card.dataset.taskReloadId;
    if (!sessionStorage.getItem(key) &&
        (document.getElementById('filter-form') || document.querySelector('[data-task-reload]'))) {
      sessionStorage.setItem(key, '1');
      window.location.reload();
    }
  });
});
