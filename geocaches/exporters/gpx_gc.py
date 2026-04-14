import xml.etree.ElementTree as ET
from xml.etree.ElementTree import Element, SubElement, tostring

from geocaches.importers.lookups import cache_type_to_gpx
from preferences.models import GPX_EXPORT_DEFAULTS

GPX_NS = "http://www.topografix.com/GPX/1/0"
GS_NS = "http://www.groundspeak.com/cache/1/0/1"
GSAK_NS = "http://www.gsak.net/xmlv1/6"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

ET.register_namespace("", GPX_NS)
ET.register_namespace("groundspeak", GS_NS)
ET.register_namespace("gsak", GSAK_NS)
ET.register_namespace("xsi", XSI_NS)

_WAYPOINT_SYM = {
    "Parking":   "Parking Area",
    "Stage":     "Stages of a Multicache",
    "Question":  "Question to Answer",
    "Final":     "Final Location",
    "Trailhead": "Trailhead",
    "Reference": "Reference Point",
    "Other":     "Reference Point",
}


def _sub(parent, ns, tag, text=None, **xml_attrs):
    el = SubElement(parent, f"{{{ns}}}{tag}", {k: str(v) for k, v in xml_attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def _gs(parent, tag, text=None, **xml_attrs):
    return _sub(parent, GS_NS, tag, text, **xml_attrs)


def _gpx(parent, tag, text=None, **xml_attrs):
    return _sub(parent, GPX_NS, tag, text, **xml_attrs)


def _gsak(parent, tag, text=None, **xml_attrs):
    return _sub(parent, GSAK_NS, tag, text, **xml_attrs)


def _add_wp_element(gpx, code, lat, lon, prefix, label, sym, wp_type):
    wp_el = SubElement(gpx, f"{{{GPX_NS}}}wpt", {
        "lat": f"{lat:.6f}",
        "lon": f"{lon:.6f}",
    })
    _gpx(wp_el, "name", f"{prefix}{code[2:]}")
    _gpx(wp_el, "desc", label)
    _gpx(wp_el, "sym",  sym)
    _gpx(wp_el, "type", f"Waypoint|{wp_type}")
    return wp_el


def _build_note_entries(cache, cc, gc_username, opts):
    """Return list of (heading, body) tuples to inject as fake log entries."""
    entries = []

    if opts.get("notes_gcforge"):
        for note in cache.notes.filter(note_type="note").order_by("created_at"):
            dt = note.created_at.strftime("%Y-%m-%d") if note.created_at else "unknown date"
            entries.append(f"Note from {dt}:\n{note.body or ''}")

    if opts.get("notes_gc_user") and cache.gc_note:
        entries.append(f"Note from geocaching.com:\n{cache.gc_note}")

    if opts.get("notes_field_notes"):
        for note in cache.notes.filter(note_type="field_note").order_by("created_at"):
            dt = note.created_at.strftime("%Y-%m-%d") if note.created_at else "unknown date"
            entries.append(f"Field note from {dt}:\n{note.body or ''}")

    if opts.get("notes_corrected") and cc:
        ts = cc.updated_at.strftime("%Y-%m-%d %H:%M") if cc.updated_at else "unknown"
        entries.append(
            f"Corrected coordinates:\n"
            f"Lat: {cc.latitude:.6f}, Lon: {cc.longitude:.6f}\n"
            f"Note: {cc.note or '(none)'}\n"
            f"Updated: {ts}"
        )

    return entries


def _inject_notes(logs_el, note_entries, gc_username, today_str):
    """Append note entries to <groundspeak:logs> element."""
    if not note_entries:
        return
    finder = gc_username or "GCForge"
    if True:  # always fuse handled by caller
        for text in note_entries:
            log_el = SubElement(logs_el, f"{{{GS_NS}}}log")
            _gs(log_el, "date", today_str)
            _gs(log_el, "type", "Write note")
            _gs(log_el, "finder", finder)
            _sub(log_el, GS_NS, "text", text, encoded="False")


_EVENT_TYPES = {"Event", "CITO", "Mega-Event", "Giga-Event"}


def export_gpx(queryset, gc_username: str = "", opts: dict | None = None) -> bytes:
    if opts is None:
        opts = GPX_EXPORT_DEFAULTS

    from datetime import date, timedelta
    today = date.today()
    today_str = f"{today.isoformat()}T00:00:00"

    # Event filtering options
    events_exclude_past = opts.get("events_exclude_past", False)
    events_days_ahead_raw = str(opts.get("events_days_ahead", "")).strip()
    events_days_ahead = int(events_days_ahead_raw) if events_days_ahead_raw.isdigit() else None
    events_cutoff = today + timedelta(days=events_days_ahead) if events_days_ahead is not None else None

    # Determine log cap
    logs_max_raw = str(opts.get("logs_max", "")).strip()
    logs_cap = int(logs_max_raw) if logs_max_raw.isdigit() else None

    # ALC stage handling: build a set of stage PKs to skip if "child_only"
    alc_stages_mode = opts.get("alc_stages", "child_and_export")

    gpx = Element(f"{{{GPX_NS}}}gpx", {
        "version": "1.0",
        "creator": "GCForge",
        f"{{{XSI_NS}}}schemaLocation": (
            "http://www.topografix.com/GPX/1/0 "
            "http://www.topografix.com/GPX/1/0/gpx.xsd "
            "http://www.groundspeak.com/cache/1/0/1 "
            "http://www.groundspeak.com/cache/1/0/1/cache.xsd "
            "http://www.gsak.net/xmlv1/6 "
            "http://www.gsak.net/xmlv1/6/gsak.xsd"
        ),
    })
    _gpx(gpx, "name", "GCForge Export")

    qs = queryset.select_related("corrected_coordinates", "adventure").prefetch_related(
        "logs", "waypoints", "attributes", "notes",
    )

    # Collect all cache objects first so we can handle ALC parent→stage linking
    all_caches = list(qs)

    # Build adventure_pk → list of stage caches mapping for ALC child WP injection
    from collections import defaultdict
    adv_to_stages: dict[int, list] = defaultdict(list)
    stage_pks_in_export: set[int] = set()
    for cache in all_caches:
        if cache.adventure_id and cache.stage_number is not None:
            adv_to_stages[cache.adventure_id].append(cache)
            stage_pks_in_export.add(cache.pk)

    for cache in all_caches:
        is_alc = cache.adventure_id is not None
        is_alc_stage = is_alc and cache.stage_number is not None
        is_alc_parent = is_alc and cache.stage_number is None

        # --- ALC stage visibility decisions ---
        if is_alc_stage:
            if alc_stages_mode == "dont_export":
                continue
            if alc_stages_mode == "child_only":
                continue  # exported only as child WP of parent; skip standalone
            # "child_and_export" → fall through and export normally
            alc_completed_mode = opts.get("alc_completed", "found_invisible")
            if cache.found and alc_completed_mode == "dont_export":
                continue

        # --- Event date filtering ---
        if cache.cache_type in _EVENT_TYPES:
            event_date = cache.hidden_date
            if events_exclude_past and event_date is not None and event_date < today:
                continue
            if events_cutoff is not None and event_date is not None and event_date > events_cutoff:
                continue

        cc = getattr(cache, "corrected_coordinates", None)
        lat = cc.latitude if cc else cache.latitude
        lon = cc.longitude if cc else cache.longitude

        wpt = SubElement(gpx, f"{{{GPX_NS}}}wpt", {
            "lat": f"{lat:.6f}",
            "lon": f"{lon:.6f}",
        })

        if cache.hidden_date:
            _gpx(wpt, "time", f"{cache.hidden_date.isoformat()}T00:00:00")

        code = cache.gc_code or cache.oc_code or cache.al_code
        _gpx(wpt, "name", code)
        d = cache.difficulty if cache.difficulty is not None else "?"
        t = cache.terrain if cache.terrain is not None else "?"
        _gpx(wpt, "desc", f"{cache.name} by {cache.placed_by or cache.owner} ({d}/{t})")

        if cache.gc_code:
            _gpx(wpt, "url",
                 f"https://www.geocaching.com/seek/cache_details.aspx?wp={cache.gc_code}")
            _gpx(wpt, "urlname", cache.name)

        gs_type = cache_type_to_gpx(cache.cache_type)
        _gpx(wpt, "sym", "Geocache Found" if cache.found else "Geocache")
        _gpx(wpt, "type", f"Geocache|{gs_type}")

        # GSAK extension — corrected coordinate support
        if cc:
            gsak_ext = SubElement(wpt, f"{{{GSAK_NS}}}wptExtension")
            _gsak(gsak_ext, "LatBeforeCorrect", f"{cache.latitude:.6f}")
            _gsak(gsak_ext, "LonBeforeCorrect", f"{cache.longitude:.6f}")
            _gsak(gsak_ext, "Code", code)

        gs_cache = SubElement(wpt, f"{{{GS_NS}}}cache", {
            "available": "False" if cache.status == "Disabled" else "True",
            "archived":  "True"  if cache.status == "Archived" else "False",
        })

        _gs(gs_cache, "name", cache.name)
        _gs(gs_cache, "placed_by", cache.placed_by or cache.owner)
        owner_el = _gs(gs_cache, "owner", cache.owner)
        if cache.owner_gc_id:
            owner_el.set("id", str(cache.owner_gc_id))
        _gs(gs_cache, "type", gs_type)
        _gs(gs_cache, "container", cache.size_override or cache.size or "")

        cache_attrs = list(cache.attributes.all())
        if cache_attrs:
            attrs_el = SubElement(gs_cache, f"{{{GS_NS}}}attributes")
            for attr in cache_attrs:
                _sub(attrs_el, GS_NS, "attribute", attr.name,
                     id=str(attr.attribute_id),
                     inc="1" if attr.is_positive else "0")

        _gs(gs_cache, "difficulty", "" if cache.difficulty is None else cache.difficulty)
        _gs(gs_cache, "terrain",    "" if cache.terrain    is None else cache.terrain)
        _gs(gs_cache, "country", cache.country)
        _gs(gs_cache, "state",   cache.state)
        _gs(gs_cache, "short_description", cache.short_description, html="True")
        _gs(gs_cache, "long_description",  cache.long_description,  html="True")
        _gs(gs_cache, "encoded_hints", cache.hint)

        # --- Logs ---
        logs_el = SubElement(gs_cache, f"{{{GS_NS}}}logs")

        all_logs = list(cache.logs.all() if logs_cap is None else cache.logs.all()[:logs_cap])
        if opts.get("logs_my_on_top") and gc_username:
            all_logs.sort(key=lambda l: (0 if l.user_name == gc_username else 1, 0))
        for log in all_logs:
            log_el = SubElement(logs_el, f"{{{GS_NS}}}log")
            if log.source_id:
                log_el.set("id", log.source_id)
            if log.logged_date:
                _gs(log_el, "date", f"{log.logged_date.isoformat()}T00:00:00")
            _gs(log_el, "type",   log.log_type)
            _gs(log_el, "finder", log.user_name)
            _sub(log_el, GS_NS, "text", log.text or "", encoded="False")

        # --- Notes appended after logs ---
        note_entries = _build_note_entries(cache, cc, gc_username, opts)
        if note_entries:
            finder = gc_username or "GCForge"
            if opts.get("notes_fuse"):
                combined = "\n\n---\n\n".join(note_entries)
                log_el = SubElement(logs_el, f"{{{GS_NS}}}log")
                _gs(log_el, "date", today_str)
                _gs(log_el, "type", "Write note")
                _gs(log_el, "finder", finder)
                _sub(log_el, GS_NS, "text", combined, encoded="False")
            else:
                for text in note_entries:
                    log_el = SubElement(logs_el, f"{{{GS_NS}}}log")
                    _gs(log_el, "date", today_str)
                    _gs(log_el, "type", "Write note")
                    _gs(log_el, "finder", finder)
                    _sub(log_el, GS_NS, "text", text, encoded="False")

        # --- Child waypoints ---
        wp_filter = cache.waypoints.all()
        if not opts.get("wp_hidden", False):
            wp_filter = wp_filter.filter(is_hidden=False)
        if not opts.get("wp_completed", True):
            wp_filter = wp_filter.filter(is_completed=False)

        for wp in wp_filter:
            if wp.latitude is None or wp.longitude is None:
                continue
            wp_el = SubElement(gpx, f"{{{GPX_NS}}}wpt", {
                "lat": f"{wp.latitude:.6f}",
                "lon": f"{wp.longitude:.6f}",
            })
            _gpx(wp_el, "name", f"{wp.prefix or 'WP'}{code[2:]}")
            _gpx(wp_el, "desc", wp.name or wp.waypoint_type)
            if wp.note:
                _gpx(wp_el, "cmt", wp.note)
            sym = _WAYPOINT_SYM.get(wp.waypoint_type, "Reference Point")
            _gpx(wp_el, "sym",  sym)
            _gpx(wp_el, "type", f"Waypoint|{wp.waypoint_type}")

        # --- Original coords as child WP ---
        if opts.get("cc_original_as_wp") and cc:
            orig_el = SubElement(gpx, f"{{{GPX_NS}}}wpt", {
                "lat": f"{cache.latitude:.6f}",
                "lon": f"{cache.longitude:.6f}",
            })
            _gpx(orig_el, "name", f"OW{code[2:]}")
            _gpx(orig_el, "desc", f"Original coordinates for {cache.name}")
            _gpx(orig_el, "sym",  "Reference Point")
            _gpx(orig_el, "type", "Waypoint|Reference Point")

        # --- ALC stages as child WPs (for parent cache) ---
        if is_alc_parent and alc_stages_mode in ("child_only", "child_and_export"):
            stages = sorted(adv_to_stages.get(cache.adventure_id, []),
                            key=lambda s: s.stage_number or 0)
            for stage in stages:
                alc_completed_mode = opts.get("alc_completed", "found_invisible")
                if stage.found and alc_completed_mode == "dont_export":
                    continue
                if stage.latitude is None or stage.longitude is None:
                    continue
                stage_sym = "Stages of a Multicache"
                stage_el = SubElement(gpx, f"{{{GPX_NS}}}wpt", {
                    "lat": f"{stage.latitude:.6f}",
                    "lon": f"{stage.longitude:.6f}",
                })
                stage_code = stage.al_code or f"{code}-{stage.stage_number}"
                _gpx(stage_el, "name", f"S{stage_code[-2:].lstrip('0') or stage.stage_number}{code[2:]}")
                _gpx(stage_el, "desc", stage.name or f"Stage {stage.stage_number}")
                _gpx(stage_el, "sym",  stage_sym)
                _gpx(stage_el, "type", "Waypoint|Stage")

    ET.indent(gpx, space="  ")
    xml_str = tostring(gpx, encoding="unicode", xml_declaration=False)
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + xml_str.encode("utf-8")
