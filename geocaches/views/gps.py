"""
GPS device endpoints — Send-to-GPS (Phase 1) and (later) field-notes
round-trip and track sync. See ``docs/gps-export-plan.md``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from geocaches.services.gps_device import detect_garmin_at_path, detect_garmin_devices

from .list import _filtered_qs

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str, default: str) -> str:
    """Reduce a user-supplied filename to ASCII alnum + ``-_.``.

    Garmin's file-system tolerates that subset reliably across all models.
    Empty input falls back to ``default``.
    """
    name = (name or "").strip()
    if not name:
        return default
    if "/" in name or "\\" in name or ".." in name:
        # Path-traversal attempt — drop it and use the default.
        return default
    if not name.lower().endswith(".gpx"):
        name = f"{name}.gpx"
    cleaned = _FILENAME_SAFE_RE.sub("_", name)
    return cleaned or default


def gps_detect_devices(request):
    """Scan removable mount points for connected Garmin handhelds.

    Returns ``{"devices": [{"path", "label", "model", "software_version"}, ...]}``.
    Empty list when no device is detected.
    """
    devices = detect_garmin_devices()
    return JsonResponse({"devices": [
        {
            "path": d.mount_path,
            "label": d.label,
            "model": d.model,
            "software_version": d.software_version,
        }
        for d in devices
    ]})


def gps_recent_devices(request):
    """Return the user's recent GPS-device folders as JSON.

    Each entry: ``{"path": "G:\\\\", "label": "Oregon 700", "date": "..."}``.
    Persisted by ``send_to_gps`` after every successful send.
    """
    from preferences.models import UserPreference
    devices = UserPreference.get("recent_gps_devices", []) or []
    return JsonResponse({"devices": devices})


@require_POST
def send_to_gps(request):
    """Write the current filter+target qs as a GPX file onto a Garmin device.

    Filter / target params are read from the URL query string (so
    ``_filtered_qs`` picks them up). The device root path comes from POST
    body. Returns JSON with the result; never raises to the client.
    """
    from geocaches.services import export_caches
    from preferences.models import GPX_EXPORT_DEFAULTS, UserPreference

    device_root = (request.POST.get("device_root") or "").strip()
    if not device_root:
        return JsonResponse({"ok": False, "error": "No device folder selected."}, status=400)

    root = Path(device_root)
    if not root.is_dir():
        return JsonResponse(
            {"ok": False, "error": f"Folder does not exist: {device_root}"},
            status=400,
        )

    device = detect_garmin_at_path(root)
    if device is None:
        return JsonResponse(
            {"ok": False, "error": "Not a Garmin device folder (missing Garmin/GarminDevice.xml)."},
            status=400,
        )

    qs, _fv = _filtered_qs(request)
    count = qs.count()
    if count == 0:
        return JsonResponse(
            {"ok": False, "error": "No caches match the current selection."},
            status=400,
        )

    # Build GPX bytes using existing exporter — same path as Export GPX.
    gc_username = UserPreference.get("gc_username", "")
    saved_opts = UserPreference.get("gpx_export_settings", {}) or {}
    opts = {**GPX_EXPORT_DEFAULTS, **saved_opts}
    data = export_caches(qs, username=gc_username, opts=opts)

    # Resolve filename and target folder.
    default_filename = f"gcforge-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.gpx"
    filename = _safe_filename(request.POST.get("filename", ""), default_filename)

    gpx_dir = root / device.gpx_folder.replace("\\", "/")
    try:
        gpx_dir.mkdir(parents=True, exist_ok=True)
        out_path = gpx_dir / filename
        out_path.write_bytes(data)
    except OSError as exc:
        return JsonResponse({"ok": False, "error": f"Write failed: {exc}"}, status=400)

    # Remember device for the dropdown's recent list.
    recent = UserPreference.get("recent_gps_devices", []) or []
    recent = [r for r in recent if r.get("path") != str(root)]
    recent.insert(0, {
        "path": str(root),
        "label": device.label,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    })
    UserPreference.set("recent_gps_devices", recent[:5])

    return JsonResponse({
        "ok": True,
        "model": device.label,
        "count": count,
        "path": str(out_path),
        "filename": filename,
    })
