// ── fx URL encoding (urlsafe-base64 of JSON, matches filter_expr.py) ─────────
// No zlib — keeps this a one-liner pair without bringing in pako.  Typical
// 4-leaf tree comes out around 300 bytes, well under URL limits.

function gcfEncodeFx(treeDict) {
  if (!treeDict || !treeDict.c || !treeDict.c.length) return '';
  var json = JSON.stringify(treeDict);
  var bytes = new TextEncoder().encode(json);
  var binary = '';
  for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  // btoa → standard base64 → URL-safe by swapping +/= for -_·
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function gcfDecodeFx(s) {
  if (!s) return { g: 'and', c: [] };
  try {
    var b64 = s.replace(/-/g, '+').replace(/_/g, '/');
    while (b64.length % 4) b64 += '=';
    var binary = atob(b64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch (e) {
    return { g: 'and', c: [] };
  }
}

// ── Toolbar quick-filter widgets → fx ────────────────────────────────────────
// The Type / Status / Size / Found / Country / Tag / Flag dropdowns no longer
// submit as URL params.  An htmx:configRequest hook (wired below) reads their
// current values, merges with the existing ?fx= tree (preserving non-toolbar
// conditions like alc.*, distance, attributes), and rewrites the request to
// send a single ?fx= plus the bona-fide non-fx params (q, ref, radius, sort,
// elevation, etc.).
//
// Most flag dropdown values translate to a boolean fx condition; the exotic
// values (ftf_possible, my_tb_inside, alc_loggable_at_center) keep flowing
// through the legacy ?flag= URL param since they don't have clean tree
// equivalents.

var _GCF_FLAG_TO_FIELD = {
  ftf:                'ftf',
  dnf:                'dnf',
  user_flag:          'user_flag',
  is_premium:         'is_premium',
  has_trackable:      'has_trackable',
  import_locked:      'import_locked',
  needs_maintenance:  'needs_maintenance',
  watch:              'watch',
  corrected_coords:   'has_corrected_coordinates',
};

var _GCF_EXOTIC_FLAGS = {
  ftf_possible: true,
  my_tb_inside: true,
  alc_loggable_at_center: true,
};

// Returns the list of toolbar-implied tree Conditions (no drop markers; the
// caller drops same-field conditions from the existing tree before adding).
function _gcfReadToolbarConditions() {
  var out = [];

  // Single-value enums: cache_type / status / size
  document.querySelectorAll('[data-fx-enum]').forEach(function (el) {
    var field = el.dataset.fxEnum;
    var value = el.value;
    if (value) {
      out.push({ f: field, op: 'in', v: [value] });
    }
  });

  // Found tri-state
  var foundEl = document.querySelector('[data-fx-found]');
  if (foundEl) {
    if (foundEl.value === '1') out.push({ f: 'found', op: 'is_true', v: true });
    else if (foundEl.value === '0') out.push({ f: 'found', op: 'is_false', v: true });
  }

  // Country (with __none__ support)
  var countryEl = document.querySelector('[data-fx-country]');
  if (countryEl) {
    var cv = countryEl.value;
    if (cv === '__none__') out.push({ f: 'country', op: 'is_none', v: true });
    else if (cv) out.push({ f: 'country', op: 'in', v: [cv] });
  }

  // Tag (with __none__ support)
  var tagEl = document.querySelector('[data-fx-tag]');
  if (tagEl) {
    var tv = tagEl.value;
    if (tv === '__none__') out.push({ f: 'tags', op: 'is_none', v: true });
    else if (tv) out.push({ f: 'tags', op: 'in', v: [tv] });
  }

  // Flag dropdown — translate simple flags to boolean conditions
  var flagEl = document.querySelector('[data-fx-flag]');
  if (flagEl && flagEl.value && !_GCF_EXOTIC_FLAGS[flagEl.value]) {
    var v = flagEl.value;
    if (v === 'alc_in_progress') {
      out.push({ f: 'alc', op: 'in_progress', v: true });
    } else if (_GCF_FLAG_TO_FIELD[v]) {
      out.push({ f: _GCF_FLAG_TO_FIELD[v], op: 'is_true', v: true });
    }
  }
  return out;
}

// Inverse of _gcfReadToolbarConditions(): set the toolbar quick-filter
// dropdowns (data-fx-*) to match the fx tree encoded in `url`. The widgets
// live outside the swapped table container, so after an in-place change a
// stale selection (e.g. a tag whose chip was just removed) would be re-applied
// the next time the filter-form submits — notably the viewport-scope moveend
// trigger (gcfOnMapMoveForScope), which would resurrect the chip on a map zoom.
// Sets values directly (no change events — those would themselves resubmit).
function _gcfSyncToolbarFromUrl(url) {
  var sp = new URLSearchParams((url || '').split('?')[1] || '');
  var tree = gcfDecodeFx(sp.get('fx') || '');
  var conds = (tree && tree.c) || [];

  function find(field, op) {
    for (var i = 0; i < conds.length; i++) {
      var c = conds[i];
      if (c && c.f === field && (op == null || c.op === op)) return c;
    }
    return null;
  }
  // Toolbar dropdowns are single-value; a multi-value condition can't be
  // represented, so leave the widget blank (the chip still carries it).
  function single(c) { return (c && Array.isArray(c.v) && c.v.length === 1) ? c.v[0] : ''; }

  document.querySelectorAll('[data-fx-enum]').forEach(function (el) {
    el.value = single(find(el.dataset.fxEnum, 'in'));
  });

  var foundEl = document.querySelector('[data-fx-found]');
  if (foundEl) {
    var fc = find('found');
    foundEl.value = fc ? (fc.op === 'is_true' ? '1' : fc.op === 'is_false' ? '0' : '') : '';
  }

  [['[data-fx-country]', 'country'], ['[data-fx-tag]', 'tags']].forEach(function (pair) {
    var el = document.querySelector(pair[0]);
    if (!el) return;
    var c = find(pair[1]);
    el.value = !c ? '' : (c.op === 'is_none' ? '__none__' : single(c));
  });

  var flagEl = document.querySelector('[data-fx-flag]');
  if (flagEl) {
    var flagParam = sp.get('flag') || '';
    if (flagParam && _GCF_EXOTIC_FLAGS[flagParam]) {
      flagEl.value = flagParam;  // exotic flags ride ?flag=, not the fx tree
    } else {
      var FIELD_TO_FLAG = {
        ftf: 'ftf', dnf: 'dnf', user_flag: 'user_flag', is_premium: 'is_premium',
        has_trackable: 'has_trackable', import_locked: 'import_locked',
        needs_maintenance: 'needs_maintenance', watch: 'watch',
        has_corrected_coordinates: 'corrected_coords',
      };
      var val = '';
      for (var i = 0; i < conds.length; i++) {
        var c = conds[i];
        if (!c) continue;
        if (c.f === 'alc' && c.op === 'in_progress') { val = 'alc_in_progress'; break; }
        if (FIELD_TO_FLAG[c.f] && c.op === 'is_true') { val = FIELD_TO_FLAG[c.f]; break; }
      }
      flagEl.value = val;
    }
  }

  if (typeof window.gcfUpdateFilterHighlights === 'function') window.gcfUpdateFilterHighlights();
}

// Set of (field) the toolbar owns — used to drop existing same-field
// conditions from the existing fx tree before merging in new ones.
function _gcfToolbarOwnedFields() {
  return new Set([
    'cache_type', 'status', 'size', 'found', 'country', 'tags', 'alc',
    'ftf', 'dnf', 'user_flag', 'is_premium', 'has_trackable',
    'import_locked', 'needs_maintenance', 'watch',
    'has_corrected_coordinates',
  ]);
}

function gcfToolbarConfigureRequest(evt) {
  var params = evt.detail.parameters;
  if (!params) return;

  // Decode current fx, drop conditions on fields the toolbar owns, append
  // fresh toolbar conditions.
  var currentFx = (new URLSearchParams(window.location.search)).get('fx') || '';
  var tree = gcfDecodeFx(currentFx);
  var op = tree.g || 'and';
  var ownedFields = _gcfToolbarOwnedFields();
  var kept = (tree.c || []).filter(function (c) {
    return c && c.f && !ownedFields.has(c.f);
  });
  var toolbarConds = _gcfReadToolbarConditions();
  var merged = { g: op, c: kept.concat(toolbarConds) };

  // Drop any leftover legacy param keys HTMX picked up from the form
  // (toolbar widgets no longer have ``name=`` so they shouldn't appear,
  // but defend in depth).
  ['type', 'status', 'size', 'found', 'flag', 'country', 'tag', 'fx'].forEach(function (k) {
    delete params[k];
  });

  // Set fx if non-empty
  var encoded = gcfEncodeFx(merged);
  if (encoded) params.fx = encoded;

  // Exotic flag values stay as ?flag= URL param.
  var flagEl = document.querySelector('[data-fx-flag]');
  if (flagEl && _GCF_EXOTIC_FLAGS[flagEl.value]) {
    params.flag = flagEl.value;
  }

  // Strip empty values so the URL doesn't accumulate noise (?q=&radius=&geo=…).
  // HTMX serialises every form field with a ``name=`` regardless of value;
  // without this the URL bar fills up with empty params on every submit.
  Object.keys(params).forEach(function (k) {
    var v = params[k];
    if (v === '' || v === null || v === undefined) delete params[k];
  });
}

// Wire on DOMContentLoaded so #filter-form exists before we attach.
document.addEventListener('DOMContentLoaded', function () {
  var form = document.getElementById('filter-form');
  if (form && window.htmx) {
    htmx.on(form, 'htmx:configRequest', gcfToolbarConfigureRequest);
  }
});

// Build a URL that matches what HTMX would currently send for the list:
// form-named fields plus the merged toolbar ?fx= and ?flag=.  Used by the
// global Now-Forging bar in base.html so toggling a scope checkbox keeps
// the active toolbar filters instead of dropping them on the way through
// /scope/?next=...
function gcfBuildCurrentListUrl() {
  var ff = document.getElementById('filter-form');
  if (!ff) return null;
  var params = new URLSearchParams(new FormData(ff));

  var currentFx = (new URLSearchParams(window.location.search)).get('fx') || '';
  var tree = gcfDecodeFx(currentFx);
  var owned = _gcfToolbarOwnedFields();
  var kept = (tree.c || []).filter(function (c) {
    return c && c.f && !owned.has(c.f);
  });
  var merged = { g: tree.g || 'and', c: kept.concat(_gcfReadToolbarConditions()) };
  var encoded = gcfEncodeFx(merged);

  params.delete('fx');
  params.delete('flag');
  if (encoded) params.set('fx', encoded);
  var flagEl = document.querySelector('[data-fx-flag]');
  if (flagEl && _GCF_EXOTIC_FLAGS[flagEl.value]) params.set('flag', flagEl.value);

  var drop = [];
  params.forEach(function (v, k) { if (v === '' || v === null) drop.push(k); });
  drop.forEach(function (k) { params.delete(k); });

  var qs = params.toString();
  return window.location.pathname + (qs ? '?' + qs : '');
}

// ── In-place list change (avoid full-page reload) ─────────────────────────────
// Apply a new list URL by swapping just #cache-table-container via htmx instead
// of navigating the whole document. On the list page in split/map layout a full
// reload tears down and rebuilds MapLibre (slow); the htmx swap keeps the map
// mounted and the existing htmx:afterSwap hook (map-layout.js) refreshes the
// markers for the new filter.
//
// opts.replace  → use history.replaceState instead of pushState (no new back
//                 entry; for view-pref toggles like scope that don't change the
//                 query string meaningfully).
// opts.forceMap → force a marker refresh after the swap even if the URL params
//                 are unchanged (for server-side session state like scope).
//
// Falls back to a full navigation when htmx or the table container is absent
// (e.g. a non-list page).
function gcfApplyListChange(url, opts) {
  opts = opts || {};
  // Capture the area-filter (geo) param before we touch history so we can tell
  // afterwards whether the drawn map shapes need re-syncing.
  var oldGeo = new URLSearchParams(window.location.search).get('geo') || '';
  if (url) sessionStorage.setItem('gcforge_list_url', url);

  var table = document.getElementById('cache-table-container');
  if (!window.htmx || !table) {
    window.location.href = url || window.location.pathname;
    return;
  }

  // Close any open Bootstrap modal (filter dialogs) so the in-place refresh
  // mirrors the old full-reload UX where the modal vanished with the page.
  if (window.bootstrap) {
    document.querySelectorAll('.modal.show').forEach(function (m) {
      var inst = bootstrap.Modal.getInstance(m);
      if (inst) inst.hide();
    });
  }

  // Reconcile the filter-form's hidden inputs to the new URL. They live outside
  // #cache-table-container, so an in-place swap leaves them stale — and the map
  // fetch (_gcfBuildFilterParams merges the form) plus the next toolbar submit
  // would otherwise re-apply a filter the user just removed (e.g. a dropped geo
  // area reappearing on the map).
  var form = document.getElementById('filter-form');
  if (form) {
    var np = new URLSearchParams((url || '').split('?')[1] || '');
    ['geo', 'where_name', 'where_sql', 'fx', 'f'].forEach(function (name) {
      var inp = form.querySelector('[name="' + name + '"]');
      if (inp) inp.value = np.get(name) || '';
    });
    // Also re-sync the nameless toolbar dropdowns (data-fx-*) so a stale
    // selection can't re-apply a just-removed filter on the next submit.
    _gcfSyncToolbarFromUrl(url);
  }

  if (url) {
    if (opts.replace) history.replaceState({}, '', url);
    else history.pushState({}, '', url);
  }

  htmx.ajax('GET', url, { target: '#cache-table-container', swap: 'innerHTML' })
    .then(function () {
      if (opts.forceMap && typeof window.gcfMapForceRefresh === 'function') {
        window.gcfMapForceRefresh();
      }
      // When the area filter changed, re-sync the drawn shapes (and the
      // "Pin as filter" panel) to the new ?geo= — otherwise a pinned shape
      // lingers on the map after its chip is removed (no full reload to rebuild it).
      var newGeo = new URLSearchParams((url || '').split('?')[1] || '').get('geo') || '';
      if (newGeo !== oldGeo && typeof window.gcfSyncGeoShapes === 'function') {
        window.gcfSyncGeoShapes();
      }
    });
}

// ── Chip removal ──────────────────────────────────────────────────────────────
// paramsCsv entries can be:
//   "paramname"        → delete entire param
//   "paramname=value"  → remove single value from a CSV param
function gcfRemoveFilter(paramsCsv, element) {
  var params = new URLSearchParams(window.location.search);
  paramsCsv.split(',').forEach(function(item) {
    item = item.trim();
    var eq = item.indexOf('=');
    if (eq !== -1) {
      var pname = item.substring(0, eq);
      var pval  = item.substring(eq + 1);
      var remaining = (params.get(pname) || '').split(',').filter(function(v) { return v.trim() && v.trim() !== pval; });
      if (remaining.length) params.set(pname, remaining.join(','));
      else params.delete(pname);
    } else {
      params.delete(item);
    }
  });
  params.delete('page');
  var url = window.location.pathname + '?' + params.toString();
  gcfApplyListChange(url);
}

// ── Tag filter helper ─────────────────────────────────────────────────────────
function filterByTag(tagName) {
  var container = document.getElementById('cache-table-container');
  var inner = container && container.firstElementChild;
  var currentTag = inner ? (new URLSearchParams(inner.dataset.params || '')).get('tag') || '' : '';
  var tagSelect = document.querySelector('#filter-form [name="tag"]');
  if (tagSelect) {
    tagSelect.value = (currentTag === tagName) ? '' : tagName;
    tagSelect.dispatchEvent(new Event('change', { bubbles: true }));
  }
};

// ── Sync sort/order from table partial ───────────────────────────────────────
(function () {
  var container = document.getElementById('cache-table-container');
  function syncFromTable() {
    var inner = container && container.firstElementChild;
    if (!inner) return;
    var sort   = inner.dataset.sort   || '';
    var order  = inner.dataset.order  || '';
    var params = inner.dataset.params || '';
    var sortInput  = document.querySelector('#filter-form [name="sort"]');
    var orderInput = document.querySelector('#filter-form [name="order"]');
    if (sortInput)  sortInput.value  = sort;
    if (orderInput) orderInput.value = order;
    var url = window.location.pathname + (params ? '?' + params : '');
    sessionStorage.setItem('gcforge_list_url', url);
  }
  syncFromTable();
  document.body.addEventListener('htmx:afterSwap', function (evt) {
    if (evt.detail.target.id !== 'cache-table-container') return;
    syncFromTable();
    // Push the updated params (including sort/order/filters) into the browser URL
    // so that a page reload preserves the current table state.
    var inner = container && container.firstElementChild;
    if (inner) {
      var params = inner.dataset.params || '';
      history.replaceState(null, '', window.location.pathname + (params ? '?' + params : ''));
    }
  });
})();

// ── Filter active highlights ──────────────────────────────────────────────────
(function () {
  var FILTER_EXCLUDE = ['ref', 'sort', 'order'];
  function updateFilterHighlights() {
    var form = document.getElementById('filter-form');
    if (!form) return;
    form.querySelectorAll('select, input:not([type="hidden"])').forEach(function (el) {
      if (FILTER_EXCLUDE.indexOf(el.name) !== -1) return;
      el.classList.toggle('filter-active', el.value !== '');
    });
  }
  updateFilterHighlights();
  // Exposed so _gcfSyncToolbarFromUrl can refresh highlights after setting
  // widget values programmatically (without firing a change that resubmits).
  window.gcfUpdateFilterHighlights = updateFilterHighlights;
  var filterForm = document.getElementById('filter-form');
  if (filterForm) {
    filterForm.addEventListener('change', updateFilterHighlights);
    filterForm.addEventListener('input', updateFilterHighlights);
  }
})();

// ── Tag → ref-point auto-select ───────────────────────────────────────────────
(function () {
  var tagSel = document.querySelector('#filter-form [name="tag"]');
  var refSel = document.querySelector('#filter-form [name="ref"]');
  if (!tagSel || !refSel) return;
  tagSel.addEventListener('change', function () {
    var opt = tagSel.options[tagSel.selectedIndex];
    var refId = opt ? opt.dataset.refId : '';
    if (refId) refSel.value = refId;
  });
})();

// ── Saved filters ─────────────────────────────────────────────────────────────
var _savedFilterSelectedPk = null;

function gcfLoadSavedFilter(selectEl) {
  var opt = selectEl.options[selectEl.selectedIndex];
  var deleteBtn = document.getElementById('saved-filter-delete-btn');
  if (!opt || !opt.value) {
    _savedFilterSelectedPk = null;
    if (deleteBtn) deleteBtn.classList.add('d-none');
    return;
  }
  _savedFilterSelectedPk = opt.value;
  var isBuiltin = opt.dataset.builtin === '1';
  if (deleteBtn) {
    if (isBuiltin) deleteBtn.classList.add('d-none');
    else deleteBtn.classList.remove('d-none');
  }
  var savedName = opt.dataset.savedName || '';
  if (!savedName) return;
  // SavedFilter is applied server-side via ?f=<name> and ANDs with any
  // existing ?fx= tree — keep ?fx= so saved filters compose with toolbar
  // filters rather than replacing them.
  var p = new URLSearchParams(window.location.search);
  p.delete('page');
  p.set('f', savedName);
  var url = window.location.pathname + '?' + p.toString();
  gcfApplyListChange(url);
};

function gcfDeleteSavedFilter() {
  if (!_savedFilterSelectedPk) return;
  if (!confirm(gettext('Delete this saved filter?'))) return;
  var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
  csrfToken = csrfToken ? csrfToken.value : '';
  // URL: /filters/<pk>/delete/.  geocaches.urls is mounted at root, no
  // /geocaches/ prefix.  Phase-4d's redesign of the saved-filter UI is
  // where a data-attribute or template-rendered URL would belong.
  fetch('/filters/' + _savedFilterSelectedPk + '/delete/', {
    method: 'POST',
    headers: {'X-CSRFToken': csrfToken, 'HX-Request': 'true'},
  }).then(function(r) { return r.text(); }).then(function(html) {
    var sel = document.getElementById('saved-filter-select');
    if (sel) {
      // Keep placeholder option, replace rest
      var placeholder = sel.options[0];
      sel.innerHTML = '';
      sel.appendChild(placeholder);
      var tmp = document.createElement('div');
      tmp.innerHTML = html;
      tmp.querySelectorAll('option').forEach(function(opt) { sel.appendChild(opt); });
    }
    _savedFilterSelectedPk = null;
    var deleteBtn = document.getElementById('saved-filter-delete-btn');
    if (deleteBtn) deleteBtn.classList.add('d-none');
  });
};

// ── Tree filter (v2 ?fx= / ?f=) ──────────────────────────────────────────────
// Power-user "Custom filter" modal — see _tree_filter_dialog.html.

function _gcfTreeShowError(msg) {
  var el = document.getElementById('tree-error');
  if (!el) return;
  if (msg) {
    el.textContent = msg;
    el.classList.remove('d-none');
  } else {
    el.textContent = '';
    el.classList.add('d-none');
  }
}

function _gcfTreeReadAndValidate() {
  var ta = document.getElementById('tree-json');
  if (!ta) return null;
  var raw = ta.value.trim();
  if (!raw) {
    _gcfTreeShowError(gettext('Tree JSON is empty.'));
    return null;
  }
  try {
    JSON.parse(raw);
  } catch (e) {
    _gcfTreeShowError(gettext('Invalid JSON: ') + e.message);
    return null;
  }
  _gcfTreeShowError('');
  return raw;
}

function gcfTreeFilterApply() {
  // Validate client-side first; server will validate again and respond 400
  // with a useful message if the tree shape is wrong.
  var raw = _gcfTreeReadAndValidate();
  if (raw === null) return;
  var form = document.getElementById('tree-filter-form');
  if (!form) return;
  var csrfEl = form.querySelector('[name=csrfmiddlewaretoken]');
  var csrfToken = csrfEl ? csrfEl.value : '';
  var fd = new FormData();
  fd.append('tree_json', raw);
  fd.append('csrfmiddlewaretoken', csrfToken);
  // Fetch with HX-Request header → server returns JSON instead of redirect /
  // 400-html, so error messages stay inline in the modal instead of taking
  // over the page.
  fetch(form.action, {
    method: 'POST',
    body: fd,
    headers: {'HX-Request': 'true', 'Accept': 'application/json'},
  }).then(function(r) {
    return r.json().then(function(data) {
      if (r.ok && data.redirect) {
        gcfApplyListChange(data.redirect);
        return;
      }
      _gcfTreeShowError(data.error || (gettext('Apply failed (status ') + r.status + ')'));
    });
  }).catch(function(err) {
    _gcfTreeShowError(gettext('Network error: ') + err);
  });
}

function gcfTreeFilterSaveAs() {
  var name = (document.getElementById('tree-save-name') || {}).value || '';
  name = name.trim();
  if (!name) {
    _gcfTreeShowError(gettext('Enter a name in the "Save as…" field.'));
    return;
  }
  var raw = _gcfTreeReadAndValidate();
  if (raw === null) return;

  var saveForm = document.getElementById('tree-save-form');
  var csrfEl = saveForm ? saveForm.querySelector('[name=csrfmiddlewaretoken]') : null;
  var csrfToken = csrfEl ? csrfEl.value : '';
  // Read the action URL off the template-rendered form so we don't hardcode
  // a path. `geocaches.urls` is mounted at root, not under /geocaches/.
  var saveUrl = saveForm ? saveForm.action : '/filters/tree/save/';
  var fd = new FormData();
  fd.append('name', name);
  fd.append('tree_json', raw);
  fd.append('csrfmiddlewaretoken', csrfToken);
  fetch(saveUrl, {
    method: 'POST',
    body: fd,
    headers: {'HX-Request': 'true'},
  }).then(function(r) {
    if (!r.ok) {
      return r.text().then(function(t) {
        _gcfTreeShowError(gettext('Save failed: ') + (t || r.status));
      });
    }
    // Add to the load dropdown if it isn't there yet, then close the modal.
    var sel = document.getElementById('tree-load-select');
    if (sel && !Array.prototype.some.call(sel.options, function(o){ return o.value === name; })) {
      var opt = document.createElement('option');
      opt.value = name; opt.textContent = name;
      sel.appendChild(opt);
    }
    if (sel) sel.value = name;
    _gcfTreeShowError('');
    // Show a brief inline confirmation
    var err = document.getElementById('tree-error');
    if (err) {
      err.classList.remove('d-none', 'text-danger');
      err.classList.add('text-success');
      err.textContent = gettext('Saved as ') + name + '.';
      setTimeout(function() {
        err.classList.add('d-none');
        err.classList.remove('text-success');
        err.classList.add('text-danger');
      }, 2000);
    }
  });
}

function gcfTreeFilterLoadSelected() {
  var sel = document.getElementById('tree-load-select');
  if (!sel || !sel.value) return;
  var p = new URLSearchParams(window.location.search);
  p.delete('page');
  p.delete('fx');  // ?f= overrides any inline ?fx=
  p.set('f', sel.value);
  var url = window.location.pathname + '?' + p.toString();
  gcfApplyListChange(url);
}

// ── Tabbed filter dialog (4d-i) ─────────────────────────────────────────────
// Builds a v2 filter-expression tree from form widgets across multiple tabs
// and POSTs it to the same /filters/tree/* endpoints the JSON-textarea modal
// uses.  Coexists with the JSON modal and the legacy "Filters" dialog.

function _gcfTabShowError(msg, isSuccess) {
  var el = document.getElementById('tabbed-tree-error');
  if (!el) return;
  if (!msg) {
    el.classList.add('d-none');
    el.textContent = '';
    return;
  }
  el.classList.remove('d-none', 'text-success', 'text-danger');
  el.classList.add(isSuccess ? 'text-success' : 'text-danger');
  el.textContent = msg;
}

function _gcfTabReadIntRange(form, minName, maxName) {
  var lo = form.elements[minName];
  var hi = form.elements[maxName];
  var loV = lo ? lo.value.trim() : '';
  var hiV = hi ? hi.value.trim() : '';
  if (!loV && !hiV) return null;
  var v = {};
  if (loV) v.gte = parseInt(loV, 10);
  if (hiV) v.lte = parseInt(hiV, 10);
  return v;
}

function _gcfTabReadFloatRange(form, minName, maxName) {
  var lo = form.elements[minName];
  var hi = form.elements[maxName];
  var loV = lo ? lo.value.trim() : '';
  var hiV = hi ? hi.value.trim() : '';
  if (!loV && !hiV) return null;
  var v = {};
  if (loV) v.gte = parseFloat(loV);
  if (hiV) v.lte = parseFloat(hiV);
  return v;
}

function _gcfTabReadSelectedValues(form, name) {
  var sel = form.elements[name];
  if (!sel) return [];
  if (sel.selectedOptions !== undefined) {
    return Array.prototype.map.call(sel.selectedOptions, function (o) { return o.value; })
      .filter(function (v) { return !!v; });
  }
  // RadioNodeList of checkboxes
  return Array.prototype.filter.call(sel, function (n) { return n.checked; })
    .map(function (n) { return n.value; });
}

function _gcfTabbedFormToTree() {
  var form = document.getElementById('tabbed-filter-form');
  if (!form) return { g: 'and', c: [] };
  var conditions = [];

  // General — text fields with an op selector.
  ['name', 'code', 'owner', 'placed_by', 'description'].forEach(function (prefix) {
    var txtEl = form.elements[prefix + '_text'];
    var opEl  = form.elements[prefix + '_op'];
    var val = txtEl ? txtEl.value.trim() : '';
    if (!val) return;
    conditions.push({ f: prefix, op: opEl ? opEl.value : 'contains', v: val });
  });

  // General — D/T/Fav numeric ranges.
  var d = _gcfTabReadFloatRange(form, 'difficulty_min', 'difficulty_max');
  if (d) conditions.push({ f: 'difficulty', op: 'between', v: d });
  var t = _gcfTabReadFloatRange(form, 'terrain_min', 'terrain_max');
  if (t) conditions.push({ f: 'terrain', op: 'between', v: t });
  var fp = _gcfTabReadIntRange(form, 'fav_points_min', 'fav_points_max');
  if (fp) conditions.push({ f: 'fav_points', op: 'between', v: fp });

  // Type / Size / Status — multi-checkbox enums.
  ['cache_type', 'size', 'status'].forEach(function (field) {
    var vals = _gcfTabReadSelectedValues(form, field);
    if (vals.length) conditions.push({ f: field, op: 'in', v: vals });
  });

  // Boolean tri-state selects: name="bool_<field>" → value yes/no/empty.
  form.querySelectorAll('select[name^="bool_"]').forEach(function (sel) {
    var v = sel.value;
    if (v !== 'true' && v !== 'false') return;
    conditions.push({
      f: sel.name.substring(5),
      op: v === 'true' ? 'is_true' : 'is_false',
      v: true,
    });
  });

  // Dates — per-field mode picker + value(s); optional negate wraps in NOT.
  ['hidden_date', 'last_found_date', 'found_date', 'dnf_date', 'updated_at', 'last_gpx_date', 'imported_at'].forEach(function (field) {
    var modeEl = form.elements[field + '_mode'];
    var mode = modeEl ? modeEl.value : '';
    if (!mode) return;
    var cond;
    if (mode === 'between') {
      var loEl = form.elements[field + '_from'];
      var hiEl = form.elements[field + '_to'];
      var loV = loEl ? loEl.value.trim() : '';
      var hiV = hiEl ? hiEl.value.trim() : '';
      if (!loV && !hiV) return;
      var v = {};
      if (loV) v.gte = loV;
      if (hiV) v.lte = hiV;
      cond = { f: field, op: 'between', v: v };
    } else if (mode === 'last_n_days') {
      var n = form.elements[field + '_n_days'];
      if (!n || !n.value.trim()) return;
      cond = { f: field, op: 'last_n_days', v: parseInt(n.value, 10) };
    } else {
      // in_past / in_future / this_week / this_month / this_year
      cond = { f: field, op: mode, v: true };
    }
    var negEl = form.elements[field + '_negate'];
    conditions.push(negEl && negEl.checked ? { g: 'not', c: [cond] } : cond);
  });

  // Location.
  var countries = _gcfTabReadSelectedValues(form, 'country');
  if (countries.length) conditions.push({ f: 'country', op: 'in', v: countries });
  ['state', 'county'].forEach(function (field) {
    var el = form.elements[field + '_text'];
    var v = el ? el.value.trim() : '';
    if (v) conditions.push({ f: field, op: 'in', v: [v] });
  });
  var dist = _gcfTabReadFloatRange(form, 'distance_min', 'distance_max');
  if (dist) conditions.push({ f: 'distance', op: 'between', v: dist });
  var bearings = _gcfTabReadSelectedValues(form, 'bearing');
  if (bearings.length) conditions.push({ f: 'bearing', op: 'direction_in', v: bearings });

  // Tags & Attributes.
  var tagsIn = _gcfTabReadSelectedValues(form, 'tags_include');
  if (tagsIn.length) conditions.push({ f: 'tags', op: 'in', v: tagsIn });
  var tagsOut = _gcfTabReadSelectedValues(form, 'tags_exclude');
  if (tagsOut.length) conditions.push({ f: 'tags', op: 'not_in', v: tagsOut });
  var attrsYes = [];
  var attrsNo = [];
  form.querySelectorAll('select[name^="attr_"]').forEach(function (sel) {
    var id = parseInt(sel.name.substring(5), 10);
    if (sel.value === 'yes') attrsYes.push(id);
    else if (sel.value === 'no') attrsNo.push(id);
  });
  if (attrsYes.length) conditions.push({ f: 'attributes', op: 'has_all', v: attrsYes });
  if (attrsNo.length)  conditions.push({ f: 'attributes', op: 'has_none', v: attrsNo });

  // Logs.
  var lastLogTypes = _gcfTabReadSelectedValues(form, 'last_log_type');
  if (lastLogTypes.length) conditions.push({ f: 'logs', op: 'last_log_type_in', v: lastLogTypes });
  var lastN = form.elements['last_n_are_dnf'];
  if (lastN && lastN.value.trim()) conditions.push({ f: 'logs', op: 'last_n_are_dnf', v: parseInt(lastN.value, 10) });
  var foundByUser = form.elements['logs_found_by_user'];
  if (foundByUser && foundByUser.value.trim()) {
    conditions.push({ f: 'logs', op: 'found_by_user', v: foundByUser.value.trim() });
  }
  var lcgte = form.elements['log_count_gte'];
  if (lcgte && lcgte.value.trim()) conditions.push({ f: 'logs', op: 'log_count_gte', v: parseInt(lcgte.value, 10) });

  // Adventures — boolean flags.
  form.querySelectorAll('input[name^="alc_bool_"]:checked').forEach(function (cb) {
    var op = cb.name.substring(9);
    var v = (op === 'loggable_from_ref') ? null : true;
    conditions.push({ f: 'alc', op: op, v: v });
  });
  var stRem = form.elements['alc_stages_remaining_gte'];
  if (stRem && stRem.value.trim()) {
    conditions.push({ f: 'alc', op: 'stages_remaining_gte', v: parseInt(stRem.value, 10) });
  }
  var stTotal = _gcfTabReadIntRange(form, 'alc_stages_total_min', 'alc_stages_total_max');
  if (stTotal) conditions.push({ f: 'alc', op: 'stages_total_between', v: stTotal });
  var gfRad = _gcfTabReadIntRange(form, 'alc_geofence_min', 'alc_geofence_max');
  if (gfRad) conditions.push({ f: 'alc', op: 'geofencing_radius_between', v: gfRad });
  var gfLat = form.elements['alc_geofence_lat'];
  var gfLon = form.elements['alc_geofence_lon'];
  if (gfLat && gfLon && gfLat.value.trim() && gfLon.value.trim()) {
    conditions.push({
      f: 'alc', op: 'geofence_contains_point',
      v: { lat: parseFloat(gfLat.value), lon: parseFloat(gfLon.value) },
    });
  }
  var advOwner = form.elements['alc_adventure_owner'];
  if (advOwner && advOwner.value.trim()) {
    conditions.push({ f: 'alc', op: 'adventure_owner_in', v: [advOwner.value.trim()] });
  }

  // Append passthrough — conditions from the original tree that couldn't be mapped to form fields.
  var pt = document.getElementById('tfd-passthrough');
  if (pt && pt.value) {
    try {
      var extra = JSON.parse(pt.value);
      if (Array.isArray(extra)) conditions = conditions.concat(extra);
    } catch (e) { /* ignore */ }
  }

  var rootOpEl = document.getElementById('tab-root-op');
  var rootOp = rootOpEl ? rootOpEl.value : 'and';
  return { g: rootOp, c: conditions };
}

function _gcfTabPost(url, fields) {
  var form = document.getElementById('tabbed-filter-form');
  var csrfEl = form ? form.querySelector('[name=csrfmiddlewaretoken]') : null;
  var csrfToken = csrfEl ? csrfEl.value : '';
  var fd = new FormData();
  Object.keys(fields).forEach(function (k) { fd.append(k, fields[k]); });
  fd.append('csrfmiddlewaretoken', csrfToken);
  return fetch(url, {
    method: 'POST',
    body: fd,
    headers: { 'HX-Request': 'true', 'Accept': 'application/json' },
  });
}

function gcfTabbedFilterApply() {
  var tree = _gcfTabbedFormToTree();
  if (!tree.c.length) {
    _gcfTabShowError(gettext('No conditions were set.'));
    return;
  }
  // The JSON-modal save form already has the right /filters/tree/apply/ URL.
  var applyUrl = (document.getElementById('tree-filter-form') || {}).action || '/filters/tree/apply/';
  _gcfTabPost(applyUrl, { tree_json: JSON.stringify(tree) })
    .then(function (r) {
      return r.json().then(function (data) {
        if (r.ok && data.redirect) {
          gcfApplyListChange(data.redirect);
          return;
        }
        _gcfTabShowError(data.error || (gettext('Apply failed (status ') + r.status + ')'));
      });
    }).catch(function (err) {
      _gcfTabShowError(gettext('Network error: ') + err);
    });
}

function gcfTabbedFilterSave() {
  var nameEl = document.getElementById('tabbed-save-name');
  var name = nameEl ? nameEl.value.trim() : '';
  if (!name) {
    _gcfTabShowError(gettext('Enter a name in the "Save as…" field.'));
    return;
  }
  var tree = _gcfTabbedFormToTree();
  if (!tree.c.length) {
    _gcfTabShowError(gettext('No conditions were set.'));
    return;
  }
  var saveUrl = (document.getElementById('tree-save-form') || {}).action || '/filters/tree/save/';
  _gcfTabPost(saveUrl, { name: name, tree_json: JSON.stringify(tree) })
    .then(function (r) {
      if (!r.ok) {
        return r.text().then(function (t) {
          _gcfTabShowError(gettext('Save failed: ') + (t || r.status));
        });
      }
      var sel = document.getElementById('tabbed-load-select');
      if (sel && !Array.prototype.some.call(sel.options, function (o) { return o.value === name; })) {
        var opt = document.createElement('option');
        opt.value = name; opt.textContent = name;
        sel.appendChild(opt);
      }
      if (sel) sel.value = name;
      _gcfTabShowError(gettext('Saved as ') + name + '.', true);
      setTimeout(function () { _gcfTabShowError(''); }, 2000);
    });
}

function gcfTabbedFilterLoadSelected() {
  var sel = document.getElementById('tabbed-load-select');
  if (!sel || !sel.value) return;
  var p = new URLSearchParams(window.location.search);
  p.delete('page');
  p.delete('fx');
  p.set('f', sel.value);
  var url = window.location.pathname + '?' + p.toString();
  gcfApplyListChange(url);
}

function gcfTabbedLoadSelectChanged() {
  var sel = document.getElementById('tabbed-load-select');
  var addBtn = document.getElementById('tabbed-add-btn');
  var delBtn = document.getElementById('tabbed-delete-btn');
  if (!sel) return;
  var opt = sel.options[sel.selectedIndex];
  var hasValue = !!sel.value;
  var isBuiltin = opt && opt.dataset && opt.dataset.builtin === 'true';
  if (addBtn) addBtn.disabled = !hasValue;
  if (delBtn) delBtn.disabled = !hasValue || isBuiltin;
}

function gcfTabbedFilterAddToCurrent() {
  var sel = document.getElementById('tabbed-load-select');
  if (!sel || !sel.value) { _gcfTabShowError(gettext('Select a saved filter first.')); return; }
  var scriptEl = document.getElementById('tfd-saved-trees');
  if (!scriptEl) return;
  var savedTrees;
  try { savedTrees = JSON.parse(scriptEl.textContent); } catch (e) { return; }
  var savedTree = savedTrees[sel.value];
  if (!savedTree || !Array.isArray(savedTree.c) || !savedTree.c.length) {
    _gcfTabShowError(gettext('No conditions to add.')); return;
  }
  var current = _gcfTabbedFormToTree();
  _gcfTabbedFormFromTree({ g: current.g, c: current.c.concat(savedTree.c) });
  _gcfTabbedUpdateBadges(_gcfTabbedTabCounts());
}

function gcfTabbedFilterDeleteSelected() {
  var sel = document.getElementById('tabbed-load-select');
  if (!sel || !sel.value) return;
  var opt = sel.options[sel.selectedIndex];
  if (!opt) return;
  var pk = opt.dataset && opt.dataset.pk;
  var isBuiltin = opt.dataset && opt.dataset.builtin === 'true';
  if (isBuiltin) { _gcfTabShowError(gettext('Built-in filters cannot be deleted.')); return; }
  if (!pk) return;
  var name = sel.value;
  if (!confirm(interpolate(gettext('Delete saved filter "%s"?'), [name]))) return;
  var csrfEl = document.querySelector('#tabbed-filter-form [name=csrfmiddlewaretoken]');
  var csrf = csrfEl ? csrfEl.value : '';
  fetch('/filters/' + pk + '/delete/', {
    method: 'POST',
    headers: { 'X-CSRFToken': csrf, 'HX-Request': 'true' },
  }).then(function (r) {
    if (!r.ok) { _gcfTabShowError(gettext('Delete failed.')); return; }
    opt.remove();
    sel.value = '';
    gcfTabbedLoadSelectChanged();
    _gcfTabShowError(interpolate(gettext('"%s" deleted.'), [name]), true);
    setTimeout(function () { _gcfTabShowError(''); }, 2000);
  });
}

function gcfTabbedFilterResetTab() {
  var activeLink = document.querySelector('#tabbedFilterDialog .nav-link.active');
  if (!activeLink) return;
  var tabId = activeLink.getAttribute('href');
  if (!tabId) return;
  var pane = document.querySelector(tabId);
  if (!pane) return;
  pane.querySelectorAll('input[type="text"], input[type="number"], input[type="date"]').forEach(function (el) { el.value = ''; });
  pane.querySelectorAll('input[type="checkbox"]').forEach(function (el) { el.checked = false; });
  pane.querySelectorAll('select').forEach(function (sel) {
    Array.prototype.forEach.call(sel.options, function (opt) { opt.selected = false; });
    if (!sel.multiple && sel.options.length) sel.selectedIndex = 0;
  });
  _gcfTabbedUpdateBadges(_gcfTabbedTabCounts());
}

function gcfTabbedFilterResetAll() {
  gcfApplyListChange(window.location.pathname);
}

// ── Tabbed filter dialog: pre-population from active fx tree ─────────────────

function _gcfTabbedResetForm() {
  var form = document.getElementById('tabbed-filter-form');
  if (!form) return;
  form.querySelectorAll('input[type="text"], input[type="number"], input[type="date"]').forEach(function (el) { el.value = ''; });
  form.querySelectorAll('input[type="checkbox"]').forEach(function (el) { el.checked = false; });
  form.querySelectorAll('select').forEach(function (sel) {
    Array.prototype.forEach.call(sel.options, function (opt) { opt.selected = false; });
    if (!sel.multiple && sel.options.length) sel.selectedIndex = 0;
  });
  var rootOp = document.getElementById('tab-root-op');
  if (rootOp) rootOp.value = 'and';
  var pt = document.getElementById('tfd-passthrough');
  if (pt) pt.value = '';
}

function _gcfTabbedFormFromTree(tree) {
  _gcfTabbedResetForm();
  var form = document.getElementById('tabbed-filter-form');
  if (!form || !tree) return;

  var rootOp = document.getElementById('tab-root-op');
  if (rootOp && (tree.g === 'and' || tree.g === 'or')) rootOp.value = tree.g;

  var passthrough = [];
  var TEXT_FIELDS = ['name', 'code', 'owner', 'placed_by', 'description'];
  var TEXT_OPS = ['contains', 'not_contains', 'equals', 'starts_with', 'in_list', 'not_in_list'];
  var BOOL_FIELDS = ['is_premium', 'has_trackable', 'needs_maintenance', 'found', 'ftf', 'dnf', 'user_flag', 'watch', 'has_corrected_coordinates', 'import_locked'];
  var DATE_FIELDS = ['hidden_date', 'last_found_date', 'found_date', 'dnf_date', 'updated_at', 'last_gpx_date', 'imported_at'];
  var DATE_MODES  = ['between', 'last_n_days', 'in_past', 'in_future', 'this_week', 'this_month', 'this_year'];
  var ALC_BOOL_OPS = ['is_adventure', 'is_stage', 'is_final', 'in_progress', 'loggable_from_ref', 'has_theme_image', 'is_highly_recommended'];

  function trySetDateCond(cond, negate) {
    if (DATE_FIELDS.indexOf(cond.f) === -1) return false;
    if (DATE_MODES.indexOf(cond.op) === -1) return false;
    var modeEl = form.elements[cond.f + '_mode'];
    if (!modeEl) return false;
    modeEl.value = cond.op;
    if (cond.op === 'between' && cond.v && typeof cond.v === 'object') {
      var loEl = form.elements[cond.f + '_from'];
      var hiEl = form.elements[cond.f + '_to'];
      if (loEl && cond.v.gte) loEl.value = cond.v.gte;
      if (hiEl && cond.v.lte) hiEl.value = cond.v.lte;
    } else if (cond.op === 'last_n_days') {
      var nEl = form.elements[cond.f + '_n_days'];
      if (nEl) nEl.value = cond.v;
    }
    if (negate) { var negEl = form.elements[cond.f + '_negate']; if (negEl) negEl.checked = true; }
    return true;
  }

  (tree.c || []).forEach(function (node) {
    if (!node) return;

    // NOT wrapper — only unmapped if the inner condition can't be represented
    if (node.g === 'not' && node.c && node.c.length === 1) {
      var inner = node.c[0];
      if (!inner.g && trySetDateCond(inner, true)) return;
      passthrough.push(node);
      return;
    }

    // Any other nested group goes to passthrough
    if (node.g) { passthrough.push(node); return; }

    var f = node.f, op = node.op, v = node.v;

    if (TEXT_FIELDS.indexOf(f) !== -1 && TEXT_OPS.indexOf(op) !== -1) {
      var opEl = form.elements[f + '_op'];
      var txEl = form.elements[f + '_text'];
      if (opEl) opEl.value = op;
      if (txEl) txEl.value = typeof v === 'string' ? v : '';
      return;
    }

    if ((f === 'difficulty' || f === 'terrain') && op === 'between' && v) {
      var minEl = form.elements[f + '_min']; var maxEl = form.elements[f + '_max'];
      if (minEl && v.gte != null) minEl.value = v.gte;
      if (maxEl && v.lte != null) maxEl.value = v.lte;
      return;
    }
    if (f === 'fav_points' && op === 'between' && v) {
      var fpMin = form.elements['fav_points_min']; var fpMax = form.elements['fav_points_max'];
      if (fpMin && v.gte != null) fpMin.value = v.gte;
      if (fpMax && v.lte != null) fpMax.value = v.lte;
      return;
    }

    if ((f === 'cache_type' || f === 'size' || f === 'status') && op === 'in' && Array.isArray(v)) {
      var vSet = {};
      v.forEach(function (val) { vSet[val] = true; });
      form.querySelectorAll('input[type="checkbox"][name="' + f + '"]').forEach(function (cb) {
        if (vSet[cb.value]) cb.checked = true;
      });
      return;
    }

    if (BOOL_FIELDS.indexOf(f) !== -1 && (op === 'is_true' || op === 'is_false')) {
      var bSel = form.elements['bool_' + f];
      if (bSel) bSel.value = op === 'is_true' ? 'true' : 'false';
      return;
    }

    if (trySetDateCond(node, false)) return;

    if (f === 'country' && op === 'in' && Array.isArray(v)) {
      var cSel = form.elements['country'];
      if (cSel) v.forEach(function (val) {
        Array.prototype.forEach.call(cSel.options, function (opt) { if (opt.value === val) opt.selected = true; });
      });
      return;
    }

    if ((f === 'state' || f === 'county') && op === 'in' && Array.isArray(v) && v.length) {
      var txEl2 = form.elements[f + '_text'];
      if (txEl2) txEl2.value = v[0];
      return;
    }

    if (f === 'distance' && op === 'between' && v) {
      var dMin = form.elements['distance_min']; var dMax = form.elements['distance_max'];
      if (dMin && v.gte != null) dMin.value = v.gte;
      if (dMax && v.lte != null) dMax.value = v.lte;
      return;
    }

    if (f === 'bearing' && op === 'direction_in' && Array.isArray(v)) {
      var bSet = {};
      v.forEach(function (dir) { bSet[dir] = true; });
      form.querySelectorAll('input[type="checkbox"][name="bearing"]').forEach(function (cb) {
        if (bSet[cb.value]) cb.checked = true;
      });
      return;
    }

    if (f === 'tags' && op === 'in' && Array.isArray(v)) {
      var tIn = form.elements['tags_include'];
      if (tIn) v.forEach(function (val) {
        Array.prototype.forEach.call(tIn.options, function (opt) { if (opt.value === val) opt.selected = true; });
      });
      return;
    }
    if (f === 'tags' && op === 'not_in' && Array.isArray(v)) {
      var tOut = form.elements['tags_exclude'];
      if (tOut) v.forEach(function (val) {
        Array.prototype.forEach.call(tOut.options, function (opt) { if (opt.value === val) opt.selected = true; });
      });
      return;
    }

    if (f === 'attributes' && op === 'has_all' && Array.isArray(v)) {
      v.forEach(function (id) { var s = form.elements['attr_' + id]; if (s) s.value = 'yes'; });
      return;
    }
    if (f === 'attributes' && op === 'has_none' && Array.isArray(v)) {
      v.forEach(function (id) { var s = form.elements['attr_' + id]; if (s) s.value = 'no'; });
      return;
    }

    if (f === 'logs' && op === 'last_log_type_in' && Array.isArray(v)) {
      var lSel = form.elements['last_log_type'];
      if (lSel) v.forEach(function (val) {
        Array.prototype.forEach.call(lSel.options, function (opt) { if (opt.value === val) opt.selected = true; });
      });
      return;
    }
    if (f === 'logs' && op === 'last_n_are_dnf') { var ldn = form.elements['last_n_are_dnf']; if (ldn) ldn.value = v; return; }
    if (f === 'logs' && op === 'found_by_user')   { var fbu = form.elements['logs_found_by_user']; if (fbu) fbu.value = v; return; }
    if (f === 'logs' && op === 'log_count_gte')   { var lcg = form.elements['log_count_gte']; if (lcg) lcg.value = v; return; }

    if (f === 'alc' && ALC_BOOL_OPS.indexOf(op) !== -1) {
      var alcCb = form.elements['alc_bool_' + op]; if (alcCb) alcCb.checked = true;
      return;
    }
    if (f === 'alc' && op === 'stages_remaining_gte') { var sr = form.elements['alc_stages_remaining_gte']; if (sr) sr.value = v; return; }
    if (f === 'alc' && op === 'stages_total_between' && v) {
      var stMin = form.elements['alc_stages_total_min']; var stMax = form.elements['alc_stages_total_max'];
      if (stMin && v.gte != null) stMin.value = v.gte;
      if (stMax && v.lte != null) stMax.value = v.lte;
      return;
    }
    if (f === 'alc' && op === 'geofencing_radius_between' && v) {
      var gMin = form.elements['alc_geofence_min']; var gMax = form.elements['alc_geofence_max'];
      if (gMin && v.gte != null) gMin.value = v.gte;
      if (gMax && v.lte != null) gMax.value = v.lte;
      return;
    }
    if (f === 'alc' && op === 'geofence_contains_point' && v) {
      var gfLat = form.elements['alc_geofence_lat']; var gfLon = form.elements['alc_geofence_lon'];
      if (gfLat) gfLat.value = v.lat; if (gfLon) gfLon.value = v.lon;
      return;
    }
    if (f === 'alc' && op === 'adventure_owner_in' && Array.isArray(v) && v.length) {
      var ao = form.elements['alc_adventure_owner']; if (ao) ao.value = v[0];
      return;
    }

    passthrough.push(node);
  });

  var pt = document.getElementById('tfd-passthrough');
  if (pt) pt.value = passthrough.length ? JSON.stringify(passthrough) : '';
}

function _gcfTabbedTabCounts() {
  var form = document.getElementById('tabbed-filter-form');
  if (!form) return {};
  var counts = { general: 0, tss: 0, dates: 0, location: 0, tagsattrs: 0, logs: 0, adv: 0 };

  ['name', 'code', 'owner', 'placed_by', 'description'].forEach(function (f) {
    var el = form.elements[f + '_text'];
    if (el && el.value.trim()) counts.general++;
  });
  if (_gcfTabReadFloatRange(form, 'difficulty_min', 'difficulty_max')) counts.general++;
  if (_gcfTabReadFloatRange(form, 'terrain_min', 'terrain_max')) counts.general++;
  if (_gcfTabReadIntRange(form, 'fav_points_min', 'fav_points_max')) counts.general++;

  ['cache_type', 'size', 'status'].forEach(function (f) {
    if (_gcfTabReadSelectedValues(form, f).length) counts.tss++;
  });
  form.querySelectorAll('select[name^="bool_"]').forEach(function (sel) {
    if (sel.value === 'true' || sel.value === 'false') counts.tss++;
  });

  ['hidden_date', 'last_found_date', 'found_date', 'dnf_date', 'updated_at', 'last_gpx_date', 'imported_at'].forEach(function (f) {
    var el = form.elements[f + '_mode'];
    if (el && el.value) counts.dates++;
  });

  if (_gcfTabReadSelectedValues(form, 'country').length) counts.location++;
  ['state', 'county'].forEach(function (f) { var el = form.elements[f + '_text']; if (el && el.value.trim()) counts.location++; });
  if (_gcfTabReadFloatRange(form, 'distance_min', 'distance_max')) counts.location++;
  if (_gcfTabReadSelectedValues(form, 'bearing').length) counts.location++;

  if (_gcfTabReadSelectedValues(form, 'tags_include').length) counts.tagsattrs++;
  if (_gcfTabReadSelectedValues(form, 'tags_exclude').length) counts.tagsattrs++;
  var hasAttr = false;
  form.querySelectorAll('select[name^="attr_"]').forEach(function (sel) { if (sel.value) hasAttr = true; });
  if (hasAttr) counts.tagsattrs++;

  if (_gcfTabReadSelectedValues(form, 'last_log_type').length) counts.logs++;
  var lastN = form.elements['last_n_are_dnf']; if (lastN && lastN.value.trim()) counts.logs++;
  var fbu = form.elements['logs_found_by_user']; if (fbu && fbu.value.trim()) counts.logs++;
  var lcg = form.elements['log_count_gte']; if (lcg && lcg.value.trim()) counts.logs++;

  form.querySelectorAll('input[name^="alc_bool_"]:checked').forEach(function () { counts.adv++; });
  var stRem = form.elements['alc_stages_remaining_gte']; if (stRem && stRem.value.trim()) counts.adv++;
  if (_gcfTabReadIntRange(form, 'alc_stages_total_min', 'alc_stages_total_max')) counts.adv++;
  if (_gcfTabReadIntRange(form, 'alc_geofence_min', 'alc_geofence_max')) counts.adv++;
  var gfLat = form.elements['alc_geofence_lat']; var gfLon = form.elements['alc_geofence_lon'];
  if (gfLat && gfLon && gfLat.value.trim() && gfLon.value.trim()) counts.adv++;
  var advOwner = form.elements['alc_adventure_owner']; if (advOwner && advOwner.value.trim()) counts.adv++;

  return counts;
}

function _gcfTabbedUpdateBadges(counts) {
  ['general', 'tss', 'dates', 'location', 'tagsattrs', 'logs', 'adv'].forEach(function (tab) {
    var badge = document.getElementById('ftab-badge-' + tab);
    if (!badge) return;
    var n = counts[tab] || 0;
    badge.textContent = n;
    badge.classList.toggle('d-none', n === 0);
  });
}

(function () {
  var tfd = document.getElementById('tabbedFilterDialog');
  if (!tfd) return;
  tfd.addEventListener('show.bs.modal', function () {
    var scriptEl = document.getElementById('tfd-current-tree');
    var tree = { g: 'and', c: [] };
    if (scriptEl) {
      try { tree = JSON.parse(scriptEl.textContent); } catch (e) { /* ignore */ }
    }
    _gcfTabbedFormFromTree(tree);
    _gcfTabbedUpdateBadges(_gcfTabbedTabCounts());
  });
  tfd.addEventListener('change', function () {
    _gcfTabbedUpdateBadges(_gcfTabbedTabCounts());
  });
})();

// ── Where-clause modal (replaces the legacy dialog's Where tab) ─────────────

function _gcfWcShowError(msg, isSuccess) {
  var el = document.getElementById('wc-error');
  if (!el) return;
  if (!msg) {
    el.classList.add('d-none');
    el.textContent = '';
    return;
  }
  el.classList.remove('d-none', 'text-danger', 'text-success');
  el.classList.add(isSuccess ? 'text-success' : 'text-danger');
  el.textContent = msg;
}

function gcfWcApply() {
  // Navigate to /?…&where_sql=<typed>&where_name=<selected name>
  // Other URL params preserved (ref, sort, fx, etc.).
  var taSql = document.getElementById('wc-where-sql');
  var hName = document.getElementById('wc-where-name');
  var sql = taSql ? taSql.value : '';
  var name = hName ? hName.value : '';
  var p = new URLSearchParams(window.location.search);
  p.delete('page');
  if (sql.trim()) {
    p.set('where_sql', sql);
  } else {
    p.delete('where_sql');
  }
  if (name) {
    p.set('where_name', name);
  } else {
    p.delete('where_name');
  }
  var url = window.location.pathname + '?' + p.toString();
  gcfApplyListChange(url);
}

function gcfWcClear() {
  var taSql = document.getElementById('wc-where-sql');
  var hName = document.getElementById('wc-where-name');
  var loadSel = document.getElementById('wc-load-select');
  var delBtn = document.getElementById('wc-delete-btn');
  if (taSql) taSql.value = '';
  if (hName) hName.value = '';
  if (loadSel) loadSel.value = '';
  if (delBtn) delBtn.style.display = 'none';
  _gcfWcShowError('');
}

function gcfWcLoadSelected(selEl) {
  var opt = selEl.options[selEl.selectedIndex];
  var delBtn = document.getElementById('wc-delete-btn');
  if (!opt || !opt.value) {
    if (delBtn) delBtn.style.display = 'none';
    return;
  }
  var taSql = document.getElementById('wc-where-sql');
  var hName = document.getElementById('wc-where-name');
  if (taSql) taSql.value = opt.dataset.sql || '';
  if (hName) hName.value = opt.dataset.name || '';
  if (delBtn) delBtn.style.display = '';
}

function gcfWcSave() {
  var nameEl = document.getElementById('wc-save-name');
  var taSql = document.getElementById('wc-where-sql');
  var name = nameEl ? nameEl.value.trim() : '';
  var sql = taSql ? taSql.value.trim() : '';
  if (!name) { _gcfWcShowError(gettext('Enter a name in the "Save as…" field.')); return; }
  if (!sql)  { _gcfWcShowError(gettext('SQL textarea is empty.')); return; }
  // Use the hidden POST form for proper CSRF.
  var saveForm = document.getElementById('wc-save-form');
  if (!saveForm) return;
  document.getElementById('wc-sf-name').value = name;
  document.getElementById('wc-sf-sql').value = sql;
  // POST via fetch so we don't navigate away.
  var csrfEl = saveForm.querySelector('[name=csrfmiddlewaretoken]');
  var csrf = csrfEl ? csrfEl.value : '';
  var fd = new FormData(saveForm);
  fetch(saveForm.action, {
    method: 'POST', body: fd,
    headers: {'HX-Request': 'true', 'X-CSRFToken': csrf},
  }).then(function (r) {
    if (!r.ok) {
      return r.text().then(function (t) { _gcfWcShowError(gettext('Save failed: ') + (t || r.status)); });
    }
    _gcfWcShowError(gettext('Saved as ') + name + '.', true);
    // Refresh the load dropdown so the new entry appears.
    return r.json().then(function (named) {
      var sel = document.getElementById('wc-load-select');
      if (!sel) return;
      // Rebuild the Named optgroup; leave Recent and the placeholder alone.
      Array.prototype.forEach.call(sel.querySelectorAll('optgroup'), function (g) {
        if (g.label === gettext('Named')) g.remove();
      });
      if (named && named.length) {
        var grp = document.createElement('optgroup');
        grp.label = gettext('Named');
        named.forEach(function (w) {
          var o = document.createElement('option');
          o.value = w.id;
          o.textContent = w.name;
          o.dataset.sql = w.sql;
          o.dataset.name = w.name;
          if (w.name === name) o.selected = true;
          grp.appendChild(o);
        });
        sel.insertBefore(grp, sel.querySelector('optgroup[label="' + gettext('Recent') + '"]') || null);
      }
      setTimeout(function () { _gcfWcShowError(''); }, 1500);
    });
  });
}

function gcfWcDeleteSelected() {
  var sel = document.getElementById('wc-load-select');
  if (!sel || !sel.value) return;
  if (!confirm(gettext('Delete this saved WHERE clause?'))) return;
  var csrfEl = document.querySelector('#wc-save-form [name=csrfmiddlewaretoken]');
  var csrf = csrfEl ? csrfEl.value : '';
  fetch('/where-clauses/' + sel.value + '/delete/', {
    method: 'POST', headers: {'X-CSRFToken': csrf, 'HX-Request': 'true'},
  }).then(function (r) {
    if (!r.ok) { _gcfWcShowError(gettext('Delete failed.')); return; }
    var pk = sel.value;
    var opt = sel.querySelector('option[value="' + pk + '"]');
    if (opt) opt.remove();
    sel.value = '';
    var delBtn = document.getElementById('wc-delete-btn');
    if (delBtn) delBtn.style.display = 'none';
    _gcfWcShowError(gettext('Deleted.'), true);
    setTimeout(function () { _gcfWcShowError(''); }, 1500);
  });
}

// ── Legacy advanced "Filters" dialog: REMOVED in 4d-ii's destructive
// cleanup.  Tabs dialog covers the form widgets via fx; the Where clause
// moved into the dedicated #whereClauseDialog modal; per-leaf chips on the
// chip bar handle removal.  The gcfSetVal / gcfSetSelectVal /
// gcfApplyDialogFilters / gcfDialogClear / gcfUpdateTabBadges /
// gcfSaveWhereClause / gcfLoadWhereClause / gcfLoadWhereById /
// gcfDeleteWhereClause / gcfCollectDialogParams / gcfDialogApply helpers
// and the #filterDialog show.bs.modal pre-populate handler all went with
// it.  ~370 lines of JS retired here.

// ── Locate me (browser Geolocation API) ──────────────────────────────────────
function gcfLocateMe() {
  var btn = document.getElementById('btn-locate-me');
  if (!navigator.geolocation) {
    alert(gettext('Geolocation is not supported by this browser.'));
    return;
  }
  var origText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '&#8987;';
  navigator.geolocation.getCurrentPosition(
    function(pos) {
      var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
      csrfToken = csrfToken ? csrfToken.value : '';
      fetch('/location/current/', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken},
        body: JSON.stringify({latitude: pos.coords.latitude, longitude: pos.coords.longitude}),
      }).then(function(r) { return r.json(); }).then(function(data) {
        btn.disabled = false;
        btn.innerHTML = origText;
        if (data.ok) {
          var sel = document.querySelector('select[name="ref"]');
          if (sel) {
            var found = false;
            for (var i = 0; i < sel.options.length; i++) {
              if (sel.options[i].value === String(data.id)) {
                sel.options[i].selected = true;
                found = true;
                break;
              }
            }
            if (!found) {
              var opt = document.createElement('option');
              opt.value = String(data.id);
              opt.text = gettext('Current Location');
              opt.selected = true;
              sel.appendChild(opt);
            }
          }
          // Submit the filter form to refresh distances with the new ref point
          var form = document.getElementById('filter-form');
          if (form) form.requestSubmit();
        } else {
          alert(interpolate(gettext('Failed to save location: %s'), [data.error || gettext('unknown error')]));
        }
      }).catch(function() {
        btn.disabled = false;
        btn.innerHTML = origText;
        alert(gettext('Failed to save location.'));
      });
    },
    function(err) {
      btn.disabled = false;
      btn.innerHTML = origText;
      var msg = gettext('Location unavailable.');
      if (err.code === 1) msg = gettext('Location permission denied.');
      else if (err.code === 3) msg = gettext('Location request timed out.');
      alert(msg);
    },
    {enableHighAccuracy: true, timeout: 10000}
  );
}


// ── Send to GPS ─────────────────────────────────────────────────────────────
var _gcfGpsDevicesLoaded = false;

function gcfLoadGpsDevices() {
  if (_gcfGpsDevicesLoaded) return;
  var dd = document.getElementById('send-to-gps-dropdown');
  if (!dd) return;
  var url = dd.dataset.recentUrl;
  if (!url) return;
  fetch(url).then(function (r) { return r.ok ? r.json() : null; }).then(function (data) {
    if (!data || !data.devices || !data.devices.length) return;
    var menu = dd.querySelector('.dropdown-menu');
    var separator = document.getElementById('gps-recent-separator');
    var header    = document.getElementById('gps-recent-header');
    separator.classList.remove('d-none');
    header.classList.remove('d-none');
    // Remove any previously appended recent items so reloads don't dupe
    Array.from(menu.querySelectorAll('li.gcf-gps-recent')).forEach(function (n) { n.remove(); });
    data.devices.forEach(function (dev) {
      var li = document.createElement('li');
      li.className = 'gcf-gps-recent';
      var a = document.createElement('a');
      a.className = 'dropdown-item';
      a.href = '#';
      a.title = dev.path + ' — last sent ' + dev.date;
      a.textContent = dev.label + ' (' + dev.path + ')';
      a.addEventListener('click', function (e) {
        e.preventDefault();
        gcfSendToGpsToFolder(dev.path);
      });
      li.appendChild(a);
      menu.appendChild(li);
    });
    _gcfGpsDevicesLoaded = true;
  });
}

// Drop the cache after each filter swap so new entries (added by send_to_gps)
// show up the next time the dropdown opens.
document.body.addEventListener('htmx:afterSwap', function () {
  _gcfGpsDevicesLoaded = false;
});

function _gcfFlashSendBtn(text, ms) {
  var dd = document.getElementById('send-to-gps-dropdown');
  if (!dd) return;
  var btn = dd.querySelector('.dropdown-toggle');
  if (!btn) return;
  var orig = btn.textContent;
  btn.textContent = text;
  setTimeout(function () { btn.textContent = orig; }, ms || 3000);
}

function gcfSendToGpsToFolder(folder) {
  var dd = document.getElementById('send-to-gps-dropdown');
  if (!dd) return;
  var url = dd.dataset.sendUrl;
  var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
  var body = new URLSearchParams();
  body.set('device_root', folder);
  fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-CSRFToken': csrfToken ? csrfToken.value : '',
    },
    body: body.toString(),
  }).then(function (r) {
    return r.json().then(function (data) {
      if (!r.ok || !data.ok) throw new Error(data.error || gettext('Send failed'));
      return data;
    });
  }).then(function (data) {
    _gcfFlashSendBtn(interpolate(gettext('✓ Sent %(count)s to %(model)s'), {count: data.count, model: data.model}, true));
    _gcfGpsDevicesLoaded = false;  // reload on next open so recent list refreshes
  }).catch(function (err) {
    alert(interpolate(gettext('Send to GPS: %s'), [err.message || String(err)]));
  });
}

function gcfDetectGps() {
  var dd = document.getElementById('send-to-gps-dropdown');
  if (!dd) return;
  var url = dd.dataset.detectUrl;
  if (!url) return;
  _gcfFlashSendBtn(gettext('Detecting…'), 60000);  // long flash; cleared by success/error
  fetch(url).then(function (r) { return r.json(); }).then(function (data) {
    var devices = (data && data.devices) || [];
    if (devices.length === 0) {
      _gcfFlashSendBtn(gettext('No device found'), 3000);
      alert(gettext('No Garmin device detected. Plug it in and ensure it is in mass-storage mode, or use "Browse for device folder…".'));
      return;
    }
    if (devices.length === 1) {
      gcfSendToGpsToFolder(devices[0].path);
      return;
    }
    // Multiple devices — ask the user to pick one
    var choices = devices.map(function (d, i) {
      return (i + 1) + '. ' + d.label + ' (' + d.path + ')';
    }).join('\n');
    var answer = prompt(
      gettext('Multiple Garmin devices detected. Type the number to send to:') + '\n\n' + choices,
      '1'
    );
    if (!answer) {
      _gcfFlashSendBtn(gettext('Cancelled'), 1500);
      return;
    }
    var idx = parseInt(answer, 10) - 1;
    if (isNaN(idx) || idx < 0 || idx >= devices.length) {
      alert(gettext('Invalid choice.'));
      return;
    }
    gcfSendToGpsToFolder(devices[idx].path);
  }).catch(function (err) {
    alert(interpolate(gettext('Detection failed: %s'), [err.message || String(err)]));
  });
}

function gcfSendToGpsBrowse() {
  var modalEl = document.getElementById('fileBrowserModal');
  if (!modalEl) return;

  var titleEl = document.querySelector('#fileBrowserModal .modal-title');
  var selectBtn = document.getElementById('fb-select-btn');
  var origTitle = titleEl.textContent;
  var origBtnText = selectBtn.textContent;
  var origConfirm = window.fbConfirmSelection;

  titleEl.textContent = gettext('Select GPS device root folder');
  selectBtn.textContent = gettext('Send to this folder');
  selectBtn.disabled = false;

  // Folder-select mode (mirrors gcfExportGpxBrowse)
  _fbTargetInputName = null;
  _fbExtensions = '';
  _fbMultiSelect = false;
  _fbFolderMode = true;
  _fbSelectedPath = null;
  _fbSelectedPaths = new Set();

  window.fbConfirmSelection = function () {
    if (!_fbCurrentDir) return;
    if (_fbModal) _fbModal.hide();
    gcfSendToGpsToFolder(_fbCurrentDir);
  };

  var origNavigate = window.fbNavigate;
  window.fbNavigate = function (path) {
    origNavigate(path);
    selectBtn.disabled = false;
  };

  function cleanup() {
    modalEl.removeEventListener('hidden.bs.modal', cleanup);
    window.fbConfirmSelection = origConfirm;
    window.fbNavigate = origNavigate;
    _fbFolderMode = false;
    titleEl.textContent = origTitle;
    selectBtn.textContent = origBtnText;
  }
  modalEl.addEventListener('hidden.bs.modal', cleanup);

  _fbModal = bootstrap.Modal.getOrCreateInstance(modalEl);
  _fbModal.show();
  fbNavigate('');
}

// ── Action target (scope) picker ─────────────────────────────────────────────
(function () {
  function _readMapBbox() {
    if (typeof gcfMap === 'undefined' || !gcfMap || !gcfMap.getBounds) return '';
    var b = gcfMap.getBounds();
    return [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()]
      .map(function (v) { return v.toFixed(6); }).join(',');
  }

  function _formInputs() {
    var form = document.getElementById('filter-form');
    if (!form) return null;
    return {
      form: form,
      target: form.querySelector('input[name="target"]'),
      vbox:   form.querySelector('input[name="vbox"]'),
    };
  }

  function _currentTarget() {
    var bar = document.getElementById('action-bar-container');
    var inner = bar && bar.firstElementChild;
    return inner ? (inner.dataset.target || 'filter') : 'filter';
  }

  function _applyBodyClass(target) {
    document.body.classList.toggle('gcf-target-viewport', target === 'viewport');
    document.body.classList.toggle('gcf-target-page',     target === 'page');
  }

  // Public — invoked from action bar onclick handlers
  window.gcfSetTarget = function (target) {
    var inputs = _formInputs();
    if (!inputs || !inputs.target) return;
    inputs.target.value = target;
    if (target === 'viewport') {
      var bbox = _readMapBbox();
      if (bbox) inputs.vbox.value = bbox;
    } else if (target === 'filter') {
      inputs.vbox.value = '';
    }
    _applyBodyClass(target);
    if (window.htmx) window.htmx.trigger(inputs.form, 'change');
  };

  // Click interceptor: when target=page, append &ids=… to action URLs at click
  // time so the URL doesn't carry IDs in the address bar persistently.
  document.body.addEventListener('click', function (evt) {
    var bar = document.getElementById('action-bar-container');
    if (!bar || !bar.contains(evt.target)) return;
    var anchor = evt.target.closest('a[href]');
    if (!anchor) return;
    var href = anchor.getAttribute('href');
    if (!href || href.charAt(0) === '#') return;
    if (_currentTarget() !== 'page') return;
    var inner = bar.firstElementChild;
    var ids = inner ? (inner.dataset.pageIds || '') : '';
    if (!ids) return;
    if (href.indexOf('ids=') === -1) {
      var sep = href.indexOf('?') === -1 ? '?' : '&';
      anchor.setAttribute('href', href + sep + 'ids=' + encodeURIComponent(ids));
    }
  }, true);

  // Initial body class + re-apply after OOB swap
  _applyBodyClass(_currentTarget());
  document.body.addEventListener('htmx:afterSwap', function () {
    _applyBodyClass(_currentTarget());
  });

  // Hook called by cache-map.js after every moveend.
  var _vboxDebounce = null;
  window.gcfOnMapMoveForScope = function () {
    var inputs = _formInputs();
    if (!inputs || !inputs.target || inputs.target.value !== 'viewport') return;
    clearTimeout(_vboxDebounce);
    _vboxDebounce = setTimeout(function () {
      var bbox = _readMapBbox();
      if (!bbox) return;
      inputs.vbox.value = bbox;
      if (window.htmx) window.htmx.trigger(inputs.form, 'change');
    }, 500);
  };
})();

// ── Refresh table when map-visibility changes elsewhere ──────────────────────
// Fired by static/js/map-context-menu.js after a right-click hide, and via
// HX-Trigger from set_map_visibility after a detail-page dropdown swap.
// Re-fetches the table so per-row eye badges (incl. AL stages hidden by a
// parent cascade) update without a full page reload.
(function () {
  function refreshTable() {
    if (typeof htmx === 'undefined') return;
    if (!document.getElementById('cache-table-container')) return;
    htmx.ajax('GET',
      window.location.pathname + window.location.search,
      { target: '#cache-table-container', swap: 'innerHTML' });
  }
  document.body.addEventListener('gcf-map-visibility-changed', refreshTable);
})();


