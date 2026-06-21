// GCForge — Dashboard charts (ECharts) ──────────────────────────────────────
// Reads chart data + translated labels from json_script blocks emitted by
// dashboard.html, so no strings are hard-coded here (i18n stays in templates).

(function () {
  function readJSON(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  // Run fn once the given outer tab is first shown — ECharts can't size a
  // chart whose tab is display:none at load, so charts in a non-default tab
  // must be built on first reveal (matches the 360 / pie-toggle approach).
  //
  // The visibility test uses offsetParent (truthy only when displayed) instead
  // of the .show class: when the page is deep-linked to this tab via #hash, the
  // hash router calls Tab.show() during parse, so shown.bs.tab can fire before
  // this listener is attached — but by the time init() runs the pane is already
  // displayed, so we render straight away. A once-guard prevents a double init.
  function renderOnFirstShow(targetSel, fn) {
    var done = false;
    function run() { if (done) return; done = true; fn(); }
    var pane = document.querySelector(targetSel);
    if (pane && pane.offsetParent !== null) { run(); return; }  // already visible
    var btn = document.querySelector(
      '#dashboard-tabs button[data-bs-target="' + targetSel + '"]');
    if (!btn) { run(); return; }
    btn.addEventListener("shown.bs.tab", run);
  }

  // Keep a chart sized to its container, and re-fit when its tab becomes
  // visible (ECharts can't size an element that was hidden at init time).
  function wireResize(chart, el) {
    new ResizeObserver(function () { chart.resize(); }).observe(el);
    var pane = el.closest(".tab-pane");
    if (!pane) return;
    var btn = document.querySelector('[data-bs-target="#' + pane.id + '"]');
    if (btn) btn.addEventListener("shown.bs.tab", function () { chart.resize(); });
  }

  // Cumulative finds over time — area (cumulative) + bar (per-month finds).
  // elId/dataId default to the Statistics-tab chart but are overridable so the
  // Adventure Lab tab can render its own copy from a separate dataset.
  function renderCumulative(labels, elId, dataId) {
    var el = document.getElementById(elId || "chart-cumulative-finds");
    if (!el) return;
    var data = readJSON(dataId || "chart-cumulative-data");
    if (!data || !data.months || !data.months.length) return;

    var chart = echarts.init(el);
    chart.setOption({
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      legend: { data: [labels.cumulative, labels.monthly], top: 0 },
      grid: { left: 48, right: 56, top: 36, bottom: 28 },
      xAxis: { type: "category", data: data.months, boundaryGap: true },
      yAxis: [
        { type: "value", name: labels.monthly, position: "left" },
        { type: "value", name: labels.cumulative, position: "right", splitLine: { show: false } },
      ],
      series: [
        {
          name: labels.monthly,
          type: "bar",
          yAxisIndex: 0,
          data: data.monthly,
          itemStyle: { color: "#8c7a1e" },
          barMaxWidth: 14,
        },
        {
          name: labels.cumulative,
          type: "line",
          yAxisIndex: 1,
          data: data.cumulative,
          smooth: true,
          showSymbol: false,
          lineStyle: { color: "#c5d12e" },
          areaStyle: { color: "rgba(197,209,46,0.35)" },
        },
      ],
    });
    wireResize(chart, el);
  }

  // Compass wind-roses (barpolar). North at top, clockwise — matches the
  // bearing labels (0°, 30°, …) straight from the data. Two roses share the
  // selected reference point: find count and average distance.
  var ROSES = [
    { el: "chart-bearing-count", labelKey: "bearingCount", field: "counts", color: "#8c7a1e", unit: "" },
    { el: "chart-bearing-distance", labelKey: "bearingDist", field: "avg_distance", color: "#c5d12e", unit: " km" },
  ];
  var roseCharts = [];

  function roseOption(title, labels, values, color, unit) {
    return {
      title: { text: title, left: "center", textStyle: { fontSize: 13, fontWeight: 600 } },
      tooltip: {
        trigger: "item",
        formatter: function (p) { return p.name + ": " + p.value + (unit || ""); },
      },
      polar: { radius: "68%" },
      angleAxis: { type: "category", data: labels, startAngle: 90, clockwise: true },
      radiusAxis: { min: 0 },
      series: [{
        type: "bar",
        coordinateSystem: "polar",
        data: values,
        itemStyle: { color: color, opacity: 0.85 },
      }],
    };
  }

  function renderBearing(labels) {
    var data = readJSON("chart-bearing-data");
    if (!data || !data.total) return;
    ROSES.forEach(function (def, i) {
      var el = document.getElementById(def.el);
      if (!el) return;
      var chart = echarts.init(el);
      chart.setOption(roseOption(labels[def.labelKey], data.labels, data[def.field], def.color, def.unit));
      wireResize(chart, el);
      roseCharts[i] = chart;
    });
    wireBearingSelect();
  }

  // Reference-point selector — re-fetch rose data and update both charts in
  // place (no full reload). Only present when more than one ref point exists.
  function wireBearingSelect() {
    var sel = document.getElementById("dash-bearing-ref");
    if (!sel) return;
    sel.addEventListener("change", function () {
      var url = sel.getAttribute("data-url") + "?ref=" + encodeURIComponent(sel.value);
      fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data || !data.labels) return;
          ROSES.forEach(function (def, i) {
            if (!roseCharts[i]) return;
            roseCharts[i].setOption({
              angleAxis: { data: data.labels },
              series: [{ data: data[def.field] }],
            });
          });
        });
    });
  }

  // Simple vertical bar chart from a [{...}] dataset.
  function renderBarChart(elId, dataId, labelField, name, color) {
    var el = document.getElementById(elId);
    if (!el) return;
    var rows = readJSON(dataId);
    if (!rows || !rows.length) return;
    var chart = echarts.init(el);
    chart.setOption({
      tooltip: { trigger: "axis" },
      grid: { left: 44, right: 12, top: 16, bottom: 24 },
      xAxis: { type: "category", data: rows.map(function (r) { return String(r[labelField]); }) },
      yAxis: { type: "value" },
      series: [{
        name: name,
        type: "bar",
        data: rows.map(function (r) { return r.count; }),
        itemStyle: { color: color },
        barMaxWidth: 28,
      }],
    });
    wireResize(chart, el);
  }

  // Donut pie for a breakdown ([{label, count}]).
  function renderPie(elId, rows) {
    var el = document.getElementById(elId);
    if (!el || !rows) return null;
    var chart = echarts.init(el);
    chart.setOption({
      tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
      legend: { type: "scroll", orient: "vertical", left: 0, top: "middle", textStyle: { fontSize: 11 } },
      series: [{
        type: "pie",
        radius: ["35%", "70%"],
        center: ["62%", "50%"],
        data: rows.map(function (r) { return { name: r.label, value: r.count }; }),
        label: { show: false },
      }],
    });
    wireResize(chart, el);
    return chart;
  }

  // Bars/pie toggle on the type & size breakdowns. The pie is built lazily on
  // first reveal (ECharts can't size a hidden element).
  function wireChartToggles() {
    var pies = {};
    document.querySelectorAll(".gcf-chart-toggle").forEach(function (group) {
      var key = group.getAttribute("data-chart");
      group.querySelectorAll("button[data-view]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          group.querySelectorAll("button").forEach(function (b) {
            b.classList.toggle("active", b === btn);
          });
          var bars = document.querySelector('.gcf-chart-bars[data-chart="' + key + '"]');
          var pie = document.querySelector('.gcf-chart-pie[data-chart="' + key + '"]');
          if (btn.getAttribute("data-view") === "pie") {
            if (bars) bars.classList.add("d-none");
            if (pie) pie.classList.remove("d-none");
            if (!pies[key]) {
              pies[key] = renderPie("chart-pie-" + key, readJSON("chart-" + key + "-data"));
            } else {
              pies[key].resize();
            }
          } else {
            if (pie) pie.classList.add("d-none");
            if (bars) bars.classList.remove("d-none");
          }
        });
      });
    });
  }

  // The Statistics and Adventure Lab tab bodies load in the background (HTMX);
  // dashboard.html calls these once each body's data is in the DOM. The charts
  // themselves are built on the tab's first reveal — ECharts can't size a
  // container that is display:none at init time.
  function renderStatsCharts() {
    if (typeof echarts === "undefined") return;
    var labels = readJSON("chart-labels") || {};
    renderOnFirstShow("#dash-stats", function () {
      renderCumulative(labels);
      renderBarChart("chart-by-year", "chart-by-year-data", "year", labels.byYear, "#8c7a1e");
      renderBarChart("chart-by-month", "chart-by-month-data", "label", labels.byMonth, "#c5d12e");
      wireChartToggles();
      renderBearing(labels);
    });
  }

  function renderAlcCharts() {
    if (typeof echarts === "undefined") return;
    var labels = readJSON("chart-labels") || {};
    renderOnFirstShow("#dash-alc", function () {
      renderCumulative(labels, "chart-alc-cumulative", "chart-alc-cumulative-data");
    });
  }

  window.gcfRenderStatsCharts = renderStatsCharts;
  window.gcfRenderAlcCharts = renderAlcCharts;
})();
