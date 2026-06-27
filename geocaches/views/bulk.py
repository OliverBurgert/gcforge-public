from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _, ngettext

from ..models import Log, Note
from .detail import _build_log_submit_context, _parse_image_attachments
from .list import _filtered_qs


def bulk_map_visibility(request):
    """Bulk-apply a map-visibility state to the filtered subset.

    POST ``state`` ∈ {visible, session, always, reset_session} + filter
    params on the query string. AL parents in the queryset cascade to
    stages — handled by ``services.map_visibility.bulk_set``.
    """
    from django.contrib import messages

    from django.urls import reverse

    from ..services.map_visibility import MapVisibility, bulk_set, reset_all_session

    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    state = request.POST.get("state", "")
    qs_str = request.GET.urlencode()
    list_url = reverse("geocaches:list")
    list_target = redirect(f"{list_url}?{qs_str}" if qs_str else list_url)

    if state == "reset_session":
        n = reset_all_session(request.session)
        if n:
            messages.success(request, ngettext(
                "Cleared %(n)d session-hide.",
                "Cleared %(n)d session-hides.",
                n,
            ) % {"n": n})
        else:
            messages.info(request, _("No session-hides to clear."))
        return list_target

    if state not in MapVisibility.CHOICES:
        return HttpResponseBadRequest(f"Invalid state: {state!r}")

    qs, _fv = _filtered_qs(request)
    result = bulk_set(qs, state, request.session)
    changed = result["changed"]
    if changed:
        if state == MapVisibility.VISIBLE:
            msg = ngettext(
                "Set %(n)d cache visible on map.",
                "Set %(n)d caches visible on map.",
                changed,
            )
        elif state == MapVisibility.SESSION:
            msg = ngettext(
                "Set %(n)d cache hidden on map (this session).",
                "Set %(n)d caches hidden on map (this session).",
                changed,
            )
        else:
            msg = ngettext(
                "Set %(n)d cache hidden on map (always).",
                "Set %(n)d caches hidden on map (always).",
                changed,
            )
        messages.success(request, msg % {"n": changed})
    else:
        messages.info(request, _("No caches changed."))
    return list_target


def bulk_logging(request):
    """Bulk logging UI — review and submit pending field notes as logs."""
    from django.contrib import messages
    from django.db.models import Max
    from datetime import datetime as _dt, timezone as _tz
    from geocaches.sync.log_submit import cache_timezone
    from geocaches.services.bulk_logging import submit_field_note

    def _note_status(note):
        if note.submitted_at:
            return "logged"
        if note.submit_error:
            return "error"
        if note.bulk_draft:
            return "draft"
        return "new"

    # Only show field notes that were imported via the field note importer
    # (they always have log_type set); GSAK-imported notes have empty log_type
    pending_notes_qs = (
        Note.objects.filter(
            note_type="field_note", submitted_at__isnull=True, log_type__gt="", bulk_dismissed=False
        )
        .select_related("geocache", "geocache__oc_extension")
        .order_by("logged_at")
    )
    done_notes_qs = (
        Note.objects.filter(note_type="field_note", submitted_at__isnull=False, log_type__gt="")
        .select_related("geocache", "geocache__oc_extension")
        .order_by("-submitted_at")[:50]
    )
    pending_notes = list(pending_notes_qs)
    done_notes = list(done_notes_qs)

    # Assign sequence numbers: step by 1 from cached total platform finds.
    # Preserve manually-stored overrides on individual notes. The cached total
    # comes from UserPreference (set on user_profile page load); we still take
    # max() with local sequence numbers so we never go backwards.
    from preferences.models import UserPreference
    cached_total = UserPreference.get("cached_total_finds", 0) or 0
    max_seq_log = (
        Log.objects.filter(is_local=True, sequence_number__isnull=False)
        .aggregate(Max("sequence_number"))["sequence_number__max"] or 0
    )
    max_seq_note = max((n.sequence_number for n in pending_notes if n.sequence_number), default=0)
    base_seq = max(cached_total, max_seq_log, max_seq_note)
    auto_i = 1
    for note in pending_notes:
        if not note.sequence_number:
            note.auto_seq = base_seq + auto_i
            auto_i += 1
        else:
            note.auto_seq = note.sequence_number

    for note in pending_notes:
        note.status = _note_status(note)
    for note in done_notes:
        note.status = "logged"

    # Handle POST
    if request.method == "POST":
        action = request.POST.get("action", "")
        note_id = request.POST.get("note_id", "")
        note = Note.objects.filter(pk=note_id, note_type="field_note").select_related("geocache").first()
        if note is None:
            messages.error(request, _("Field note not found."))
            return redirect("geocaches:bulk_logging")

        def _next_url():
            ids = [n.pk for n in pending_notes]
            try:
                cur_idx = ids.index(note.pk)
            except ValueError:
                cur_idx = -1
            # Prefer the note immediately after current; fall back to the one before
            for n in pending_notes[cur_idx + 1:]:
                if n.pk != note.pk:
                    return f"{request.path}?note={n.pk}"
            for n in reversed(pending_notes[:cur_idx]):
                if n.pk != note.pk:
                    return f"{request.path}?note={n.pk}"
            return request.path

        if action == "delete":
            note.bulk_dismissed = True
            note.save(update_fields=["bulk_dismissed"])
            return redirect(_next_url())

        log_type = request.POST.get("log_type", note.log_type or "Found it")
        text = request.POST.get("text", "")
        logged_at_str = request.POST.get("logged_at", "")
        seq_str = request.POST.get("sequence_number", "").strip()
        sequence_number = int(seq_str) if seq_str else None
        platforms = request.POST.getlist("platforms")
        passphrase = request.POST.get("passphrase", "").strip()
        give_favourite = bool(request.POST.get("give_favourite"))
        recommend = bool(request.POST.get("recommend"))

        cache_tz_obj = cache_timezone(note.geocache.latitude, note.geocache.longitude)
        try:
            naive = _dt.strptime(logged_at_str, "%Y-%m-%dT%H:%M")
            logged_at_utc = naive.replace(tzinfo=cache_tz_obj).astimezone(_tz.utc)
        except ValueError:
            logged_at_utc = note.logged_at

        if action == "save_draft":
            note.log_type = log_type
            note.draft_body = text
            note.logged_at = logged_at_utc
            note.sequence_number = sequence_number
            note.bulk_draft = True
            note.submit_error = ""
            note.save(update_fields=["log_type", "draft_body", "logged_at", "sequence_number", "bulk_draft", "submit_error"])
            return redirect(_next_url())

        if action == "submit_now":
            from preferences.models import UserPreference as _UP
            _strip_exif = _UP.get("log_image_strip_exif", True)
            _max_px = _UP.get("log_image_max_px", 1024)
            image_attachments = _parse_image_attachments(
                request, strip_exif_default=_strip_exif, max_px_default=_max_px
            )
            result = submit_field_note(
                note,
                log_type=log_type,
                logged_at_utc=logged_at_utc,
                text=text,
                sequence_number=sequence_number,
                platforms=platforms,
                passphrase=passphrase,
                images=image_attachments,
                give_favourite=give_favourite,
                recommend=recommend,
            )
            # The bulk editor submits this via fetch so a failed submit (e.g. a
            # wrong OC passphrase) keeps the page in place — preserving the typed
            # log text, passphrase and the client-side image attachments. Only
            # advance to the next note on success.
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                from django.http import JsonResponse
                if result.success:
                    return JsonResponse({"success": True, "next_url": _next_url()})
                return JsonResponse({"success": False, "error": result.submit_error})
            return redirect(_next_url())

        return redirect(f"{request.path}?note={note_id}")

    # Build editor context for selected note
    selected_id = request.GET.get("note") or (str(pending_notes[0].pk) if pending_notes else None)
    selected_note = None
    if selected_id:
        selected_note = next((n for n in pending_notes if str(n.pk) == selected_id), None)
        if selected_note is None:
            selected_note = next((n for n in done_notes if str(n.pk) == selected_id), None)

    editor_ctx: dict = {}
    if selected_note and selected_note.geocache:
        cache_tz_obj = cache_timezone(selected_note.geocache.latitude, selected_note.geocache.longitude)
        logged_at_value = (
            selected_note.logged_at.astimezone(cache_tz_obj).strftime("%Y-%m-%dT%H:%M")
            if selected_note.logged_at else ""
        )
        seq_val = getattr(selected_note, "auto_seq", None) or selected_note.sequence_number
        editor_ctx = _build_log_submit_context(
            selected_note.geocache,
            selected_log_type=selected_note.log_type or "",
            logged_at_value=logged_at_value,
            sequence_number_value=seq_val,
            log_text_value=selected_note.draft_body or selected_note.body or "",
        )

    active_tab = request.GET.get("tab", "pending")

    return render(request, "geocaches/tools/bulk_logging.html", {
        "pending_notes": pending_notes,
        "done_notes": done_notes,
        "selected_note": selected_note,
        "active_tab": active_tab,
        **editor_ctx,
    })
