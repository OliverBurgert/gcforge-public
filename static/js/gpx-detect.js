/* GPX format auto-detection for the import page */

var _detectTimer = null;

function _getDetectUrl() {
  var input = document.getElementById('gpx_path');
  return input ? input.dataset.detectUrl : '';
}

function detectGpxFormat(path) {
  if (!path) {
    _hideFormatBadge();
    _enableWpts();
    return;
  }
  var url = _getDetectUrl();
  if (!url) return;
  // Debounce: wait 300ms after last keystroke
  clearTimeout(_detectTimer);
  _detectTimer = setTimeout(function() {
    fetch(url + '?path=' + encodeURIComponent(path))
      .then(function(r) { return r.json(); })
      .then(function(data) { _applyFormat(data.format); })
      .catch(function() { _applyFormat('unknown'); });
  }, 300);
}

function _applyFormat(fmt) {
  var badge = document.getElementById('format-badge');
  var label = document.getElementById('format-label');
  var wptsSel = document.getElementById('include_wpts');
  var wptsHelp = document.getElementById('wpts-help');

  if (!badge || !label) return;

  if (fmt === 'oc') {
    label.textContent = 'Opencaching';
    label.className = 'badge bg-success';
    badge.style.display = '';
    // OC files have waypoints inline — disable the dropdown
    wptsSel.value = 'include';
    wptsSel.disabled = true;
    wptsHelp.textContent = 'Opencaching files include waypoints inline — no companion file needed.';
  } else if (fmt === 'gc') {
    label.textContent = 'geocaching.com';
    label.className = 'badge bg-primary';
    badge.style.display = '';
    _enableWpts();
  } else {
    _hideFormatBadge();
    _enableWpts();
  }
}

function _hideFormatBadge() {
  var badge = document.getElementById('format-badge');
  if (badge) badge.style.display = 'none';
}

function _enableWpts() {
  var wptsSel = document.getElementById('include_wpts');
  var wptsHelp = document.getElementById('wpts-help');
  if (wptsSel) wptsSel.disabled = false;
  if (wptsHelp) {
    wptsHelp.innerHTML =
      'When importing a <code>.gpx</code> file, the companion <code>-wpts.gpx</code> ' +
      'file in the same folder is detected and imported automatically. ' +
      'For <code>.zip</code> archives, waypoints inside the archive are always included.';
  }
}

function selectRecentGpx(path) {
  var input = document.querySelector('[name=gpx_path]');
  if (input) {
    input.value = path;
    detectGpxFormat(path);
  }
}

// Listen for changes on the path input (also fires after file browser selection)
document.addEventListener('DOMContentLoaded', function() {
  var input = document.getElementById('gpx_path');
  if (!input) return;

  input.addEventListener('change', function() {
    detectGpxFormat(this.value.trim());
  });
});
