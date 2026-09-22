// Trackable list page: Log-TB-by-code modal, bulk sync (mine/discovered/
// moved/by-code), tracking-code backfill, missing-location resolve, and
// Refresh filtered. Endpoint URLs come from the #tb-list-config data
// attributes (templates/geocaches/trackable_list.html).

var _tbCfg = document.getElementById('tb-list-config');
function _tbUrl(name) { return _tbCfg ? _tbCfg.dataset[name] : ''; }

function _gcfTbPostJson(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: {'X-CSRFToken': _gcfTbCsrf(), 'Content-Type': 'application/x-www-form-urlencoded'},
    body: body || ''
  }).then(function(r) { return r.json(); });
}

function gcfTbBulkSync(scope) {
  scope = scope || 'mine';
  var btn      = document.getElementById('sync-btn');
  var status   = document.getElementById('sync-status');
  var bar      = document.getElementById('sync-progress');
  var fill     = document.getElementById('sync-progress-bar');
  if (btn) { btn.disabled = true; btn.textContent = gettext("Syncing…"); }
  if (status) status.textContent = gettext("Fetching") + ' ' + scope + ' ' + gettext("list…");
  if (bar) bar.classList.remove('d-none');
  if (fill) fill.style.width = '0%';

  _gcfTbPostJson(_tbUrl('urlSyncPlan'), 'scope=' + encodeURIComponent(scope))
    .then(function(plan) {
      if (!plan.ok) throw new Error(plan.error || 'plan failed');
      var refs = plan.refs || [];
      var skipped = plan.skipped || 0;
      if (!refs.length) {
        var msg = gettext("Nothing to sync for") + ' ' + scope;
        if (skipped) msg += ' (' + skipped + ' ' + gettext("already up-to-date") + ')';
        msg += '.';
        if (status) status.textContent = msg;
        if (btn) { btn.disabled = false; btn.textContent = gettext("Sync"); }
        if (bar) bar.classList.add('d-none');
        return;
      }
      return _gcfTbSyncSequentially(refs, plan.tag || '', scope, skipped, status, fill);
    })
    .then(function(result) {
      if (!result) return;
      if (result.errors.length) {
        alert(gettext("Sync done:") + ' ' + result.ok + ' ' + gettext("synced,") + ' ' + result.errors.length + ' ' + gettext("failed.") + '\n\n' + result.errors.join('\n'));
      }
      window.location.reload();
    })
    .catch(function(err) {
      if (status) status.textContent = '';
      if (bar) bar.classList.add('d-none');
      if (btn) { btn.disabled = false; btn.textContent = gettext("Sync"); }
      alert(gettext("Sync failed:") + ' ' + err);
    });
}

function gcfTbSyncTrackingCodes() {
  var btn    = document.getElementById('sync-btn');
  var status = document.getElementById('sync-status');
  var bar    = document.getElementById('sync-progress');
  var fill   = document.getElementById('sync-progress-bar');
  if (btn) { btn.disabled = true; btn.textContent = gettext("Syncing…"); }
  if (status) status.textContent = gettext("Finding owned TBs without tracking codes…");
  if (bar) bar.classList.remove('d-none');
  if (fill) fill.style.width = '0%';

  _gcfTbPostJson(_tbUrl('urlTrackingCodesPlan'))
    .then(function(plan) {
      if (!plan.ok) throw new Error(plan.error || 'plan failed');
      var refs = plan.refs || [];
      if (!refs.length) {
        if (status) status.textContent = gettext("No owned TBs are missing a tracking code.");
        if (btn) { btn.disabled = false; btn.textContent = gettext("Sync"); }
        if (bar) bar.classList.add('d-none');
        return;
      }
      return _gcfTbScrapeCodesSequentially(refs, status, fill);
    })
    .then(function(result) {
      if (!result) return;
      if (result.errors.length) {
        alert(gettext("Tracking code sync:") + ' ' + result.ok + ' ' + gettext("fetched,") + ' ' + result.errors.length + ' ' + gettext("failed.") + '\n\n' + result.errors.join('\n'));
      }
      window.location.reload();
    })
    .catch(function(err) {
      if (status) status.textContent = '';
      if (bar) bar.classList.add('d-none');
      if (btn) { btn.disabled = false; btn.textContent = gettext("Sync"); }
      alert(gettext("Tracking code sync failed:") + ' ' + err);
    });
}

function _gcfTbScrapeCodesSequentially(refs, status, fill) {
  var total  = refs.length;
  var done   = 0;
  var ok     = 0;
  var errors = [];
  function step() {
    if (done >= total) return Promise.resolve({ok: ok, errors: errors});
    var ref = refs[done];
    if (status) status.textContent = gettext("Tracking codes:") + ' ' + (done + 1) + ' / ' + total + ' (' + ref + ')…';
    return _gcfTbPostJson(_tbUrl('urlTrackingCodeFetch'), 'ref=' + encodeURIComponent(ref))
      .then(function(r) {
        if (r.ok) ok++;
        else errors.push(ref + ': ' + (r.error || gettext("unknown error")));
      })
      .catch(function(err) { errors.push(ref + ': ' + err); })
      .then(function() {
        done++;
        if (fill) fill.style.width = Math.round((done / total) * 100) + '%';
        // Pace the website scrape gently — same shape as the API sync pacing.
        if (done < total && done % _GCF_TB_BATCH_SIZE === 0) {
          return new Promise(function(res) { setTimeout(res, _GCF_TB_BATCH_PAUSE); }).then(step);
        }
        return step();
      });
  }
  return step();
}

function gcfTbSyncByCode() {
  var ta    = document.getElementById('tb-sync-codes');
  var tagEl = document.getElementById('tb-sync-tag');
  var err   = document.getElementById('tb-sync-codes-error');
  err.classList.add('d-none');
  err.textContent = '';

  var raw = (ta.value || '').toUpperCase();
  // Split on anything that isn't [A-Z0-9]; keep tokens that look like TB####
  var tokens = raw.split(/[^A-Z0-9]+/).filter(function(s) { return s.length > 0; });
  var refs   = [];
  var seen   = {};
  var bad    = [];
  for (var i = 0; i < tokens.length; i++) {
    var tok = tokens[i];
    if (!/^TB[A-Z0-9]{3,8}$/.test(tok)) { bad.push(tok); continue; }
    if (!seen[tok]) { seen[tok] = 1; refs.push(tok); }
  }
  if (bad.length) {
    err.textContent = gettext("Not a valid TB code:") + ' ' + bad.slice(0, 5).join(', ') + (bad.length > 5 ? '…' : '');
    err.classList.remove('d-none');
    return;
  }
  if (!refs.length) {
    err.textContent = gettext("Enter at least one TB code.");
    err.classList.remove('d-none');
    return;
  }

  // Close the modal and run the existing sequential-sync helper with the
  // hand-built ref list (skipping the plan endpoint entirely).
  var modal = bootstrap.Modal.getInstance(document.getElementById('tbSyncByCodeModal'));
  if (modal) modal.hide();

  var btn    = document.getElementById('sync-btn');
  var status = document.getElementById('sync-status');
  var bar    = document.getElementById('sync-progress');
  var fill   = document.getElementById('sync-progress-bar');
  if (btn) { btn.disabled = true; btn.textContent = gettext("Syncing…"); }
  if (bar) bar.classList.remove('d-none');
  if (fill) fill.style.width = '0%';

  _gcfTbSyncSequentially(refs, (tagEl.value || '').trim(), 'by-code', 0, status, fill)
    .then(function(result) {
      if (result.errors.length) {
        alert(gettext("Sync done:") + ' ' + result.ok + ' ' + gettext("synced,") + ' ' + result.errors.length + ' ' + gettext("failed.") + '\n\n' + result.errors.join('\n'));
      }
      window.location.reload();
    })
    .catch(function(e) {
      if (btn) { btn.disabled = false; btn.textContent = gettext("Sync"); }
      if (bar) bar.classList.add('d-none');
      alert(gettext("Sync failed:") + ' ' + e);
    });
}

// Log TB by tracking code — Discover / Retrieve / Just Fetch / Show on gc.com.
// All four take the same comma/space/semicolon-separated tracking-code list.
// Discover/Retrieve verify every code first (step 2: review table + log
// message) before submitting anything — GC requires non-empty log text, and
// showing the resolved name/location first catches typos before they burn a
// real GC log. Fetch/Show act straight from the step-1 code list.
var _GCF_TB_LOGCODE_ACTION = '';
var _GCF_TB_LOGCODE_LABELS = {
  discover: gettext("Discover"),
  retrieve: gettext("Retrieve"),
  fetch:    gettext("Just Fetch"),
  show:     gettext("Show on geocaching.com"),
};
// Populated by the verify step: [{code, ref_code, name, current_geocache_code, holder, error}]
var _GCF_TB_LOGCODE_REVIEW = [];

function _gcfTbLogCodeEsc(s) {
  return String(s || '').replace(/[&<>"]/g, function(c) { return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]; });
}

function gcfTbLogByCodeOpen(action) {
  _GCF_TB_LOGCODE_ACTION = action;
  _gcfTbLogByCodeShowStep1();
  var label = _GCF_TB_LOGCODE_LABELS[action] || action;
  var title = document.getElementById('tb-logcode-title');
  if (title) title.textContent = label + ' — ' + gettext("by tracking code");
  var runBtn = document.getElementById('tb-logcode-run');
  if (runBtn) {
    runBtn.disabled = false;
    runBtn.textContent = (action === 'discover' || action === 'retrieve')
      ? gettext("Verify codes…")
      : ((action === 'show') ? gettext("Open tabs") : label);
  }
  var modal = bootstrap.Modal.getOrCreateInstance(document.getElementById('tbLogByCodeModal'));
  modal.show();
}

function _gcfTbLogByCodeShowStep1() {
  document.getElementById('tb-logcode-step1').classList.remove('d-none');
  document.getElementById('tb-logcode-step2').classList.add('d-none');
  document.getElementById('tb-logcode-back').classList.add('d-none');
  var err = document.getElementById('tb-logcode-error');
  if (err) { err.classList.add('d-none'); err.textContent = ''; }
  var status = document.getElementById('tb-logcode-status');
  if (status) status.textContent = '';
  var bar = document.getElementById('tb-logcode-progress');
  if (bar) bar.classList.add('d-none');
}

function gcfTbLogByCodeBack() {
  _gcfTbLogByCodeShowStep1();
  var runBtn = document.getElementById('tb-logcode-run');
  if (runBtn) { runBtn.disabled = false; runBtn.textContent = gettext("Verify codes…"); }
}

function _gcfTbLogByCodeParse() {
  var ta = document.getElementById('tb-logcode-codes');
  var raw = (ta.value || '').toUpperCase();
  // Split on anything that isn't [A-Z0-9] — covers comma, blanks, semicolons, newlines.
  var tokens = raw.split(/[^A-Z0-9]+/).filter(function(s) { return s.length > 0; });
  var codes = [];
  var seen = {};
  tokens.forEach(function(tok) {
    if (!seen[tok]) { seen[tok] = 1; codes.push(tok); }
  });
  return codes;
}

function gcfTbLogByCodeRun() {
  // Step 2 (review) is showing → this click actually submits the logs.
  if (!document.getElementById('tb-logcode-step2').classList.contains('d-none')) {
    _gcfTbLogByCodeSubmitLogs();
    return;
  }

  var err = document.getElementById('tb-logcode-error');
  err.classList.add('d-none');
  err.textContent = '';
  var codes = _gcfTbLogByCodeParse();
  if (!codes.length) {
    err.textContent = gettext("Enter at least one tracking code.");
    err.classList.remove('d-none');
    return;
  }

  if (_GCF_TB_LOGCODE_ACTION === 'show') {
    _gcfTbLogByCodeShow(codes);
    return;
  }
  if (_GCF_TB_LOGCODE_ACTION === 'fetch') {
    _gcfTbLogByCodeRunSimple(codes, _gcfTbLogByCodeFetchOne);
    return;
  }
  // discover / retrieve — verify first, then show the review step.
  _gcfTbLogByCodeRunVerify(codes);
}

function _gcfTbLogByCodeShow(codes) {
  if (codes.length > 10) {
    var proceed = confirm(
      gettext("This will open") + ' ' + codes.length + ' ' + gettext("browser tabs, one per code. Continue?")
    );
    if (!proceed) return;
  }
  var modal = bootstrap.Modal.getInstance(document.getElementById('tbLogByCodeModal'));
  if (modal) modal.hide();
  codes.forEach(function(code) {
    window.open('https://www.geocaching.com/track/details.aspx?tracker=' + encodeURIComponent(code), '_blank');
  });
}

function _gcfTbLogByCodeFetchOne(code) {
  return _gcfTbPostJson(_tbUrl('urlVerify'), 'tracking_code=' + encodeURIComponent(code))
    .then(function(v) {
      if (!v.ok) return v;
      if (!v.ref_code) return {ok: false, error: 'could not resolve tracking code'};
      return _gcfTbPostJson(_tbUrl('urlSyncOne'), 'ref=' + encodeURIComponent(v.ref_code));
    });
}

// Plain single-step runner — used by "Just Fetch", which needs no review step.
function _gcfTbLogByCodeRunSimple(codes, stepFn) {
  var status = document.getElementById('tb-logcode-status');
  var bar    = document.getElementById('tb-logcode-progress');
  var fill   = document.getElementById('tb-logcode-progress-bar');
  var runBtn = document.getElementById('tb-logcode-run');
  if (runBtn) runBtn.disabled = true;
  if (bar) bar.classList.remove('d-none');
  if (fill) fill.style.width = '0%';

  _gcfTbLogByCodeSequentially(codes, stepFn, status, fill)
    .then(function(result) {
      var modal = bootstrap.Modal.getInstance(document.getElementById('tbLogByCodeModal'));
      if (modal) modal.hide();
      if (result.errors.length) {
        alert(gettext("Done:") + ' ' + result.ok + ' ' + gettext("ok,") + ' ' + result.errors.length + ' ' + gettext("failed.") + '\n\n' + result.errors.join('\n'));
      }
      window.location.reload();
    })
    .catch(function(e) {
      if (runBtn) runBtn.disabled = false;
      if (bar) bar.classList.add('d-none');
      alert(gettext("Failed:") + ' ' + e);
    });
}

// Step 2a: verify every code (resolve name / current location), then render
// the review table + log-message inputs. A verify failure is shown inline on
// its own row rather than aborting the whole batch — the rest can still be
// logged.
function _gcfTbLogByCodeRunVerify(codes) {
  var status = document.getElementById('tb-logcode-status');
  var bar    = document.getElementById('tb-logcode-progress');
  var fill   = document.getElementById('tb-logcode-progress-bar');
  var runBtn = document.getElementById('tb-logcode-run');
  if (runBtn) runBtn.disabled = true;
  if (bar) bar.classList.remove('d-none');
  if (fill) fill.style.width = '0%';

  _GCF_TB_LOGCODE_REVIEW = [];

  _gcfTbLogByCodeSequentially(codes, function(code) {
    return _gcfTbPostJson(_tbUrl('urlVerify'), 'tracking_code=' + encodeURIComponent(code))
      .then(function(v) {
        _GCF_TB_LOGCODE_REVIEW.push({
          code: code,
          ref_code: v.ok ? (v.ref_code || '') : '',
          name: v.ok ? (v.name || '') : '',
          current_geocache_code: v.ok ? (v.current_geocache_code || '') : '',
          holder: v.ok ? v.holder : null,
          error: v.ok ? '' : (v.error || gettext("not recognised")),
        });
        return v;
      })
      .catch(function(e) {
        _GCF_TB_LOGCODE_REVIEW.push({code: code, error: String(e)});
        return {ok: false, error: String(e)};
      });
  }, status, fill)
    .then(function(result) {
      if (bar) bar.classList.add('d-none');
      if (status) status.textContent = '';
      if (result.ok === 0) {
        if (runBtn) runBtn.disabled = false;
        var err = document.getElementById('tb-logcode-error');
        err.textContent = gettext("None of the entered codes could be verified.");
        err.classList.remove('d-none');
        return;
      }
      _gcfTbLogByCodeRenderReview();
    })
    .catch(function(e) {
      if (runBtn) runBtn.disabled = false;
      if (bar) bar.classList.add('d-none');
      alert(gettext("Failed:") + ' ' + e);
    });
}

function _gcfTbLogByCodeRenderReview() {
  document.getElementById('tb-logcode-step1').classList.add('d-none');
  document.getElementById('tb-logcode-step2').classList.remove('d-none');
  document.getElementById('tb-logcode-back').classList.remove('d-none');
  var runBtn = document.getElementById('tb-logcode-run');
  if (runBtn) {
    runBtn.disabled = false;
    runBtn.textContent = _GCF_TB_LOGCODE_LABELS[_GCF_TB_LOGCODE_ACTION] || _GCF_TB_LOGCODE_ACTION;
  }

  var tbody = document.getElementById('tb-logcode-review-rows');
  tbody.innerHTML = '';
  _GCF_TB_LOGCODE_REVIEW.forEach(function(row, idx) {
    var tr = document.createElement('tr');
    if (row.error) {
      tr.innerHTML =
        '<td class="font-monospace small">' + _gcfTbLogCodeEsc(row.code) + '</td>' +
        '<td colspan="3" class="text-danger small">' + _gcfTbLogCodeEsc(row.error) + '</td>';
      tbody.appendChild(tr);
      return;
    }
    var loc = '<span class="text-muted">—</span>';
    if (row.holder && row.holder.username) {
      loc = gettext("Held by") + ' <strong>' + _gcfTbLogCodeEsc(row.holder.username) + '</strong>';
    } else if (row.current_geocache_code) {
      loc = '<a href="https://coord.info/' + _gcfTbLogCodeEsc(row.current_geocache_code) +
            '" target="_blank" rel="noopener" class="font-monospace">' +
            _gcfTbLogCodeEsc(row.current_geocache_code) + '</a>';
    }
    tr.innerHTML =
      '<td class="font-monospace small">' + _gcfTbLogCodeEsc(row.code) + '</td>' +
      '<td class="small">' + _gcfTbLogCodeEsc(row.ref_code) + ' — ' + _gcfTbLogCodeEsc(row.name) + '</td>' +
      '<td class="small">' + loc + '</td>' +
      '<td class="small text-nowrap">' +
        '<a href="https://www.geocaching.com/track/details.aspx?tracker=' + encodeURIComponent(row.code) +
        '" target="_blank" rel="noopener">' + gettext("GC page") + '</a>' +
      '</td>';
    tbody.appendChild(tr);

    var textTr = document.createElement('tr');
    textTr.setAttribute('data-tb-logcode-text-row', idx);
    textTr.innerHTML =
      '<td></td><td colspan="3" class="pb-2">' +
        '<textarea class="form-control form-control-sm" rows="2" data-tb-logcode-text="' + idx +
        '" placeholder="' + gettext("Log message") + '"></textarea>' +
      '</td>';
    tbody.appendChild(textTr);
  });

  gcfTbLogByCodeToggleSameText();
}

function gcfTbLogByCodeToggleSameText() {
  var same = document.getElementById('tb-logcode-same-text').checked;
  document.getElementById('tb-logcode-shared-text-wrap').classList.toggle('d-none', !same);
  document.querySelectorAll('[data-tb-logcode-text-row]').forEach(function(tr) {
    tr.classList.toggle('d-none', same);
  });
}

// Step 2b: actually submit the Discover/Retrieve logs — one call per
// successfully-verified row, using either the shared message or that row's
// own textarea.
function _gcfTbLogByCodeSubmitLogs() {
  var err = document.getElementById('tb-logcode-error');
  err.classList.add('d-none');
  err.textContent = '';

  var sameChecked = document.getElementById('tb-logcode-same-text').checked;
  var sharedText  = (document.getElementById('tb-logcode-shared-text').value || '').trim();

  var items = [];
  for (var i = 0; i < _GCF_TB_LOGCODE_REVIEW.length; i++) {
    var row = _GCF_TB_LOGCODE_REVIEW[i];
    if (row.error) continue;
    var text = sameChecked
      ? sharedText
      : ((document.querySelector('[data-tb-logcode-text="' + i + '"]') || {}).value || '').trim();
    if (!text) {
      err.textContent = gettext("Enter a log message for") + ' ' + row.code + '.';
      err.classList.remove('d-none');
      return;
    }
    items.push({code: row.code, text: text});
  }
  if (!items.length) {
    err.textContent = gettext("Nothing to submit — all codes failed verification.");
    err.classList.remove('d-none');
    return;
  }

  var status  = document.getElementById('tb-logcode-status');
  var bar     = document.getElementById('tb-logcode-progress');
  var fill    = document.getElementById('tb-logcode-progress-bar');
  var runBtn  = document.getElementById('tb-logcode-run');
  var backBtn = document.getElementById('tb-logcode-back');
  if (runBtn) runBtn.disabled = true;
  if (backBtn) backBtn.disabled = true;
  if (bar) bar.classList.remove('d-none');
  if (fill) fill.style.width = '0%';

  _gcfTbLogByCodeSequentially(items, function(item) {
    return _gcfTbPostJson(
      _tbUrl('urlLogByCode'),
      'tracking_code=' + encodeURIComponent(item.code) +
      '&action=' + encodeURIComponent(_GCF_TB_LOGCODE_ACTION) +
      '&text=' + encodeURIComponent(item.text)
    );
  }, status, fill)
    .then(function(result) {
      var modal = bootstrap.Modal.getInstance(document.getElementById('tbLogByCodeModal'));
      if (modal) modal.hide();
      if (result.errors.length) {
        alert(gettext("Done:") + ' ' + result.ok + ' ' + gettext("ok,") + ' ' + result.errors.length + ' ' + gettext("failed.") + '\n\n' + result.errors.join('\n'));
      }
      window.location.reload();
    })
    .catch(function(e) {
      if (runBtn) runBtn.disabled = false;
      if (backBtn) backBtn.disabled = false;
      if (bar) bar.classList.add('d-none');
      alert(gettext("Failed:") + ' ' + e);
    });
}

// Generic sequential runner — each item is either a bare code string or an
// object exposing `.code` (used by the log-submit step). Same batch pacing
// as _gcfTbSyncSequentially below (batch-size/pause vars are assigned before
// any button click can fire, so referencing them here is fine).
function _gcfTbLogByCodeSequentially(items, stepFn, status, fill) {
  var total  = items.length;
  var done   = 0;
  var ok     = 0;
  var errors = [];
  function step() {
    if (done >= total) return Promise.resolve({ok: ok, errors: errors, total: total});
    var item  = items[done];
    var label = (typeof item === 'string') ? item : item.code;
    if (status) status.textContent = (done + 1) + ' / ' + total + ' (' + label + ')…';
    return stepFn(item)
      .then(function(r) {
        if (r.ok) ok++;
        else errors.push(label + ': ' + (r.error || gettext("unknown error")));
      })
      .catch(function(err) { errors.push(label + ': ' + err); })
      .then(function() {
        done++;
        if (fill) fill.style.width = Math.round((done / total) * 100) + '%';
        if (done < total && done % _GCF_TB_BATCH_SIZE === 0) {
          return new Promise(function(res) { setTimeout(res, _GCF_TB_BATCH_PAUSE); }).then(step);
        }
        return step();
      });
  }
  return step();
}

function gcfTbResolveLocations() {
  var btn    = document.getElementById('sync-btn');
  var status = document.getElementById('sync-status');
  var bar    = document.getElementById('sync-progress');
  if (btn) { btn.disabled = true; }
  if (status) status.textContent = gettext("Resolving locations…");
  var body = new URLSearchParams(window.location.search).toString();
  _gcfTbPostJson(_tbUrl('urlResolveLocations'), body)
    .then(function(d) {
      if (btn) btn.disabled = false;
      if (bar) bar.classList.add('d-none');
      if (d.ok) {
        if (status) status.textContent = gettext("Resolved") + ' ' + d.resolved + ' ' + gettext("of") + ' ' + d.total_missing + ' ' + gettext("missing location(s).");
        if (d.resolved > 0) setTimeout(function() { window.location.reload(); }, 800);
      } else {
        if (status) status.textContent = gettext("Error:") + ' ' + (d.error || gettext("unknown"));
        alert(gettext("Resolve locations failed:") + ' ' + (d.error || gettext("unknown error")));
      }
    })
    .catch(function(e) {
      if (btn) btn.disabled = false;
      if (bar) bar.classList.add('d-none');
      alert(gettext("Resolve locations failed:") + ' ' + e);
    });
}

function gcfTbRefreshFiltered() {
  var btn    = document.getElementById('sync-btn');
  var status = document.getElementById('sync-status');
  var bar    = document.getElementById('sync-progress');
  var fill   = document.getElementById('sync-progress-bar');
  if (btn) { btn.disabled = true; btn.textContent = gettext("Syncing…"); }
  if (status) status.textContent = gettext("Finding filtered trackables…");
  if (bar) bar.classList.remove('d-none');
  if (fill) fill.style.width = '0%';

  var body = new URLSearchParams(window.location.search).toString();
  _gcfTbPostJson(_tbUrl('urlRefreshFilteredPlan'), body)
    .then(function(plan) {
      if (!plan.ok) throw new Error(plan.error || 'plan failed');
      var refs = plan.refs || [];
      if (!refs.length) {
        if (status) status.textContent = gettext("Nothing matches the current filter.");
        if (btn) { btn.disabled = false; btn.textContent = gettext("Sync"); }
        if (bar) bar.classList.add('d-none');
        return;
      }
      // Per-ref discovered/moved tag membership, resolved once by the plan
      // endpoint (GC-side history lookup) rather than per TB.
      var discoveredSet = {};
      (plan.discovered || []).forEach(function(r) { discoveredSet[r] = 1; });
      var movedSet = {};
      (plan.moved || []).forEach(function(r) { movedSet[r] = 1; });
      var tagForRef = function(ref) {
        var tags = [];
        if (discoveredSet[ref]) tags.push('discovered');
        if (movedSet[ref]) tags.push('moved');
        return tags.join(',');
      };
      return _gcfTbSyncSequentially(refs, tagForRef, gettext("Refresh filtered"), 0, status, fill);
    })
    .then(function(result) {
      if (!result) return;
      if (result.errors.length) {
        alert(gettext("Refresh done:") + ' ' + result.ok + ' ' + gettext("refreshed,") + ' ' + result.errors.length + ' ' + gettext("failed.") + '\n\n' + result.errors.join('\n'));
      }
      window.location.reload();
    })
    .catch(function(err) {
      if (status) status.textContent = '';
      if (bar) bar.classList.add('d-none');
      if (btn) { btn.disabled = false; btn.textContent = gettext("Sync"); }
      alert(gettext("Refresh failed:") + ' ' + err);
    });
}

// Sync TBs sequentially; pause briefly after each batch of N to give the GC
// API breathing room. Important for large discovered/moved lists which can
// run to thousands of refs for prolific cachers.
var _GCF_TB_BATCH_SIZE  = 10;
var _GCF_TB_BATCH_PAUSE = 300;  // ms

function _gcfTbSyncSequentially(refs, tagOrFn, scope, skipped, status, fill) {
  var total  = refs.length;
  var done   = 0;
  var ok     = 0;
  var errors = [];
  var suffix = skipped ? ' (' + skipped + ' skipped)' : '';

  function step() {
    if (done >= total) return Promise.resolve({ok: ok, errors: errors});
    var ref = refs[done];
    if (status) status.textContent = scope + ': ' + (done + 1) + ' / ' + total + ' (' + ref + ')' + suffix + '…';
    // tagOrFn is either a fixed tag string applied to every ref (existing
    // scope/by-code syncs) or a function(ref) → tag(s) for a per-ref value
    // (Refresh filtered, where discovered/moved membership varies per TB).
    var tag = (typeof tagOrFn === 'function') ? tagOrFn(ref) : tagOrFn;
    var body = 'ref=' + encodeURIComponent(ref);
    if (tag) body += '&tag=' + encodeURIComponent(tag);
    return _gcfTbPostJson(_tbUrl('urlSyncOne'), body)
      .then(function(r) {
        if (r.ok) ok++;
        else errors.push(ref + ': ' + (r.error || gettext("unknown error")));
      })
      .catch(function(err) { errors.push(ref + ': ' + err); })
      .then(function() {
        done++;
        if (fill) fill.style.width = Math.round((done / total) * 100) + '%';
        if (done < total && done % _GCF_TB_BATCH_SIZE === 0) {
          return new Promise(function(res) { setTimeout(res, _GCF_TB_BATCH_PAUSE); }).then(step);
        }
        return step();
      });
  }
  return step();
}
