// Import GSAK Locations page (templates/geocaches/import/
// import_gsak_locations.html): "check all" toggles every candidate-location
// checkbox.

(function () {
  var checkAll = document.getElementById('check-all');
  if (!checkAll) return;
  checkAll.addEventListener('change', function () {
    document.querySelectorAll('.loc-check').forEach(function (cb) { cb.checked = checkAll.checked; });
  });
})();
