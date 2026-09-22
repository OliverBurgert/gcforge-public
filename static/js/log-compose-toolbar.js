// Log compose toolbar (templates/geocaches/partials/_log_compose_toolbar.html)
// — formatting buttons + smiley/template pickers, operating on the textarea
// identified by the toolbar's data-textarea-id (default "logFormText").

// Resolve the active textarea per click — falls back to the legacy id
// "logFormText" when the toolbar's data-textarea-id is missing or stale.
// This keeps multiple toolbars on a page (e.g. compose dialog + template
// editor) independent without globals fighting each other.
function _gcfFmtTa(srcEl) {
  var src = srcEl || (typeof event !== 'undefined' ? event.currentTarget : null);
  var toolbar = src && src.closest ? src.closest('[data-textarea-id]') : null;
  var id = (toolbar && toolbar.getAttribute('data-textarea-id')) || 'logFormText';
  return document.getElementById(id);
}

function _gcfFmtSelection(ta) {
  return {
    start: ta.selectionStart || 0,
    end:   ta.selectionEnd   || 0,
    value: ta.value,
  };
}

function _gcfFmtReplace(ta, start, end, replacement, cursorStart, cursorEnd) {
  ta.value = ta.value.substring(0, start) + replacement + ta.value.substring(end);
  var newStart = (typeof cursorStart === 'number') ? cursorStart : start + replacement.length;
  var newEnd   = (typeof cursorEnd   === 'number') ? cursorEnd   : newStart;
  ta.selectionStart = newStart;
  ta.selectionEnd   = newEnd;
  ta.focus();
  ta.dispatchEvent(new Event('input', { bubbles: true }));
}

// Returns the extra leading newlines needed to guarantee a blank line
// before the block starting at `lineStart`. Block-level markdown on gc.com
// (lists, blockquotes, HR) requires a preceding blank line — without it,
// lists render as literal '* foo' and a leading '---' becomes a setext h2
// of the previous line.
function _gcfFmtBlankLineLead(value, lineStart) {
  if (lineStart === 0) return '';
  if (lineStart === 1 && value[0] === '\n') return '';
  if (value[lineStart - 2] === '\n') return '';
  return '\n';
}

// Wrap selection (or insert at cursor) with `before`/`after` markers.
function gcfFmtWrap(before, after) {
  var ta = _gcfFmtTa(); if (!ta) return;
  var s = _gcfFmtSelection(ta);
  var selected = s.value.substring(s.start, s.end);
  var replacement = before + selected + after;
  if (selected) {
    _gcfFmtReplace(ta, s.start, s.end, replacement,
             s.start + before.length,
             s.start + before.length + selected.length);
  } else {
    _gcfFmtReplace(ta, s.start, s.end, replacement,
             s.start + before.length, s.start + before.length);
  }
}

// Insert literal text at cursor.
function gcfFmtInsert(text) {
  var ta = _gcfFmtTa(); if (!ta) return;
  var s = _gcfFmtSelection(ta);
  _gcfFmtReplace(ta, s.start, s.end, text);
}

// Insert `text` on its own line, guaranteeing a blank line before it
// (needed for HR — without a blank, gc.com turns "prev\n---" into an h2).
function gcfFmtInsertLine(text) {
  var ta = _gcfFmtTa(); if (!ta) return;
  var s = _gcfFmtSelection(ta);
  var before = s.value.substring(0, s.start);
  var after  = s.value.substring(s.end);
  var lead;
  if (before.length === 0)            lead = '';
  else if (before.endsWith('\n\n'))   lead = '';
  else if (before.endsWith('\n'))     lead = '\n';
  else                                lead = '\n\n';
  var trail = (after.length === 0 || after.startsWith('\n')) ? '' : '\n';
  _gcfFmtReplace(ta, s.start, s.end, lead + text + trail);
}

// Prefix every selected line (or current line if no selection) with `prefix`.
// Inserts a blank line before the block when missing, so list/quote markdown
// is rendered correctly on gc.com (it requires a blank line above lists).
function gcfFmtLines(prefix) {
  var ta = _gcfFmtTa(); if (!ta) return;
  var s = _gcfFmtSelection(ta);
  var before = s.value.substring(0, s.start);
  var after  = s.value.substring(s.end);
  var lineStart = before.lastIndexOf('\n') + 1;
  var lineEndOffset = after.indexOf('\n');
  var lineEnd = (lineEndOffset === -1) ? s.value.length : s.end + lineEndOffset;
  var block = s.value.substring(lineStart, lineEnd);
  var prefixed = block.split('\n').map(function (line) {
    return line.length ? prefix + line : line;
  }).join('\n');
  var lead = _gcfFmtBlankLineLead(s.value, lineStart);
  var replacement = lead + prefixed;
  _gcfFmtReplace(ta, lineStart, lineEnd, replacement,
           lineStart + lead.length, lineStart + replacement.length);
}

// Number every selected line (or current line) as 1., 2., 3., ...
// Same blank-line-before guarantee as gcfFmtLines.
function gcfFmtNumbered() {
  var ta = _gcfFmtTa(); if (!ta) return;
  var s = _gcfFmtSelection(ta);
  var before = s.value.substring(0, s.start);
  var after  = s.value.substring(s.end);
  var lineStart = before.lastIndexOf('\n') + 1;
  var lineEndOffset = after.indexOf('\n');
  var lineEnd = (lineEndOffset === -1) ? s.value.length : s.end + lineEndOffset;
  var block = s.value.substring(lineStart, lineEnd);
  var n = 0;
  var numbered = block.split('\n').map(function (line) {
    if (!line.length) return line;
    n += 1;
    return n + '. ' + line;
  }).join('\n');
  var lead = _gcfFmtBlankLineLead(s.value, lineStart);
  var replacement = lead + numbered;
  _gcfFmtReplace(ta, lineStart, lineEnd, replacement,
           lineStart + lead.length, lineStart + replacement.length);
}

// Insert a template body at the cursor. The body has been pre-expanded
// server-side against the cache context; [find_count] is substituted here
// from the current "Find #" input value (the user-visible source of
// truth for find numbering — manual edits stick).
function gcfFmtInsertTemplate(body) {
  var ta = _gcfFmtTa(); if (!ta) return;
  var seqInput = document.querySelector('input[name="sequence_number"]');
  var findCount = (seqInput && seqInput.value) ? seqInput.value : '';
  body = body.split('[find_count]').join(findCount);
  var s = _gcfFmtSelection(ta);
  _gcfFmtReplace(ta, s.start, s.end, body);
}

// Hide template entries whose scope doesn't match the currently selected
// log type ("any" templates are always shown).
function _gcfFmtFilterTemplatesByLogType() {
  var logTypeSel = document.querySelector('select[name="log_type"]');
  var current = logTypeSel ? logTypeSel.value : '';
  var entries = document.querySelectorAll('[data-template-scope]');
  for (var i = 0; i < entries.length; i++) {
    var scope = entries[i].getAttribute('data-template-scope');
    var li = entries[i].closest('li');
    if (!li) continue;
    li.style.display = (scope === 'any' || scope === current) ? '' : 'none';
  }
}
document.addEventListener('DOMContentLoaded', function () {
  _gcfFmtFilterTemplatesByLogType();
  var sel = document.querySelector('select[name="log_type"]');
  if (sel) sel.addEventListener('change', _gcfFmtFilterTemplatesByLogType);
});

// Prompt for URL, wrap selection (or 'link') as Markdown link.
function gcfFmtLink() {
  var ta = _gcfFmtTa(); if (!ta) return;
  var s = _gcfFmtSelection(ta);
  var url = window.prompt(gettext("Link URL:"), 'https://');
  if (!url) return;
  var label = s.value.substring(s.start, s.end) || 'link';
  var replacement = '[' + label + '](' + url + ')';
  _gcfFmtReplace(ta, s.start, s.end, replacement);
}
