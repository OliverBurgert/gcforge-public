from datetime import datetime, timezone

from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _

from ..models import CorrectedCoordinates, Log, Note
from .detail import _get_cache, _parse_image_attachments, _parse_logged_at


def log_submit(request, gc_code):
    """Submit a new log for this cache to platform(s) and store locally."""
    from django.contrib import messages

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    cache = _get_cache(gc_code)

    log_type = request.POST.get("log_type", "Found it")
    logged_at_str = request.POST.get("logged_at", "")
    text = request.POST.get("text", "")
    platforms = request.POST.getlist("platforms")
    seq = request.POST.get("sequence_number", "").strip()
    sequence_number = int(seq) if seq else None
    passphrase = request.POST.get("passphrase", "").strip()
    give_favourite = bool(request.POST.get("give_favourite"))
    recommend = bool(request.POST.get("recommend"))

    # Parse as naive datetime in cache timezone, convert to UTC
    from geocaches.sync.log_submit import submit_log, cache_timezone

    cache_tz = cache_timezone(cache.latitude, cache.longitude)
    try:
        naive = datetime.strptime(logged_at_str, "%Y-%m-%dT%H:%M")
    except ValueError:
        messages.error(request, _("Invalid date/time format."))
        return redirect("geocaches:detail", gc_code=cache.display_code)

    logged_at_utc = naive.replace(tzinfo=cache_tz).astimezone(timezone.utc)

    from preferences.models import UserPreference as _UP
    strip_exif_default = _UP.get("log_image_strip_exif", True)
    max_px_default = _UP.get("log_image_max_px", 1024)
    image_attachments = _parse_image_attachments(
        request, strip_exif_default=strip_exif_default, max_px_default=max_px_default
    )

    tb_actions = _parse_tb_actions(request.POST, request.FILES)

    result = submit_log(cache, log_type, logged_at_utc, text, platforms,
                        sequence_number=sequence_number, passphrase=passphrase,
                        images=image_attachments,
                        give_favourite=give_favourite, recommend=recommend,
                        tb_actions=tb_actions)

    if result.gc_success:
        messages.success(request, _("GC log submitted (%(ref)s)") % {"ref": result.gc_ref_code})
    elif result.gc_success is False:
        messages.error(request, _("GC log failed: %(error)s") % {"error": result.gc_error})

    if result.oc_success:
        messages.success(request, _("OC log submitted (%(ref)s)") % {"ref": result.oc_ref_code})
    elif result.oc_success is False:
        messages.error(request, _("OC log failed: %(error)s") % {"error": result.oc_error})

    for msg in result.messages:
        messages.info(request, msg)
    for err in result.image_errors:
        messages.warning(request, _("Image upload: %(error)s") % {"error": err})

    for tb in result.tb_results:
        label = tb.ref_code or _("(unknown TB)")
        if tb.success:
            messages.success(request, _("TB %(action)s %(label)s submitted") % {"action": tb.action, "label": label})
        else:
            messages.warning(request, _("TB %(action)s %(label)s failed: %(error)s") % {"action": tb.action, "label": label, "error": tb.error})

    return redirect("geocaches:detail", gc_code=cache.display_code)


def _parse_tb_actions(post, files=None) -> list[dict]:
    """Pull tb_action_<idx>/tb_ref_<idx>/tb_tracking_<idx>/tb_text_<idx> tuples out of POST.

    Also harvests ``tb_image_<idx>_<n>`` file uploads (with optional
    ``tb_image_title_<idx>_<n>`` / ``tb_image_desc_<idx>_<n>``) into the
    ``images`` key for later upload after the TB log gets a referenceCode.
    """
    out: list[dict] = []
    for key in post:
        if not key.startswith("tb_action_"):
            continue
        idx = key.split("_", 2)[-1]
        action = (post.get(key) or "").strip().lower()
        if not action:
            continue
        out.append({
            "action":        action,
            "ref_code":      (post.get(f"tb_ref_{idx}") or "").strip().upper(),
            "tracking_code": (post.get(f"tb_tracking_{idx}") or "").strip().upper(),
            "text":          post.get(f"tb_text_{idx}") or "",
            "images":        _parse_tb_row_images(post, files, idx) if files else [],
        })
    return out


def _parse_tb_row_images(post, files, idx: str) -> list:
    """Collect ImageAttachment objects for one TB row from the request files.

    Files arrive under a single ``tb_image_<idx>`` field — the file input
    in ``_log_tb_sections.html`` is ``multiple``, so multiple files share
    one field name and we use ``getlist`` to retrieve them.
    """
    from geocaches.image_upload import ImageAttachment
    attachments = []
    for f in files.getlist(f"tb_image_{idx}"):
        try:
            attachments.append(ImageAttachment(
                file_bytes=f.read(),
                filename=f.name,
            ))
        except (OSError, ValueError):
            pass
    return attachments


def oc_passphrase_save(request, gc_code):
    """Save the passphrase for an OC cache that requires one."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    cache = _get_cache(gc_code)
    passphrase = request.POST.get("passphrase", "").strip()
    oc_ext = getattr(cache, "oc_extension", None)
    if oc_ext:
        oc_ext.passphrase = passphrase
        oc_ext.save(update_fields=["passphrase"])
    return redirect("geocaches:detail", gc_code=cache.display_code)


def note_add(request, gc_code):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    cache = _get_cache(gc_code)
    body = request.POST.get("body", "").strip()
    note_type = request.POST.get("note_type", "note")
    note_format = request.POST.get("format", "plain")
    logged_at = _parse_logged_at(request.POST.get("logged_at", ""))
    now = datetime.now(timezone.utc)
    # Don't create empty notes, unless it's a field note with a date
    if body or (note_type == "field_note" and logged_at):
        Note.objects.create(
            geocache=cache,
            note_type=note_type,
            format=note_format,
            body=body,
            logged_at=logged_at,
            created_at=now,
            updated_at=now,
        )
    return redirect("geocaches:detail", gc_code=gc_code)


def log_delete(request, log_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    log = get_object_or_404(Log, pk=log_id)
    gc_code = log.geocache.display_code
    log.delete()
    return redirect("geocaches:detail", gc_code=gc_code)


def note_update(request, note_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    note = get_object_or_404(Note, pk=note_id)
    note.body = request.POST.get("body", "").strip()
    note.note_type = request.POST.get("note_type", note.note_type)
    note.format = request.POST.get("format", note.format)
    note.logged_at = _parse_logged_at(request.POST.get("logged_at", ""))
    note.updated_at = datetime.now(timezone.utc)
    note.save(update_fields=["body", "note_type", "format", "logged_at", "updated_at"])
    return redirect("geocaches:detail", gc_code=note.geocache.display_code)


def note_delete(request, note_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    note = get_object_or_404(Note, pk=note_id)
    display_code = note.geocache.display_code
    note.delete()
    return redirect("geocaches:detail", gc_code=display_code)


def corrected_coords_save(request, gc_code):
    from geocaches.geo.coords import parse_lat_lon

    cache = _get_cache(gc_code)

    if request.method == "POST":
        lat_str = request.POST.get("latitude", "").strip()
        lon_str = request.POST.get("longitude", "").strip()
        note = request.POST.get("note", "").strip()
        clear = request.POST.get("clear", "")

        fit_map = False
        if clear or (not lat_str and not lon_str):
            CorrectedCoordinates.objects.filter(geocache=cache).delete()
            if cache.has_corrected_coordinates:
                cache.has_corrected_coordinates = False
                cache.save(update_fields=["has_corrected_coordinates"])
            fit_map = True
        else:
            result = parse_lat_lon(lat_str, lon_str)
            if result:
                lat, lon = result
                CorrectedCoordinates.objects.update_or_create(
                    geocache=cache,
                    defaults={"latitude": lat, "longitude": lon, "note": note},
                )
                if not cache.has_corrected_coordinates:
                    cache.has_corrected_coordinates = True
                    cache.save(update_fields=["has_corrected_coordinates"])
                fit_map = True
            else:
                messages.error(request, _(
                    "Could not parse coordinates: lat=%(lat)r, lon=%(lon)r. "
                    "Accepted formats: 48.30315 | N 48° 18.189' | N 48° 18' 11.3\""
                ) % {"lat": lat_str, "lon": lon_str})

        suffix = "?fit_map=1" if fit_map else ""
        from django.urls import reverse
        return redirect(reverse("geocaches:detail", kwargs={"gc_code": gc_code}) + suffix)

    return redirect("geocaches:detail", gc_code=gc_code)
