"""
Pocket Query web automation — trigger PQ runs via geocaching.com website.

The GC API has no endpoint to *run* a Pocket Query (only to download one that
has already been generated).  This module drives the
geocaching.com/pocket/default.aspx page directly using an authenticated
requests.Session from geocaches.sync.gc_web.session.

How it works
------------
Each PQ row on the page shows a checkbox for each day of the week (Su-Sa).
Each day cell contains an <a> link:

    /pocket/default.aspx?pq=<GUID>&d=<DAY>&opt=<OPT>

where:
  GUID  = PQ's UUID
  DAY   = 0=Su, 1=Mo, 2=Tu, 3=We, 4=Th, 5=Fr, 6=Sa
  OPT   = 1 means "enable/check" (currently unchecked),
          0 means "disable/uncheck" (currently checked)

GETting a link with opt=1 schedules the PQ for that day, triggering a run.

Limitations:
  - Max 10 PQ runs per 24-hour period (PST day).
  - A PQ that already ran today cannot be re-triggered.
  - PQs are processed in batches; scheduling does NOT mean immediate availability.

Downloading a ready PQ (added 2026-08-15)
------------------------------------------
This page has a second tab, "Pocket Queries Ready for Download", never
previously scraped here — everything above only ever touched the "Active
Pocket Queries" tab. Each ready row's name links to::

    /pocket/downloadpq.ashx?g=<GUID>&src=web

**Confirmed live**: ``src=web`` is a fixed, non-secret constant (identical
across every ready PQ on a real account), so no CSRF/page-scoped token is
needed beyond the authenticated session's own cookies — simpler than the
``geocache.logbook`` endpoint's ``tkn``. The response is
``content-type: application/zip`` with real ZIP magic bytes (confirmed via
a real download), the **same format**
``gcprivate.gc_client.GCClient.download_pocket_query()`` already returns —
so it feeds straight into ``service.py``'s existing zip-extract-and-import
logic unchanged. See ``list_downloadable_pqs_web()``/``download_pq_web()``
below and ``service.download_and_import_pq_web()``.

``wait_for_pq_generation_web()`` (also added 2026-08-15) closes the loop:
after triggering (already web-only before this effort — see the top of
this docstring), it polls this same "Ready for Download" tab instead of
the official API to detect completion, so the whole trigger → wait →
download → import pipeline (``service.trigger_and_download_web()``) now
needs no partner-API token at all.

Note the "Last Generated" column on this tab is **date-only** (e.g.
``"2026-08-12 (4 days remaining)"``), coarser than the official API's full
``lastUpdatedDateUtc`` timestamp — fine for "is a fresh run ready" checks
within the trigger/wait workflow above, since GC's PQ batches don't run
faster than same-PST-day granularity anyway, but not a drop-in replacement
for anything needing sub-day precision.
"""

import logging
import re
import time
import unicodedata
from collections.abc import Callable
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger("geocaches.pq")


def _pq_session():
    """Lazy handle to the authenticated GC web session (public — needs only a stored password)."""
    from geocaches.sync.gc_web.session import get_session
    return get_session()


def _pq_reset_session():
    from geocaches.sync.gc_web.session import reset_session
    reset_session()

_PQ_PAGE_URL = "https://www.geocaching.com/pocket/default.aspx"
# Deletion postbacks must target the directory URL the page is served from
# (posting to default.aspx returns 200 but silently drops the delete event).
_PQ_DIR_URL = "https://www.geocaching.com/pocket/"
_INTER_TRIGGER_DELAY: float = 2.0
_MAX_RUNS_PER_DAY = 10
_GC_TZ = ZoneInfo("America/Los_Angeles")

# Python weekday (0=Mon) -> GC day index (0=Sun)
_PY_WEEKDAY_TO_GC_DAY = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 0}

# GC's PQ batch processor can take anywhere from seconds to hours to run a
# query depending on load, so generation waits poll quickly at first and back
# off for a long tail rather than giving up after a fixed short timeout.
_QUICK_POLL_INTERVAL = 30.0
_QUICK_PHASE_DURATION = 900.0  # 15 minutes of fast polling
_SLOW_POLL_INTERVAL = 300.0  # then every 5 minutes
_GENERATION_TIMEOUT = 4 * 3600.0  # give up after 4 hours


def _poll_interval(start: float) -> float:
    """Backoff schedule for generation waits: quick at first, then slower."""
    if time.monotonic() - start < _QUICK_PHASE_DURATION:
        return _QUICK_POLL_INTERVAL
    return _SLOW_POLL_INTERVAL


def _gc_today_pst() -> date:
    """Return today's date in GC server timezone (PST/PDT)."""
    return datetime.now(_GC_TZ).date()


def _gc_day_today() -> int:
    """Return today's GC day index (0=Su .. 6=Sa) in GC server timezone."""
    gc_weekday = datetime.now(_GC_TZ).weekday()
    return _PY_WEEKDAY_TO_GC_DAY[gc_weekday]


def _fetch_page(url: str = _PQ_PAGE_URL) -> BeautifulSoup:
    """GET the PQ list page. Raises RuntimeError if session expired."""
    session = _pq_session()
    r = session.get(url, timeout=20)
    r.raise_for_status()

    if "account/signin" in r.url or "login" in r.url.lower():
        _pq_reset_session()
        raise RuntimeError(
            "Web session expired while loading PQ page — session has been "
            "reset, please try again."
        )

    return BeautifulSoup(r.text, "html.parser")


def _find_active_pq_table(soup: BeautifulSoup) -> Tag | None:
    """Find the Active Pocket Queries table, excluding My Finds."""
    # The page has a heading/panel for "Active Pocket Queries" and a
    # separate section for "My Finds Pocket Queries".  We look for the
    # first table that has day-toggle links (pq=&d=&opt=) but skip any
    # table preceded by a "My Finds" header.
    #
    # Strategy: find all tables, return the first one that contains
    # day-toggle links.  "My Finds" tables typically don't have them.
    for table in soup.find_all("table"):
        if not isinstance(table, Tag):
            continue
        # Check if this table has any day-toggle links
        link = table.find("a", href=re.compile(r"pq=.*&d=.*&opt="))
        if link:
            return table
    return None


def _parse_last_gen(td: Tag) -> str | None:
    """Extract the Last Generated date string from a <td>.

    The date is shown as e.g. "03/29/2026" in a <span> or directly as text,
    and bolded when it was generated today.  Returns the raw text content
    for further parsing, or None if empty.
    """
    text = td.get_text(strip=True)
    if not text or text == "\xa0":
        return None
    return text


def _parse_gen_date(raw: str) -> date | None:
    """Try to parse the Last Generated date from the website text."""
    if not raw:
        return None
    # Try MM/DD/YYYY first (common US format on gc.com)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def _parse_pq_rows(soup: BeautifulSoup, gc_day: int) -> list[dict]:
    """
    Parse all PQ rows on the Active Pocket Queries tab.

    Returns list of dicts:
      name, guid, trigger_url, already_ran, already_sched, last_gen_text
    """
    rows: list[dict] = []
    today_pst = _gc_today_pst()

    table = _find_active_pq_table(soup)
    if not table:
        # Fall back to scanning all rows
        table = soup

    for tr in table.find_all("tr"):
        if not isinstance(tr, Tag):
            continue

        day_links: dict[int, dict] = {}
        for a in tr.find_all("a", href=True):
            href: str = a["href"]
            if "pq=" not in href or "&d=" not in href or "opt=" not in href:
                continue
            try:
                parsed = urlparse(href)
                qs = parse_qs(parsed.query)
                d = int(qs["d"][0])
                opt = qs["opt"][0]
                guid = qs["pq"][0]
                day_links[d] = {"href": href, "opt": opt, "guid": guid}
            except (KeyError, ValueError, IndexError):
                continue

        if not day_links:
            continue

        # Extract PQ name from non-toggle link in the row
        name = ""
        for a in tr.find_all("a", href=True):
            href = a.get("href", "")
            if "/pocket/" in href and "default.aspx" not in href:
                name = a.get_text(strip=True) or a.get("title", "").strip()
                if name:
                    break

        guid = next((v["guid"] for v in day_links.values()), "")

        today_link = day_links.get(gc_day)
        already_sched = False
        trigger_url = ""
        if today_link:
            already_sched = today_link["opt"] == "0"
            if not already_sched:
                trigger_url = "https://www.geocaching.com" + today_link["href"]

        # Checkbox value = the PQ's numeric internal id (used for web deletion;
        # this is NOT the GUID nor the API referenceCode).
        delete_id = ""
        cb = tr.find("input", {"type": "checkbox"})
        if isinstance(cb, Tag) and cb.get("value"):
            delete_id = cb["value"]

        # A PQ deleted today lingers struck-through (span.Strike) until the
        # PST day rolls over.
        is_deleted = bool(tr.find("span", class_="Strike"))

        # Parse Last Generated column (last <td> in the row)
        already_ran = False
        last_gen_text = ""
        tds = tr.find_all("td")
        if tds:
            last_td = tds[-1] if isinstance(tds[-1], Tag) else None
            if last_td:
                last_gen_text = last_td.get_text(strip=True)
                # Bold = ran today
                bold = last_td.find(["b", "strong"])
                if bold:
                    gen_date = _parse_gen_date(bold.get_text(strip=True))
                    if gen_date and gen_date == today_pst:
                        already_ran = True
                elif last_gen_text:
                    # Even if not bold, check the date
                    gen_date = _parse_gen_date(last_gen_text)
                    if gen_date and gen_date == today_pst:
                        already_ran = True

        rows.append({
            "name": name,
            "guid": guid,
            "delete_id": delete_id,
            "is_deleted": is_deleted,
            "trigger_url": trigger_url,
            "already_ran": already_ran,
            "already_sched": already_sched,
            "last_gen_text": last_gen_text,
        })

    return rows


# ---------------------------------------------------------------------------
# "Ready for Download" tab — separate from the Active table above (added
# 2026-08-15, see module docstring's addendum).
# ---------------------------------------------------------------------------

_PQ_DOWNLOAD_URL = "https://www.geocaching.com/pocket/downloadpq.ashx"
_FILE_SIZE_RE = re.compile(r"^([\d.]+)\s*(KB|MB|GB)$", re.IGNORECASE)
_SIZE_MULTIPLIERS = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}
_LAST_GEN_DOWNLOAD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\s*\((\d+)\s*days?\s*remaining\))?", re.IGNORECASE)


def _find_downloadable_pq_table(soup: BeautifulSoup) -> Tag | None:
    """Find the "Pocket Queries Ready for Download" table (has downloadpq.ashx links)."""
    for table in soup.find_all("table"):
        if isinstance(table, Tag) and table.find("a", href=re.compile(r"downloadpq\.ashx")):
            return table
    return None


def _parse_file_size(text: str) -> int | None:
    """Parse "623.07 KB" / "1.42 MB" into a byte count. None if unparseable."""
    m = _FILE_SIZE_RE.match(text.strip())
    if not m:
        return None
    value, unit = m.groups()
    try:
        return int(float(value) * _SIZE_MULTIPLIERS[unit.upper()])
    except (ValueError, KeyError):
        return None


def _parse_downloadable_pq_rows(soup: BeautifulSoup) -> list[dict]:
    """
    Parse all PQ rows on the "Ready for Download" tab.

    Returns list of dicts: name, guid, file_size_text, file_size_bytes,
    waypoint_count, last_generated_text, last_generated_date, expires_in_days.
    """
    rows: list[dict] = []
    table = _find_downloadable_pq_table(soup)
    if not table:
        return rows

    for tr in table.find_all("tr"):
        if not isinstance(tr, Tag):
            continue
        link = tr.find("a", href=re.compile(r"downloadpq\.ashx"))
        if not link:
            continue

        qs = parse_qs(urlparse(link["href"]).query)
        guid = (qs.get("g") or [""])[0]
        if not guid:
            continue

        name = link.get_text(strip=True)
        tds = tr.find_all("td")
        # Columns: [checkbox, #, name (with link), file size, waypoints, last generated]
        file_size_text = tds[3].get_text(strip=True) if len(tds) > 3 else ""
        waypoints_text = tds[4].get_text(strip=True) if len(tds) > 4 else ""
        last_gen_text = tds[5].get_text(strip=True) if len(tds) > 5 else ""

        last_generated_date = None
        expires_in_days = None
        m = _LAST_GEN_DOWNLOAD_RE.match(last_gen_text)
        if m:
            try:
                last_generated_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except ValueError:
                pass
            if m.group(2):
                expires_in_days = int(m.group(2))

        rows.append({
            "name": name,
            "guid": guid,
            "file_size_text": file_size_text,
            "file_size_bytes": _parse_file_size(file_size_text),
            "waypoint_count": int(waypoints_text) if waypoints_text.isdigit() else None,
            "last_generated_text": last_gen_text,
            "last_generated_date": last_generated_date,
            "expires_in_days": expires_in_days,
        })

    return rows


def list_downloadable_pqs_web() -> list[dict]:
    """Return all Pocket Queries ready for download, scraped from the website.

    No partner-API token needed — see module docstring's 2026-08-15 addendum.
    """
    soup = _fetch_page()
    return _parse_downloadable_pq_rows(soup)


def download_pq_web(guid: str, *, session=None) -> bytes:
    """Download a ready Pocket Query's ZIP via the classic website endpoint.

    Returns the same ``application/zip`` bytes
    ``gcprivate.gc_client.GCClient.download_pocket_query()`` returns — see
    module docstring's 2026-08-15 addendum for how this was confirmed.
    """
    session = session or _pq_session()
    r = session.get(_PQ_DOWNLOAD_URL, params={"g": guid, "src": "web"}, timeout=60)
    r.raise_for_status()
    if "account/signin" in r.url:
        _pq_reset_session()
        raise RuntimeError("Web session expired while downloading Pocket Query")
    return r.content


def wait_for_pq_generation_web(
    guids: list[str],
    since: datetime,
    *,
    poll_interval: float | None = None,
    timeout: float = _GENERATION_TIMEOUT,
    task_info=None,
) -> dict[str, bool]:
    """Poll the "Ready for Download" tab until the given PQs show a
    last-generated date on/after ``since``'s PST calendar day.

    Web equivalent of ``wait_for_pq_generation()`` (added 2026-08-15) —
    polls ``list_downloadable_pqs_web()`` instead of the official API's
    ``lastUpdatedDateUtc``. Date-only granularity is fine here: GC only
    lets a PQ run once per PST day (confirmed by the "Active" tab's own
    ``already_ran`` check, same day-based limit this module has always
    enforced for triggering), so there's no same-day case a coarser
    timestamp could conflate with a stale prior generation.

    Returns ``{guid: True/False}`` indicating which ones completed.
    """
    since_date = since.astimezone(_GC_TZ).date()

    start = time.monotonic()
    deadline = start + timeout
    pending = set(guids)
    completed: dict[str, bool] = {}

    poll_count = 0
    while pending and time.monotonic() < deadline:
        if task_info and task_info.cancel_event.is_set():
            logger.info("PQ web wait cancelled after %d poll(s)", poll_count)
            break

        interval = poll_interval if poll_interval is not None else _poll_interval(start)

        if task_info:
            done = len(guids) - len(pending)
            for remaining in range(int(interval), 0, -1):
                if task_info.cancel_event.is_set():
                    break
                task_info.phase = (
                    f"Waiting for PQs to generate "
                    f"({done}/{len(guids)} ready) — next check in {remaining}s"
                )
                time.sleep(1)
        else:
            time.sleep(interval)
        poll_count += 1

        try:
            rows = list_downloadable_pqs_web()
        except Exception as exc:
            logger.warning("PQ web wait poll #%d failed: %s", poll_count, exc)
            continue

        by_guid = {r["guid"]: r for r in rows}
        for guid in list(pending):
            row = by_guid.get(guid)
            last_gen = row.get("last_generated_date") if row else None
            if not last_gen:
                continue
            ready = last_gen >= since_date
            logger.info(
                "PQ web wait poll #%d: %s last_generated=%s since=%s ready=%s",
                poll_count, guid, last_gen, since_date, ready,
            )
            if ready:
                pending.discard(guid)
                completed[guid] = True

        if task_info:
            done = len(guids) - len(pending)
            task_info.phase = f"Waiting for generation ({done}/{len(guids)})"

    if pending:
        logger.warning(
            "PQ web wait timed out after %d poll(s): still pending %s",
            poll_count, list(pending),
        )
    for guid in pending:
        completed[guid] = False

    return completed


def get_pq_web_status() -> tuple[list[dict], dict]:
    """
    Return all PQs visible on the website with their trigger status,
    plus a summary dict.

    Returns:
        (rows, summary) where summary has:
            today_pst: str (ISO date)
            ran_today: int (count of PQs that already ran today)
            remaining_triggers: int (10 - ran_today)
    """
    soup = _fetch_page()
    gc_day = _gc_day_today()
    rows = _parse_pq_rows(soup, gc_day)

    today_pst = _gc_today_pst()
    ran_today = sum(1 for r in rows if r["already_ran"])

    summary = {
        "today_pst": today_pst.isoformat(),
        "ran_today": ran_today,
        "remaining_triggers": max(0, _MAX_RUNS_PER_DAY - ran_today),
    }

    return rows, summary


def trigger_pq(guid: str) -> str:
    """
    Trigger a single PQ by its GUID.

    Returns the PQ name on success.
    Raises RuntimeError if the PQ can't be triggered.
    """
    soup = _fetch_page()
    gc_day = _gc_day_today()
    rows = _parse_pq_rows(soup, gc_day)

    ran_today = sum(1 for r in rows if r["already_ran"])
    if ran_today >= _MAX_RUNS_PER_DAY:
        raise RuntimeError(
            f"Daily limit reached: {ran_today}/{_MAX_RUNS_PER_DAY} PQs already ran today (PST). "
            "Try again after midnight PST."
        )

    row = next((r for r in rows if r["guid"] == guid), None)
    if not row:
        raise RuntimeError(f"PQ with GUID {guid} not found on the website.")

    if row["already_ran"]:
        raise RuntimeError(f"'{row['name']}' already ran today — cannot re-trigger.")

    if row["already_sched"]:
        return row["name"]  # already scheduled, will run soon

    if not row["trigger_url"]:
        raise RuntimeError(
            f"'{row['name']}' has no trigger URL for today — "
            "today may not be in its schedule."
        )

    logger.info("PQ trigger attempt: %s (guid=%s)", row["name"], guid)
    session = _pq_session()
    r = session.get(row["trigger_url"], timeout=20, headers={"Referer": _PQ_PAGE_URL})
    r.raise_for_status()

    logger.info("PQ trigger success: %s", row["name"])
    return row["name"]


def trigger_pqs_by_name(names: list[str]) -> list[dict]:
    """
    Trigger multiple PQs by name (exact match).

    Returns list of {name, status} where status is one of:
      "triggered", "already_ran", "already_scheduled", "no_trigger_url",
      "not_found", "limit_reached"
    """
    soup = _fetch_page()
    gc_day = _gc_day_today()
    rows = _parse_pq_rows(soup, gc_day)

    ran_today = sum(1 for r in rows if r["already_ran"])
    triggered_count = 0

    results = []
    session = _pq_session()

    for i, target_name in enumerate(names):
        row = next((r for r in rows if r["name"] == target_name), None)
        if not row:
            results.append({"name": target_name, "status": "not_found"})
            continue

        if row["already_ran"]:
            results.append({"name": target_name, "status": "already_ran"})
            continue

        if row["already_sched"]:
            results.append({"name": target_name, "status": "already_scheduled"})
            continue

        if not row["trigger_url"]:
            results.append({"name": target_name, "status": "no_trigger_url"})
            continue

        if ran_today + triggered_count >= _MAX_RUNS_PER_DAY:
            results.append({"name": target_name, "status": "limit_reached"})
            continue

        logger.info("PQ trigger attempt: %s (guid=%s)", target_name, row["guid"])
        r = session.get(row["trigger_url"], timeout=20, headers={"Referer": _PQ_PAGE_URL})
        r.raise_for_status()
        results.append({"name": target_name, "status": "triggered"})
        triggered_count += 1
        logger.info("PQ trigger success: %s", target_name)

        if i < len(names) - 1:
            time.sleep(_INTER_TRIGGER_DELAY)

    return results


def trigger_pqs_by_guid(guids: list[str]) -> list[dict]:
    """
    Trigger multiple PQs by their GUID (the identifier carried per row in the
    UI).  Fetches the PQ page once, then triggers each.

    Returns list of {guid, name, status} where status is one of:
      "triggered", "already_ran", "already_scheduled", "no_trigger_url",
      "not_found", "limit_reached"
    """
    soup = _fetch_page()
    gc_day = _gc_day_today()
    rows = _parse_pq_rows(soup, gc_day)
    by_guid = {r["guid"]: r for r in rows if r["guid"]}

    ran_today = sum(1 for r in rows if r["already_ran"])
    triggered_count = 0

    results = []
    session = _pq_session()

    for i, guid in enumerate(guids):
        row = by_guid.get(guid)
        if not row:
            results.append({"guid": guid, "name": "", "status": "not_found"})
            continue

        name = row["name"]
        if row["already_ran"]:
            results.append({"guid": guid, "name": name, "status": "already_ran"})
            continue
        if row["already_sched"]:
            results.append({"guid": guid, "name": name, "status": "already_scheduled"})
            continue
        if not row["trigger_url"]:
            results.append({"guid": guid, "name": name, "status": "no_trigger_url"})
            continue
        if ran_today + triggered_count >= _MAX_RUNS_PER_DAY:
            results.append({"guid": guid, "name": name, "status": "limit_reached"})
            continue

        logger.info("PQ trigger attempt: %s (guid=%s)", name, guid)
        r = session.get(row["trigger_url"], timeout=20, headers={"Referer": _PQ_PAGE_URL})
        r.raise_for_status()
        results.append({"guid": guid, "name": name, "status": "triggered"})
        triggered_count += 1
        logger.info("PQ trigger success: %s", name)

        if i < len(guids) - 1:
            time.sleep(_INTER_TRIGGER_DELAY)

    return results


def delete_pqs(delete_ids: list[str]) -> dict:
    """
    Delete one or more PQs on geocaching.com by their numeric internal id
    (the checkbox value from the Active Pocket Queries table).

    Mirrors the website's "Delete Selected" action: an ASP.NET ``__doPostBack``
    on ``lnkDeleteSelected`` with the selected ids stuffed into the
    ``...PQListControl1$hidIds`` hidden field (comma-joined, trailing comma).

    Returns {"deleted": [ids actually gone], "requested": [ids asked for]}.
    Raises RuntimeError if the delete control can't be found.
    """
    ids = [str(i).strip() for i in delete_ids if str(i).strip()]
    if not ids:
        return {"deleted": [], "requested": []}

    # Fetch from (and post back to) the directory URL the browser uses — posting
    # to default.aspx is accepted with HTTP 200 but never runs the delete.
    soup = _fetch_page(_PQ_DIR_URL)

    form = soup.find("form", id="aspnetForm")
    if not isinstance(form, Tag):
        raise RuntimeError("Could not find the Pocket Query form on the page.")

    # The Active-PQ delete link lives under PQListControl1 (the download list
    # has its own, which we must not use).
    link = None
    for a in form.find_all("a", id=re.compile(r"lnkDeleteSelected$")):
        if "PQListControl1" in (a.get("id") or ""):
            link = a
            break
    if not isinstance(link, Tag):
        raise RuntimeError("Could not find the 'Delete Selected' control.")

    m = re.search(r"__doPostBack\('([^']+)','([^']*)'\)", link.get("href", ""))
    if not m:
        raise RuntimeError("Could not parse the delete postback target.")
    event_target, event_arg = m.group(1), m.group(2)

    # Replicate the browser's postback: the __VIEWSTATE* chunks + generator,
    # the two hidIds fields, and the event target/argument.  setSelected()
    # comma-joins the selected ids with a trailing comma into PQListControl1's
    # hidIds; the server reads the ids from there.
    data: dict[str, str] = {}
    for inp in form.find_all("input"):
        nm = inp.get("name", "")
        if nm.startswith("__VIEWSTATE") or nm == "__VIEWSTATEGENERATOR" or nm.endswith("hidIds"):
            data[nm] = inp.get("value", "") or ""

    data["__EVENTTARGET"] = event_target
    data["__EVENTARGUMENT"] = event_arg
    data["ctl00$ContentBody$PQListControl1$hidIds"] = ",".join(ids) + ","

    logger.info("PQ delete attempt: ids=%s", ids)
    session = _pq_session()
    r = session.post(
        _PQ_DIR_URL, data=data, timeout=30,
        headers={
            "Referer": _PQ_DIR_URL,
            "Origin": "https://www.geocaching.com",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    r.raise_for_status()

    # A deleted PQ is removed from the active list OR lingers (struck through
    # with a <span class="Strike">) for the rest of the day — either counts as
    # deleted.  A still-present, non-struck row means it wasn't deleted.
    after = BeautifulSoup(r.text, "html.parser")
    table = after.find("table", id="pqRepeater") or after
    rows_by_id = {}
    for tr in table.find_all("tr"):
        if not isinstance(tr, Tag):
            continue
        cb = tr.find("input", {"type": "checkbox"})
        if isinstance(cb, Tag) and cb.get("value"):
            rows_by_id[cb["value"]] = tr

    deleted = []
    for i in ids:
        tr = rows_by_id.get(i)
        if tr is None or tr.find("span", class_="Strike"):
            deleted.append(i)
    logger.info("PQ delete result: requested=%d deleted=%d", len(ids), len(deleted))
    return {"deleted": deleted, "requested": ids}


def match_pqs_by_pattern(pattern: str) -> tuple[list[dict], dict]:
    """
    Return PQs matching a name pattern and a summary.

    Used by the "Show matching" preview.
    Returns (matching_rows, summary) where summary includes run counts.
    """
    soup = _fetch_page()
    gc_day = _gc_day_today()
    rows = _parse_pq_rows(soup, gc_day)

    today_pst = _gc_today_pst()
    ran_today = sum(1 for r in rows if r["already_ran"])

    pattern_lower = pattern.lower()
    matching = [r for r in rows if pattern_lower in r["name"].lower()]

    would_trigger = sum(
        1 for r in matching
        if not r["already_ran"] and not r["already_sched"] and r["trigger_url"]
    )

    summary = {
        "today_pst": today_pst.isoformat(),
        "ran_today": ran_today,
        "remaining_triggers": max(0, _MAX_RUNS_PER_DAY - ran_today),
        "would_trigger": would_trigger,
        "exceeds_limit": (ran_today + would_trigger) > _MAX_RUNS_PER_DAY,
    }

    return matching, summary


def wait_for_pq_generation(
    reference_codes: list[str],
    since: datetime,
    *,
    poll_interval: float | None = None,
    timeout: float = _GENERATION_TIMEOUT,
    task_info=None,
) -> dict[str, bool]:
    """
    Poll the GC API until the given PQs have a lastUpdatedDateUtc on or after
    `since`'s **PST date** (not the exact instant — GC only regenerates a PQ
    once per PST day, and comparing exact instants is vulnerable to a PQ
    finishing generation, and getting its timestamp stamped by GC, a couple
    of seconds before our own locally-captured `since` — clock skew, or GC
    simply being faster than our own request pipeline. That one bad instant
    comparison then fails permanently for the rest of the day since the PQ
    won't regenerate again).

    Polling backs off over time (see the module-level ``_QUICK_*``/``_SLOW_*``
    constants) since GC's batch processor can take anywhere from seconds to
    hours; pass an explicit ``poll_interval`` to use a fixed cadence instead.

    Returns {reference_code: True/False} indicating which ones completed.
    """
    from geocaches.pq.service import list_pocket_queries

    start = time.monotonic()
    deadline = start + timeout
    pending = set(reference_codes)
    completed = {}

    poll_count = 0
    while pending and time.monotonic() < deadline:
        if task_info and task_info.cancel_event.is_set():
            logger.info("PQ wait cancelled after %d poll(s)", poll_count)
            break

        interval = poll_interval if poll_interval is not None else _poll_interval(start)

        # Sleep between checks. When attached to a task, tick the phase down
        # once a second so the UI can show a live "next check in Ns" countdown.
        if task_info:
            done = len(reference_codes) - len(pending)
            for remaining in range(int(interval), 0, -1):
                if task_info.cancel_event.is_set():
                    break
                task_info.phase = (
                    f"Waiting for PQs to generate "
                    f"({done}/{len(reference_codes)} ready) — next check in {remaining}s"
                )
                time.sleep(1)
        else:
            time.sleep(interval)
        poll_count += 1

        try:
            pqs = list_pocket_queries()
        except Exception as exc:
            logger.warning("PQ poll #%d failed: %s", poll_count, exc)
            continue

        for pq in pqs:
            ref = pq.get("referenceCode", "")
            if ref not in pending:
                continue
            updated = pq.get("lastUpdatedDateUtc", "")
            if updated:
                try:
                    updated_dt = datetime.fromisoformat(updated.rstrip("Z")).replace(
                        tzinfo=timezone.utc
                    )
                    # Compare PST *dates*, not exact instants — see the
                    # identical comment in wait_for_pq_generation_by_name()
                    # for why an exact `>=` intermittently sticks a PQ
                    # "pending" forever once it's generated for the day.
                    ready = (
                        updated_dt.astimezone(_GC_TZ).date()
                        >= since.astimezone(_GC_TZ).date()
                    )
                    logger.info(
                        "PQ poll #%d: %s lastUpdated=%s since=%s ready=%s",
                        poll_count, ref,
                        updated_dt.strftime("%H:%M:%S UTC"),
                        since.strftime("%H:%M:%S UTC"),
                        ready,
                    )
                    if ready:
                        pending.discard(ref)
                        completed[ref] = True
                except ValueError:
                    logger.warning("PQ poll #%d: could not parse lastUpdatedDateUtc %r for %s", poll_count, updated, ref)
            else:
                logger.info("PQ poll #%d: %s lastUpdated=(none)", poll_count, ref)

        if task_info:
            done = len(reference_codes) - len(pending)
            task_info.phase = f"Waiting for generation ({done}/{len(reference_codes)})"

    if pending:
        logger.warning(
            "PQ wait timed out after %d poll(s): still pending %s",
            poll_count, list(pending),
        )
    for ref in pending:
        completed[ref] = False

    return completed


def _normalize_pq_name(name: str) -> str:
    """Normalize a PQ name for cross-source comparison.

    The website scrape and the official API can report the same PQ under
    subtly different strings for the exact same name — e.g. the site's HTML
    using ``&nbsp;`` somewhere in the name (decoding to U+00A0, not a plain
    space) where the API has an ordinary space, or the two sides using
    different Unicode normalization forms for a name with diacritics
    (ö/ü). Collapsing whitespace and normalizing to NFC makes name-based
    matching robust to both without touching the display name used
    elsewhere (2026-09-05: two names in a 5-PQ pattern trigger — one with a
    doubled internal space, one with umlauts — never matched the API's
    ``name`` field, permanently blocking that batch until its 4-hour
    timeout).
    """
    name = unicodedata.normalize("NFC", name).replace("\xa0", " ")
    return re.sub(r"\s+", " ", name).strip()


def wait_for_pq_generation_by_name(
    since_by_name: dict[str, datetime | None],
    *,
    timeout: float = _GENERATION_TIMEOUT,
    task_info=None,
    on_resolved: Callable[[str, str], None] | None = None,
) -> dict[str, str]:
    """
    Poll the GC API for freshly generated PQs, resolving each by name to its
    referenceCode.

    Used for PQs triggered from the website by GUID, which have no knowable
    referenceCode ahead of time — a PQ that hasn't recently generated simply
    doesn't appear in the API's pocket-query list at all, so unlike
    ``wait_for_pq_generation`` (which polls for an already-known reference
    code), this has to discover the code once the entry shows up. Matching
    is done on ``_normalize_pq_name()`` of both sides, not the raw strings —
    see that helper for why.

    ``since_by_name[name]`` controls the readiness check per name:
      - a ``datetime`` requires the entry's ``lastUpdatedDateUtc``, compared
        by **PST date** (not exact instant — see the comparison itself for
        why), to be on or after it — so a stale hit from a *previous* day
        isn't mistaken for the fresh run
      - ``None`` accepts the entry as soon as it appears, at any recency (for
        PQs that already ran earlier today and won't regenerate)

    Polling backs off over time (see the module-level ``_QUICK_*``/``_SLOW_*``
    constants) since GC's batch processor can take anywhere from seconds to
    hours.

    ``on_resolved(name, referenceCode)``, if given, fires the moment each
    name resolves — so a caller can enqueue that PQ's download right away
    instead of waiting for the whole batch (a slow/stuck straggler no longer
    blocks the ones that are already ready).

    Returns {name: referenceCode} for names resolved within the timeout;
    names still pending when the timeout elapses are simply absent.
    """
    from geocaches.pq.service import list_pocket_queries

    start = time.monotonic()
    deadline = start + timeout
    pending = set(since_by_name)
    resolved: dict[str, str] = {}

    poll_count = 0
    while pending and time.monotonic() < deadline:
        if task_info and task_info.cancel_event.is_set():
            logger.info("PQ wait-by-name cancelled after %d poll(s)", poll_count)
            break

        interval = _poll_interval(start)
        if task_info:
            done = len(since_by_name) - len(pending)
            for remaining in range(int(interval), 0, -1):
                if task_info.cancel_event.is_set():
                    break
                task_info.phase = (
                    f"Waiting for PQs to generate "
                    f"({done}/{len(since_by_name)} ready) — next check in {remaining}s"
                )
                time.sleep(1)
        else:
            time.sleep(interval)
        poll_count += 1

        try:
            pqs = list_pocket_queries()
        except Exception as exc:
            logger.warning("PQ poll-by-name #%d failed: %s", poll_count, exc)
            continue

        by_name = {_normalize_pq_name(pq.get("name", "")): pq for pq in pqs}
        for name in list(pending):
            pq = by_name.get(_normalize_pq_name(name))
            ref = pq.get("referenceCode", "") if pq else ""
            if not ref:
                continue

            required_since = since_by_name[name]
            if required_since is None:
                ready = True
            else:
                ready = False
                updated = pq.get("lastUpdatedDateUtc", "")
                if updated:
                    try:
                        updated_dt = datetime.fromisoformat(updated.rstrip("Z")).replace(
                            tzinfo=timezone.utc
                        )
                        # Compare PST *dates*, not exact instants. GC only
                        # regenerates a PQ once per PST day, so date equality
                        # is sufficient to distinguish "this run" from a stale
                        # prior-day hit — an exact `>=` on the instant is too
                        # strict: GC can finish generating (and stamp
                        # lastUpdatedDateUtc) within a couple of seconds of the
                        # trigger click, which can land *before* our own
                        # locally-captured `since` once clock skew or the
                        # sequential multi-item trigger loop's own overhead is
                        # accounted for. Since a PQ never regenerates again
                        # the same day, that one bad instant comparison then
                        # fails permanently for the rest of the day (2026-09-06:
                        # the 2 earliest-triggered PQs in a 7-PQ pattern
                        # trigger got stuck exactly this way, while the other
                        # 5 — triggered a few seconds later, with more margin
                        # — happened to clear the check).
                        ready = (
                            updated_dt.astimezone(_GC_TZ).date()
                            >= required_since.astimezone(_GC_TZ).date()
                        )
                    except ValueError:
                        logger.warning(
                            "PQ poll-by-name #%d: could not parse lastUpdatedDateUtc %r for %r",
                            poll_count, updated, name,
                        )

            if ready:
                pending.discard(name)
                resolved[name] = ref
                logger.info("PQ poll-by-name #%d: %r ready as %s", poll_count, name, ref)
                if on_resolved:
                    on_resolved(name, ref)

        if task_info:
            done = len(since_by_name) - len(pending)
            task_info.phase = f"Waiting for generation ({done}/{len(since_by_name)})"

    if pending:
        logger.warning(
            "PQ wait-by-name timed out after %d poll(s): still pending %s",
            poll_count, sorted(pending),
        )

    return resolved
