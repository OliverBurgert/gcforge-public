document.addEventListener('DOMContentLoaded', function() {
  var form = document.getElementById('import-form');
  if (!form) return;
  form.addEventListener('submit', function() {
    var btn = this.querySelector('.import-btn');
    var spinner = this.querySelector('.import-spinner');
    if (btn) btn.disabled = true;
    if (spinner) spinner.classList.remove('d-none');
  });
});
