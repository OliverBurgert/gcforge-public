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
  sessionStorage.setItem('gcforge_list_url', url);
  window.location.href = url;
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
  var rawParams = opt.dataset.params || '{}';
  var paramsObj;
  try { paramsObj = JSON.parse(rawParams); } catch(e) { paramsObj = {}; }
  // Merge into current query string so existing filters are preserved
  var p = new URLSearchParams(window.location.search);
  p.delete('page');
  Object.keys(paramsObj).forEach(function(k) { if (paramsObj[k]) p.set(k, paramsObj[k]); });
  var url = window.location.pathname + '?' + p.toString();
  sessionStorage.setItem('gcforge_list_url', url);
  window.location.href = url;
};

function gcfDeleteSavedFilter() {
  if (!_savedFilterSelectedPk) return;
  if (!confirm('Delete this saved filter?')) return;
  var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
  csrfToken = csrfToken ? csrfToken.value : '';
  fetch('/geocaches/filters/' + _savedFilterSelectedPk + '/delete/', {
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

// ── Dialog helpers ────────────────────────────────────────────────────────────
function gcfSetVal(id, val) {
  var el = document.getElementById(id);
  if (el) el.value = val;
}
function gcfSetSelectVal(id, val) {
  var el = document.getElementById(id);
  if (el) {
    el.value = val;
    if (el.value !== val) el.value = el.options[0] ? el.options[0].value : '';
  }
};

// ── Dialog: pre-populate on show ──────────────────────────────────────────────
var dlg = document.getElementById('filterDialog');
if (dlg) {
  dlg.addEventListener('show.bs.modal', function() {
    var p = new URLSearchParams(window.location.search);

    // General tab
    gcfSetSelectVal('d-fname-op', p.get('fname_op') || 'contains');
    gcfSetVal('d-fname', p.get('fname') || '');
    gcfSetSelectVal('d-fcode-op', p.get('fcode_op') || 'contains');
    gcfSetVal('d-fcode', p.get('fcode') || '');
    gcfSetSelectVal('d-fowner-op', p.get('fowner_op') || 'contains');
    gcfSetVal('d-fowner', p.get('fowner') || '');
    gcfSetSelectVal('d-fplacedby-op', p.get('fplacedby_op') || 'contains');
    gcfSetVal('d-fplacedby', p.get('fplacedby') || '');
    gcfSetVal('d-ftext', p.get('ftext') || '');
    gcfSetVal('d-diff-min', p.get('diff_min') || '');
    gcfSetVal('d-diff-max', p.get('diff_max') || '');
    gcfSetVal('d-terr-min', p.get('terr_min') || '');
    gcfSetVal('d-terr-max', p.get('terr_max') || '');
    gcfSetVal('d-fav-min', p.get('fav_min') || '');
    gcfSetVal('d-fav-max', p.get('fav_max') || '');

    // Type/Size tab — sync from both quick-bar and multi-value params
    var types = (p.get('types') || (p.get('type') ? p.get('type') : '') || '').split(',').filter(Boolean);
    document.querySelectorAll('#d-types .form-check-input').forEach(function(cb) {
      cb.checked = types.includes(cb.value);
    });
    var sizes = (p.get('sizes') || (p.get('size') ? p.get('size') : '') || '').split(',').filter(Boolean);
    document.querySelectorAll('#d-sizes .form-check-input').forEach(function(cb) {
      cb.checked = sizes.includes(cb.value);
    });
    var statuses = (p.get('statuses') || (p.get('status') ? p.get('status') : '') || '').split(',').filter(Boolean);
    document.querySelectorAll('#d-statuses .form-check-input').forEach(function(cb) {
      cb.checked = statuses.includes(cb.value);
    });

    // Dates tab
    gcfSetVal('d-hidden-from', p.get('hidden_from') || '');
    gcfSetVal('d-hidden-to',   p.get('hidden_to')   || '');
    gcfSetVal('d-lf-from',     p.get('lf_from')     || '');
    gcfSetVal('d-lf-to',       p.get('lf_to')       || '');
    gcfSetVal('d-fd-from',     p.get('fd_from')      || '');
    gcfSetVal('d-fd-to',       p.get('fd_to')        || '');

    // Flags tab — sync from both quick-bar flag and multi-value flags
    var flags = (p.get('flags') || (p.get('flag') ? p.get('flag') : '') || '').split(',').filter(Boolean);
    document.querySelectorAll('.d-flag').forEach(function(cb) {
      cb.checked = flags.includes(cb.value);
    });
    var flagsNot = (p.get('flags_not') || '').split(',').filter(Boolean);
    document.querySelectorAll('.d-flag-not').forEach(function(cb) {
      cb.checked = flagsNot.includes(cb.value);
    });

    // Attributes tab
    var attrsYes = (p.get('attrs_yes') || '').split(',').filter(Boolean);
    var attrsNo  = (p.get('attrs_no')  || '').split(',').filter(Boolean);
    document.querySelectorAll('.d-attr-yes').forEach(function(cb) {
      cb.checked = attrsYes.includes(cb.value);
    });
    document.querySelectorAll('.d-attr-no').forEach(function(cb) {
      cb.checked = attrsNo.includes(cb.value);
    });

    // Location tab
    gcfSetVal('d-state',  p.get('state')  || '');
    gcfSetVal('d-county', p.get('county') || '');
    var bearing = (p.get('bearing') || '').split(',').filter(Boolean);
    document.querySelectorAll('.d-bearing-cb').forEach(function(cb) {
      cb.checked = bearing.includes(cb.value);
    });
    // Location excludes
    var countryExc = (p.get('country_exclude') || '').split(',').filter(Boolean);
    document.querySelectorAll('#d-country-exclude option').forEach(function(opt) {
      opt.selected = countryExc.includes(opt.value);
    });
    gcfSetVal('d-state-exclude', p.get('state_exclude') || '');
    gcfSetVal('d-county-exclude', p.get('county_exclude') || '');

    // Tags tab
    var tagsInc = (p.get('tags_include') || '').split(',').filter(Boolean);
    document.querySelectorAll('#d-tags-include .d-tag-inc').forEach(function(cb) {
      cb.checked = tagsInc.includes(cb.value);
    });
    var tagsExc = (p.get('tags_exclude') || '').split(',').filter(Boolean);
    document.querySelectorAll('#d-tags-exclude .d-tag-exc').forEach(function(cb) {
      cb.checked = tagsExc.includes(cb.value);
    });

    // Where tab
    gcfSetVal('d-where-sql', p.get('where_sql') || '');

    gcfUpdateTabBadges();
  });

  dlg.addEventListener('input', gcfUpdateTabBadges);
  dlg.addEventListener('change', gcfUpdateTabBadges);
}

// ── Dialog: collect params from dialog fields into URLSearchParams ─────────────
function gcfCollectDialogParams() {
  var p = new URLSearchParams(window.location.search);

  // Clear all dialog-managed params (and page)
  ['fname','fname_op','fcode','fcode_op','fowner','fowner_op','fplacedby','fplacedby_op',
   'ftext','types','sizes','statuses','diff_min','diff_max','terr_min','terr_max',
   'fav_min','fav_max','hidden_from','hidden_to','lf_from','lf_to','fd_from','fd_to',
   'flags','flags_not','tags_include','tags_exclude','attrs_yes','attrs_no','state','county',
   'country_exclude','state_exclude','county_exclude','bearing','where_sql','where_name',
   'page'].forEach(function(k) { p.delete(k); });

  function val(id) { var el = document.getElementById(id); return el ? el.value.trim() : ''; }
  function rawval(id) { var el = document.getElementById(id); return el ? el.value : ''; }
  function sel(id) { var el = document.getElementById(id); return el ? el.value : ''; }
  function checked(sel) { return Array.from(document.querySelectorAll(sel + ':checked')).map(function(cb) { return cb.value; }); }

  // General tab
  var fnameOp = sel('d-fname-op'); var fname = val('d-fname');
  if (fname || fnameOp === 'empty' || fnameOp === 'not_empty') { p.set('fname', fname); p.set('fname_op', fnameOp); }
  var fcodeOp = sel('d-fcode-op'); var fcode = val('d-fcode');
  if (fcode) { p.set('fcode', fcode); p.set('fcode_op', fcodeOp); }
  var fownerOp = sel('d-fowner-op'); var fowner = val('d-fowner');
  if (fowner || fownerOp === 'empty' || fownerOp === 'not_empty') { p.set('fowner', fowner); p.set('fowner_op', fownerOp); }
  var fplacedbyOp = sel('d-fplacedby-op'); var fplacedby = val('d-fplacedby');
  if (fplacedby || fplacedbyOp === 'empty' || fplacedbyOp === 'not_empty') { p.set('fplacedby', fplacedby); p.set('fplacedby_op', fplacedbyOp); }
  var ftext = val('d-ftext'); if (ftext) p.set('ftext', ftext);
  var diffMin = val('d-diff-min'); if (diffMin) p.set('diff_min', diffMin);
  var diffMax = val('d-diff-max'); if (diffMax) p.set('diff_max', diffMax);
  var terrMin = val('d-terr-min'); if (terrMin) p.set('terr_min', terrMin);
  var terrMax = val('d-terr-max'); if (terrMax) p.set('terr_max', terrMax);
  var favMin = val('d-fav-min'); if (favMin) p.set('fav_min', favMin);
  var favMax = val('d-fav-max'); if (favMax) p.set('fav_max', favMax);

  // Type/Size tab — sync single → quick-bar param, multiple → multi param
  var types = checked('#d-types .form-check-input');
  if (types.length === 1) { p.set('type', types[0]); } else if (types.length > 1) { p.set('types', types.join(',')); p.delete('type'); } else { p.delete('type'); }
  var sizes = checked('#d-sizes .form-check-input');
  if (sizes.length === 1) { p.set('size', sizes[0]); } else if (sizes.length > 1) { p.set('sizes', sizes.join(',')); p.delete('size'); } else { p.delete('size'); }
  var statuses = checked('#d-statuses .form-check-input');
  if (statuses.length === 1) { p.set('status', statuses[0]); } else if (statuses.length > 1) { p.set('statuses', statuses.join(',')); p.delete('status'); } else { p.delete('status'); }

  // Dates tab
  var hf = rawval('d-hidden-from'); if (hf) p.set('hidden_from', hf);
  var ht = rawval('d-hidden-to');   if (ht) p.set('hidden_to',   ht);
  var lf = rawval('d-lf-from');     if (lf) p.set('lf_from', lf);
  var lt = rawval('d-lf-to');       if (lt) p.set('lf_to',   lt);
  var ff = rawval('d-fd-from');     if (ff) p.set('fd_from', ff);
  var ft = rawval('d-fd-to');       if (ft) p.set('fd_to',   ft);

  // Flags tab — always use 'flags' (multi-value) so corrected_coords and all flags work uniformly
  p.delete('flag');
  var flags = checked('.d-flag');
  if (flags.length) p.set('flags', flags.join(','));
  var flagsNot = checked('.d-flag-not');
  if (flagsNot.length) p.set('flags_not', flagsNot.join(','));

  // Tags
  var tagsInc = checked('#d-tags-include .d-tag-inc');
  if (tagsInc.length) p.set('tags_include', tagsInc.join(','));
  else p.delete('tags_include');

  var tagsExc = checked('#d-tags-exclude .d-tag-exc');
  if (tagsExc.length) p.set('tags_exclude', tagsExc.join(','));
  else p.delete('tags_exclude');

  // Attributes tab
  var ay = checked('.d-attr-yes'); if (ay.length) p.set('attrs_yes', ay.join(','));
  var an = checked('.d-attr-no');  if (an.length) p.set('attrs_no',  an.join(','));

  // Location tab
  var state = val('d-state'); if (state) p.set('state', state);
  var county = val('d-county'); if (county) p.set('county', county);
  var bearings = checked('.d-bearing-cb'); if (bearings.length) p.set('bearing', bearings.join(','));

  var countryExc = [];
  document.querySelectorAll('#d-country-exclude option:checked').forEach(function(opt) {
    countryExc.push(opt.value);
  });
  if (countryExc.length) p.set('country_exclude', countryExc.join(','));
  else p.delete('country_exclude');

  var stateExc = val('d-state-exclude');
  if (stateExc) p.set('state_exclude', stateExc);
  else p.delete('state_exclude');

  var countyExc = val('d-county-exclude');
  if (countyExc) p.set('county_exclude', countyExc);
  else p.delete('county_exclude');

  // Where tab
  var wsql = val('d-where-sql'); if (wsql) p.set('where_sql', wsql);

  return p;
};

// ── Dialog: Apply ─────────────────────────────────────────────────────────────
function gcfDialogApply() {
  var p = gcfCollectDialogParams();
  var url = window.location.pathname + '?' + p.toString();

  var saveNameEl = document.getElementById('d-save-filter-name');
  var saveName = saveNameEl ? saveNameEl.value.trim() : '';

  if (saveName) {
    // Use a real form POST (reliable CSRF, synchronous navigation)
    var paramsObj = {};
    p.forEach(function(v, k) { paramsObj[k] = v; });
    document.getElementById('sf-name').value   = saveName;
    document.getElementById('sf-params').value = JSON.stringify(paramsObj);
    document.getElementById('sf-next').value   = url;
    document.getElementById('save-filter-form').submit();
  } else {
    sessionStorage.setItem('gcforge_list_url', url);
    window.location.href = url;
  }
};

// ── Dialog: Clear all advanced fields ────────────────────────────────────────
function gcfDialogClear() {
  ['d-fname','d-fcode','d-fowner','d-fplacedby','d-ftext',
   'd-diff-min','d-diff-max','d-terr-min','d-terr-max','d-fav-min','d-fav-max',
   'd-hidden-from','d-hidden-to','d-lf-from','d-lf-to','d-fd-from','d-fd-to',
   'd-state','d-county','d-state-exclude','d-county-exclude','d-where-sql'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.value = '';
  });
  document.querySelectorAll('#d-country-exclude option').forEach(function(opt) {
    opt.selected = false;
  });
  ['d-fname-op','d-fcode-op','d-fowner-op','d-fplacedby-op'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.value = 'contains';
  });
  document.querySelectorAll('#d-types .form-check-input, #d-sizes .form-check-input, #d-statuses .form-check-input, .d-flag, .d-flag-not, .d-attr-yes, .d-attr-no, .d-bearing-cb').forEach(function(cb) {
    cb.checked = false;
  });
  document.querySelectorAll('#d-tags-include .d-tag-inc, #d-tags-exclude .d-tag-exc')
    .forEach(function(cb) { cb.checked = false; });
  gcfUpdateTabBadges();
};

// ── Dialog: toggle all/none for checkbox groups ───────────────────────────────
function gcfToggleAll(groupId) {
  document.querySelectorAll('#' + groupId + ' .form-check-input').forEach(function(cb) { cb.checked = true; });
  gcfUpdateTabBadges();
}
function gcfToggleNone(groupId) {
  document.querySelectorAll('#' + groupId + ' .form-check-input').forEach(function(cb) { cb.checked = false; });
  gcfUpdateTabBadges();
}

// ── Dialog: tab badge counts ──────────────────────────────────────────────────
function gcfUpdateTabBadges() {
  function setbadge(id, count) {
    var el = document.getElementById(id);
    if (!el) return;
    el.textContent = count > 0 ? count : '';
    el.style.display = count > 0 ? '' : 'none';
  }
  // General
  var gen = 0;
  ['d-fname','d-fcode','d-fowner','d-fplacedby','d-ftext','d-diff-min','d-diff-max','d-terr-min','d-terr-max','d-fav-min','d-fav-max'].forEach(function(id) {
    var el = document.getElementById(id); if (el && el.value.trim()) gen++;
  });
  ['d-fname-op','d-fcode-op','d-fowner-op','d-fplacedby-op'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el && (el.value === 'empty' || el.value === 'not_empty')) gen++;
  });
  setbadge('badge-general', gen);

  // Type/Size
  var ts = document.querySelectorAll('#d-types .form-check-input:checked, #d-sizes .form-check-input:checked, #d-statuses .form-check-input:checked').length;
  setbadge('badge-typesize', ts);

  // Dates
  var dates = ['d-hidden-from','d-hidden-to','d-lf-from','d-lf-to','d-fd-from','d-fd-to'].filter(function(id) {
    var el = document.getElementById(id); return el && el.value;
  }).length;
  setbadge('badge-dates', dates);

  // Flags
  var flags = document.querySelectorAll('.d-flag:checked, .d-flag-not:checked').length;
  setbadge('badge-flags', flags);

  // Tags
  var tags = document.querySelectorAll('#d-tags-include .d-tag-inc:checked, #d-tags-exclude .d-tag-exc:checked').length;
  setbadge('badge-tags', tags);

  // Attributes
  var attrs = document.querySelectorAll('.d-attr-yes:checked, .d-attr-no:checked').length;
  setbadge('badge-attributes', attrs);

  // Location
  var loc = 0;
  ['d-state','d-county'].forEach(function(id) { var el = document.getElementById(id); if (el && el.value.trim()) loc++; });
  loc += document.querySelectorAll('.d-bearing-cb:checked').length;
  var _seEl = document.getElementById('d-state-exclude'); if (_seEl && _seEl.value.trim()) loc++;
  var _ceEl = document.getElementById('d-county-exclude'); if (_ceEl && _ceEl.value.trim()) loc++;
  if (document.querySelector('#d-country-exclude option:checked')) loc++;
  setbadge('badge-location', loc);

  // Where
  var wh = 0;
  var wEl = document.getElementById('d-where-sql'); if (wEl && wEl.value.trim()) wh = 1;
  setbadge('badge-where', wh);
};

// ── Where clause: load from select ────────────────────────────────────────────
function gcfLoadWhereClause(selectEl) {
  var opt = selectEl.options[selectEl.selectedIndex];
  if (!opt || !opt.value) {
    document.getElementById('d-where-delete-btn').style.display = 'none';
    return;
  }
  var sql = opt.dataset.sql || '';
  gcfSetVal('d-where-sql', sql);
  var isNamed = opt.dataset.name && opt.dataset.name.length > 0;
  var deleteBtn = document.getElementById('d-where-delete-btn');
  if (deleteBtn) deleteBtn.style.display = isNamed ? '' : 'none';
  gcfUpdateTabBadges();
};

function gcfLoadWhereById(pk, sql) {
  gcfSetVal('d-where-sql', sql);
  var sel = document.getElementById('d-where-load');
  if (sel) sel.value = pk;
  var deleteBtn = document.getElementById('d-where-delete-btn');
  if (deleteBtn) deleteBtn.style.display = '';
  gcfUpdateTabBadges();
};

// ── Where clause: save named ──────────────────────────────────────────────────
function gcfSaveWhereClause() {
  var name = document.getElementById('d-where-save-name').value.trim();
  var sql  = document.getElementById('d-where-sql').value.trim();
  if (!name || !sql) { alert('Enter both a name and a SQL clause.'); return; }
  var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
  csrfToken = csrfToken ? csrfToken.value : '';
  var body = new URLSearchParams();
  body.set('name', name); body.set('sql', sql);
  fetch('/geocaches/where-clauses/save/', {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrfToken, 'HX-Request': 'true'},
    body: body.toString(),
  }).then(function(r) { return r.text(); }).then(function() {
    document.getElementById('d-where-save-name').value = '';
    // Reload page to pick up new clause in select (simple approach)
    location.reload();
  });
};

// ── Where clause: delete named ───────────────────────────────────────────────
function gcfDeleteWhereClause() {
  var sel = document.getElementById('d-where-load');
  var opt = sel && sel.options[sel.selectedIndex];
  if (!opt || !opt.value) return;
  if (!confirm('Delete this where clause?')) return;
  var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
  csrfToken = csrfToken ? csrfToken.value : '';
  fetch('/geocaches/where-clauses/' + opt.value + '/delete/', {
    method: 'POST',
    headers: {'X-CSRFToken': csrfToken, 'HX-Request': 'true'},
  }).then(function() { location.reload(); });
};

// ── Locate me (browser Geolocation API) ──────────────────────────────────────
function gcfLocateMe() {
  var btn = document.getElementById('btn-locate-me');
  if (!navigator.geolocation) {
    alert('Geolocation is not supported by this browser.');
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
              opt.text = 'Current Location';
              opt.selected = true;
              sel.appendChild(opt);
            }
          }
          // Submit the filter form to refresh distances with the new ref point
          var form = document.getElementById('filter-form');
          if (form) form.requestSubmit();
        } else {
          alert('Failed to save location: ' + (data.error || 'unknown error'));
        }
      }).catch(function() {
        btn.disabled = false;
        btn.innerHTML = origText;
        alert('Failed to save location.');
      });
    },
    function(err) {
      btn.disabled = false;
      btn.innerHTML = origText;
      var msg = 'Location unavailable.';
      if (err.code === 1) msg = 'Location permission denied.';
      else if (err.code === 3) msg = 'Location request timed out.';
      alert(msg);
    },
    {enableHighAccuracy: true, timeout: 10000}
  );
}


// ── GPX export folder picker ─────────────────────────────────────────────────

var _gcfExportFoldersLoaded = false;

function gcfLoadExportFolders() {
  if (_gcfExportFoldersLoaded) return;
  var dd = document.getElementById('export-gpx-dropdown');
  if (!dd) return;
  var url = dd.dataset.recentUrl;
  fetch(url).then(function(r) { return r.json(); }).then(function(data) {
    var folders = data.folders || [];
    if (!folders.length) return;
    document.getElementById('export-recent-separator').classList.remove('d-none');
    document.getElementById('export-recent-header').classList.remove('d-none');
    var menu = dd.querySelector('.dropdown-menu');
    for (var i = 0; i < folders.length; i++) {
      var li = document.createElement('li');
      var a = document.createElement('a');
      a.className = 'dropdown-item small';
      a.href = '#';
      a.textContent = folders[i].path;
      a.title = 'Last used: ' + folders[i].date;
      (function(p) {
        a.onclick = function(e) { e.preventDefault(); gcfExportGpxToFolder(p); };
      })(folders[i].path);
      li.appendChild(a);
      menu.appendChild(li);
    }
    _gcfExportFoldersLoaded = true;
  });
}

function gcfExportGpxDownload() {
  var dd = document.getElementById('export-gpx-dropdown');
  if (dd) window.location.href = dd.dataset.exportUrl;
}

function gcfExportGpxToFolder(folder) {
  var dd = document.getElementById('export-gpx-dropdown');
  if (!dd) return;
  var url = dd.dataset.exportUrl + '&dest=' + encodeURIComponent(folder);
  fetch(url).then(function(r) {
    if (!r.ok) return r.json().then(function(d) { throw new Error(d.error || 'Export failed'); });
    return r.json();
  }).then(function(data) {
    // Brief flash on the button
    var btn = dd.querySelector('.dropdown-toggle');
    var orig = btn.textContent;
    btn.textContent = 'Saved!';
    setTimeout(function() { btn.textContent = orig; }, 2000);
    // Refresh recent folders on next open
    _gcfExportFoldersLoaded = false;
  }).catch(function(err) { alert(err.message || String(err)); });
}

function gcfExportGpxBrowse() {
  var modalEl = document.getElementById('fileBrowserModal');
  if (!modalEl) return;

  var titleEl = document.querySelector('#fileBrowserModal .modal-title');
  var selectBtn = document.getElementById('fb-select-btn');
  var origTitle = titleEl.textContent;
  var origBtnText = selectBtn.textContent;
  var origConfirm = window.fbConfirmSelection;

  titleEl.textContent = 'Select export folder';
  selectBtn.textContent = 'Select current folder';
  selectBtn.disabled = false;

  // Folder-select mode
  _fbTargetInputName = null;
  _fbExtensions = '';
  _fbMultiSelect = false;
  _fbFolderMode = true;
  _fbSelectedPath = null;
  _fbSelectedPaths = new Set();

  // Override confirm to export to the current directory
  window.fbConfirmSelection = function() {
    if (!_fbCurrentDir) return;
    if (_fbModal) _fbModal.hide();
    gcfExportGpxToFolder(_fbCurrentDir);
  };

  // Wrap fbNavigate to keep the select button enabled after each navigation
  var origNavigate = window.fbNavigate;
  window.fbNavigate = function(path) {
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

  // Navigate to last used folder or default
  var dd = document.getElementById('export-gpx-dropdown');
  var startDir = '';
  var recentItems = dd ? dd.querySelectorAll('#export-recent-separator ~ li a') : [];
  if (recentItems.length) startDir = recentItems[0].textContent;
  fbNavigate(startDir);
}

