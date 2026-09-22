// GCForge — Dashboard Treasures tab ──────────────────────────────────────────
// The list loads via HTMX. This drives the Refresh button: POST to scrape
// geocaching.com (web session), then tell HTMX to reload the list.

(function () {
  function csrf() {
    var el = document.querySelector("#dash-treasures [name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }
  function status(msg) {
    var el = document.getElementById("treasure-status");
    if (el) el.textContent = msg || "";
  }

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("treasure-refresh");
    if (!btn) return;
    btn.addEventListener("click", function () {
      btn.disabled = true;
      status(btn.dataset.busy);
      fetch(btn.dataset.url, {
        method: "POST",
        headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          btn.disabled = false;
          if (!res.ok || !res.d.ok) { status(res.d.error || "—"); return; }
          status("");
          if (window.htmx) htmx.trigger(document.body, "treasures:reload");
        })
        .catch(function () { btn.disabled = false; status("—"); });
    });
  });
})();
