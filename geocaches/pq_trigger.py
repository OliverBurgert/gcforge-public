"""
Pocket Query web automation — trigger PQ runs via geocaching.com website.

The GC API has no endpoint to *run* a Pocket Query (only to download one that
has already been generated).  This module drives the
geocaching.com/pocket/default.aspx page directly using an authenticated
requests.Session from accounts.gc_web_session.

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
"""

import logging
import re
import time
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from accounts.gc_web_session import get_session, reset_session

logger = logging.getLogger(__name__)

_PQ_PAGE_URL = "https://www.geocaching.com/pocket/default.aspx"
_INTER_TRIGGER_DELAY: float = 2.0
_MAX_RUNS_PER_DAY = 10
_GC_TZ = ZoneInfo("America/Los_Angeles")

# Python weekday (0=Mon) -> GC day index (0=Sun)
_PY_WEEKDAY_TO_GC_DAY = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 0}


def _gc_today_pst() -> date:
    """Return today's date in GC server timezone (PST/PDT)."""
    return datetime.now(_GC_TZ).date()


def _gc_day_today() -> int:
    """Return today's GC day index (0=Su .. 6=Sa) in GC server timezone."""
    gc_weekday = datetime.now(_GC_TZ).weekday()
    return _PY_WEEKDAY_TO_GC_DAY[gc_weekday]


def _fetch_page() -> BeautifulSoup:
    """GET the PQ list page. Raises RuntimeError if session expired."""
    session = get_session()
    r = session.get(_PQ_PAGE_URL, timeout=20)
    r.raise_for_status()

    if "account/signin" in r.url or "login" in r.url.lower():
        reset_session()
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
            "trigger_url": trigger_url,
            "already_ran": already_ran,
            "already_sched": already_sched,
            "last_gen_text": last_gen_text,
        })

    return rows


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

    session = get_session()
    r = session.get(row["trigger_url"], timeout=20, headers={"Referer": _PQ_PAGE_URL})
    r.raise_for_status()

    logger.info("Triggered PQ run: %s", row["name"])
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
    session = get_session()

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

        r = session.get(row["trigger_url"], timeout=20, headers={"Referer": _PQ_PAGE_URL})
        r.raise_for_status()
        results.append({"name": target_name, "status": "triggered"})
        triggered_count += 1
        logger.info("Triggered PQ: %s", target_name)

        if i < len(names) - 1:
            time.sleep(_INTER_TRIGGER_DELAY)

    return results


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
    poll_interval: float = 30.0,
    timeout: float = 900.0,
    task_info=None,
) -> dict[str, bool]:
    """
    Poll the GC API until the given PQs have a lastUpdatedDateUtc after `since`.

    Returns {reference_code: True/False} indicating which ones completed.
    """
    from geocaches.pq_service import list_pocket_queries

    deadline = time.monotonic() + timeout
    pending = set(reference_codes)
    completed = {}

    while pending and time.monotonic() < deadline:
        if task_info and task_info.cancel_event.is_set():
            break

        time.sleep(poll_interval)

        try:
            pqs = list_pocket_queries()
        except Exception as exc:
            logger.warning("Poll failed: %s", exc)
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
                    if updated_dt >= since:
                        pending.discard(ref)
                        completed[ref] = True
                        logger.info("PQ %s is ready (updated %s)", ref, updated)
                except ValueError:
                    pass

        if task_info:
            done = len(reference_codes) - len(pending)
            task_info.phase = f"Waiting for generation ({done}/{len(reference_codes)})"

    for ref in pending:
        completed[ref] = False

    return completed
