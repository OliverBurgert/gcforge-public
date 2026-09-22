// Fetch by List Generation page (templates/geocaches/import/
// import_by_list_generation.html): tag-suggestion chips for the tags input.
// Tags endpoint URL comes from the #list-gen-config data-* element.

(function() {
  var input = document.getElementById('list-gen-tags-input');
  var box = document.getElementById('list-gen-tag-suggestions');
  if (!input || !box) return;
  var cfg = document.getElementById('list-gen-config');
  var url = cfg ? cfg.dataset.tagsUrl : '';
  if (!url) return;
  fetch(url)
    .then(function(r) { return r.json(); })
    .then(function(names) {
      names.forEach(function(name) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm btn-outline-secondary';
        btn.textContent = name;
        btn.addEventListener('click', function() {
          var cur = input.value.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
          if (cur.indexOf(name) === -1) cur.push(name);
          input.value = cur.join(', ');
        });
        box.appendChild(btn);
      });
    });
})();
