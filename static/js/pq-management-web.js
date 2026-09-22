// Pocket Queries via Website page (templates/geocaches/pq_management_web.html):
// wires tag-suggestion chips (fetched once) into both the "Ready for
// Download" and "Active Pocket Queries" tag inputs. Endpoint URL comes from
// the #pq-web-config data-* element.

var _pqWebCfg = document.getElementById('pq-web-config');
function _pqWebUrl(name) { return _pqWebCfg ? _pqWebCfg.dataset[name] : ''; }

function gcfPqWebWireTagSuggestions(inputId, boxId, names) {
  var input = document.getElementById(inputId);
  var box = document.getElementById(boxId);
  if (!input || !box) return;
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
}

fetch(_pqWebUrl('tagsUrl'))
  .then(function(r) { return r.json(); })
  .then(function(names) {
    gcfPqWebWireTagSuggestions('pq-web-ready-tags-input', 'pq-web-ready-tag-suggestions', names);
    gcfPqWebWireTagSuggestions('pq-web-active-tags-input', 'pq-web-active-tag-suggestions', names);
  });
