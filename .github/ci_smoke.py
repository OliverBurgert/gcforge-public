#!/usr/bin/env python3
"""CI smoke test: launch the built GCForge bundle and confirm it serves HTTP.

Runs the PyInstaller artifact (dist/GCForge/GCForge[.exe]) on a known port
(via GCFORGE_PORT) and polls http://127.0.0.1:<port>/ until it answers — proving
the bundled Django app actually started and serves a page. Exits non-zero if it
never comes up, crashes, or times out, so a broken build fails the job before it
is packaged/released.

A fixed port + an active HTTP check is deliberate: a frozen app block-buffers
stdout when piped, so a stdout "ready" marker (and the printed port) can't be
relied on. Stdlib only; runs under the setup-python interpreter.
"""
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

FAIL_MARKER = "did not start"
STARTUP_TIMEOUT_S = 150


def _binary() -> Path:
    base = Path("dist") / "GCForge"
    for name in ("GCForge.exe", "GCForge"):
        candidate = base / name
        if candidate.exists():
            return candidate
    print(f"ERROR: built binary not found under {base}/", file=sys.stderr)
    sys.exit(2)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill(proc: subprocess.Popen) -> None:
    """Kill the launcher (it blocks forever on server_thread.join) and any children."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _serves(port: int) -> bool:
    """True if the local server answers / with a non-error status (redirects ok)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as resp:
            return 200 <= resp.status < 400
    except urllib.error.HTTPError as exc:
        print(f"  / returned HTTP {exc.code} — server up but unhealthy", flush=True)
        return False
    except Exception:
        return False  # not accepting connections yet


def main() -> int:
    binary = _binary()
    port = _free_port()
    print(f"==> Launching {binary} on 127.0.0.1:{port} ...", flush=True)

    env = dict(os.environ, GCFORGE_PORT=str(port), PYTHONUNBUFFERED="1")
    popen_kwargs = {} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(
        [str(binary)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        **popen_kwargs,
    )

    state = {"failed": False}

    def reader() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            sys.stdout.write(line)
            sys.stdout.flush()
            if FAIL_MARKER in line:
                state["failed"] = True

    threading.Thread(target=reader, daemon=True).start()

    ok = False
    deadline = time.time() + STARTUP_TIMEOUT_S
    while time.time() < deadline:
        if state["failed"]:
            print("\nERROR: launcher reported the server did not start.", file=sys.stderr)
            break
        if proc.poll() is not None:
            print(f"\nERROR: app exited early (exit code {proc.returncode}) without serving.",
                  file=sys.stderr)
            break
        if _serves(port):
            print(f"==> SMOKE OK: GCForge started and is serving on 127.0.0.1:{port}.")
            ok = True
            break
        time.sleep(1.0)
    else:
        print(f"\nERROR: app did not serve within {STARTUP_TIMEOUT_S}s.", file=sys.stderr)

    _kill(proc)
    if ok:
        return 0
    print("==> SMOKE FAILED — see output above.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
