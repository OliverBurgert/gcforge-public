// Image attachment dialog (templates/geocaches/partials/_log_image_dialog.html)
// — included once per page that uses _log_form_fields.html. Config (default
// max size / strip-EXIF prefs) comes from #logImageDialogConfig data
// attributes.

var _imgDlgCfg = document.getElementById('logImageDialogConfig');

// State
var _editIdx = null;          // null = adding new, N = editing existing
var _images  = [];            // [{file, title, desc, spoiler, rotate, maxPx, stripExif, name}]
var _platforms = [];          // e.g. ['gc', 'oc_de'] — set by gcfImageSetPlatforms()
var _defaultMaxPx   = _imgDlgCfg ? parseInt(_imgDlgCfg.dataset.defaultMaxPx, 10) : 1024;
var _defaultStripExif = _imgDlgCfg ? _imgDlgCfg.dataset.defaultStripExif === 'true' : true;

var modal   = null;  // Bootstrap Modal instance, lazily created
var _previewUrl = null; // current object URL shown in the preview, revoked on change

function _getModal() {
  if (!modal) {
    var el = document.getElementById('logImageModal');
    modal = new bootstrap.Modal(el);
  }
  return modal;
}

function _hasGC()   { return _platforms.indexOf('gc') >= 0; }
function _hasOCDE() { return _platforms.some(function(p){ return p === 'oc_de'; }); }

function _openDialog(idx) {
  _editIdx = (idx === undefined || idx === null) ? null : idx;
  var existing = (_editIdx !== null) ? _images[_editIdx] : null;

  document.getElementById('imgDlgFile').value = '';
  document.getElementById('imgDlgFileInfo').textContent = '';
  document.getElementById('imgDlgTitle').value   = existing ? existing.title   : '';
  document.getElementById('imgDlgDesc').value    = existing ? existing.desc    : '';
  document.getElementById('imgDlgSpoiler').checked = existing ? existing.spoiler : false;
  document.getElementById('imgDlgRotate').value  = existing ? String(existing.rotate) : '0';
  document.getElementById('imgDlgMaxPx').value   = existing ? String(existing.maxPx)  : String(_defaultMaxPx);
  document.getElementById('imgDlgStripExif').checked = existing ? existing.stripExif : _defaultStripExif;
  document.getElementById('imgDlgError').textContent = '';

  // Show/hide platform-specific fields
  document.getElementById('imgDlgDescWrap').style.display    = _hasGC()   ? '' : 'none';
  document.getElementById('imgDlgSpoilerWrap').style.display = _hasOCDE() ? '' : 'none';

  if (existing && existing.name) {
    document.getElementById('imgDlgFileInfo').textContent = existing.name + ' ' + gettext("(already attached; choose new file to replace)");
  }

  _setPreview(existing ? existing.file : null);
  _updatePreviewRotation();

  _getModal().show();
}

function _setPreview(file) {
  var img = document.getElementById('imgDlgPreview');
  var placeholder = document.getElementById('imgDlgPreviewPlaceholder');

  if (_previewUrl) {
    URL.revokeObjectURL(_previewUrl);
    _previewUrl = null;
  }

  if (file) {
    _previewUrl = URL.createObjectURL(file);
    img.src = _previewUrl;
    img.style.display = '';
    placeholder.style.display = 'none';
  } else {
    img.removeAttribute('src');
    img.style.display = 'none';
    placeholder.style.display = '';
  }
}

function _updatePreviewRotation() {
  var deg = parseInt(document.getElementById('imgDlgRotate').value, 10) || 0;
  document.getElementById('imgDlgPreview').style.transform = deg ? 'rotate(' + deg + 'deg)' : '';
}

function _onAttach() {
  var fileInput = document.getElementById('imgDlgFile');
  var hasNewFile = fileInput.files && fileInput.files.length > 0;
  var isEdit = (_editIdx !== null);

  if (!hasNewFile && !isEdit) {
    document.getElementById('imgDlgError').textContent = gettext("Please select a file.");
    return;
  }

  var entry = isEdit ? Object.assign({}, _images[_editIdx]) : {};
  entry.title    = document.getElementById('imgDlgTitle').value.trim();
  entry.desc     = document.getElementById('imgDlgDesc').value.trim();
  entry.spoiler  = document.getElementById('imgDlgSpoiler').checked;
  entry.rotate   = parseInt(document.getElementById('imgDlgRotate').value, 10) || 0;
  entry.maxPx    = parseInt(document.getElementById('imgDlgMaxPx').value, 10);
  entry.stripExif = document.getElementById('imgDlgStripExif').checked;

  if (hasNewFile) {
    entry.file = fileInput.files[0];
    entry.name = entry.file.name;
  }

  if (isEdit) {
    _images[_editIdx] = entry;
  } else {
    _images.push(entry);
  }

  _getModal().hide();
  _renderList();
}

function _remove(idx) {
  _images.splice(idx, 1);
  _renderList();
}

function _renderList() {
  var containers = document.querySelectorAll('[data-role="image-list"]');
  containers.forEach(function(container) {
    // Build hidden inputs + visible rows, then sync file inputs
    var html = '';
    _images.forEach(function(img, i) {
      html += '<div class="d-flex align-items-center gap-2 py-1 border-bottom" style="font-size:.8rem">';
      html += '<span class="text-truncate" style="max-width:160px" title="' + _gcfEsc(img.name) + '">' + _gcfEsc(img.name) + '</span>';
      if (img.title) html += '<span class="text-muted text-truncate" style="max-width:120px">' + _gcfEsc(img.title) + '</span>';
      if (img.spoiler) html += '<span class="badge bg-warning text-dark" style="font-size:.65rem">spoiler</span>';
      html += '<button type="button" class="btn btn-outline-secondary btn-sm py-0 ms-1" style="font-size:.75rem" onclick="gcfImageEdit(' + i + ')">' + gettext("Edit") + '</button>';
      html += '<button type="button" class="btn btn-outline-danger btn-sm py-0" style="font-size:.75rem" onclick="gcfImageRemove(' + i + ')">✕</button>';
      html += '</div>';
    });
    container.innerHTML = html;
  });

  // Sync hidden file inputs in each form that has an image-slot area
  document.querySelectorAll('[data-role="image-slots"]').forEach(function(wrap) {
    wrap.innerHTML = '';
    _images.forEach(function(img, i) {
      // We cannot programmatically set file input values, so we use a DataTransfer trick
      // and fall back to storing a data URL in a hidden text field.
      var dt = new DataTransfer();
      if (img.file) dt.items.add(img.file);

      var fileInput = document.createElement('input');
      fileInput.type = 'file';
      fileInput.name = 'image_file_' + i;
      fileInput.style.display = 'none';
      if (img.file) {
        try { fileInput.files = dt.files; } catch(e) {}
      }
      wrap.appendChild(fileInput);

      function _hidden(name, val) {
        var h = document.createElement('input');
        h.type = 'hidden'; h.name = name; h.value = val;
        wrap.appendChild(h);
      }
      _hidden('image_title_'      + i, img.title);
      _hidden('image_desc_'       + i, img.desc);
      _hidden('image_spoiler_'    + i, img.spoiler ? '1' : '0');
      _hidden('image_rotate_'     + i, img.rotate);
      _hidden('image_max_px_'     + i, img.maxPx);
      _hidden('image_strip_exif_' + i, img.stripExif ? '1' : '0');
    });
  });
}

// File info + image preview
document.getElementById('imgDlgFile').addEventListener('change', function() {
  var f = this.files[0];
  if (!f) {
    document.getElementById('imgDlgFileInfo').textContent = '';
    _setPreview(null);
    return;
  }
  var mb = (f.size / 1048576).toFixed(1);
  document.getElementById('imgDlgFileInfo').textContent = f.name + ' (' + mb + ' MB)';
  _setPreview(f);
  _updatePreviewRotation();
});

document.getElementById('imgDlgRotate').addEventListener('change', _updatePreviewRotation);

document.getElementById('imgDlgAttach').addEventListener('click', _onAttach);

// Public API
function gcfImageOpen()    { _openDialog(null); }
function gcfImageEdit(idx) { _openDialog(idx); }
function gcfImageRemove(idx) { _remove(idx); }
function gcfImageSetPlatforms(platforms) { _platforms = platforms; }
