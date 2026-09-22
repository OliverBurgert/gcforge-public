// Pocket Query management page: row selection, tag insert, Show matching /
// Propose Split preview dialogs, background website-derived status poll,
// task progress poll, and post-trigger list auto-refresh. Endpoint URLs come
// from the #pq-config data attributes (templates/geocaches/pq_management.html).

var _pqCfg = document.getElementById('pq-config');
function _pqUrl(name) { return _pqCfg ? _pqCfg.dataset[name] : ''; }

var _pqLastTagInput = null;

function gcfPqInsertTag(tag) {
  var input = _pqLastTagInput;
  if (!input) return;
  var current = input.value.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
  if (current.indexOf(tag) === -1) {
    current.push(tag);
  }
  input.value = current.join(', ');
  input.focus();
}

// --- Row selection + bulk actions ---
var PQ_I18N = {
  selected: gettext("selected"),
  selectFirst: gettext("Select at least one pocket query first."),
  confirmDelete: gettext("Permanently delete the selected pocket queries on geocaching.com? This cannot be undone.")
};

function gcfPqCheckedCount() {
  return document.querySelectorAll('.pq-row-check:checked').length;
}

function gcfPqUpdateSelCount() {
  var n = gcfPqCheckedCount();
  var el = document.getElementById('pq-sel-count');
  if (el) el.textContent = n + ' ' + PQ_I18N.selected;
  var master = document.getElementById('pq-check-all');
  if (master) {
    var total = document.querySelectorAll('.pq-row-check').length;
    master.checked = n > 0 && n === total;
    master.indeterminate = n > 0 && n < total;
  }
}

// (Re)bind handlers that live on table rows — also called after the background
// web-status swap replaces the tbody.
function gcfPqBindRows() {
  document.querySelectorAll('input[name^="tags_"]').forEach(function(input) {
    input.addEventListener('focus', function() { _pqLastTagInput = this; });
  });
  if (!_pqLastTagInput) _pqLastTagInput = document.querySelector('input[name^="tags_"]');
  document.querySelectorAll('.pq-row-check').forEach(function(cb) {
    cb.addEventListener('change', gcfPqUpdateSelCount);
  });
  gcfPqUpdateSelCount();
}

(function() {
  var master = document.getElementById('pq-check-all');
  if (master) {
    master.addEventListener('change', function() {
      document.querySelectorAll('.pq-row-check').forEach(function(cb) {
        cb.checked = master.checked;
      });
      gcfPqUpdateSelCount();
    });
  }
  gcfPqBindRows();
})();

// --- Background website check: swap in web-derived chips once ready ---
(function() {
  var form = document.getElementById('pq-form');
  if (!form || form.dataset.pollWeb !== '1') return;
  var spinner = document.getElementById('pq-web-check');
  function showSpinner(on) {
    if (!spinner) return;
    spinner.classList.toggle('d-none', !on);
    spinner.classList.toggle('d-inline-flex', on);
  }
  showSpinner(true);
  var tries = 0;
  function poll() {
    if (++tries > 40) { showSpinner(false); return; }
    fetch(_pqUrl('urlRowsJson'))
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data && data.ready && data.html) {
          var tbody = document.getElementById('pq-tbody');
          if (tbody) { tbody.innerHTML = data.html; gcfPqBindRows(); }
          document.querySelectorAll('.pq-needs-web').forEach(function(b) { b.disabled = false; });
          showSpinner(false);
        } else {
          setTimeout(poll, 1500);
        }
      })
      .catch(function() { setTimeout(poll, 2500); });
  }
  setTimeout(poll, 800);
})();

function gcfPqRequireSelection() {
  if (gcfPqCheckedCount() === 0) {
    alert(PQ_I18N.selectFirst);
    return false;
  }
  return true;
}

function gcfPqConfirmDelete() {
  if (gcfPqCheckedCount() === 0) {
    alert(PQ_I18N.selectFirst);
    return false;
  }
  return confirm(PQ_I18N.confirmDelete);
}

// --- Show matching preview ---
function gcfPqShowMatching() {
  var pattern = document.getElementById('pq-trigger-pattern').value.trim();
  if (!pattern) return;

  var body = document.getElementById('pq-match-body');
  body.innerHTML = '<p class="text-muted">Loading...</p>';
  var modal = new bootstrap.Modal(document.getElementById('pq-match-modal'));
  modal.show();

  fetch(_pqUrl('urlMatchPreview') + '?pattern=' + encodeURIComponent(pattern))
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) {
        body.innerHTML = '<div class="alert alert-danger mb-0">' + data.error + '</div>';
        return;
      }

      var html = '';
      var s = data.summary;

      // Run counter
      html += '<p class="small mb-2">PQs ran on ' + s.today_pst + ' (PST): <strong>'
            + s.ran_today + '/10</strong>, remaining: ' + s.remaining_triggers + '</p>';

      if (data.matching.length === 0) {
        html += '<p class="text-muted">No PQs match "' + pattern + '".</p>';
      } else {
        html += '<p class="mb-1"><strong>' + data.matching.length + '</strong> PQ(s) match "' + pattern + '"';
        if (s.would_trigger > 0) {
          html += ', <strong>' + s.would_trigger + '</strong> would be triggered';
        }
        html += ':</p>';

        if (s.exceeds_limit) {
          html += '<div class="alert alert-warning py-1 small mb-2">'
                + 'Triggering ' + s.would_trigger + ' PQ(s) would exceed the daily limit of 10! '
                + 'Only ' + s.remaining_triggers + ' trigger(s) remaining today.'
                + '</div>';
        }

        html += '<ul class="small mb-0">';
        data.matching.forEach(function(pq) {
          var status = '';
          if (pq.already_ran) status = ' <span class="badge bg-info">Ran today</span>';
          else if (pq.already_sched) status = ' <span class="badge bg-warning text-dark">Scheduled</span>';
          else if (pq.has_trigger_url) status = ' <span class="badge bg-success">Will trigger</span>';
          else status = ' <span class="badge bg-secondary">No trigger URL</span>';
          html += '<li>' + pq.name + status + '</li>';
        });
        html += '</ul>';
      }

      body.innerHTML = html;
    })
    .catch(function(err) {
      body.innerHTML = '<div class="alert alert-danger mb-0">Request failed: ' + err + '</div>';
    });
}

// --- Propose Split preview ---
var PQ_SPLIT_I18N = {
  loading: gettext("Loading..."),
  requestFailed: gettext("Request failed:"),
  candidateCaches: gettext("Combined local candidate caches:"),
  noDateExcludedTpl: gettext("{n} cache(s) excluded — no hidden date"),
  criteriaMismatch: gettext("Selected PQs do not share identical search criteria:"),
  cantVerify: gettext("Some criteria cannot be fully verified locally:"),
  needsMorePqs: gettext("This split needs more date ranges than PQs selected — create additional PQ(s) for:"),
  fewerPqsNeeded: gettext("These selected PQs are no longer needed for this split:"),
  colPq: gettext("Pocket Query"),
  colFrom: gettext("Hidden date from"),
  colTo: gettext("Hidden date to"),
  colCount: gettext("Caches"),
  openStart: gettext("(no lower bound)"),
  openEnd: gettext("(no upper bound)"),
  noCandidates: gettext("No candidate caches found."),
  confirmApplyTpl: gettext("Write these {n} date range(s) into their PQs on geocaching.com now? This changes your live account and cannot be undone automatically."),
  confirmRenameNoteTpl: gettext("PQs will also be renamed to: {names}"),
  applying: gettext("Applying…"),
  applied: gettext("Applied"),
  failed: gettext("Failed")
};

function gcfPqSplitEsc(s) {
  return String(s).replace(/[&<>"]/g, function(c) {
    return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c];
  });
}

var _pqLastSplitProposal = null;

function gcfPqShowSplitPreview() {
  if (gcfPqCheckedCount() === 0) {
    alert(PQ_I18N.selectFirst);
    return;
  }

  _pqLastSplitProposal = null;
  var applyBtn = document.getElementById('pq-split-apply-btn');
  applyBtn.disabled = true;
  document.getElementById('pq-split-rename-base').value = '';

  var body = document.getElementById('pq-split-body');
  body.innerHTML = '<p class="text-muted">' + PQ_SPLIT_I18N.loading + '</p>';
  var modal = new bootstrap.Modal(document.getElementById('pq-split-modal'));
  modal.show();

  fetch(_pqUrl('urlSplitPreview'), {
    method: 'POST',
    body: new FormData(document.getElementById('pq-form')),
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) {
        body.innerHTML = '<div class="alert alert-danger mb-0">' + gcfPqSplitEsc(data.error) + '</div>';
        return;
      }

      var clean = data.buckets.length > 0 && data.criteria_warnings.length === 0
        && data.unmapped_extra_buckets.length === 0 && data.unmapped_extra_pqs.length === 0;
      if (clean) {
        _pqLastSplitProposal = data;
        applyBtn.disabled = false;
      }

      var html = '';
      html += '<p class="small mb-2">' + PQ_SPLIT_I18N.candidateCaches + ' <strong>' + data.total_count + '</strong>';
      if (data.no_date_count > 0) {
        html += ' (' + PQ_SPLIT_I18N.noDateExcludedTpl.replace('{n}', data.no_date_count) + ')';
      }
      html += '</p>';

      if (data.criteria_warnings.length) {
        html += '<div class="alert alert-warning py-1 small mb-2"><strong>' + PQ_SPLIT_I18N.criteriaMismatch + '</strong><ul class="mb-0">';
        data.criteria_warnings.forEach(function(w) { html += '<li>' + gcfPqSplitEsc(w) + '</li>'; });
        html += '</ul></div>';
      }
      if (data.unsupported_warnings.length) {
        html += '<div class="alert alert-warning py-1 small mb-2"><strong>' + PQ_SPLIT_I18N.cantVerify + '</strong><ul class="mb-0">';
        data.unsupported_warnings.forEach(function(w) { html += '<li>' + gcfPqSplitEsc(w) + '</li>'; });
        html += '</ul></div>';
      }

      if (data.buckets.length) {
        html += '<table class="table table-sm mb-2"><thead><tr>'
          + '<th>' + PQ_SPLIT_I18N.colPq + '</th>'
          + '<th>' + PQ_SPLIT_I18N.colFrom + '</th>'
          + '<th>' + PQ_SPLIT_I18N.colTo + '</th>'
          + '<th class="text-end">' + PQ_SPLIT_I18N.colCount + '</th>'
          + '</tr></thead><tbody>';
        data.buckets.forEach(function(b) {
          html += '<tr><td>' + gcfPqSplitEsc(b.pq_name) + '</td>'
            + '<td>' + (b.date_from || PQ_SPLIT_I18N.openStart) + '</td>'
            + '<td>' + (b.date_to || PQ_SPLIT_I18N.openEnd) + '</td>'
            + '<td class="text-end">' + b.count + '</td></tr>';
        });
        html += '</tbody></table>';
      }

      if (data.unmapped_extra_buckets.length) {
        html += '<div class="alert alert-info py-1 small mb-2">' + PQ_SPLIT_I18N.needsMorePqs + '<ul class="mb-0">';
        data.unmapped_extra_buckets.forEach(function(b) {
          html += '<li>' + (b.date_from || PQ_SPLIT_I18N.openStart) + ' – ' + (b.date_to || PQ_SPLIT_I18N.openEnd) + ' (' + b.count + ')</li>';
        });
        html += '</ul></div>';
      }
      if (data.unmapped_extra_pqs.length) {
        html += '<div class="alert alert-info py-1 small mb-2">' + PQ_SPLIT_I18N.fewerPqsNeeded + '<ul class="mb-0">';
        data.unmapped_extra_pqs.forEach(function(name) { html += '<li>' + gcfPqSplitEsc(name) + '</li>'; });
        html += '</ul></div>';
      }

      body.innerHTML = html || '<p class="text-muted">' + PQ_SPLIT_I18N.noCandidates + '</p>';
    })
    .catch(function(err) {
      body.innerHTML = '<div class="alert alert-danger mb-0">' + PQ_SPLIT_I18N.requestFailed + ' ' + gcfPqSplitEsc(err) + '</div>';
    });
}

function gcfPqExecuteSplit() {
  if (!_pqLastSplitProposal) return;
  var buckets = _pqLastSplitProposal.buckets;
  var renameBase = document.getElementById('pq-split-rename-base').value.trim();
  var items = buckets.map(function(b, i) {
    var item = {
      reference_code: b.reference_code, guid: b.guid, pq_name: b.pq_name,
      date_from: b.date_from, date_to: b.date_to,
    };
    if (renameBase) item.new_name = renameBase + ' ' + (i + 1);
    return item;
  });

  var msg = PQ_SPLIT_I18N.confirmApplyTpl.replace('{n}', items.length);
  if (renameBase) {
    var names = items.map(function(it) { return it.new_name; }).join(', ');
    msg += '\n' + PQ_SPLIT_I18N.confirmRenameNoteTpl.replace('{names}', names);
  }
  if (!confirm(msg)) return;

  var btn = document.getElementById('pq-split-apply-btn');
  btn.disabled = true;
  var body = document.getElementById('pq-split-body');
  body.insertAdjacentHTML('beforeend', '<p class="text-muted small mb-0" id="pq-split-apply-status">' + PQ_SPLIT_I18N.applying + '</p>');

  var csrf = document.querySelector('#pq-form [name=csrfmiddlewaretoken]').value;
  fetch(_pqUrl('urlApplySplit'), {
    method: 'POST',
    headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/json'},
    body: JSON.stringify({items: items}),
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var statusEl = document.getElementById('pq-split-apply-status');
      if (data.error) {
        statusEl.outerHTML = '<div class="alert alert-danger mb-0">' + gcfPqSplitEsc(data.error) + '</div>';
        return;
      }
      var html = '<ul class="small mb-0">';
      data.results.forEach(function(r) {
        if (r.status === 'applied') {
          html += '<li><span class="badge bg-success">' + PQ_SPLIT_I18N.applied + '</span> ' + gcfPqSplitEsc(r.pq_name) + '</li>';
        } else {
          html += '<li><span class="badge bg-danger">' + PQ_SPLIT_I18N.failed + '</span> ' + gcfPqSplitEsc(r.pq_name) + ' — ' + gcfPqSplitEsc(r.error) + '</li>';
        }
      });
      html += '</ul>';
      statusEl.outerHTML = html;
    })
    .catch(function(err) {
      var statusEl = document.getElementById('pq-split-apply-status');
      statusEl.outerHTML = '<div class="alert alert-danger mb-0">' + PQ_SPLIT_I18N.requestFailed + ' ' + gcfPqSplitEsc(err) + '</div>';
    });
}

// --- Task progress polling ---
(function() {
  var el = document.getElementById('pq-task-result');
  if (!el) return;
  var state = el.dataset.taskState;
  var taskId = el.dataset.taskId;
  if (state !== 'running') {
    if (state === 'completed' && el.dataset.taskAction === 'trigger') _gcfStartPqRefresh();
    return;
  }

  function poll() {
    fetch(_pqUrl('urlTaskStatusTpl').replace('PLACEHOLDER', taskId))
      .then(function(r) {
        if (r.status === 404) {
          window.location.href = window.location.pathname;
          return null;
        }
        return r.json();
      })
      .then(function(data) {
        if (!data) return;
        var inner = document.getElementById('pq-progress-inner');
        if (!inner) return;
        if (data.state === 'running') {
          inner.textContent = data.name + ' — ' + (data.phase || '') + ' ' + data.progress_pct + '%';
          setTimeout(poll, 2000);
        } else if (data.state === 'completed' || data.state === 'failed' || data.state === 'cancelled') {
          window.location.href = window.location.pathname + '?task_id=' + taskId;
        }
      })
      .catch(function() { setTimeout(poll, 3000); });
  }
  setTimeout(poll, 2000);
})();

// --- PQ list auto-refresh after trigger ---
var _pqRefreshTimer = null;
var _pqRefreshCount = 0;
var _PQ_REFRESH_MAX = 20;
var _PQ_REFRESH_INTERVAL = 30000;

function _gcfStartPqRefresh() {
  if (_pqRefreshTimer) return;
  _pqRefreshCount = 0;
  _pqRefreshTimer = setInterval(_gcfRefreshPqList, _PQ_REFRESH_INTERVAL);
}

function _gcfRefreshPqList() {
  _pqRefreshCount++;
  if (_pqRefreshCount > _PQ_REFRESH_MAX) {
    clearInterval(_pqRefreshTimer);
    _pqRefreshTimer = null;
    return;
  }

  fetch(_pqUrl('urlListJson'))
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (!data.pqs) return;
      var tbody = document.getElementById('pq-tbody');
      if (!tbody) return;

      var anyNewReady = false;
      data.pqs.forEach(function(pq) {
        var row = tbody.querySelector('tr[data-ref="' + pq.referenceCode + '"]');
        if (!row) return;

        var statusTd = row.querySelector('.pq-status');
        if (!statusTd) return;

        // Check if this PQ just became ready (had no "Ready" badge before)
        var hadReady = statusTd.innerHTML.indexOf('Ready') !== -1;
        if (!hadReady && pq.lastUpdatedDateUtc) {
          anyNewReady = true;
        }
      });

      // A triggered PQ just finished generating — full reload to get correct status chips
      if (anyNewReady) {
        clearInterval(_pqRefreshTimer);
        _pqRefreshTimer = null;
        window.location.href = window.location.pathname;
      }
    })
    .catch(function() {});
}
