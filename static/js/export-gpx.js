// Shared GPX export dropdown logic — used by list and detail pages.
// Requires: file-browser.js, Bootstrap, an #export-gpx-dropdown element with
//   data-export-url and data-recent-url attributes.

var _gcfExportFoldersLoaded = false;

function gcfLoadExportFolders() {
  if (_gcfExportFoldersLoaded) return;
  var dd = document.getElementById('export-gpx-dropdown');
  if (!dd) return;
  var url = dd.dataset.recentUrl;
  fetch(url).then(function(r) { return r.json(); }).then(function(data) {
    var folders = data.folders || [];
    if (!folders.length) return;
    document.getElementById('export-recent-separator').classList.remove('d-none');
    document.getElementById('export-recent-header').classList.remove('d-none');
    var menu = dd.querySelector('.dropdown-menu');
    for (var i = 0; i < folders.length; i++) {
      var li = document.createElement('li');
      var a = document.createElement('a');
      a.className = 'dropdown-item small';
      a.href = '#';
      a.textContent = folders[i].path;
      a.title = interpolate(gettext('Last used: %s'), [folders[i].date]);
      (function(p) {
        a.onclick = function(e) { e.preventDefault(); gcfExportGpxToFolder(p); };
      })(folders[i].path);
      li.appendChild(a);
      menu.appendChild(li);
    }
    _gcfExportFoldersLoaded = true;
  });
}

function gcfExportGpxDownload() {
  var dd = document.getElementById('export-gpx-dropdown');
  if (!dd) return;
  _gcfShowInfo(gettext('Preparing GPX download…'));
  window.location.href = dd.dataset.exportUrl;
}

function gcfExportGpxToFolder(folder) {
  var dd = document.getElementById('export-gpx-dropdown');
  if (!dd) return;
  var url = dd.dataset.exportUrl + '&dest=' + encodeURIComponent(folder);
  var dismissPending = _gcfShowPending(gettext('Exporting GPX…'));
  fetch(url).then(function(r) {
    if (!r.ok) return r.json().then(function(d) { throw new Error(d.error || gettext('Export failed')); });
    return r.json();
  }).then(function(data) {
    dismissPending();
    _gcfShowInfo(interpolate(
      gettext('GPX export: %(caches)s caches, %(wps)s WPs → %(dest)s'),
      {
        caches: data.cache_count || '?',
        wps: data.wp_count || 0,
        dest: data.file || folder
      }, true));
    _gcfExportFoldersLoaded = false;
  }).catch(function(err) { dismissPending(); alert(err.message || String(err)); });
}

function gcfExportGpxBrowse() {
  var modalEl = document.getElementById('fileBrowserModal');
  if (!modalEl) return;

  var titleEl = document.querySelector('#fileBrowserModal .modal-title');
  var selectBtn = document.getElementById('fb-select-btn');
  var origTitle = titleEl.textContent;
  var origBtnText = selectBtn.textContent;
  var origConfirm = window.fbConfirmSelection;

  titleEl.textContent = gettext('Select export folder');
  selectBtn.textContent = gettext('Select current folder');
  selectBtn.disabled = false;

  _fbTargetInputName = null;
  _fbExtensions = '';
  _fbMultiSelect = false;
  _fbFolderMode = true;
  _fbSelectedPath = null;
  _fbSelectedPaths = new Set();

  window.fbConfirmSelection = function() {
    if (!_fbCurrentDir) return;
    if (_fbModal) _fbModal.hide();
    gcfExportGpxToFolder(_fbCurrentDir);
  };

  var origNavigate = window.fbNavigate;
  window.fbNavigate = function(path) {
    origNavigate(path);
    selectBtn.disabled = false;
  };

  function cleanup() {
    modalEl.removeEventListener('hidden.bs.modal', cleanup);
    window.fbConfirmSelection = origConfirm;
    window.fbNavigate = origNavigate;
    _fbFolderMode = false;
    titleEl.textContent = origTitle;
    selectBtn.textContent = origBtnText;
  }
  modalEl.addEventListener('hidden.bs.modal', cleanup);

  _fbModal = bootstrap.Modal.getOrCreateInstance(modalEl);
  _fbModal.show();

  var dd = document.getElementById('export-gpx-dropdown');
  var startDir = '';
  var recentItems = dd ? dd.querySelectorAll('#export-recent-separator ~ li a') : [];
  if (recentItems.length) startDir = recentItems[0].textContent;
  fbNavigate(startDir);
}

function _gcfShowInfo(text) {
  var el = document.createElement('div');
  el.textContent = text;
  el.style.cssText = 'position:fixed;bottom:20px;right:20px;' +
    'background:rgba(0,0,0,0.8);color:#fff;padding:6px 16px;border-radius:4px;' +
    'font-size:0.85rem;z-index:9999;pointer-events:none;max-width:480px;word-break:break-all;';
  document.body.appendChild(el);
  setTimeout(function() {
    el.style.transition = 'opacity 0.4s';
    el.style.opacity = '0';
    setTimeout(function() { document.body.removeChild(el); }, 400);
  }, 4000);
}

function _gcfShowPending(text) {
  var el = document.createElement('div');
  el.style.cssText = 'position:fixed;bottom:20px;right:20px;' +
    'background:rgba(0,0,0,0.8);color:#fff;padding:6px 16px;border-radius:4px;' +
    'font-size:0.85rem;z-index:9999;pointer-events:none;max-width:480px;word-break:break-all;' +
    'display:flex;align-items:center;gap:10px;';
  var spinner = document.createElement('span');
  spinner.className = 'spinner-border spinner-border-sm text-light';
  spinner.setAttribute('role', 'status');
  var textEl = document.createElement('span');
  textEl.textContent = text;
  el.appendChild(spinner);
  el.appendChild(textEl);
  document.body.appendChild(el);
  return function() { if (el.parentNode) el.parentNode.removeChild(el); };
}
