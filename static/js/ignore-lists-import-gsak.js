// Import GSAK ignore list page (templates/geocaches/tools/
// ignore_lists_import_gsak.html): "Check all" toggles every detected-database
// checkbox, and each checkbox in turn keeps "Check all" in sync.

(function () {
  var checkAll = document.getElementById('gsak_check_all');
  if (!checkAll) return;
  var boxes = document.querySelectorAll('.gsak-db-check');
  checkAll.addEventListener('change', function () {
    boxes.forEach(function (b) { b.checked = checkAll.checked; });
  });
  boxes.forEach(function (b) {
    b.addEventListener('change', function () {
      checkAll.checked = Array.from(boxes).every(function (x) { return x.checked; });
    });
  });
})();
