// GCForge — Dashboard Souvenirs tab ──────────────────────────────────────────
//
// The list itself is loaded + filtered via HTMX (see dashboard.html). This
// module only drives the Refresh-all / Refresh-latest buttons: POST to the
// refresh endpoint, then tell HTMX to reload the list.

(function () {
  function csrf() {
    var el = document.querySelector("#dash-souvenirs [name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  function status(msg) {
    var el = document.getElementById("souv-status");
    if (el) el.textContent = msg || "";
  }

  function refresh(btn) {
    var all = document.getElementById("souv-refresh-all");
    var latest = document.getElementById("souv-refresh-latest");
    if (all) all.disabled = true;
    if (latest) latest.disabled = true;
    status(btn.dataset.busy);
    fetch(btn.dataset.url, {
      method: "POST",
      headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (all) all.disabled = false;
        if (latest) latest.disabled = false;
        if (!res.ok || !res.d.ok) {
          status(res.d.error || "—");
          return;
        }
        var t = document.getElementById("souv-total");
        if (t && typeof res.d.total === "number") {
          // refresh-latest reports only the pages it walked; bump by new finds.
          var added = res.d.added || 0;
          t.textContent = (parseInt(t.textContent, 10) || 0) + added;
        }
        status(btn.dataset.done.replace("{n}", res.d.added || 0));
        if (window.htmx) htmx.trigger(document.body, "souvenirs:reload");
      })
      .catch(function () {
        if (all) all.disabled = false;
        if (latest) latest.disabled = false;
        status("—");
      });
  }

  // Close the per-souvenir tag modal after a successful save (the set-tags
  // response fires `souvenir-tags-saved` via HX-Trigger, which bubbles to body).
  document.body.addEventListener("souvenir-tags-saved", function () {
    var el = document.getElementById("souv-tag-modal");
    var m = el && window.bootstrap && bootstrap.Modal.getInstance(el);
    if (m) m.hide();
  });

  document.addEventListener("DOMContentLoaded", function () {
    ["souv-refresh-all", "souv-refresh-latest"].forEach(function (id) {
      var btn = document.getElementById(id);
      if (btn) btn.addEventListener("click", function () { refresh(btn); });
    });
  });
})();
