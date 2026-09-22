// Import field notes page (templates/geocaches/import/import_fieldnotes.html):
// auto-submit the file-path form when the file browser confirms a selection,
// the "Fetch caches from GC/OC" flow (kick off a map-sync fetch, poll its
// tasks, auto re-import once they finish), and the redirect-to-bulk-logging
// timeout after an import that produced pending notes. Endpoint URLs come
// from the #fieldnotes-config data-* element.

var _fnCfg = document.getElementById('fieldnotes-config');
function _fnUrl(name) { return _fnCfg ? _fnCfg.dataset[name] : ''; }

function gcfFieldnotesPathChange() {
  if (this.value.trim()) {
    document.getElementById('form-import-file').submit();
  }
}

function _gcfFieldnotesPollTasks(taskIds, filePath) {
  var statusUrlTemplate = _fnUrl('taskStatusUrlTemplate');

  function checkAll() {
    var checks = taskIds.map(function (id) {
      return fetch(statusUrlTemplate.replace('PLACEHOLDER', id)).then(function (r) { return r.json(); });
    });
    Promise.all(checks).then(function (results) {
      var active = results.filter(function (t) {
        return t.state === 'running' || t.state === 'pending';
      });
      document.getElementById('fetch-status-text').textContent =
        active.length ? interpolate(gettext('Fetching caches… (%s task(s) running)'), [active.length]) : gettext('Fetch complete.');
      if (active.length === 0) {
        document.getElementById('fetch-progress-bar').classList.remove('progress-bar-animated');
        document.getElementById('fetch-progress-bar').classList.add('bg-success');
        document.getElementById('fetch-status-text').textContent = gettext('Caches fetched successfully — importing…');
        setTimeout(function () {
          document.getElementById('form-reimport').submit();
        }, 1000);
      } else {
        setTimeout(checkAll, 2000);
      }
    }).catch(function () {
      setTimeout(checkAll, 2000);
    });
  }
  setTimeout(checkAll, 1500);
}

function gcfFieldnotesFetchMissing() {
  var btn = document.getElementById('btn-fetch-missing');
  var platforms = JSON.parse(btn.dataset.platforms || '{}');
  var filePath = btn.dataset.filePath;

  btn.disabled = true;
  document.getElementById('fetch-progress').classList.remove('d-none');

  var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;

  fetch(_fnUrl('mapSyncUrl'), {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken},
    body: JSON.stringify({platforms: platforms, log_count: 5}),
  })
  .then(function (r) { return r.json(); })
  .then(function (data) {
    var taskIds = data.task_ids || [];
    if (!taskIds.length) {
      document.getElementById('fetch-status-text').textContent = gettext('No tasks started — check platform accounts in Settings.');
      document.getElementById('fetch-progress-bar').classList.add('bg-warning');
      return;
    }
    _gcfFieldnotesPollTasks(taskIds, filePath);
  })
  .catch(function (err) {
    document.getElementById('fetch-status-text').textContent = interpolate(gettext('Fetch failed: %s'), [err]);
  });
}

(function () {
  // Auto-submit when the file browser confirms a selection.
  // The "Files in fieldnotes/" links just set .value directly (no change event), so they are unaffected.
  var pathInput = document.getElementById('fieldnote_path');
  if (pathInput) pathInput.addEventListener('change', gcfFieldnotesPathChange);

  var btn = document.getElementById('btn-fetch-missing');
  if (btn) btn.addEventListener('click', gcfFieldnotesFetchMissing);

  if (_fnUrl('redirectToBulk') === 'true') {
    setTimeout(function () {
      window.location.href = _fnUrl('bulkLoggingUrl');
    }, 1000);
  }
})();
