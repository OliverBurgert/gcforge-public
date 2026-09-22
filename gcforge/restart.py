"""Process-restart mechanics for the in-session "Switch user" flow (P3).

Three cases, one API — see docs/multi-profile-plan.md §6:

  1. Frozen launcher (gcforge_launcher.py, packaged build) — sys.executable
     IS GCForge.exe. Respawn: subprocess.Popen a new instance, os._exit(0).
  2. Dev `manage.py runserver <addr> --noreload` — same respawn; argv already
     carries the address so the child binds the same port.
  3. Dev `manage.py runserver` *with* the autoreloader — touch a watched
     module file and let Django's own StatReloader re-exec the child. Do NOT
     respawn in this case: the watcher parent is still alive and would start
     its own replacement, giving two servers fighting for one port.

Django-aware only via lazy imports (functions, not module scope) — this
module is also touched by gcforge_launcher.py before django.setup() runs
(wait_for_port_free()), so it must stay importable without Django configured.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time


def can_auto_restart() -> tuple[bool, str]:
    """Which restart mechanism (if any) applies to the current process.

    Returns (True, mode) where mode is "frozen", "noreload", or "autoreload",
    or (False, reason) when no automatic mechanism is available — callers
    should fall back to the manual "please restart the server" message.
    """
    if getattr(sys, "frozen", False):
        return True, "frozen"

    if "runserver" in sys.argv and "--noreload" in sys.argv:
        return True, "noreload"

    if os.environ.get("RUN_MAIN") == "true":
        if "gcforge._restart_trigger" in sys.modules:
            return True, "autoreload"
        return False, "restart trigger module not loaded — falling back to manual restart"

    return False, "not running under a restart-capable server (RUN_MAIN not set)"


def wait_for_port_free(port: int, timeout: float = 15) -> bool:
    """Block until nothing answers on 127.0.0.1:port, or timeout elapses.

    Used by a respawned child before it binds — Windows SO_REUSEADDR has
    hijack semantics, so binding blind while the old process still holds the
    socket isn't safe. Returns True once the port is free, False on timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                pass
        except OSError:
            return True
        time.sleep(0.1)
    return False


def trigger_autoreload() -> None:
    """Touch gcforge/_restart_trigger.py's mtime (content unchanged) so
    Django's StatReloader — which is already watching every imported module,
    including this one (imported at the bottom of gcforge/settings.py) —
    re-execs the server child on its own.
    """
    import gcforge._restart_trigger as _trigger

    os.utime(_trigger.__file__, None)


def _respawn() -> None:
    """Spawn a new instance of this process on the same port, then exit.

    Deliberately no DETACHED_PROCESS / CREATE_NEW_CONSOLE on Windows: the
    frozen build runs console=True, and letting the child inherit the
    parent's console keeps a single window instead of popping a new one.
    """
    port = os.environ.get("GCFORGE_PORT", "")
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, *sys.argv[1:]]  # sys.executable IS GCForge.exe
    else:
        cmd = [sys.executable, *sys.argv]  # e.g. python manage.py runserver 127.0.0.1:8000 --noreload

    env = os.environ.copy()
    if port:
        env["GCFORGE_PORT"] = port
        env["GCFORGE_AWAIT_PORT"] = port

    subprocess.Popen(cmd, env=env, cwd=os.getcwd(), close_fds=True)
    os._exit(0)


def _do_restart() -> None:
    ok, mode = can_auto_restart()
    if not ok:
        return  # caller should already have guarded this via can_auto_restart()
    if mode == "autoreload":
        trigger_autoreload()
    else:
        _respawn()


def request_restart(delay_s: float = 1.5) -> None:
    """Arm a delayed restart and return immediately.

    The delay exists so the calling view's HTTP response (the "Switching to
    <name>…" page) is flushed to the client before the process starts tearing
    itself down — os._exit(0) inside the request itself would kill the
    connection mid-response. Non-negotiable per the plan.
    """
    timer = threading.Timer(delay_s, _do_restart)
    timer.daemon = True
    timer.start()
