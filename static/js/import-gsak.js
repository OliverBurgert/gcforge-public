document.addEventListener('DOMContentLoaded', function() {
  var form = document.getElementById('import-form');
  if (!form) return;
  // Remove the generic listener added by import-form.js and replace with GSAK-specific one
  form.addEventListener('submit', function() {
    var customRadio = this.querySelector('#gsak_custom');
    if (customRadio && customRadio.checked) {
      customRadio.value = (this.querySelector('#gsak_custom_path_input')?.value || '').trim();
    }
  });
});
