// Dashboard page (templates/geocaches/dashboard.html): heatmap min-count
// filter, "Find missing in DB", calendar subscribe/add-missing, tab hash
// routing (outer dashboard tabs + Maps sub-tabs), and kicking off each
// HTMX-loaded tab body's own JS once it settles. Endpoint URLs come from
// the #dashboard-config data attributes.

var _dashCfg = document.getElementById('dashboard-config');
function _dashUrl(name) { return _dashCfg ? _dashCfg.dataset[name] : ''; }
function _dashCsrf() {
  return (document.querySelector('input[name=csrfmiddlewaretoken]') || {}).value || '';
}

// White-out heatmap cells below the chosen minimum (client-side, no reload).
function gcfDashMinChange(sectionId, minVal) {
  var min = parseInt(minVal, 10) || 1;
  var section = document.getElementById(sectionId);
  if (!section) return;
  section.querySelectorAll('td.gcf-cell[data-count]').forEach(function(td) {
    var count = parseInt(td.getAttribute('data-count'), 10);
    td.style.background = (min > 1 && count < min)
      ? '#fff'
      : (td.getAttribute('data-color') || '');
  });
}

// "Find missing in DB" — read the type from the per-table select (typeSelectId)
// or fall back to the global select, plus an optional per-table minimum.
// typeSelectId is omitted for the overall (case a) button.
function gcfDashMissing(which, minSelectId, typeSelectId) {
  var typeEl = typeSelectId
    ? document.getElementById(typeSelectId)
    : document.getElementById('dash-stat-type');
  var p = new URLSearchParams();
  p.set('which', which);
  if (typeEl && typeEl.value) p.set('stat_type', typeEl.value);
  if (minSelectId) {
    var ms = document.getElementById(minSelectId);
    if (ms) p.set('minimum', ms.value);
  }
  window.location.href = _dashUrl('urlMissing') + '?' + p.toString();
}

// Auto-dismiss the calendar confirmation toast ~7s after it's swapped in.
document.body.addEventListener('htmx:afterSwap', function (e) {
  if (!e.target || e.target.id !== 'dash-cal-agenda') return;
  var toast = e.target.querySelector('.gcf-cal-toast');
  if (!toast) return;
  setTimeout(function () {
    toast.classList.remove('show');
    setTimeout(function () { if (toast.parentNode) toast.remove(); }, 300);
  }, 7000);
});

// Copy the calendar subscription URL to the clipboard.
function gcfCopyCalUrl() {
  var el = document.getElementById('cal-feed-url');
  if (!el) return;
  el.select();
  if (navigator.clipboard) navigator.clipboard.writeText(el.value);
  else document.execCommand('copy');
}

// Switch to the Home (calendar) tab.
function gcfShowHome() {
  var btn = document.querySelector('#dashboard-tabs button[data-bs-target="#dash-home"]');
  if (btn) bootstrap.Tab.getOrCreateInstance(btn).show();
}

// "Add missing days to calendar" from a stat-table — read its type + minimum
// selects, POST, then reload the agenda and jump to the Home tab.
function gcfCalAddMissing(typeId, minId, alc) {
  var typeEl = document.getElementById(typeId);
  var minEl = minId ? document.getElementById(minId) : null;
  var body = new URLSearchParams();
  if (typeEl && typeEl.value) body.set('stat_type', typeEl.value);
  if (minEl) body.set('minimum', minEl.value);
  if (alc) body.set('alc', '1');
  body.set('days', '365');
  fetch(_dashUrl('urlCalendarAddMissing'), {
    method: 'POST',
    headers: {
      'X-CSRFToken': _dashCsrf(),
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: body.toString(),
  }).then(function () {
    htmx.trigger(document.body, 'calendar:reload');
    gcfShowHome();
  });
}

// Hash routing: outer dashboard tabs + inner Maps sub-tabs.
// Outer:  #home / #stats / #maps
// Inner:  #maps-world / #maps-countries / #maps-<iso> (e.g. #maps-de)
(function () {
  var OUTER = { '#home': '#dash-home', '#stats': '#dash-stats',
                '#alc': '#dash-alc', '#jasmer': '#dash-jasmer', '#360': '#dash-360',
                '#souvenirs': '#dash-souvenirs', '#treasures': '#dash-treasures' };

  function activateOuter(target) {
    var btn = document.querySelector('#dashboard-tabs button[data-bs-target="' + target + '"]');
    if (btn) bootstrap.Tab.getOrCreateInstance(btn).show();
  }
  function activateInner(name) {
    var btn = document.querySelector('#maps-subtabs button[data-maps-hash="' + name + '"]');
    if (btn) bootstrap.Tab.getOrCreateInstance(btn).show();
  }

  var hash = window.location.hash || '';
  // The Maps sub-tabs only exist once that tab's body loads (HTMX), so a
  // #maps-<iso> deep link is applied later in wireMapsSubtabs().
  var pendingMapsSub = null;
  if (OUTER[hash]) {
    activateOuter(OUTER[hash]);
  } else if (hash === '#maps' || hash.indexOf('#maps-') === 0) {
    activateOuter('#dash-maps');
    pendingMapsSub = hash === '#maps' ? null : hash.slice(6);  // "world"/"countries"/"de"
  }

  document.querySelectorAll('#dashboard-tabs button[data-bs-toggle="tab"]').forEach(function (b) {
    b.addEventListener('shown.bs.tab', function () {
      var target = b.getAttribute('data-bs-target');
      for (var h in OUTER) {
        if (OUTER[h] === target) { history.replaceState(null, '', h); return; }
      }
      if (target === '#dash-maps') {
        var act = document.querySelector('#maps-subtabs button.nav-link.active');
        var name = act ? act.getAttribute('data-maps-hash') : 'world';
        history.replaceState(null, '', '#maps-' + name);
      }
    });
  });

  // Wire Maps sub-tab hash persistence once the (deferred) body is in place,
  // then apply any #maps-<iso> deep link.
  function wireMapsSubtabs() {
    document.querySelectorAll('#maps-subtabs button[data-maps-hash]').forEach(function (b) {
      if (b._gcfHashWired) return;
      b._gcfHashWired = true;
      b.addEventListener('shown.bs.tab', function () {
        history.replaceState(null, '', '#maps-' + b.getAttribute('data-maps-hash'));
      });
    });
    if (pendingMapsSub) { activateInner(pendingMapsSub); pendingMapsSub = null; }
  }

  // Heavy tab bodies load in the background via HTMX; kick off their JS once
  // each one settles into the DOM.
  document.body.addEventListener('htmx:afterSettle', function (e) {
    var id = e.target && e.target.id;
    if (id === 'dash-stats-body') {
      if (window.gcfRenderStatsCharts) window.gcfRenderStatsCharts();
      if (window.gcfWireAttrSort) window.gcfWireAttrSort();
    } else if (id === 'dash-alc-body') {
      if (window.gcfRenderAlcCharts) window.gcfRenderAlcCharts();
    } else if (id === 'dash-maps-body') {
      wireMapsSubtabs();
      // MapLibre needs a visible container, so only build now if the Maps tab
      // is already on screen (the user opened it before the body finished
      // loading); otherwise dashboard-maps.js builds it on the tab's shown event.
      var pane = document.getElementById('dash-maps');
      if (pane && pane.classList.contains('active') && window.gcfStartDashboardMaps) {
        window.gcfStartDashboardMaps();
      }
    }
  });
})();
