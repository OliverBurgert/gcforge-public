// GCForge — Dashboard "360° from Location" tab ──────────────────────────────
//
// Splits the compass into 360 one-degree sectors around a chosen location and
// shows, for finds within a max distance:
//   Overview — a 360-spoke ring (green = goal reached, orange = partial,
//              red = no finds) + a statistics panel
//   Table    — bearing range, #found, first ten find codes
//   Map      — center pin + find density (MapLibre, lazy-loaded)
//
// Goal changes recolour client-side (no refetch); location / max-distance
// changes refetch from dashboard_360_data. Data loads lazily when the tab is
// first opened. ECharts is already loaded by the dashboard template.

(function () {
  var COLORS = { complete: "#2ecc40", partial: "#ff851b", missing: "#ff4136" };

  var root, cfg, data = null, loaded = false;
  var ring = null, map = null, mapReady = false, centerMarker = null;

  function $(id) { return document.getElementById(id); }

  function status(msg) { var el = $("h360-status"); if (el) el.textContent = msg || ""; }

  function goal() { var el = $("h360-goal"); return el ? parseInt(el.value, 10) || 1 : 1; }

  function corrected() { var el = $("h360-corrected"); return !!(el && el.checked); }


  function sectorColor(count, g) {
    if (count >= g) return COLORS.complete;
    if (count > 0) return COLORS.partial;
    return COLORS.missing;
  }

  // ── Data fetch ────────────────────────────────────────────────────────────
  function fetchData() {
    var ref = $("h360-ref"), km = $("h360-km");
    if (!ref) return;
    var url = cfg.url + "?ref=" + encodeURIComponent(ref.value) +
              "&max_km=" + encodeURIComponent(km ? km.value : cfg.defaultKm) +
              "&corrected=" + (corrected() ? "1" : "0");
    status(cfg.loadingLabel);
    fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.sectors) { status("–"); return; }
        data = d;
        status("");
        renderRing();
        renderStats();
        renderTable();
        updateMap();
      })
      .catch(function () { status("–"); });
  }

  // ── Overview: ring + statistics ─────────────────────────────────────────────
  function ringData(g) {
    return data.sectors.map(function (s) {
      return {
        name: s.i + "–" + (s.i + 1) + "°",
        value: 1,
        count: s.count,
        itemStyle: { color: sectorColor(s.count, g) },
      };
    });
  }

  function renderRing() {
    var el = $("h360-ring");
    if (!el || typeof echarts === "undefined") return;
    if (!ring) ring = echarts.init(el);
    var findsLabel = cfg.findsLabel;
    ring.setOption({
      animation: false,
      tooltip: {
        trigger: "item",
        formatter: function (p) {
          return p.name + ": " + (p.data.count || 0) + " " + findsLabel;
        },
      },
      series: [{
        type: "pie",
        radius: ["28%", "92%"],
        center: ["50%", "50%"],
        label: { show: false },
        labelLine: { show: false },
        emphasis: { scale: false },
        itemStyle: { borderWidth: 0 },
        data: ringData(goal()),
      }],
    });
    wireResize(ring, el);
  }

  function recolorRing() {
    if (ring) ring.setOption({ series: [{ data: ringData(goal()) }] });
  }

  function renderStats() {
    if (!data) return;
    var g = goal();
    var counts = data.sectors.map(function (s) { return s.count; });
    var min = Infinity, max = 0, missing = 0, completed = 0, toMissing = 0;
    counts.forEach(function (c) {
      if (c < min) min = c;
      if (c > max) max = c;
      if (c === 0) missing++;
      if (c >= g) completed++;
      if (c < g) toMissing += (g - c);
    });
    if (min === Infinity) min = 0;
    setText("h360-stat-goal", g);
    setText("h360-stat-min", min);
    setText("h360-stat-max", max);
    setText("h360-stat-missing", missing);
    setText("h360-stat-completed", completed);
    setText("h360-stat-tomissing", toMissing);
  }

  function setText(id, v) { var el = $(id); if (el) el.textContent = v; }

  // ── Table ───────────────────────────────────────────────────────────────────
  function renderTable() {
    var body = $("h360-table-body");
    if (!body || !data) return;
    var frag = document.createDocumentFragment();
    data.sectors.forEach(function (s) {
      var tr = document.createElement("tr");

      var b = document.createElement("td");
      b.textContent = s.i + "–" + (s.i + 1);
      tr.appendChild(b);

      var n = document.createElement("td");
      n.className = "text-end";
      n.textContent = s.count;
      tr.appendChild(n);

      var codes = document.createElement("td");
      s.codes.forEach(function (code, idx) {
        if (idx) codes.appendChild(document.createTextNode(", "));
        var a = document.createElement("a");
        a.href = cfg.detailTemplate.replace("CODEXX", code);
        a.textContent = code;
        codes.appendChild(a);
      });
      tr.appendChild(codes);

      frag.appendChild(tr);
    });
    body.replaceChildren(frag);
  }

  // ── Map (lazy MapLibre) ─────────────────────────────────────────────────────
  function ensureMap() {
    if (mapReady || !data) return;
    if (typeof gcfLoadMapLibre !== "function") return;
    gcfLoadMapLibre({
      scripts: ["/static/js/map-styles.js"],
      onReady: initMap,
    });
  }

  function initMap() {
    if (map || typeof maplibregl === "undefined" || typeof GCF_STYLES === "undefined") return;
    var styleId = localStorage.getItem("gcforge_map_style") || "street";
    if (!GCF_STYLES[styleId]) styleId = "street";
    map = new maplibregl.Map({
      container: "h360-map-canvas",
      style: GCF_STYLES[styleId],
      center: [data.ref_lon, data.ref_lat],
      zoom: 8,
      attributionControl: true,
      transformRequest: gcfMapTransformRequest,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-left");
    map.on("load", function () {
      // Sectors first (below), coloured by goal; find dots on top.
      map.addSource("h360-sectors", { type: "geojson", data: sectorsGeoJSON(goal()) });
      map.addLayer({
        id: "h360-sectors",
        type: "fill",
        source: "h360-sectors",
        paint: {
          "fill-color": ["get", "color"],
          "fill-opacity": 0.32,
          "fill-outline-color": "rgba(0,0,0,0)",
        },
      });
      map.addSource("h360-points", { type: "geojson", data: pointsGeoJSON() });
      map.addLayer({
        id: "h360-points",
        type: "circle",
        source: "h360-points",
        paint: {
          "circle-radius": 2.5,
          "circle-color": "#0b6b2e",
          "circle-opacity": 0.6,
        },
      });
      mapReady = true;
      placeCenter();
      fitToData();
      map.resize();
    });
    if (window.ResizeObserver) {
      new ResizeObserver(function () { if (map) map.resize(); }).observe($("h360-map-canvas"));
    }
  }

  function pointsGeoJSON() {
    return {
      type: "FeatureCollection",
      features: (data ? data.points : []).map(function (p) {
        return {
          type: "Feature",
          properties: { code: p.code },
          geometry: { type: "Point", coordinates: [p.lon, p.lat] },
        };
      }),
    };
  }

  // Great-circle destination point — for drawing the sector wedges out to the
  // max-distance radius.
  function destPoint(lat, lon, brngDeg, distKm) {
    var R = 6371, d = distKm / R, b = brngDeg * Math.PI / 180;
    var la1 = lat * Math.PI / 180, lo1 = lon * Math.PI / 180;
    var la2 = Math.asin(Math.sin(la1) * Math.cos(d) + Math.cos(la1) * Math.sin(d) * Math.cos(b));
    var lo2 = lo1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(la1),
                               Math.cos(d) - Math.sin(la1) * Math.sin(la2));
    return [lo2 * 180 / Math.PI, la2 * 180 / Math.PI];
  }

  // One full-radius wedge polygon per 1° sector, coloured by completeness.
  function sectorsGeoJSON(g) {
    if (!data) return { type: "FeatureCollection", features: [] };
    var c = [data.ref_lon, data.ref_lat], R = data.max_km;
    return {
      type: "FeatureCollection",
      features: data.sectors.map(function (s) {
        var p0 = destPoint(data.ref_lat, data.ref_lon, s.i, R);
        var pm = destPoint(data.ref_lat, data.ref_lon, s.i + 0.5, R);
        var p1 = destPoint(data.ref_lat, data.ref_lon, s.i + 1, R);
        return {
          type: "Feature",
          properties: { color: sectorColor(s.count, g) },
          geometry: { type: "Polygon", coordinates: [[c, p0, pm, p1, c]] },
        };
      }),
    };
  }

  function recolorSectorsLayer() {
    if (!mapReady) return;
    var src = map.getSource("h360-sectors");
    if (src) src.setData(sectorsGeoJSON(goal()));
  }

  function placeCenter() {
    if (!map || !data) return;
    if (centerMarker) centerMarker.remove();
    centerMarker = new maplibregl.Marker().setLngLat([data.ref_lon, data.ref_lat]).addTo(map);
  }

  function fitToData() {
    if (!map || !data) return;
    var b = new maplibregl.LngLatBounds();
    b.extend([data.ref_lon, data.ref_lat]);
    // Cover the full max-distance disk so empty (red) sectors stay in view.
    [0, 90, 180, 270].forEach(function (brg) {
      b.extend(destPoint(data.ref_lat, data.ref_lon, brg, data.max_km));
    });
    map.fitBounds(b, { padding: 40, maxZoom: 13, duration: 300 });
  }

  function updateMap() {
    if (!mapReady) return;
    var sp = map.getSource("h360-sectors");
    if (sp) sp.setData(sectorsGeoJSON(goal()));
    var pp = map.getSource("h360-points");
    if (pp) pp.setData(pointsGeoJSON());
    placeCenter();
    fitToData();
  }

  // ── Misc ────────────────────────────────────────────────────────────────────
  function wireResize(chart, el) {
    new ResizeObserver(function () { chart.resize(); }).observe(el);
  }

  function debounce(fn, ms) {
    var t;
    return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  // ── Grid search ─────────────────────────────────────────────────────────────
  function gridWidth() {
    var el = $("h360-grid-width");
    return el ? parseInt(el.value, 10) || 9 : 9;
  }

  function runGridSearch() {
    var ref = $("h360-ref"), km = $("h360-km"), btn = $("h360-grid-btn");
    if (!ref) return;
    var url = cfg.gridUrl + "?ref=" + encodeURIComponent(ref.value) +
              "&max_km=" + encodeURIComponent(km ? km.value : cfg.defaultKm) +
              "&goal=" + goal() +
              "&corrected=" + (corrected() ? "1" : "0") +
              "&grid_width=" + gridWidth();
    if (btn) btn.disabled = true;
    setText("h360-grid-status", cfg.searchingLabel);
    fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (btn) btn.disabled = false;
        if (!d || !d.results) { setText("h360-grid-status", "–"); return; }
        setText("h360-grid-status", "");
        renderGrid(d.results);
      })
      .catch(function () {
        if (btn) btn.disabled = false;
        setText("h360-grid-status", "–");
      });
  }

  function renderGrid(results) {
    var body = $("h360-grid-body");
    if (!body) return;
    var updateLabel = $("h360-grid-btn").getAttribute("data-update-label") || "Update";
    var frag = document.createDocumentFragment();
    results.forEach(function (r) {
      var tr = document.createElement("tr");

      var c = document.createElement("td");
      c.textContent = r.coord;
      c.style.fontFamily = "monospace";
      tr.appendChild(c);

      var m = document.createElement("td");
      m.className = "text-end";
      m.textContent = r.missing;
      tr.appendChild(m);

      var d = document.createElement("td");
      d.className = "text-end text-muted";
      d.textContent = r.dist_m + " m";
      tr.appendChild(d);

      var act = document.createElement("td");
      act.className = "text-end";
      var b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-sm btn-outline-secondary py-0";
      b.textContent = updateLabel;
      b.addEventListener("click", function () { updateLocation(r.lat, r.lon); });
      act.appendChild(b);
      tr.appendChild(act);

      frag.appendChild(tr);
    });
    body.replaceChildren(frag);
  }

  function updateLocation(lat, lon) {
    var ref = $("h360-ref");
    if (!ref) return;
    var fd = new FormData();
    fd.append("ref", ref.value);
    fd.append("lat", lat);
    fd.append("lon", lon);
    fd.append("csrfmiddlewaretoken", cfg.csrf);
    setText("h360-grid-status", cfg.searchingLabel);
    fetch(cfg.setlocUrl, {
      method: "POST",
      headers: { "X-CSRFToken": cfg.csrf },
      body: fd,
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        setText("h360-grid-status", "");
        if (!d || !d.ok) return;
        fetchData();        // re-centre the ring/table/map on the moved location
        runGridSearch();    // re-search around the new centre
      })
      .catch(function () { setText("h360-grid-status", "–"); });
  }

  function load() {
    if (loaded) return;
    loaded = true;
    fetchData();
  }

  function wire() {
    var ref = $("h360-ref");
    if (!ref) return;  // no locations configured → empty state
    ref.addEventListener("change", fetchData);

    var km = $("h360-km");
    if (km) km.addEventListener("input", debounce(fetchData, 450));

    var corr = $("h360-corrected");
    if (corr) corr.addEventListener("change", fetchData);

    var g = $("h360-goal");
    if (g) g.addEventListener("input", function () {
      var v = $("h360-goal-val");
      if (v) v.textContent = goal();
      recolorRing();
      renderStats();
      recolorSectorsLayer();
    });

    var gw = $("h360-grid-width");
    if (gw) gw.addEventListener("input", function () {
      var v = $("h360-grid-width-val");
      if (v) v.textContent = gridWidth();
    });

    var gridBtn = $("h360-grid-btn");
    if (gridBtn) gridBtn.addEventListener("click", runGridSearch);

    var missing = $("h360-missing-btn");
    if (missing) missing.addEventListener("click", function () {
      var p = new URLSearchParams();
      p.set("ref", ref.value);
      p.set("max_km", km ? km.value : cfg.defaultKm);
      p.set("goal", goal());
      p.set("corrected", corrected() ? "1" : "0");
      window.location.href = missing.dataset.url + "?" + p.toString();
    });

    // Resize the ring when the Overview sub-tab is revealed.
    document.querySelectorAll('#h360-subtabs button[data-bs-toggle="tab"]').forEach(function (b) {
      b.addEventListener("shown.bs.tab", function () {
        if (b.getAttribute("data-bs-target") === "#h360-map") ensureMap();
        else if (ring) ring.resize();
      });
    });

    // Lazy-load data when the outer tab is first opened (or immediately if the
    // page was deep-linked to #360).
    var outerBtn = document.querySelector('#dashboard-tabs [data-bs-target="#dash-360"]');
    if (outerBtn) outerBtn.addEventListener("shown.bs.tab", load);
    if ((window.location.hash || "") === "#360") load();
  }

  function init() {
    root = $("dash-360-root");
    if (!root) return;
    cfg = {
      url: root.dataset.url,
      defaultRef: root.dataset.defaultRef,
      defaultKm: root.dataset.defaultKm,
      findsLabel: root.dataset.findsLabel || "finds",
      loadingLabel: root.dataset.loadingLabel || "Loading…",
      searchingLabel: root.dataset.searchingLabel || "Searching…",
      gridUrl: root.dataset.gridUrl,
      setlocUrl: root.dataset.setlocUrl,
      detailTemplate: root.dataset.detailUrlTemplate,
      csrf: (root.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "",
    };
    wire();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
