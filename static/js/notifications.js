// Instant Notifications page (templates/geocaches/notifications.html): the
// edit modal (populate fields from the clicked row's data-* attributes) and
// the bulk-create modal's location-dropdown lat/lon pre-fill.

(function () {
  // Edit modal — populate fields from the row's data-* attributes when opened.
  const editModal = document.getElementById("editModal");
  if (editModal) {
    editModal.addEventListener("show.bs.modal", function (event) {
      const btn = event.relatedTarget;
      const pk = btn.dataset.pk;
      const editUrlTemplate = editModal.dataset.editUrlTemplate;
      editModal.querySelector("form").action = editUrlTemplate.replace("/0/", "/" + pk + "/");
      editModal.querySelector("[name=name]").value = btn.dataset.name || "";
      editModal.querySelector("[name=radius_km]").value = btn.dataset.radius || "20";
      editModal.querySelector("[name=latitude]").value = btn.dataset.latitude || "";
      editModal.querySelector("[name=longitude]").value = btn.dataset.longitude || "";
      editModal.querySelector("[name=recipient_email]").value = btn.dataset.recipient || "";

      const locId = btn.dataset.locationId || "";
      const locSel = editModal.querySelector("[name=location_id]");
      if (locSel) locSel.value = locId;

      const checked = (btn.dataset.logEvents || "").split(",").map(s => s.trim()).filter(Boolean);
      editModal.querySelectorAll("[name=log_event_ids]").forEach(cb => {
        cb.checked = checked.includes(cb.value);
      });
    });
  }

  // Bulk-create modal: location dropdown pre-fills lat/lon when a location is picked.
  const bulkModal = document.getElementById("bulkCreateModal");
  if (bulkModal) {
    const locSel = bulkModal.querySelector("[name=location_id]");
    if (locSel) {
      locSel.addEventListener("change", function () {
        const opt = locSel.selectedOptions[0];
        if (opt && opt.dataset.lat && opt.dataset.lon) {
          bulkModal.querySelector("[name=latitude]").value = opt.dataset.lat;
          bulkModal.querySelector("[name=longitude]").value = opt.dataset.lon;
        }
      });
    }
  }

  // OC tab (templates/geocaches/partials/_notify_oc_tab.html, included once
  // per page load — not htmx-swapped): location dropdown pre-fills lat/lon
  // on each per-card notification form, same as the bulk-create one above.
  document.querySelectorAll('#pane-oc select[name=location_id]').forEach(function (sel) {
    sel.addEventListener('change', function () {
      var opt = sel.selectedOptions[0];
      if (!opt || !opt.dataset.lat) return;
      var form = sel.closest('form');
      if (!form) return;
      var lat = form.querySelector('input[name=latitude]');
      var lon = form.querySelector('input[name=longitude]');
      if (lat) lat.value = opt.dataset.lat;
      if (lon) lon.value = opt.dataset.lon;
    });
  });
})();
