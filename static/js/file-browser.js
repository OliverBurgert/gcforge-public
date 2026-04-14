/* Server-side file browser for GCForge import pages */

var _fbTargetInputName = null;
var _fbExtensions = "";
var _fbSelectedPath = null;
var _fbSelectedPaths = new Set();
var _fbMultiSelect = false;
var _fbFolderMode = false;
var _fbCurrentDir = "";
var _fbModal = null;

function fbOpen(targetInputName, extensions, multiSelect) {
  _fbTargetInputName = targetInputName;
  _fbExtensions = extensions || "";
  _fbSelectedPath = null;
  _fbSelectedPaths = new Set();
  _fbMultiSelect = !!multiSelect;
  _fbFolderMode = false;

  var modalEl = document.getElementById("fileBrowserModal");
  _fbModal = bootstrap.Modal.getOrCreateInstance(modalEl);

  // Update modal title
  document.querySelector("#fileBrowserModal .modal-title").textContent =
    _fbMultiSelect ? "Select file(s) or folder" : "Select file";

  // Reset UI
  _fbUpdateFooter();
  document.getElementById("fb-select-btn").disabled = true;

  // Start from the current value in the target input, or default
  var input = document.querySelector("[name='" + _fbTargetInputName + "']");
  var startDir = "";
  if (input && input.value.trim()) {
    var val = input.value.trim().split(";")[0]; // take first path if multi
    var lastSep = Math.max(val.lastIndexOf("\\"), val.lastIndexOf("/"));
    if (lastSep > 0) {
      startDir = val.substring(0, lastSep);
    } else {
      startDir = val;
    }
  }

  _fbModal.show();
  fbNavigate(startDir);
}

function fbNavigate(path) {
  _fbSelectedPath = null;
  if (!_fbMultiSelect) _fbSelectedPaths = new Set();
  _fbUpdateFooter();
  document.getElementById("fb-select-btn").disabled = !_fbFolderMode;

  var browseUrl = document.getElementById("fileBrowserModal").dataset.browseUrl;
  var url = browseUrl + "?ext=" + encodeURIComponent(_fbExtensions);
  if (path) {
    url += "&dir=" + encodeURIComponent(path);
  }

  fetch(url)
    .then(function(resp) {
      if (!resp.ok) {
        return resp.json().then(function(data) {
          throw new Error(data.error || "Request failed");
        });
      }
      return resp.json();
    })
    .then(function(data) {
      _fbCurrentDir = data.current;
      document.getElementById("fb-path-input").value = data.current;
      _fbRenderListing(data.entries);
      if (_fbFolderMode) {
        document.getElementById("fb-selected-display").textContent = data.current;
        document.getElementById("fb-select-btn").disabled = false;
      }
    })
    .catch(function(err) {
      document.getElementById("fb-listing").innerHTML =
        '<div class="text-danger small p-2">' + _fbEscape(err.message) + '</div>';
    });
}

function fbNavigateFromInput() {
  var path = document.getElementById("fb-path-input").value.trim();
  if (path) {
    fbNavigate(path);
  }
}

function fbSelect(path) {
  _fbSelectedPath = path;
  if (!_fbMultiSelect) _fbSelectedPaths = new Set();
  _fbSelectedPaths.add(path);
  _fbUpdateFooter();
  document.getElementById("fb-select-btn").disabled = false;

  // Highlight selected row
  var rows = document.querySelectorAll("#fb-listing .fb-entry");
  for (var i = 0; i < rows.length; i++) {
    rows[i].classList.remove("active");
    if (_fbMultiSelect) {
      if (_fbSelectedPaths.has(rows[i].dataset.path)) {
        rows[i].classList.add("active");
      }
    } else if (rows[i].dataset.path === path) {
      rows[i].classList.add("active");
    }
  }
}

function fbToggleSelect(path) {
  if (_fbSelectedPaths.has(path)) {
    _fbSelectedPaths.delete(path);
  } else {
    _fbSelectedPaths.add(path);
  }
  _fbSelectedPath = _fbSelectedPaths.size > 0 ? path : null;
  _fbUpdateFooter();
  document.getElementById("fb-select-btn").disabled = _fbSelectedPaths.size === 0;

  // Update highlights
  var rows = document.querySelectorAll("#fb-listing .fb-entry");
  for (var i = 0; i < rows.length; i++) {
    if (_fbSelectedPaths.has(rows[i].dataset.path)) {
      rows[i].classList.add("active");
    } else {
      rows[i].classList.remove("active");
    }
  }
}

function fbSelectFolder() {
  if (!_fbCurrentDir || !_fbTargetInputName) return;
  var input = document.querySelector("[name='" + _fbTargetInputName + "']");
  if (input) {
    input.value = _fbCurrentDir;
    input.dispatchEvent(new Event("change"));
  }
  if (_fbModal) _fbModal.hide();
}

function fbConfirmSelection() {
  if (!_fbTargetInputName) return;

  var input = document.querySelector("[name='" + _fbTargetInputName + "']");
  if (!input) return;

  if (_fbMultiSelect && _fbSelectedPaths.size > 0) {
    input.value = Array.from(_fbSelectedPaths).join(";");
  } else if (_fbSelectedPath) {
    input.value = _fbSelectedPath;
  } else {
    return;
  }
  input.dispatchEvent(new Event("change"));
  if (_fbModal) _fbModal.hide();
}

function _fbUpdateFooter() {
  var display = document.getElementById("fb-selected-display");
  if (_fbMultiSelect && _fbSelectedPaths.size > 1) {
    display.textContent = _fbSelectedPaths.size + " files selected";
  } else if (_fbSelectedPath) {
    display.textContent = _fbSelectedPath;
  } else {
    display.textContent = "";
  }
}

function _fbRenderListing(entries) {
  var listing = document.getElementById("fb-listing");
  if (!entries || entries.length === 0) {
    listing.innerHTML = '<div class="text-muted small p-2">Empty directory</div>';
    return;
  }

  var html = '';

  // "Select this folder" button in multi-select mode
  if (_fbMultiSelect) {
    html += '<div class="p-2 border-bottom">'
      + '<button type="button" class="btn btn-sm btn-outline-secondary" onclick="fbSelectFolder()">'
      + '&#128193; Select this folder</button></div>';
  }

  html += '<div class="list-group list-group-flush">';
  for (var i = 0; i < entries.length; i++) {
    var e = entries[i];
    if (e.is_dir) {
      html += '<a href="#" class="list-group-item list-group-item-action py-1 px-2 small fb-entry"'
        + ' data-path="' + _fbEscape(e.path) + '"'
        + ' onclick="fbNavigate(\'' + _fbEscapeJs(e.path) + '\'); return false;">'
        + '<span class="me-2">&#128193;</span>'
        + '<span>' + _fbEscape(e.name) + '</span>'
        + '</a>';
    } else {
      var isSelected = _fbSelectedPaths.has(e.path);
      if (_fbMultiSelect) {
        html += '<a href="#" class="list-group-item list-group-item-action py-1 px-2 small fb-entry'
          + (isSelected ? ' active' : '') + '"'
          + ' data-path="' + _fbEscape(e.path) + '"'
          + ' onclick="fbToggleSelect(\'' + _fbEscapeJs(e.path) + '\'); return false;"'
          + ' ondblclick="fbToggleSelect(\'' + _fbEscapeJs(e.path) + '\'); return false;">'
          + '<span class="me-2">&#128196;</span>'
          + '<span>' + _fbEscape(e.name) + '</span>'
          + '<span class="text-muted ms-2">' + _fbFormatSize(e.size) + '</span>'
          + '</a>';
      } else {
        html += '<a href="#" class="list-group-item list-group-item-action py-1 px-2 small fb-entry"'
          + ' data-path="' + _fbEscape(e.path) + '"'
          + ' onclick="fbSelect(\'' + _fbEscapeJs(e.path) + '\'); return false;"'
          + ' ondblclick="fbSelect(\'' + _fbEscapeJs(e.path) + '\'); fbConfirmSelection(); return false;">'
          + '<span class="me-2">&#128196;</span>'
          + '<span>' + _fbEscape(e.name) + '</span>'
          + '<span class="text-muted ms-2">' + _fbFormatSize(e.size) + '</span>'
          + '</a>';
      }
    }
  }
  html += '</div>';
  listing.innerHTML = html;
}

function _fbFormatSize(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function _fbEscape(str) {
  var div = document.createElement("div");
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}

function _fbEscapeJs(str) {
  return str.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}
