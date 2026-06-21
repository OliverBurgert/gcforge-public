"""Managed-calendar views: token-gated ``.ics`` feed + dashboard populate actions."""
from datetime import date
from urllib.parse import urlencode

from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from ..models import CalendarEntry
from ..services import calendar as calendar_service


def calendar_feed(request, token):
    """Serve the whole calendar as ``.ics``.  Guarded by the install token."""
    if token != calendar_service.get_or_create_token():
        raise Http404("invalid calendar token")
    ics = calendar_service.build_ics()
    response = HttpResponse(ics, content_type="text/calendar; charset=utf-8; method=PUBLISH")
    response["Content-Disposition"] = 'inline; filename="gcforge.ics"'
    return response


def calendar_agenda(request):
    """HTMX partial: the next-N-days agenda.  ``?days=N`` updates the stored window."""
    raw = request.GET.get("days")
    if raw is not None:
        try:
            calendar_service.set_agenda_days(int(raw))
        except (TypeError, ValueError):
            pass
    return _render_agenda(request)


@require_POST
def calendar_sync_events(request):
    res = calendar_service.sync_events()
    if res["added"]:
        msg = ngettext("Added %(n)d event.", "Added %(n)d events.", res["added"]) % {"n": res["added"]}
    elif res["removed"]:
        msg = ngettext(
            "Removed %(n)d past event.", "Removed %(n)d past events.", res["removed"]
        ) % {"n": res["removed"]}
    else:
        msg = _("Calendar already up to date.")
    return _render_agenda(request, msg)


@require_POST
def calendar_add_missing(request):
    cache_type = (request.POST.get("stat_type") or "").strip() or None
    try:
        minimum = max(1, int(request.POST.get("minimum", 1)))
    except (TypeError, ValueError):
        minimum = 1
    days = calendar_service.RANGE_DAYS.get(request.POST.get("days", "365"), 365)
    alc = request.POST.get("alc") == "1"
    res = calendar_service.add_missing_days(cache_type, minimum, days, alc=alc)
    if res["added"]:
        msg = ngettext(
            "Added %(n)d %(type)s to-do.", "Added %(n)d %(type)s to-dos.", res["added"]
        ) % {"n": res["added"], "type": res["type_label"]}
    else:
        msg = _("No missing days to add in this range.")
    return _render_agenda(request, msg)


@require_POST
def calendar_clear(request):
    n = calendar_service.clear(request.POST.get("kind", "all"))
    msg = ngettext("Removed %(n)d entry.", "Removed %(n)d entries.", n) % {"n": n}
    return _render_agenda(request, msg)


@require_POST
def calendar_toggle_alarm(request, pk):
    entry = calendar_service.toggle_alarm(pk)
    if entry is None:
        raise Http404("no such entry")
    msg = _("Reminder turned on.") if entry.alarm else _("Reminder turned off.")
    return _render_agenda(request, msg)


@require_POST
def calendar_delete_entry(request, pk):
    calendar_service.delete_entry(pk)
    return _render_agenda(request, _("Removed from calendar."))


def calendar_day_candidates(request):
    """Bounce to the list view filtered to one day's candidates (``?date=``)."""
    try:
        d = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        return redirect(reverse("geocaches:list"))
    sql = calendar_service.day_candidates_where_sql(d)
    return redirect(reverse("geocaches:list") + "?" + urlencode({"where_sql": sql}))


def _render_agenda(request, message: str = ""):
    # NB: do not emit an HX-Trigger calendar:reload here — these responses
    # already swap #dash-cal-agenda directly, and a reload would immediately
    # re-fetch the agenda and wipe the confirmation message.
    return render(request, "geocaches/partials/_dashboard_calendar_agenda.html", {
        "agenda": calendar_service.agenda(),
        "agenda_days": calendar_service.get_agenda_days(),
        "calendar_total": CalendarEntry.objects.count(),
        "calendar_message": message,
    })
