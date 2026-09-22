"""Pre-Django first-run profile picker.

Stdlib-only, like gcforge/profiles.py — this runs *before* django.setup() (see
blocker B5 in docs/multi-profile-plan.md: touching django.conf.settings before
the conf is written freezes the wrong DB for the rest of the process). Serves
a single self-contained HTML page from a plain http.server on the app's own
port, lets the user pick (or rename) a profile, writes gcforge.conf via
gcforge.profiles, then shuts itself down so gcforge_launcher.main() can
continue booting Django on the same port.

English-only by decision (2026-08-18): this page runs before Django's i18n
machinery exists, and translating it would need either a hardcoded-bilingual
page or a pre-Django language hint. Given how rarely it's seen (once per
profile-count change, only in the packaged launcher), plain English was
chosen over that complexity — see docs/multi-profile-plan.md §11.
"""

from __future__ import annotations

import html as _html
import http.server
import os
import threading
import urllib.parse
import webbrowser
from pathlib import Path

from gcforge import profiles as gcforge_profiles


def _format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


_PAGE_CSS = """
  body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
         background: #f4f5f7; color: #212529; margin: 0; padding: 2.5rem 1rem; }
  .wrap { max-width: 30rem; margin: 0 auto; }
  h1 { font-size: 1.35rem; margin: 0 0 .25rem; }
  p.sub { color: #6c757d; margin: 0 0 1.5rem; font-size: .9rem; }
  .error { background: #f8d7da; color: #842029; border: 1px solid #f5c2c7;
           border-radius: .375rem; padding: .5rem .75rem; margin-bottom: 1rem; font-size: .9rem; }
  .card { background: #fff; border: 1px solid #dee2e6; border-radius: .5rem;
          margin-bottom: .75rem; padding: .9rem 1rem; }
  .row { display: flex; align-items: center; gap: .75rem; }
  .name { font-weight: 600; flex: 1 1 auto; }
  .size { color: #6c757d; font-size: .8rem; white-space: nowrap; }
  button, .btn { font: inherit; font-size: .85rem; padding: .35rem .75rem; border-radius: .375rem;
         border: 1px solid #0d6efd; background: #0d6efd; color: #fff; cursor: pointer; }
  button.secondary { background: #fff; color: #495057; border-color: #ced4da; }
  .rename-form { display: none; margin-top: .6rem; gap: .5rem; }
  .rename-form.open { display: flex; }
  .rename-form input[type=text] { flex: 1 1 auto; font: inherit; font-size: .85rem;
         padding: .3rem .5rem; border: 1px solid #ced4da; border-radius: .375rem; }
  .toggle-rename { background: none; border: none; color: #6c757d; font-size: .8rem;
         text-decoration: underline; cursor: pointer; padding: 0; margin-top: .4rem; }
"""

_PICKER_JS = """
  function toggleRename(id) {
    document.getElementById('rename-' + id).classList.toggle('open');
  }
"""

_RECONNECT_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>GCForge</title>
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
         background: #f4f5f7; color: #212529; display: flex; align-items: center;
         justify-content: center; height: 100vh; margin: 0; }
  .box { text-align: center; }
  .spinner { width: 2rem; height: 2rem; border: 3px solid #dee2e6; border-top-color: #0d6efd;
             border-radius: 50%; margin: 0 auto 1rem; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style></head>
<body>
  <div class="box">
    <div class="spinner"></div>
    <p>Starting GCForge&hellip;</p>
  </div>
  <script>
    (function poll() {
      fetch(location.origin + '/', { cache: 'no-store' })
        .then(function () { location.reload(); })
        .catch(function () { setTimeout(poll, 500); });
    })();
    setTimeout(function () {
      (function retry() {
        fetch(location.origin + '/', { cache: 'no-store' })
          .then(function () { location.reload(); })
          .catch(function () { setTimeout(retry, 500); });
      })();
    }, 500);
  </script>
</body></html>
"""


def _render_picker_page(profile_list: list, error: str | None = None) -> str:
    error_html = f'<div class="error">{_html.escape(error)}</div>' if error else ""
    rows = []
    for i, p in enumerate(profile_list):
        path_attr = _html.escape(str(p.path))
        name = _html.escape(p.display_name)
        rows.append(f"""
        <div class="card">
          <div class="row">
            <span class="name">{name}</span>
            <span class="size">{_format_size(p.size)}</span>
            <form method="post" action="/choose">
              <input type="hidden" name="path" value="{path_attr}">
              <button type="submit">Switch</button>
            </form>
          </div>
          <button type="button" class="toggle-rename" onclick="toggleRename({i})">Rename&hellip;</button>
          <form method="post" action="/rename" class="rename-form" id="rename-{i}">
            <input type="hidden" name="path" value="{path_attr}">
            <input type="text" name="new_name" placeholder="New name" pattern="[a-zA-Z0-9_-]+" required>
            <button type="submit" class="secondary">Save</button>
          </form>
        </div>""")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>GCForge — Who's using this?</title>
<style>{_PAGE_CSS}</style></head>
<body>
  <div class="wrap">
    <h1>Who's using GCForge?</h1>
    <p class="sub">Multiple profiles were found on this computer. Pick one to continue.</p>
    {error_html}
    {''.join(rows)}
  </div>
  <script>{_PICKER_JS}</script>
</body></html>
"""


def maybe_pick_profile(base_dir: Path, data_dir: Path, port: int) -> bool:
    """Show the first-run picker if 2+ profiles exist and no explicit override is set.

    Returns True if the picker was shown and a browser tab was opened on
    `port` for it (so the caller should skip its own webbrowser.open() call —
    this same tab polls itself back once the real server comes up).
    """
    if os.environ.get("GCFORGE_DATABASE"):
        return False

    profile_list = gcforge_profiles.list_profiles(data_dir)
    if len(profile_list) < 2:
        return False

    result = {"chosen": False}
    server_holder: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass  # keep the launcher console output limited to the printed picker URL

        def _write(self, body: bytes, status: int = 200, content_type: str = "text/html; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def _serve_picker(self, error: str | None = None):
            html_str = _render_picker_page(gcforge_profiles.list_profiles(data_dir), error)
            self._write(html_str.encode("utf-8"))

        def do_GET(self):
            if self.path.split("?", 1)[0] not in ("/", ""):
                self._write(b"Not found", status=404, content_type="text/plain")
                return
            self._serve_picker()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw_body = self.rfile.read(length).decode("utf-8") if length else ""
            fields = urllib.parse.parse_qs(raw_body)
            raw_path = (fields.get("path") or [""])[0]

            if self.path == "/choose":
                self._handle_choose(raw_path)
            elif self.path == "/rename":
                new_name = (fields.get("new_name") or [""])[0]
                self._handle_rename(raw_path, new_name)
            else:
                self._write(b"Not found", status=404, content_type="text/plain")

        def _handle_choose(self, raw_path: str):
            target = Path(raw_path)
            known = {p.path.resolve() for p in gcforge_profiles.list_profiles(data_dir)}
            if not raw_path or target.resolve() not in known:
                self._serve_picker(error="Not a known profile.")
                return

            default_path = gcforge_profiles.default_profile_path(data_dir)
            if target.resolve() == default_path.resolve():
                gcforge_profiles.clear_active_db(base_dir, data_dir)
            else:
                gcforge_profiles.write_active_db(target, base_dir, data_dir)

            result["chosen"] = True
            self._write(_RECONNECT_HTML.encode("utf-8"))
            # Called from this request's own worker thread (ThreadingHTTPServer
            # spawns one per request) — safe to stop serve_forever() from here;
            # the response above has already been written and flushed.
            threading.Thread(target=server_holder["server"].shutdown, daemon=True).start()

        def _handle_rename(self, raw_path: str, new_name: str):
            try:
                gcforge_profiles.rename_profile(Path(raw_path), new_name, base_dir, data_dir)
                self._serve_picker()
            except gcforge_profiles.ProfileError as exc:
                self._serve_picker(error=str(exc))

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server_holder["server"] = server
    url = f"http://127.0.0.1:{port}/"
    print(f"Multiple profiles found. Choose one in your browser: {url}", flush=True)
    webbrowser.open(url)
    server.serve_forever()
    server.server_close()
    return result["chosen"]
