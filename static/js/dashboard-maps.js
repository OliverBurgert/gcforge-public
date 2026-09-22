// ── Dashboard → Maps tab: find-count choropleths ────────────────────────────
//
// Renders the configured statistics maps (world, per-continent) as MapLibre
// choropleths shading admin areas by find count.  Base style is intentionally
// minimal — a blue "sea" background with white land — so the heat colours read
// clearly.  Boundary polygons come from the bundled world GeoJSON; deeper levels
// (country regions, counties) are added in later phases.
//
// Maps are built lazily (IntersectionObserver) and only after the Maps tab is
// first shown, because MapLibre needs a sized, visible container.

(function () {
  'use strict';

  var VALID_CONTINENTS = ['Europe', 'North America', 'Asia',
                          'South America', 'Africa', 'Oceania'];
  var SEA_COLOR = '#aadcf5';
  var ZERO_COLOR = '#ffffff';

  var geojson = null;     // bundled FeatureCollection
  var counts = null;      // { iso_a2: find_count }
  var allCounts = null;   // { iso_a2: total_cache_count } (for find-less trips)
  var maps = [];          // built MapLibre instances (for resize)
  var started = false;
  var listUrl = '';       // list view, for the "unmapped" filter button
  var detailTemplate = ''; // cache-detail URL with a 'CODEXX' placeholder
  var districtTemplate = ''; // district-data URL, 'ISOXX'/'STATEXX' placeholders
  var _regionMenuEl = null; // open right-click region menu, if any

  function _readJson(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // green (low) → red (high), matching the stats heatmaps; white for zero.
  // Logarithmic so the low end (1–few finds) spreads across more of the scale
  // — find counts are very skewed (one home country dwarfs the rest).
  function _heat(count, max) {
    if (!count || max <= 0) return ZERO_COLOR;
    var t = max > 1 ? Math.log(count) / Math.log(max) : 1;
    var hue = 120 * (1 - t);
    return 'hsl(' + hue.toFixed(0) + ', 70%, 45%)';
  }

  // Copy a feature subset with `count` + `fillColor` baked into properties so
  // the fill layer can read them with a simple ['get', …] expression.
  function _decorate(features) {
    var max = 0;
    features.forEach(function (f) {
      var c = counts[f.properties.iso_a2] || 0;
      if (c > max) max = c;
    });
    var out = features.map(function (f) {
      var c = counts[f.properties.iso_a2] || 0;
      return {
        type: 'Feature',
        geometry: f.geometry,
        properties: {
          name: f.properties.name,
          iso: f.properties.iso_a2 || '',
          count: c,
          fillColor: _heat(c, max)
        }
      };
    });
    return { type: 'FeatureCollection', features: out };
  }

  // Outlier-aware fit: ignore features whose bbox-centroid sits far from the
  // median (e.g. Alaska + Hawaii vs CONUS, French Guiana vs mainland France) so
  // the country map zooms to the main landmass instead of spanning an empty ocean.
  function _smartBounds(features) {
    if (features.length <= 2) return _bounds(features);
    var centroids = features.map(function (f) {
      var b = _bounds([f]);
      return [(b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2];
    });
    function median(arr) {
      var s = arr.slice().sort(function (a, b) { return a - b; });
      return s[Math.floor(s.length / 2)];
    }
    var medLon = median(centroids.map(function (c) { return c[0]; }));
    var medLat = median(centroids.map(function (c) { return c[1]; }));
    var dists = centroids.map(function (c) {
      return Math.max(Math.abs(c[0] - medLon), Math.abs(c[1] - medLat));
    });
    var threshold = Math.max(median(dists) * 4, 5);
    var keep = features.filter(function (_, i) { return dists[i] <= threshold; });
    return keep.length >= 3 ? _bounds(keep) : _bounds(features);
  }

  function _bounds(features) {
    var minX = 180, minY = 90, maxX = -180, maxY = -90;
    function scan(c) {
      if (typeof c[0] === 'number') {
        if (c[0] < minX) minX = c[0];
        if (c[0] > maxX) maxX = c[0];
        if (c[1] < minY) minY = c[1];
        if (c[1] > maxY) maxY = c[1];
      } else {
        for (var i = 0; i < c.length; i++) scan(c[i]);
      }
    }
    features.forEach(function (f) { if (f.geometry) scan(f.geometry.coordinates); });
    return [[minX, minY], [maxX, maxY]];
  }

  function _buildMap(containerEl, data, fitBounds, center, zoom, opts) {
    opts = opts || {};
    var style = {
      version: 8,
      sources: {},
      layers: [{ id: 'sea', type: 'background', paint: { 'background-color': SEA_COLOR } }]
    };
    // Globe projection avoids Mercator's high-latitude stretching (Alaska,
    // northern Russia, etc.) so polygons read closer to their true shape.
    if (opts.projection) style.projection = { type: opts.projection };

    var map = new maplibregl.Map({
      container: containerEl,
      style: style,
      center: center || [10, 25],
      zoom: zoom || 1.2,
      attributionControl: false,
      dragRotate: false
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

    map.on('load', function () {
      map.addSource('areas', { type: 'geojson', data: data });
      map.addLayer({
        id: 'areas-fill', type: 'fill', source: 'areas',
        paint: { 'fill-color': ['get', 'fillColor'], 'fill-opacity': 0.9 }
      });
      map.addLayer({
        id: 'areas-line', type: 'line', source: 'areas',
        paint: { 'line-color': '#7a8a99', 'line-width': 0.4 }
      });

      if (fitBounds) {
        try { map.fitBounds(fitBounds, { padding: 18, animate: false }); } catch (e) { /* ignore */ }
      }

      var popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
      map.on('mousemove', 'areas-fill', function (e) {
        if (!e.features.length) return;
        map.getCanvas().style.cursor = 'pointer';
        var p = e.features[0].properties;
        popup.setLngLat(e.lngLat)
          .setHTML('<strong>' + _esc(p.name) + '</strong><br>' +
                   interpolate(gettext('%s finds'), [p.count]))
          .addTo(map);
      });
      map.on('mouseleave', 'areas-fill', function () {
        map.getCanvas().style.cursor = '';
        popup.remove();
      });

      // Right-click a polygon → found/unfound/all menu.  Region/county maps
      // carry filterKeys (state/county); the world map carries an ISO code.
      map.on('contextmenu', 'areas-fill', function (e) {
        if (!e.features.length) return;
        var props = e.features[0].properties;
        var urlFor, count;
        if (props.filterKeys) {
          var fk;
          try { fk = JSON.parse(props.filterKeys); } catch (err) { return; }
          if (!fk.length) return;
          urlFor = function (m) { return _regionFilterUrl(fk, m); };
          count = props.count;
        } else if (props.iso && _countryHasCaches(props.iso)) {
          var iso = props.iso;
          urlFor = function (m) { return _countryFilterUrl(iso, m); };
          count = counts[iso] || 0;
        } else {
          return;
        }
        if (e.originalEvent) e.originalEvent.preventDefault();
        _showFilterMenu(e.originalEvent.clientX, e.originalEvent.clientY,
                        urlFor, count);
      });
    });

    maps.push(map);
    return map;
  }

  // Attach a build function to a placeholder.  With an IntersectionObserver,
  // build when scrolled near; without one (used by per-pane sub-tab builds where
  // the pane is already visible), build immediately.
  function _lazy(observer, el, buildFn) {
    if (observer) {
      el._gcfBuild = buildFn;
      observer.observe(el);
    } else {
      buildFn();
    }
  }

  // Run `builder` exactly once: either now (if the tab is already active when
  // wired) or the first time the tab is shown.
  function _wireBuilder(tabBtn, builder) {
    var built = false;
    function go() { if (built) return; built = true; builder(); }
    tabBtn.addEventListener('shown.bs.tab', go);
    if (tabBtn.classList.contains('active')) go();
  }

  // Countries (within `features`) that have finds, ranked desc.
  function _rankRows(features) {
    var rows = [];
    features.forEach(function (f) {
      var iso = f.properties.iso_a2;
      var c = (iso && counts[iso]) || 0;
      if (c > 0) rows.push({ iso: iso, name: f.properties.name, count: c });
    });
    rows.sort(function (a, b) { return b.count - a.count; });
    return rows;
  }

  function _countryHasCaches(iso) {
    return (counts[iso] || 0) > 0 || (allCounts[iso] || 0) > 0;
  }

  // Ranked table beside a map: flag · country · count · share-of-this-map %.
  function _countryTable(rows, total) {
    var wrap = document.createElement('div');
    wrap.className = 'gcf-dash-table';

    var head = document.createElement('div');
    head.className = 'gcf-dash-table-head';
    head.textContent = interpolate(
      ngettext('Found in %s country:', 'Found in %s countries:', rows.length),
      [rows.length]);
    wrap.appendChild(head);

    var tbl = document.createElement('table');
    tbl.className = 'table table-sm gcf-dash-country-table mb-0';
    var tbody = document.createElement('tbody');
    rows.forEach(function (r) {
      var pct = total ? (r.count / total * 100) : 0;
      var flag = r.iso ? '<span class="fi fi-' + r.iso.toLowerCase() + '"></span>' : '';
      var url = _countryFilterUrl(r.iso, 'found');
      var nameCell = (url && r.iso)
        ? '<a href="' + _esc(url) + '" title="' +
          _esc(gettext('Show found caches (right-click for found/unfound/all)')) +
          '">' + _esc(r.name) + '</a>'
        : _esc(r.name);
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td class="gcf-flag-cell">' + flag + '</td>' +
        '<td>' + nameCell + '</td>' +
        '<td class="text-end">' + r.count + '</td>' +
        '<td class="text-end text-muted">' + pct.toFixed(2) + '%</td>';
      if (r.iso) {
        tr.addEventListener('contextmenu', function (e) {
          e.preventDefault();
          _showFilterMenu(e.clientX, e.clientY, function (m) {
            return _countryFilterUrl(r.iso, m);
          }, r.count);
        });
      }
      tbody.appendChild(tr);
    });
    tbl.appendChild(tbody);
    wrap.appendChild(tbl);
    return wrap;
  }

  function _legend(max) {
    var el = document.createElement('div');
    el.className = 'gcf-dash-legend';
    el.innerHTML = '<span>1</span><span class="gcf-dash-legend-bar"></span>' +
                   '<span>' + max + '</span>';
    return el;
  }

  // One choropleth + its ranked table, side by side.  The map builds lazily.
  function _buildSection(parent, features, fit, center, zoom, observer, opts) {
    var rows = _rankRows(features);
    var total = rows.reduce(function (s, r) { return s + r.count; }, 0);
    var max = rows.length ? rows[0].count : 0;

    var row = document.createElement('div');
    row.className = 'gcf-dash-maprow';

    var mapCol = document.createElement('div');
    mapCol.className = 'gcf-dash-mapcol';
    var mapEl = document.createElement('div');
    mapEl.className = 'gcf-dash-map';
    mapCol.appendChild(mapEl);
    if (max > 0) mapCol.appendChild(_legend(max));
    row.appendChild(mapCol);

    row.appendChild(_countryTable(rows, total));
    parent.appendChild(row);

    _lazy(observer, mapEl, function () {
      var map = _buildMap(mapEl, _decorate(features), fit, center, zoom, opts);
      // Settle the flex layout before MapLibre measures the canvas.
      setTimeout(function () { try { map.resize(); } catch (e) { /* ignore */ } }, 50);
    });
  }

  function _renderWorld(section, observer) {
    var head = document.createElement('h6');
    head.textContent = gettext('World — countries');
    section.appendChild(head);
    _buildSection(section, geojson.features, null, [10, 25], 1.2, observer);
  }

  function _renderContinents(section, observer) {
    // Sum finds per continent to decide which maps to show.
    var byCont = {};
    geojson.features.forEach(function (f) {
      var cont = f.properties.continent;
      var c = counts[f.properties.iso_a2] || 0;
      if (c > 0 && VALID_CONTINENTS.indexOf(cont) !== -1) {
        byCont[cont] = (byCont[cont] || 0) + c;
      }
    });
    var present = VALID_CONTINENTS.filter(function (c) { return byCont[c]; });
    if (!present.length) {
      var none = document.createElement('p');
      none.className = 'text-muted small';
      none.textContent = gettext('No continent has finds yet.');
      section.appendChild(none);
      return;
    }
    present.forEach(function (cont) {
      var feats = geojson.features.filter(function (f) {
        return f.properties.continent === cont;
      });
      var head = document.createElement('h6');
      head.className = 'mt-3';
      head.textContent = cont;
      section.appendChild(head);
      _buildSection(section, feats, _smartBounds(feats), null, null, observer,
                    { projection: 'globe' });
    });
  }

  // Region table: flag · region name · count · % (flag column blank when
  // the boundary download didn't find a region flag on flagcdn).
  function _regionTable(features, meta) {
    var rows = features
      .filter(function (f) { return f.properties.count > 0; })
      .map(function (f) {
        return {
          name: f.properties.name,
          count: f.properties.count,
          flag: f.properties.flag || '',
          filterKeys: f.properties.filter_keys || []
        };
      });
    rows.sort(function (a, b) { return b.count - a.count; });

    var wrap = document.createElement('div');
    wrap.className = 'gcf-dash-table';

    var head = document.createElement('div');
    head.className = 'gcf-dash-table-head';
    head.textContent = interpolate(
      ngettext('Found in %s region:', 'Found in %s regions:', rows.length),
      [rows.length]);
    wrap.appendChild(head);

    var tbl = document.createElement('table');
    tbl.className = 'table table-sm gcf-dash-country-table mb-0';
    var tbody = document.createElement('tbody');
    rows.forEach(function (r) {
      var pct = meta.total ? (r.count / meta.total * 100) : 0;
      var flag = r.flag
        ? '<img src="' + _esc(r.flag) + '" alt="" class="gcf-region-flag">'
        : '';
      // Link the name to the list view filtered to this region's *found*
      // caches (matching the count); right-click for found/unfound/all.  The
      // stored state/county values differ from the boundary's display name.
      var url = _regionFilterUrl(r.filterKeys, 'found');
      var nameCell = url
        ? '<a href="' + _esc(url) + '" title="' +
          _esc(gettext('Show found caches (right-click for found/unfound/all)')) +
          '">' + _esc(r.name) + '</a>'
        : _esc(r.name);
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td class="gcf-flag-cell">' + flag + '</td>' +
        '<td>' + nameCell + '</td>' +
        '<td class="text-end">' + r.count + '</td>' +
        '<td class="text-end text-muted">' + pct.toFixed(2) + '%</td>';
      if (r.filterKeys && r.filterKeys.length) {
        tr.addEventListener('contextmenu', function (e) {
          e.preventDefault();
          _showFilterMenu(e.clientX, e.clientY, function (m) {
            return _regionFilterUrl(r.filterKeys, m);
          }, r.count);
        });
      }
      tbody.appendChild(tr);
    });
    tbl.appendChild(tbody);
    wrap.appendChild(tbl);
    return wrap;
  }

  function _sqlStr(s) {
    return "'" + String(s == null ? '' : s).replace(/'/g, "''") + "'";
  }

  // where_sql=… URL filtering the list view to a region's caches by their
  // stored (state, county) values.  Each key is [state, county]; an empty
  // county (region tier) filters by state alone.  `mode` narrows by find
  // status: 'found' (the default, matching the map's counts), 'unfound', or
  // 'all'.  A find is found=1 OR completed=1 (ALC parents use completed).
  function _regionFilterUrl(filterKeys, mode) {
    if (!filterKeys || !filterKeys.length || !listUrl) return '';
    var clauses = filterKeys.map(function (k) {
      var s = 'state=' + _sqlStr(k[0]);
      return k[1] ? '(' + s + ' AND county=' + _sqlStr(k[1]) + ')' : s;
    });
    var sql = '(' + clauses.join(' OR ') + ')';
    if (mode === 'found') sql += ' AND (found=1 OR completed=1)';
    else if (mode === 'unfound') sql += ' AND found=0 AND completed=0';
    return listUrl + '?where_sql=' + encodeURIComponent(sql);
  }

  // where_sql=… URL filtering the list view to one country (world map) by its
  // ISO code, narrowed by find status like _regionFilterUrl.
  function _countryFilterUrl(iso, mode) {
    if (!iso || !listUrl) return '';
    var sql = 'iso_country_code=' + _sqlStr(iso.toUpperCase());
    if (mode === 'found') sql += ' AND (found=1 OR completed=1)';
    else if (mode === 'unfound') sql += ' AND found=0 AND completed=0';
    return listUrl + '?where_sql=' + encodeURIComponent(sql);
  }

  // Right-click menu on a region/country (table row or map polygon): jump to
  // the list filtered to its found / unfound / all caches.  ``urlFor(mode)``
  // builds the target URL.  With no finds yet (count 0) only "show unfound" is
  // offered — useful for planning a trip there.
  function _showFilterMenu(clientX, clientY, urlFor, count) {
    _dismissRegionMenu();
    var menu = document.createElement('div');
    menu.className = 'dropdown-menu show';
    menu.style.position = 'fixed';
    menu.style.left = clientX + 'px';
    menu.style.top = clientY + 'px';
    menu.style.zIndex = '2000';
    var opts = (count > 0)
      ? [['found', gettext('Show found')],
         ['unfound', gettext('Show unfound')],
         ['all', gettext('Show all')]]
      : [['unfound', gettext('Show unfound')]];
    opts.forEach(function (opt) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'dropdown-item';
      b.textContent = opt[1];
      b.addEventListener('click', function () {
        var url = urlFor(opt[0]);
        _dismissRegionMenu();
        if (url) window.location.href = url;
      });
      menu.appendChild(b);
    });
    document.body.appendChild(menu);
    _regionMenuEl = menu;
    // Defer so the opening click/contextmenu doesn't immediately dismiss it.
    setTimeout(function () {
      document.addEventListener('mousedown', _onRegionMenuDocDown);
      document.addEventListener('keydown', _onRegionMenuKey);
    }, 0);
  }

  function _dismissRegionMenu() {
    if (!_regionMenuEl) return;
    _regionMenuEl.remove();
    _regionMenuEl = null;
    document.removeEventListener('mousedown', _onRegionMenuDocDown);
    document.removeEventListener('keydown', _onRegionMenuKey);
  }

  function _onRegionMenuDocDown(e) {
    if (_regionMenuEl && !_regionMenuEl.contains(e.target)) _dismissRegionMenu();
  }

  function _onRegionMenuKey(e) {
    if (e.key === 'Escape') _dismissRegionMenu();
  }

  // Build a single where_sql=… list-view URL matching every cache in `caches`
  // by its canonical code (gc/oc/al), so OC and lab finds land too.
  function _filterUrl(caches) {
    var codes = caches
      .map(function (c) { return c.code; })
      .filter(Boolean)
      .map(function (c) { return "'" + c.replace(/'/g, "''") + "'"; });
    if (!codes.length || !listUrl) return '';
    var inList = '(' + codes.join(',') + ')';
    // Parenthesised so the OR composes safely if the list view ANDs it with
    // other active filters.
    var sql = '(gc_code IN ' + inList + ' OR oc_code IN ' + inList +
              ' OR al_code IN ' + inList + ')';
    return listUrl + '?where_sql=' + encodeURIComponent(sql);
  }

  // Panel listing the caches whose state/county didn't join a polygon, with a
  // button that opens the list view filtered to exactly them so the user can
  // fix the location data with the existing tools.  Returns null when nothing
  // is unmapped.  `tier` is 'county' or 'region' (for the count wording).
  function _unmatchedPanel(meta, tier) {
    var n = (meta && meta.unmatched) || 0;
    var caches = (meta && meta.unmatched_caches) || [];
    if (!n && !caches.length) return null;

    var wrap = document.createElement('div');
    wrap.className = 'gcf-dash-unmapped mt-3';

    var note = document.createElement('p');
    note.className = 'text-muted small mb-2';
    note.textContent = interpolate(
      tier === 'region'
        ? ngettext('%s find in unmapped regions', '%s finds in unmapped regions', n)
        : ngettext('%s find in unmapped counties', '%s finds in unmapped counties', n),
      [n]);
    wrap.appendChild(note);

    if (!caches.length) return wrap;

    var tbl = document.createElement('table');
    tbl.className = 'table table-sm gcf-dash-unmapped-table mb-2';
    tbl.innerHTML =
      '<thead><tr>' +
      '<th>' + _esc(gettext('Code')) + '</th>' +
      '<th>' + _esc(gettext('Name')) + '</th>' +
      '<th>' + _esc(gettext('Country')) + '</th>' +
      '<th>' + _esc(gettext('State')) + '</th>' +
      '<th>' + _esc(gettext('County')) + '</th>' +
      '</tr></thead>';
    var tbody = document.createElement('tbody');
    var country = (meta && meta.country) || '';
    caches.forEach(function (c) {
      var nameCell;
      if (c.code && detailTemplate) {
        var href = detailTemplate.replace('CODEXX', encodeURIComponent(c.code));
        nameCell = '<a href="' + _esc(href) + '">' + _esc(c.name) + '</a>';
      } else {
        nameCell = _esc(c.name);
      }
      var tr = document.createElement('tr');
      tr.innerHTML =
        '<td>' + _esc(c.code) + '</td>' +
        '<td>' + nameCell + '</td>' +
        '<td>' + _esc(country) + '</td>' +
        '<td>' + _esc(c.state) + '</td>' +
        '<td>' + _esc(c.county) + '</td>';
      tbody.appendChild(tr);
    });
    tbl.appendChild(tbody);
    wrap.appendChild(tbl);

    var url = _filterUrl(caches);
    if (url) {
      var btn = document.createElement('a');
      btn.className = 'btn btn-sm btn-outline-primary';
      btn.href = url;
      btn.textContent = gettext('Select as filter');
      wrap.appendChild(btn);
    }
    return wrap;
  }

  // Decorate region features (already have .count) with fillColor.
  function _decorateRegions(features) {
    var max = 0;
    features.forEach(function (f) { if (f.properties.count > max) max = f.properties.count; });
    return {
      type: 'FeatureCollection',
      features: features.map(function (f) {
        return {
          type: 'Feature',
          geometry: f.geometry,
          properties: {
            name: f.properties.name,
            count: f.properties.count,
            fillColor: _heat(f.properties.count, max),
            // MapLibre stringifies object properties, so JSON-encode here and
            // parse on right-click.
            filterKeys: JSON.stringify(f.properties.filter_keys || [])
          }
        };
      })
    };
  }

  function _buildRegionSection(parent, data, meta, observer) {
    var max = 0;
    data.features.forEach(function (f) { if (f.properties.count > max) max = f.properties.count; });

    var row = document.createElement('div');
    row.className = 'gcf-dash-maprow';

    var mapCol = document.createElement('div');
    mapCol.className = 'gcf-dash-mapcol';
    var mapEl = document.createElement('div');
    mapEl.className = 'gcf-dash-map';
    mapCol.appendChild(mapEl);
    if (max > 0) mapCol.appendChild(_legend(max));
    row.appendChild(mapCol);

    row.appendChild(_regionTable(data.features, meta));
    parent.appendChild(row);

    var panel = _unmatchedPanel(meta, 'region');
    if (panel) parent.appendChild(panel);

    var decorated = _decorateRegions(data.features);
    var fit = _smartBounds(data.features);
    _lazy(observer, mapEl, function () {
      var map = _buildMap(mapEl, decorated, fit, null, null, { projection: 'globe' });
      setTimeout(function () { try { map.resize(); } catch (e) {} }, 50);
    });
  }

  // For the county tier, split the country's counties into per-state groups
  // and render one sub-map per state with finds — that's the "Baden-Württemberg
  // example from GSAK stats1.html" drill-down the user originally asked for.
  function _renderCountyByState(section, data, iso) {
    var groups = {};
    data.features.forEach(function (f) {
      var ps = f.properties.parent_state || '';
      if (!groups[ps]) {
        groups[ps] = {
          name: ps, features: [], total: 0,
          flag: f.properties.parent_state_flag || '',
        };
      }
      groups[ps].features.push(f);
      groups[ps].total += f.properties.count || 0;
    });
    var entries = Object.keys(groups)
      .map(function (k) { return groups[k]; })
      .filter(function (e) { return e.total > 0; });
    entries.sort(function (a, b) { return b.total - a.total; });

    if (!entries.length) {
      var none = document.createElement('p');
      none.className = 'text-muted small';
      none.textContent = gettext('No counties have finds yet.');
      section.appendChild(none);
      return;
    }

    entries.forEach(function (entry) {
      var head = document.createElement('h6');
      head.className = 'mt-3 d-flex align-items-center gap-2';
      if (entry.flag) {
        head.innerHTML = '<img src="' + _esc(entry.flag) +
          '" alt="" class="gcf-region-flag"> ' +
          _esc(entry.name || gettext('Unmapped'));
      } else {
        head.textContent = entry.name || gettext('Unmapped');
      }
      section.appendChild(head);
      // Container kept in order; a single-county state (Berlin, DC, …) tries
      // its sub-county district breakdown, falling back to the lone polygon.
      var container = document.createElement('div');
      section.appendChild(container);
      if (entry.features.length === 1 && districtTemplate && iso && entry.name) {
        _renderDistricts(container, iso, entry);
      } else {
        _buildRegionSection(container,
          { type: 'FeatureCollection', features: entry.features },
          { total: entry.total, unmatched: 0 }, null);
      }
    });

    // Country-wide unmatched panel: counties whose name didn't join, listed
    // with a button to open them filtered in the list view for cleanup.
    var panel = _unmatchedPanel(data.meta, 'county');
    if (panel) section.appendChild(panel);
  }

  // Fetch a single-county state's districts (Bezirke/wards) and render the
  // breakdown; fall back to the single county polygon when they aren't cached.
  function _renderDistricts(container, iso, entry) {
    var url = districtTemplate
      .replace('ISOXX', encodeURIComponent(iso))
      .replace('STATEXX', encodeURIComponent(entry.name));
    var fallback = function () {
      _buildRegionSection(container,
        { type: 'FeatureCollection', features: entry.features },
        { total: entry.total, unmatched: 0 }, null);
    };
    fetch(url)
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (dd) {
        if (!dd.features || !dd.features.length) return fallback();
        _buildRegionSection(container, dd,
          dd.meta || { total: entry.total, unmatched: 0 }, null);
      })
      .catch(fallback);
  }

  // Render one tier (region OR county) for a country inside its pane.  Each
  // tier renders into its own sub-section so a country can show both stacked.
  function _renderTier(pane, iso, tier, downloaded, settingsUrl, urlTemplate) {
    var section = document.createElement('div');
    section.className = 'gcf-dash-tier mb-4';
    var head = document.createElement('h6');
    head.textContent = tier === 'region' ? gettext('Regions') : gettext('Counties');
    section.appendChild(head);
    pane.appendChild(section);

    if (!downloaded) {
      var ph = document.createElement('p');
      ph.className = 'text-muted small';
      ph.innerHTML = gettext('Boundary not downloaded.') + ' <a href="' +
        _esc(settingsUrl) + '">' + gettext('Settings → Dashboard') + '</a>.';
      section.appendChild(ph);
      return;
    }
    var loading = document.createElement('p');
    loading.className = 'text-muted small mb-0';
    loading.textContent = gettext('Loading…');
    section.appendChild(loading);
    fetch(urlTemplate.replace('/XX/', '/' + iso + '/'))
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        loading.remove();
        if (tier === 'county') {
          _renderCountyByState(section, data, iso);
        } else {
          _buildRegionSection(section, data,
                              data.meta || { total: 0, unmatched: 0 }, null);
        }
      })
      .catch(function () {
        loading.textContent = interpolate(
          gettext('Could not load region data for %s.'), [iso]);
        loading.className = 'text-danger small';
      });
  }

  // Render the country sub-tab's full content (region + county, when enabled).
  function _renderOneCountry(pane, opts) {
    if (opts.showRegion) {
      _renderTier(pane, opts.iso, 'region', opts.regionDownloaded,
                  opts.settingsUrl, opts.regionUrl);
    }
    if (opts.showCounty) {
      _renderTier(pane, opts.iso, 'county', opts.countyDownloaded,
                  opts.settingsUrl, opts.countyUrl);
    }
  }

  // Wire each sub-tab inside the Maps tab to its lazy builder.  Called once
  // after MapLibre has loaded.
  function _wireSubTabs(root) {
    var settingsUrl = root.dataset.settingsUrl || '';
    var regionUrl = root.dataset.regionUrlTemplate || '';
    var countyUrl = root.dataset.countyUrlTemplate || '';
    listUrl = root.dataset.listUrl || '';
    detailTemplate = root.dataset.detailUrlTemplate || '';
    districtTemplate = root.dataset.districtUrlTemplate || '';

    // World + continents pane.
    var worldPane = root.querySelector('#maps-world');
    var worldTab = document.querySelector(
      '#maps-subtabs button[data-maps-hash="world"]');
    if (worldPane && worldTab) {
      _wireBuilder(worldTab, function () {
        fetch(root.dataset.geojsonUrl)
          .then(function (r) { return r.json(); })
          .then(function (gj) {
            geojson = gj;
            worldPane.querySelectorAll('.gcf-dash-map-section').forEach(function (sec) {
              if (sec.dataset.level === 'world') _renderWorld(sec, null);
              else if (sec.dataset.level === 'continent') _renderContinents(sec, null);
            });
          })
          .catch(function () {
            worldPane.insertAdjacentHTML('beforeend',
              '<p class="text-danger">' + _esc(gettext('Could not load boundary data.')) + '</p>');
          });
      });
    }

    // Countries index pane — clicking a row opens that country's sub-tab.
    var countriesPane = root.querySelector('#maps-countries');
    if (countriesPane) {
      countriesPane.querySelectorAll('.gcf-country-row').forEach(function (row) {
        row.addEventListener('click', function () {
          var iso = (row.getAttribute('data-iso') || '').toLowerCase();
          var btn = document.querySelector(
            '#maps-subtabs button[data-maps-hash="' + iso + '"]');
          if (btn) bootstrap.Tab.getOrCreateInstance(btn).show();
        });
      });
    }

    // Per-country panes — build region (+county) on first show.
    root.querySelectorAll('.tab-pane[id^="maps-c-"]').forEach(function (pane) {
      var inner = pane.querySelector('.gcf-dash-country-pane');
      var btn = document.querySelector(
        '#maps-subtabs button[data-bs-target="#' + pane.id + '"]');
      if (!btn || !inner) return;
      _wireBuilder(btn, function () {
        _renderOneCountry(inner, {
          iso: pane.getAttribute('data-iso'),
          showRegion: pane.getAttribute('data-show-region') === '1',
          showCounty: pane.getAttribute('data-show-county') === '1',
          regionDownloaded: pane.getAttribute('data-region-downloaded') === '1',
          countyDownloaded: pane.getAttribute('data-county-downloaded') === '1',
          settingsUrl: settingsUrl,
          regionUrl: regionUrl,
          countyUrl: countyUrl,
        });
      });
    });
  }

  function _start() {
    if (started) return;
    var root = document.getElementById('dash-maps-root');
    if (!root) return;
    started = true;

    counts = _readJson(root.dataset.countsId) || {};
    allCounts = _readJson(root.dataset.allCountsId) || {};

    if (typeof gcfLoadMapLibre !== 'function') {
      root.insertAdjacentHTML('beforeend',
        '<p class="text-danger">' + _esc(gettext('Map library failed to load.')) + '</p>');
      return;
    }

    gcfLoadMapLibre({ onReady: function () { _wireSubTabs(root); } });
  }

  // Exposed so dashboard.html can kick off the build when the Maps tab body
  // (loaded in the background via HTMX) settles while the tab is already open.
  // Idempotent: the `started` guard makes repeat calls no-ops.
  window.gcfStartDashboardMaps = _start;

  document.addEventListener('DOMContentLoaded', function () {
    // Build only once the Maps tab is shown (MapLibre needs a visible container).
    var tabBtn = document.querySelector('#dashboard-tabs button[data-bs-target="#dash-maps"]');
    if (!tabBtn) return;
    tabBtn.addEventListener('shown.bs.tab', _start);
    // The page may have loaded with #maps in the URL — the inline hash-persistence
    // script already activated the tab before this listener was registered, so
    // kick off _start now if we're already on the Maps tab.
    if (tabBtn.classList.contains('active')) _start();
  });
})();
