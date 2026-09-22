// GCForge shared helpers, loaded by base.html before any page script and
// before map-loader.js (so its dynamically-injected scripts — cache-map.js,
// map-draw.js — see them too). Consolidates helpers that were previously
// duplicated per-file across static/js (see
// docs/architecture-review-2026-09-workplan.md WP-12's final commit).

// ── HTML-escape a string for safe interpolation into innerHTML ──────────────
// Canonical variant: null/undefined-safe (not just falsy — so 0 stringifies
// to "0" rather than ""), escapes & < > " and ' (the broadest of the five
// near-identical copies this replaces).
function _gcfEsc(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ── Format a distance in metres for a map tooltip/label ─────────────────────
// Two behaviours existed and are kept under distinct names rather than
// silently picking one:
//   _gcfFmtDistSafe  (was cache-detail-map.js's _gcfFmtDist) — returns '—'
//                    for non-finite input; whole metres below 1 km.
//   _gcfFmtDistRound (was map-draw.js's _gcfFmtDist) — no non-finite guard;
//                    rounds metres with Math.round instead of toFixed(0).
function _gcfFmtDistSafe(m) {
  if (!isFinite(m)) return '—';
  if (m < 1000) return m.toFixed(0) + ' m';
  return (m / 1000).toFixed(2) + ' km';
}

function _gcfFmtDistRound(m) {
  return m >= 1000 ? (m / 1000).toFixed(2) + ' km' : Math.round(m) + ' m';
}

// ── CSRF token for a fetch()/XHR call ────────────────────────────────────────
// Tries the page's csrfmiddlewaretoken input first (present on pages with a
// Django form), falls back to the csrftoken cookie (always present once the
// user has loaded any page).
function _gcfTbCsrf() {
  var input = document.querySelector('input[name=csrfmiddlewaretoken]');
  if (input && input.value) return input.value;
  var m = document.cookie.match(/csrftoken=([^;]+)/);
  return m ? m[1] : '';
}
