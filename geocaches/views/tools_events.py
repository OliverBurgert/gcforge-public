import re as _re

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import ngettext

from ..models import Geocache
# Shared with the managed-calendar service (single source of truth).
from ..services.calendar import _EVENT_TYPES, _extract_event_time


def tools_event_calendar(request):
    from datetime import date
    from preferences.models import ReferencePoint, UserPreference
    from ..geo import haversine_km

    today = date.today()
    events = (
        Geocache.objects
        .filter(cache_type__in=_EVENT_TYPES, hidden_date__gte=today)
        .prefetch_related('tags')
        .order_by('hidden_date', 'name')
    )

    distance_unit = UserPreference.get("distance_unit", "km")
    ref = ReferencePoint.objects.filter(is_default=True).first()
    if ref is None:
        refs = list(ReferencePoint.objects.all())
        ref = refs[0] if refs else None

    rows = []
    for ev in events:
        if ref and ev.latitude is not None and ev.longitude is not None:
            km = haversine_km(ref.latitude, ref.longitude, ev.latitude, ev.longitude)
            dist = km if distance_unit == "km" else km * 0.621371
            dist_str = f"{dist:.1f} {distance_unit}"
        else:
            dist_str = "—"
        rows.append({"cache": ev, "distance": dist_str})

    return render(request, "geocaches/tools/tools_event_calendar.html", {
        "rows": rows,
        "ref": ref,
        "today": today,
    })


def tools_remove_unattended_past_events(request):
    from django.utils.timezone import localdate
    from django.contrib import messages
    from django.shortcuts import redirect

    today = localdate()
    past_events = (
        Geocache.objects
        .filter(cache_type__in=_EVENT_TYPES, hidden_date__lt=today, found=False)
        .order_by('hidden_date', 'name')
    )

    if request.method == 'POST':
        count = past_events.count()
        past_events.delete()
        messages.success(request, ngettext(
            "Deleted %(n)d past event cache.",
            "Deleted %(n)d past event caches.",
            count,
        ) % {"n": count})
        return redirect('geocaches:tools_event_calendar')

    return render(request, "geocaches/tools/tools_remove_past_events.html", {
        "events": list(past_events),
        "today": today,
    })


def tools_event_ical(request, pk):
    from datetime import date, timedelta

    cache = get_object_or_404(Geocache, pk=pk)
    event_date = cache.hidden_date or date.today()
    time_info = _extract_event_time(cache)

    uid = f"{cache.display_code or cache.pk}@gcforge"
    summary = cache.name or cache.display_code or str(cache.pk)
    location = f"{cache.latitude},{cache.longitude}" if cache.latitude and cache.longitude else ""

    if time_info:
        h, mn = time_info
        dtstart = f"{event_date.strftime('%Y%m%d')}T{h:02d}{mn:02d}00"
        if cache.event_end_time:
            dtend = f"{event_date.strftime('%Y%m%d')}T{cache.event_end_time.hour:02d}{cache.event_end_time.minute:02d}00"
        else:
            end_h = h + 2
            dtend_dt = event_date + timedelta(days=1) if end_h >= 24 else event_date
            dtend = f"{dtend_dt.strftime('%Y%m%d')}T{end_h % 24:02d}{mn:02d}00"
        dt_prefix = "DTSTART"
        dt_end_prefix = "DTEND"
    else:
        dtstart = event_date.strftime('%Y%m%d')
        dtend = (event_date + timedelta(days=1)).strftime('%Y%m%d')
        dt_prefix = "DTSTART;VALUE=DATE"
        dt_end_prefix = "DTEND;VALUE=DATE"

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GCForge//Event Calendar//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"{dt_prefix}:{dtstart}",
        f"{dt_end_prefix}:{dtend}",
        f"SUMMARY:{summary}",
    ]
    if location:
        lines.append(f"LOCATION:{location}")
    if cache.external_url:
        lines.append(f"URL:{cache.external_url}")
    lines += ["END:VEVENT", "END:VCALENDAR"]

    ics = "\r\n".join(lines) + "\r\n"
    slug = _re.sub(r'[^\w-]', '_', summary)[:40]
    response = HttpResponse(ics, content_type="text/calendar; charset=utf-8")
    response['Content-Disposition'] = f'attachment; filename="{slug}.ics"'
    return response
