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

// Tag-suggestion chips (templates/geocaches/partials/_import_base.html,
// shared by every importer page that extends it). Tags endpoint URL comes
// from the #import-base-config data-* element.
(function() {
  var input = document.getElementById('import-tags-input');
  var box = document.getElementById('import-tag-suggestions');
  if (!input || !box) return;
  var cfg = document.getElementById('import-base-config');
  var url = cfg ? cfg.dataset.tagsUrl : '';
  if (!url) return;
  fetch(url)
    .then(function(r) { return r.json(); })
    .then(function(names) {
      if (!names.length) return;
      names.forEach(function(name) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm btn-outline-secondary me-1 mb-1';
        btn.textContent = name;
        btn.addEventListener('click', function() {
          var cur = input.value.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
          if (cur.indexOf(name) === -1) { cur.push(name); }
          input.value = cur.join(', ');
        });
        box.appendChild(btn);
      });
    });
})();
