// ── Manual location / elevation editor ──────────────────────────────────────
//
// On the cache detail page, opens via the small "Edit" link next to the
// Location and Elev lines.  Country / state / county dropdowns cascade,
// populated from the downloaded boundary files.  When a sub-tier isn't on
// disk for the selected country, the dropdown stays disabled with a note.
// Submission posts to cache_save_location which sets manual_location=True
// so the enrichment passes skip the cache.

(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    var modal = document.getElementById('cache-location-edit');
    if (!modal) return;

    var optsUrl = modal.dataset.optionsUrl;
    var initial = {
      country: modal.dataset.country || '',
      state: modal.dataset.state || '',
      county: modal.dataset.county || '',
      elevation: modal.dataset.elevation || '',
    };
    var selCountry = document.getElementById('cache-loc-country');
    var selState = document.getElementById('cache-loc-state');
    var selCounty = document.getElementById('cache-loc-county');
    var noteState = document.getElementById('cache-loc-state-note');
    var noteCounty = document.getElementById('cache-loc-county-note');
    var inpElevation = document.getElementById('cache-loc-elevation');

    function fetchOpts(country, state) {
      var p = new URLSearchParams();
      if (country) p.set('country', country);
      if (state) p.set('state', state);
      return fetch(optsUrl + (p.toString() ? '?' + p.toString() : ''))
        .then(function (r) { return r.json(); });
    }

    function emptyLabel() { return '— ' + gettext('none') + ' —'; }

    // Fill a <select> with values; preserves the current selection even when
    // the dataset doesn't contain it (e.g. county polys not downloaded).
    function fillSelect(sel, values, current, isObjList) {
      sel.innerHTML = '';
      var opt = document.createElement('option');
      opt.value = '';
      opt.textContent = emptyLabel();
      sel.appendChild(opt);
      var found = false;
      values.forEach(function (v) {
        var o = document.createElement('option');
        if (isObjList) {
          o.value = v.iso || '';
          o.textContent = v.name || v.iso || '';
        } else {
          o.value = v;
          o.textContent = v;
        }
        if (o.value === current) {
          o.selected = true;
          found = true;
        }
        sel.appendChild(o);
      });
      if (!found && current) {
        var o2 = document.createElement('option');
        o2.value = current;
        o2.textContent = current + ' (' + gettext('current') + ')';
        o2.selected = true;
        sel.appendChild(o2);
      }
    }

    function loadCountries() {
      return fetchOpts('', '').then(function (data) {
        fillSelect(selCountry, data.countries || [], initial.country, true);
      });
    }

    function loadStates(country) {
      if (!country) {
        fillSelect(selState, [], '', false);
        selState.disabled = true;
        noteState.textContent = '';
        return Promise.resolve();
      }
      return fetchOpts(country, '').then(function (data) {
        var values = data.states || [];
        if (values.length) {
          fillSelect(selState, values, initial.state, false);
          selState.disabled = false;
          noteState.textContent = '';
        } else {
          fillSelect(selState, [], initial.state, false);
          selState.disabled = true;
          noteState.textContent = gettext(
            'Region boundaries for this country are not downloaded.');
        }
      });
    }

    function loadCounties(country, state) {
      if (!country || !state) {
        fillSelect(selCounty, [], '', false);
        selCounty.disabled = true;
        noteCounty.textContent = '';
        return Promise.resolve();
      }
      return fetchOpts(country, state).then(function (data) {
        var values = data.counties || [];
        if (values.length) {
          fillSelect(selCounty, values, initial.county, false);
          selCounty.disabled = false;
          noteCounty.textContent = '';
        } else {
          fillSelect(selCounty, [], initial.county, false);
          selCounty.disabled = true;
          noteCounty.textContent = gettext(
            'County boundaries for this country/state are not downloaded.');
        }
      });
    }

    modal.addEventListener('show.bs.modal', function () {
      inpElevation.value = initial.elevation;
      loadCountries().then(function () {
        return loadStates(initial.country);
      }).then(function () {
        return loadCounties(initial.country, initial.state);
      });
    });

    selCountry.addEventListener('change', function () {
      initial.state = '';
      initial.county = '';
      loadStates(selCountry.value).then(function () {
        return loadCounties(selCountry.value, '');
      });
    });

    selState.addEventListener('change', function () {
      initial.county = '';
      loadCounties(selCountry.value, selState.value);
    });
  });
})();
