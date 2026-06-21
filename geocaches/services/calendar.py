"""Managed local calendar — DB-backed, served live as a subscribable ``.ics``.

The user subscribes to :func:`feed_urls` once; populate actions
(:func:`sync_events`, :func:`add_missing_days`) upsert :class:`CalendarEntry`
rows by stable UID so the feed never duplicates and stays current.
"""
import html as _html_module
import re as _re
import secrets
from datetime import date, timedelta

from ..models import EVENT_CACHE_TYPES, CacheType, CalendarEntry, Geocache

# Event-style cache types whose upcoming instances become calendar entries.
# Single source of truth: geocaches.models.EVENT_CACHE_TYPES. Kept under the
# legacy name so tools_events.py keeps importing _EVENT_TYPES from here.
_EVENT_TYPES = EVENT_CACHE_TYPES

_TIME_RE = _re.compile(
    r'(?:(?:Beginn|Start|Zeit|Time|um|at)\s*[:\s]?\s*)?'
    r'\b(\d{1,2})[:\.](\d{2})\s*(?:Uhr|uhr|h\b|AM|PM|am|pm)?',
    _re.IGNORECASE,
)


def _strip_html(text):
    """Remove HTML tags and decode entities."""
    text = _re.sub(r'<[^>]+>', ' ', text)
    return _html_module.unescape(text)


def _extract_event_time(cache):
    """Return (hour, minute) from model fields or description regex, or None."""
    if cache.event_start_time:
        return cache.event_start_time.hour, cache.event_start_time.minute
    combined = _strip_html(
        (cache.short_description or '') + ' ' + (cache.long_description or '')
    )
    m = _TIME_RE.search(combined)
    if not m:
        return None
    h, mn = int(m.group(1)), int(m.group(2))
    if 0 <= h <= 23 and 0 <= mn <= 59:
        return h, mn
    return None


# ---------------------------------------------------------------------------
# Subscription token + feed URLs
# ---------------------------------------------------------------------------

_TOKEN_KEY = "calendar_token"


def get_or_create_token() -> str:
    """Stable per-install token guarding the public ``.ics`` feed URL."""
    from preferences.models import UserPreference

    token = UserPreference.get(_TOKEN_KEY)
    if not token:
        token = secrets.token_urlsafe(16)
        UserPreference.set(_TOKEN_KEY, token)
    return token


def feed_urls(request) -> dict:
    """Absolute ``{http, webcal, download}`` URLs for the live feed."""
    from django.urls import reverse

    path = reverse("geocaches:calendar_feed", args=[get_or_create_token()])
    http = request.build_absolute_uri(path)
    webcal = _re.sub(r'^https?://', 'webcal://', http)
    return {"http": http, "webcal": webcal, "download": http}


# ---------------------------------------------------------------------------
# ICS rendering
# ---------------------------------------------------------------------------

def _ics_escape(text: str) -> str:
    """Escape a TEXT value per RFC 5545 (backslash, semicolon, comma, newline)."""
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _fold(line: str) -> str:
    """Fold a content line to <=75 octets, continuation lines start with a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out = []
    while len(raw) > 75:
        # Don't split inside a multi-byte UTF-8 sequence.
        cut = 75
        while cut > 0 and (raw[cut] & 0xC0) == 0x80:
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = raw[cut:]
    out.append(raw.decode("utf-8"))
    return "\r\n ".join(out)


def _utc_stamp(d, t, tz_name: str) -> str:
    """Combine a naive local (date, time) at ``tz_name`` and render as UTC.

    Events happen in the target location's wall-clock time; emitting the UTC
    instant (``...Z``) lets every calendar app show it correctly regardless of
    the viewer's own timezone.  Falls back to floating local time when the
    timezone is unknown.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if not tz_name:
        return f"{d.strftime('%Y%m%d')}T{t.hour:02d}{t.minute:02d}00"
    try:
        local = datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=ZoneInfo(tz_name))
    except Exception:  # noqa: BLE001 — bad/unknown tz => floating fallback
        return f"{d.strftime('%Y%m%d')}T{t.hour:02d}{t.minute:02d}00"
    from datetime import timezone as _tz
    u = local.astimezone(_tz.utc)
    return f"{u.strftime('%Y%m%d')}T{u.hour:02d}{u.minute:02d}00Z"


def _alarm_lines(entry: CalendarEntry) -> list[str]:
    """A VALARM: 1h before a timed event, or 09:00 on an all-day to-do."""
    trigger = "-PT1H" if entry.start_time else "PT9H"  # PT9H = 09:00 (from midnight)
    rel = "" if entry.start_time else ";RELATED=START"
    return [
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_ics_escape(entry.title)}",
        f"TRIGGER{rel}:{trigger}",
        "END:VALARM",
    ]


def _dt_utc(dt) -> str:
    """Format an aware/naive datetime as an iCal UTC timestamp (``...Z``)."""
    from datetime import timezone as _tz
    if dt is None:
        from datetime import datetime
        dt = datetime.now(_tz.utc)
    if dt.tzinfo is not None:
        dt = dt.astimezone(_tz.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _entry_lines(entry: CalendarEntry) -> list[str]:
    d = entry.start_date.strftime("%Y%m%d")
    # DTSTAMP is REQUIRED by RFC 5545; stricter parsers reject events without it.
    lines = ["BEGIN:VEVENT", f"UID:{entry.uid}", f"DTSTAMP:{_dt_utc(entry.updated_at)}"]
    if entry.created_at:
        lines.append(f"CREATED:{_dt_utc(entry.created_at)}")
    if entry.updated_at:
        lines.append(f"LAST-MODIFIED:{_dt_utc(entry.updated_at)}")
    lines.append("SEQUENCE:0")
    if entry.start_time:
        st = entry.start_time
        lines.append(f"DTSTART:{_utc_stamp(entry.start_date, st, entry.tz_name)}")
        if entry.end_time:
            lines.append(f"DTEND:{_utc_stamp(entry.start_date, entry.end_time, entry.tz_name)}")
        else:
            from datetime import time as _t
            end_h = st.hour + 2
            end_d = entry.start_date + timedelta(days=1) if end_h >= 24 else entry.start_date
            lines.append(f"DTEND:{_utc_stamp(end_d, _t(end_h % 24, st.minute), entry.tz_name)}")
    else:
        lines.append(f"DTSTART;VALUE=DATE:{d}")
        lines.append(f"DTEND;VALUE=DATE:{(entry.start_date + timedelta(days=1)).strftime('%Y%m%d')}")
    lines.append(f"SUMMARY:{_ics_escape(entry.title)}")
    if entry.location:
        lines.append(f"LOCATION:{_ics_escape(entry.location)}")
    if entry.url:
        lines.append(f"URL:{entry.url}")
    if entry.description:
        lines.append(f"DESCRIPTION:{_ics_escape(entry.description)}")
    if entry.alarm:
        lines += _alarm_lines(entry)
    lines.append("END:VEVENT")
    return lines


def build_ics() -> str:
    """Render the whole calendar as one VCALENDAR string."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GCForge//Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:GCForge",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    for entry in CalendarEntry.objects.select_related("geocache").all():
        lines += _entry_lines(entry)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


# ---------------------------------------------------------------------------
# Populate / clear actions
# ---------------------------------------------------------------------------

def sync_events() -> dict:
    """Upsert an entry for every upcoming event cache; prune stale event entries.

    Returns ``{"added", "updated", "removed", "total"}``.
    """
    today = date.today()
    events = (
        Geocache.objects
        .filter(cache_type__in=_EVENT_TYPES, hidden_date__gte=today)
        .order_by("hidden_date", "name")
    )

    seen = set()
    added = updated = 0
    for ev in events:
        uid = f"evt-{ev.display_code or ev.pk}@gcforge"
        seen.add(uid)
        time_info = _extract_event_time(ev)
        start_time = ev.event_start_time or (None if time_info is None else _time(time_info))
        has_coords = ev.latitude is not None and ev.longitude is not None
        # Resolve the event's local timezone from its coordinates so the .ics
        # renders the correct instant for viewers in any timezone.
        tz_name = ""
        if start_time and has_coords:
            tz_name = _location_tz(ev.latitude, ev.longitude)
        defaults = {
            "kind": CalendarEntry.KIND_EVENT,
            "title": ev.name or ev.display_code or str(ev.pk),
            "start_date": ev.hidden_date,
            "start_time": start_time,
            "end_time": ev.event_end_time,
            "location": f"{ev.latitude},{ev.longitude}" if has_coords else "",
            "url": ev.external_url or "",
            "tz_name": tz_name,
            "cache_type": ev.cache_type,
            "geocache": ev,
        }
        # alarm is intentionally excluded so per-entry bell toggles survive re-sync.
        _, created = CalendarEntry.objects.update_or_create(uid=uid, defaults=defaults)
        added += created
        updated += not created

    removed = (
        CalendarEntry.objects
        .filter(kind=CalendarEntry.KIND_EVENT)
        .exclude(uid__in=seen)
        .delete()[0]
    )
    return {
        "added": added, "updated": updated, "removed": removed,
        "total": CalendarEntry.objects.count(),
    }


def _time(hm):
    from datetime import time as _t
    return _t(hm[0], hm[1])


# Day range presets offered in the UI (label key -> number of days ahead).
RANGE_DAYS = {"30": 30, "90": 90, "365": 365}


def add_missing_days(cache_type: str | None, minimum: int, days: int, *,
                     alc: bool = False) -> dict:
    """Add reminders on upcoming days where finds of ``cache_type`` are < ``minimum``.

    A day (MM-DD) is "missing" when its found-date matrix cell count is below
    ``minimum``.  For each missing day we add a reminder on every occurrence in
    ``[today, today+days]``.  Existing ``missing_day`` reminders for this
    ``cache_type`` are replaced, so days satisfied since last run drop out.

    ``alc`` switches the gap computation to Adventure Lab stage finds (always,
    regardless of the dashboard's "Include Adventure Lab caches" toggle); the
    reminders are still typed ``cache_type`` (the lab type) so a day's candidate
    list resolves to unfound lab caches.

    Returns ``{"added", "days_missing", "total"}``.
    """
    from . import stats as stats_service

    minimum = max(1, int(minimum))
    cache_type = cache_type or None

    cal = (
        stats_service.alc_finds_by_found_date() if alc
        else stats_service.finds_by_found_date(cache_type)
    )
    missing_md = set()  # {(month, day)}
    for mi, row in enumerate(cal["rows"], start=1):
        for di, cell in enumerate(row["cells"], start=1):
            if cell["valid"] and cell["count"] < minimum:
                missing_md.add((mi, di))

    from django.utils.translation import gettext as _

    today = date.today()
    horizon = today + timedelta(days=days)
    any_label = _("any cache")
    type_label = dict(CacheType.choices).get(cache_type, any_label) if cache_type else any_label
    type_key = cache_type or "any"

    # Replace this type's reminders so satisfied days are dropped, but keep any
    # per-entry alarm toggles the user set (re-applied by UID below).
    existing = CalendarEntry.objects.filter(
        kind=CalendarEntry.KIND_MISSING_DAY, cache_type=cache_type or "",
    )
    alarmed_uids = set(existing.filter(alarm=True).values_list("uid", flat=True))
    existing.delete()

    added = 0
    d = today
    while d <= horizon:
        if (d.month, d.day) in missing_md:
            uid = f"miss-{type_key}-{d.strftime('%Y%m%d')}@gcforge"
            CalendarEntry.objects.update_or_create(uid=uid, defaults={
                "kind": CalendarEntry.KIND_MISSING_DAY,
                "title": _("Find a %(type)s (day not yet filled)") % {"type": type_label},
                "start_date": d,
                "start_time": None,
                "cache_type": cache_type or "",
                "alarm": uid in alarmed_uids,
            })
            added += 1
        d += timedelta(days=1)

    return {
        "added": added,
        "days_missing": len(missing_md),
        "type_label": type_label,
        "total": CalendarEntry.objects.count(),
    }


def clear(kind: str) -> int:
    """Delete entries: ``all`` | ``event`` | ``missing_day``.  Returns count."""
    qs = CalendarEntry.objects.all()
    if kind in (CalendarEntry.KIND_EVENT, CalendarEntry.KIND_MISSING_DAY):
        qs = qs.filter(kind=kind)
    elif kind != "all":
        return 0
    return qs.delete()[0]


def agenda(days: int | None = None) -> list[dict]:
    """Entries in ``[today, today+days)`` grouped by date for the Home tab.

    Event entries are enriched (in place) with ``tag_list`` and ``distance_str``
    (distance from the first tag's centre point, else the default location).
    """
    if days is None:
        days = get_agenda_days()
    today = date.today()
    end = today + timedelta(days=days)
    entries = (
        CalendarEntry.objects.select_related("geocache")
        .prefetch_related("geocache__tags__default_ref_point")
        .filter(start_date__gte=today, start_date__lt=end)
        .order_by("start_date", "start_time")
    )
    default_ref = _default_ref_point()
    unit = _distance_unit()
    groups: dict[date, list] = {}
    for e in entries:
        if e.kind == CalendarEntry.KIND_EVENT and e.geocache_id:
            e.tag_list = list(e.geocache.tags.all())
            e.distance_str = _event_distance(e.geocache, e.tag_list, default_ref, unit)
        groups.setdefault(e.start_date, []).append(e)
    return [{"date": d, "entries": groups[d]} for d in sorted(groups)]


# ---------------------------------------------------------------------------
# Per-entry actions + preferences
# ---------------------------------------------------------------------------

def toggle_alarm(entry_id: int) -> CalendarEntry | None:
    entry = CalendarEntry.objects.filter(pk=entry_id).first()
    if entry is None:
        return None
    entry.alarm = not entry.alarm
    entry.save(update_fields=["alarm"])
    return entry


def delete_entry(entry_id: int) -> bool:
    return CalendarEntry.objects.filter(pk=entry_id).delete()[0] > 0


def day_candidates_where_sql(d) -> str:
    """Raw WHERE matching candidate caches for one calendar day.

    Covers every entry on day ``d``: event caches (the specific rows) plus
    unfound caches of each ``missing_day`` to-do's type (an empty type means
    "any unfound cache").  Feeds the list view via ``?where_sql=``.
    """
    entries = CalendarEntry.objects.filter(start_date=d)
    event_ids = [e.geocache_id for e in entries if e.kind == CalendarEntry.KIND_EVENT and e.geocache_id]
    types = {e.cache_type for e in entries if e.kind == CalendarEntry.KIND_MISSING_DAY}

    parts = []
    if event_ids:
        parts.append("id IN (%s)" % ",".join(str(i) for i in event_ids))
    if "" in types:  # an "any cache" to-do => all unfound caches
        parts.append("(found = 0 AND completed = 0)")
    else:
        # Validate against the choices — values are interpolated, so guard injection.
        valid = sorted(t for t in types if t in dict(CacheType.choices))
        if valid:
            quoted = ",".join("'%s'" % t for t in valid)
            parts.append("(found = 0 AND completed = 0 AND cache_type IN (%s))" % quoted)
    return " OR ".join(parts) if parts else "1 = 0"


_AGENDA_DAYS_KEY = "calendar_agenda_days"


def get_agenda_days() -> int:
    from preferences.models import UserPreference
    try:
        return _clamp_days(int(UserPreference.get(_AGENDA_DAYS_KEY, 10)))
    except (TypeError, ValueError):
        return 10


def set_agenda_days(n: int) -> int:
    from preferences.models import UserPreference
    n = _clamp_days(n)
    UserPreference.set(_AGENDA_DAYS_KEY, n)
    return n


def _clamp_days(n: int) -> int:
    return min(max(int(n), 1), 366)


# ---------------------------------------------------------------------------
# Timezone + distance helpers
# ---------------------------------------------------------------------------

def _location_tz(lat: float, lon: float) -> str:
    """IANA timezone name for coordinates (empty string if unknown)."""
    from ..sync.log_submit import cache_timezone
    try:
        return cache_timezone(lat, lon).key
    except Exception:  # noqa: BLE001 — never block a sync on tz lookup
        return ""


def _default_ref_point():
    from preferences.models import ReferencePoint
    return (
        ReferencePoint.objects.filter(is_default=True).first()
        or ReferencePoint.objects.first()
    )


def _distance_unit() -> str:
    from preferences.models import UserPreference
    return UserPreference.get("distance_unit", "km")


def _event_distance(cache, tags, default_ref, unit) -> str:
    """Distance from a tag's centre point (first tag with one) else default."""
    from ..geo import haversine_km

    ref = next((t.default_ref_point for t in tags if t.default_ref_point_id), None) or default_ref
    if ref is None or cache.latitude is None or cache.longitude is None:
        return ""
    km = haversine_km(ref.latitude, ref.longitude, cache.latitude, cache.longitude)
    dist = km if unit == "km" else km * 0.621371
    return f"{dist:.1f} {unit}"
