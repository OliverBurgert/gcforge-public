from django.http import HttpResponseNotAllowed

from preferences.models import GpxExportPreset, GPX_EXPORT_DEFAULTS, UserPreference
from ._helpers import _redirect_tab


def _get_gpx_export_settings():
    """Return current GPX export settings, merging with defaults."""
    saved = UserPreference.get("gpx_export_settings", {}) or {}
    return {**GPX_EXPORT_DEFAULTS, **saved}


def save_gpx_export(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    p = request.POST
    settings = {
        "notes_gcforge":          "notes_gcforge"    in p,
        "notes_gc_user":          "notes_gc_user"    in p,
        "notes_field_notes":      "notes_field_notes" in p,
        "notes_corrected":        "notes_corrected"  in p,
        "notes_fuse":             p.get("notes_fuse", "fuse") == "fuse",
        "wp_hidden":              "wp_hidden"         in p,
        "wp_completed":           "wp_completed"      in p,
        "wp_completed_as_hidden": "wp_completed_as_hidden" in p,
        "cc_original_as_wp":      "cc_original_as_wp" in p,
        "logs_max":               p.get("logs_max", "").strip(),
        "logs_my_on_top":         "logs_my_on_top"   in p,
        "alc_stages":             p.get("alc_stages",    "child_and_export"),
        "alc_completed":          p.get("alc_completed", "found_invisible"),
        "events_exclude_past":    "events_exclude_past" in p,
        "events_days_ahead":      p.get("events_days_ahead", "").strip(),
    }
    UserPreference.set("gpx_export_settings", settings)
    return _redirect_tab("gpx-export")


def add_gpx_export_preset(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    name = request.POST.get("preset_name", "").strip()
    if name:
        settings = _get_gpx_export_settings()
        GpxExportPreset.objects.update_or_create(name=name, defaults={"settings": settings})
    return _redirect_tab("gpx-export")


def delete_gpx_export_preset(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    pk = request.POST.get("preset_id")
    GpxExportPreset.objects.filter(pk=pk).delete()
    return _redirect_tab("gpx-export")


def load_gpx_export_preset(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    pk = request.POST.get("preset_id")
    preset = GpxExportPreset.objects.filter(pk=pk).first()
    if preset:
        merged = {**GPX_EXPORT_DEFAULTS, **preset.settings}
        UserPreference.set("gpx_export_settings", merged)
    return _redirect_tab("gpx-export")


def reset_gpx_export(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    UserPreference.set("gpx_export_settings", GPX_EXPORT_DEFAULTS)
    return _redirect_tab("gpx-export")
