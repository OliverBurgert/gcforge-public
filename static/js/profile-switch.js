// Polling logic for the "Switching to <name>..." reconnecting page
// (templates/preferences/switching_profile.html).
//
// Extracted here — rather than left inline in the template — so it's
// unit-testable (see static/js/__tests__/profile-switch.test.js). The
// template still ships it inline via {% include %} into a <script> block
// instead of a <script src> tag: the page must stay fully self-contained
// while the server is mid-restart (no {% static %} fetch would succeed
// during the gap), see docs/multi-profile-plan.md R8.
//
// Plain function declaration (not a `window.xxx =` assignment) per project
// convention for globally-called functions — see
// feedback_external_js_functions.md.

function gcfProfileSwitchPoll(opts) {
  opts = opts || {};
  var maxAttempts = opts.maxAttempts || 40;
  var delayMs = opts.delayMs || 750;
  var fetchFn = opts.fetchFn || (typeof fetch !== 'undefined' ? fetch.bind(window) : null);
  var onReconnected = opts.onReconnected || function () { location.reload(); };
  var onGiveUp = opts.onGiveUp || function () {
    var fallback = document.getElementById('gcf-manual-link');
    if (fallback) fallback.style.display = '';
  };

  var attempts = 0;

  function tick() {
    attempts += 1;
    fetchFn(location.origin + '/', { cache: 'no-store' }).then(
      function () { onReconnected(); },
      function () {
        if (attempts >= maxAttempts) {
          onGiveUp();
        } else {
          setTimeout(tick, delayMs);
        }
      }
    );
  }

  tick();
}
