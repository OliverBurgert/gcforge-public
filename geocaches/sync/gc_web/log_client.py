"""
Web/tRPC-based geocache log client — geocaching.com's internal Next.js API
(``/live/geocache/{code}/log``), the fallback for users without a partner-API
token.

``create_log()``, ``update_log()``, and ``delete_log()`` are all
**live-verified 2026-08-13**: shape pinned to real, successful HAR captures of
``web.logs.createGeocacheLog``, ``web.logs.updateGeocacheLog``, and
``web.logs.deleteGeocacheLog`` (see ``geocaches/sync/gc_web/trpc.py``'s module
docstring for the exact envelope, and ``docs/reference/geocaching-com-web.md``
§4/§10). All three share the ``web.logs.`` prefix, resolving what first
looked like a discrepancy against the 2026-08-12 recon's bare
``createGeocacheLog``/``updateGeocacheLog``/``deleteGeocacheLog`` names.
Note that ``delete``'s body is **flat** (``referenceCode`` + ``reasonText``,
no nested ``body`` object like create/update) and its response has no
``data`` key at all — see ``trpc._decode_response()``.

``submit_log()`` wraps ``create_log()`` to match
``gcprivate.gc_client.GCClient.submit_log()``'s call shape (GCForge
``LogType`` string in, ISO datetime string, plain text) so a future resolver
can pick either backend interchangeably. The log-type id lookup now uses the
public ``geocaches.sync.gc_reference._REVERSE_LOG_TYPE_MAP`` (moved there
from the private ``gcprivate/gc_client.py`` for this reason — same numeric
ids, GC's DB backend is shared between the two surfaces).
``use_favourite_point`` is **live-confirmed 2026-08-14** (a real find,
Oliver's GCBPNTE log): the field is ``usedFavoritePoint``, same name the
output already used. Only confirmed as a follow-up ``update_log()`` edit
after create, not on create's own body — so ``submit_log()`` makes a second
call when it's requested rather than guessing that untested position.

``upload_log_image()`` is also **live-verified end to end 2026-08-13** — see
its own docstring for the two-call upload+caption flow.

``create_log()``/``update_log()``'s ``trackables`` list items are confirmed
live too: ``{"trackableCode": <TBxxxxxx>, "trackableLogTypeId": <int>}`` in,
``{"activityReferenceCode": <TLxxxxxxx>, "trackableReferenceCode": <TBxxxxxx>}``
out (per item, inside the log's own ``trackables`` response list) — the
trackable action rides inside the *same* log mutation, not a separate
procedure. Only ``75`` = "Visited" is independently HAR-confirmed on *this*
path, but it matches ``geocaches.sync.trackable_constants
.DEFAULT_TRACKABLE_LOG_TYPE_IDS["Visited"]`` exactly — the same table already
verified against the official partner API's own ``/trackablelogtypes``
endpoint (2026-05-11). Same shared-numbering-space pattern already proven
for geocache log types (``gc_reference._LOG_TYPE_MAP``), so ``trackable_entry()``
below uses that table for discover/retrieve/drop/grab too — high-confidence
by that precedent, not independently HAR-confirmed for those specific
actions yet. A wrong id would 400, not silently misbehave.

``GCWebLogClient`` wraps ``submit_log()``/``upload_log_image()``/
``create_log()``/``update_log()``/``delete_log()``/``attach_images()`` in a
class satisfying ``geocaches.sync.base.GCLogClient`` (structural
``Protocol``, no inheritance) — see that Protocol's docstring for why image
upload isn't part of the shared shape. Everything stays available as a
module-level function too; the class is for Protocol conformance, not to
replace them.

``attach_images()`` (2026-08-14) closes the gap this leaves for
``geocaches.sync.log_submit``'s interactive "New log" flow, which posts the
log first and attaches images after — the web API can't do that directly
(images must be uploaded *before* they can be referenced at all), so this
wraps ``update_log()`` to re-send the just-created log's own type/text/date
plus the newly-uploaded guids, matching the official API's ordering from
the caller's side even though the underlying mechanism differs.
"""

import logging
from datetime import date, datetime, time, timezone

import requests

from geocaches.sync.gc_reference import _REVERSE_LOG_TYPE_MAP
from geocaches.sync.gc_web import trpc
from geocaches.sync.gc_web.session import get_session
from geocaches.sync.rate_limiter import polite_delay
from geocaches.sync.trackable_constants import DEFAULT_TRACKABLE_LOG_TYPE_IDS

logger = logging.getLogger(__name__)

_LOG_PAGE_URL = "https://www.geocaching.com/live/geocache/{code}/log"
_LOG_EDIT_PAGE_URL = "https://www.geocaching.com/live/geocache/{code}/log/{log_ref}/edit?logType={log_type_id}"
_LOG_VIEW_PAGE_URL = "https://www.geocaching.com/live/log/{log_ref}"
_IMAGE_UPLOAD_URL = "https://www.geocaching.com/api/live/v1/logdrafts/images"
_IMAGE_UPDATE_URL = "https://www.geocaching.com/api/live/v1/images/{guid}/replace"


def _format_log_date(log_date: date | datetime) -> str:
    """Format a date/datetime as the noon-UTC ISO string the real client sends.

    Matches the captured request's ``"2026-08-12T12:00:00.000Z"`` — noon UTC
    sidesteps date-shifting from timezone conversion, same convention already
    used for ``found_date`` elsewhere in GCForge.
    """
    d = log_date.date() if isinstance(log_date, datetime) else log_date
    dt = datetime.combine(d, time(12, 0, 0), tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def trackable_entry(trackable_code: str, action: str) -> dict:
    """Build one item of ``create_log()``'s/``update_log()``'s ``trackables`` list.

    ``action`` is a GC trackable-log-type string (``"Visited"``,
    ``"Discovered It"``, ``"Retrieve It from a Cache"``, ``"Dropped Off"``,
    ``"Grab It (Not from a Cache)"``, ...) looked up in
    ``geocaches.sync.trackable_constants.DEFAULT_TRACKABLE_LOG_TYPE_IDS`` —
    see module docstring for why only ``"Visited"``'s id is independently
    confirmed on this path, the rest are high-confidence by the shared
    numbering-space precedent.
    """
    type_id = DEFAULT_TRACKABLE_LOG_TYPE_IDS.get(action)
    if type_id is None:
        raise ValueError(f"Unknown trackable action: {action!r}")
    return {"trackableCode": trackable_code, "trackableLogTypeId": type_id}


def create_log(
    gc_code: str,
    *,
    log_type_id: int,
    log_text: str,
    log_date: date | datetime,
    images: list | None = None,
    trackables: list | None = None,
    session: requests.Session | None = None,
) -> dict:
    """Create a geocache log via the website's tRPC API.

    Returns the raw procedure output dict (``logReferenceCode`` is the new
    log's ``GLxxxxxxxx`` reference code — same format the partner API uses).

    No favourite-point param here on purpose — the field
    (``usedFavoritePoint``) was only ever live-confirmed on ``update_log()``
    (see its docstring), as a same-session follow-up edit after create.
    ``submit_log()`` below does exactly that two-call sequence when
    ``use_favourite_point=True`` rather than guessing this untested position.
    """
    session = session or get_session()
    csrf_token = trpc.fetch_csrf_token(session, _LOG_PAGE_URL.format(code=gc_code))

    input_data = {
        "referenceCode": gc_code,
        "body": {
            "images": images or [],
            "logDate": _format_log_date(log_date),
            "logText": log_text,
            "logType": log_type_id,
            "trackables": trackables or [],
            "geocacheReferenceCode": "",
        },
    }
    logger.info("Web log create: code=%s type=%s", gc_code, log_type_id)
    return trpc.mutate(session, "web.logs.createGeocacheLog", input_data, csrf_token=csrf_token)


def update_log(
    gc_code: str,
    log_ref: str,
    *,
    log_type_id: int,
    log_text: str,
    log_date: date | datetime,
    images: list | None = None,
    trackables: list | None = None,
    used_favorite_point: bool | None = None,
    session: requests.Session | None = None,
) -> dict:
    """Update an existing geocache log via the website's tRPC API.

    ``gc_code`` is needed only to build the edit page URL the CSRF token is
    scraped from (``/live/geocache/{code}/log/{log_ref}/edit``, confirmed via
    the captured request's ``Referer`` header); the mutation itself addresses
    the log purely by ``log_ref``.

    ``used_favorite_point`` — **live-confirmed 2026-08-14** via a real find
    (Oliver's GCBPNTE log, ``GL1H54C2W``): sending ``"usedFavoritePoint":
    true`` in the body on an update call echoed back
    ``"usedFavoritePoint": true`` in the response — same field name the
    output already used elsewhere, now confirmed live as a writable input
    too. Left ``None``-default (field omitted) rather than always sending
    ``False``, so a plain edit that isn't touching the favourite status
    doesn't risk revoking one set earlier by some other path.
    """
    session = session or get_session()
    page_url = _LOG_EDIT_PAGE_URL.format(code=gc_code, log_ref=log_ref, log_type_id=log_type_id)
    csrf_token = trpc.fetch_csrf_token(session, page_url)

    body = {
        "images": images or [],
        "logDate": _format_log_date(log_date),
        "logText": log_text,
        "logType": log_type_id,
        "trackables": trackables or [],
        "geocacheReferenceCode": "",
    }
    if used_favorite_point is not None:
        body["usedFavoritePoint"] = used_favorite_point
    input_data = {"referenceCode": log_ref, "body": body}
    logger.info("Web log update: ref=%s", log_ref)
    return trpc.mutate(session, "web.logs.updateGeocacheLog", input_data, csrf_token=csrf_token)


def delete_log(
    log_ref: str,
    *,
    reason_text: str = "",
    session: requests.Session | None = None,
) -> None:
    """Delete a geocache log via the website's tRPC API.

    Unlike create/update, the body is flat (no nested ``body`` object) and
    the CSRF token is scraped from the log's own view page
    (``/live/log/{log_ref}``, confirmed via the captured request's
    ``Referer`` header) rather than an edit page — no cache code needed.
    """
    session = session or get_session()
    csrf_token = trpc.fetch_csrf_token(session, _LOG_VIEW_PAGE_URL.format(log_ref=log_ref))

    input_data = {"referenceCode": log_ref, "reasonText": reason_text}
    logger.info("Web log delete: ref=%s", log_ref)
    trpc.mutate(session, "web.logs.deleteGeocacheLog", input_data, csrf_token=csrf_token)


def submit_log(
    gc_code: str,
    log_type: str,
    logged_at_iso: str,
    text: str,
    *,
    use_favourite_point: bool = False,
    session: requests.Session | None = None,
) -> dict:
    """Submit a geocache log via the website's tRPC API.

    Matches ``gcprivate.gc_client.GCClient.submit_log()``'s call shape (see
    module docstring) so callers/a future resolver don't need to know which
    backend is active. Returns ``{"referenceCode": <GLxxxxxxxx>}`` — aliased
    from ``create_log()``'s ``logReferenceCode`` to match the official
    client's key name.

    Only the *date* portion of ``logged_at_iso`` survives — GC's web log form
    has no time-of-day field; every live capture posted noon UTC regardless
    of the actual time (see ``_format_log_date()``).

    ``use_favourite_point`` — **live-confirmed working 2026-08-14** (see
    ``update_log()``'s docstring), but only as a *follow-up edit* after
    create, not on the create call itself (untested there). So when
    requested, this makes a second call — ``update_log()`` re-sending the
    same type/text/date plus the favourite flag — right after create
    succeeds. If that second call fails, the exception propagates (the log
    itself is already posted by then; the caller sees a real log ref in the
    traceback's local state but no confirmation the favourite point landed)
    rather than being swallowed, so a failure here isn't silently lost.
    """
    log_type_id = _REVERSE_LOG_TYPE_MAP.get(log_type)
    if log_type_id is None:
        raise ValueError(f"Unknown log type for GC web client: {log_type!r}")

    log_date = date.fromisoformat(logged_at_iso[:10])
    result = create_log(
        gc_code, log_type_id=log_type_id, log_text=text, log_date=log_date, session=session,
    )
    log_ref = result.get("logReferenceCode", "")

    if use_favourite_point and log_ref:
        result = update_log(
            gc_code, log_ref, log_type_id=log_type_id, log_text=text, log_date=log_date,
            used_favorite_point=True, session=session,
        )

    return {"referenceCode": log_ref}


def attach_images(
    gc_code: str,
    log_ref: str,
    log_type: str,
    logged_at_iso: str,
    text: str,
    image_guids: list[str],
    *,
    session: requests.Session | None = None,
) -> dict:
    """Attach already-uploaded image guids to an existing log.

    Unlike the official partner API (which POSTs each image against an
    existing log ref *after* creating the log), the web tRPC API has no
    separate "attach image to existing log" call — images must be uploaded
    (``upload_log_image()``) before they can be referenced at all, and the
    only way to associate a guid with a log is ``update_log()``'s ``images``
    list, which rewrites the log's full body. This re-sends the log's own
    type/text/date alongside the new images so the net *ordering* still
    matches the official API's "create, then attach" shape from the
    caller's point of view, even though the two backends' underlying
    mechanisms differ (see ``geocaches.sync.base.GCLogClient``'s docstring
    for why that isn't unified into the shared Protocol).
    """
    log_type_id = _REVERSE_LOG_TYPE_MAP.get(log_type)
    if log_type_id is None:
        raise ValueError(f"Unknown log type for GC web client: {log_type!r}")
    log_date = date.fromisoformat(logged_at_iso[:10])
    return update_log(
        gc_code, log_ref, log_type_id=log_type_id, log_text=text, log_date=log_date,
        images=image_guids, session=session,
    )


def upload_log_image(
    gc_code: str,
    image_bytes: bytes,
    mime_type: str,
    *,
    name: str = "",
    description: str = "",
    filename: str = "image.jpg",
    session: requests.Session | None = None,
) -> dict:
    """Upload an image for an in-progress log via the website's REST API
    (plain multipart REST, not tRPC).

    **Live-verified end to end 2026-08-13**: real upload with a caption, a
    "Write note" log referencing it (the response's ``images`` entry came
    back with the exact ``name``/``description`` sent), and immediate
    cleanup via ``delete_log()`` — GC9WVK7, no trace left. Two separate
    calls, confirmed via HAR capture (a first attempt bundling the caption
    into the upload call itself silently didn't take — captions genuinely
    need the second call):

    1. ``POST /api/live/v1/logdrafts/images`` — multipart, one part named
       ``"file"``. Returns ``{"guid", "url", "thumbnailUrl", "success"}``,
       201.
    2. ``PUT /api/live/v1/images/{guid}/replace`` — separate, **text-only**
       multipart with ``"name"``/``"description"`` fields (captured live,
       byte-for-byte: field names, no file part). Only called here when
       ``name`` or ``description`` is non-empty. Returns
       ``{"id", "name", "description", "guid", "url", "orderId",
       "dateTaken", "createdDateUtc"}``.

    Note: a bare ``requests`` ``data=`` dict sends
    ``application/x-www-form-urlencoded``, not ``multipart/form-data`` —
    step 2 needs the ``files={"name": (None, value), ...}`` trick to match
    what the real client sends (confirmed the hard way: the first live
    attempt 400'd until this was fixed).

    Call before ``create_log()``/``update_log()`` — the returned ``guid`` is
    what goes in their ``images`` list.
    """
    session = session or get_session()
    csrf_token = trpc.fetch_csrf_token(session, _LOG_PAGE_URL.format(code=gc_code))

    files = {"file": (filename, image_bytes, mime_type)}
    polite_delay()
    resp = session.post(
        _IMAGE_UPLOAD_URL,
        files=files,
        headers={trpc.CSRF_HEADER: csrf_token, "Accept": "application/json"},
        timeout=60,
    )
    resp.raise_for_status()
    result = resp.json()

    if name or description:
        guid = result.get("guid")
        # requests only emits multipart/form-data (what the captured request
        # used) when `files=` is involved — a bare `data=` dict here would
        # silently fall back to application/x-www-form-urlencoded instead.
        # The (None, value) form sends a plain text field as a multipart
        # part with no filename.
        text_fields = {"name": (None, name), "description": (None, description)}
        polite_delay()
        resp2 = session.put(
            _IMAGE_UPDATE_URL.format(guid=guid),
            files=text_fields,
            headers={trpc.CSRF_HEADER: csrf_token, "Accept": "application/json"},
            timeout=30,
        )
        resp2.raise_for_status()
        result = resp2.json()

    return result


class GCWebLogClient:
    """Class wrapper around this module's free functions.

    Satisfies ``geocaches.sync.base.GCLogClient`` structurally (no
    inheritance declared — that's the point of a ``Protocol``), so
    ``geocaches.sync.gc_access`` can hold either this or
    ``gcprivate.gc_client.GCClient`` behind the same variable and call
    ``.submit_log(...)`` without knowing which backend is active.

    Everything beyond ``submit_log()`` (``upload_log_image()``,
    ``create_log()``/``update_log()``/``delete_log()``,
    ``trackable_entry()``) stays available as both a method here and a
    module-level function — the class exists for Protocol conformance and
    convenience, not to hide the module-level functions, and
    ``upload_log_image()`` intentionally keeps its own web-specific
    signature rather than faking a match to ``GCClient``'s (see
    ``GCLogClient``'s docstring for why).
    """

    platform = "gc"

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session

    def submit_log(
        self, gc_code: str, log_type: str, logged_at_iso: str, text: str,
        *, use_favourite_point: bool = False,
    ) -> dict:
        return submit_log(
            gc_code, log_type, logged_at_iso, text,
            use_favourite_point=use_favourite_point, session=self._session,
        )

    def upload_log_image(
        self, gc_code: str, image_bytes: bytes, mime_type: str,
        *, name: str = "", description: str = "", filename: str = "image.jpg",
    ) -> dict:
        return upload_log_image(
            gc_code, image_bytes, mime_type,
            name=name, description=description, filename=filename, session=self._session,
        )

    def attach_images(
        self, gc_code: str, log_ref: str, log_type: str, logged_at_iso: str, text: str,
        image_guids: list[str],
    ) -> dict:
        return attach_images(
            gc_code, log_ref, log_type, logged_at_iso, text, image_guids, session=self._session,
        )

    def create_log(
        self, gc_code: str, *, log_type_id: int, log_text: str, log_date: date | datetime,
        images: list | None = None, trackables: list | None = None,
    ) -> dict:
        return create_log(
            gc_code, log_type_id=log_type_id, log_text=log_text, log_date=log_date,
            images=images, trackables=trackables, session=self._session,
        )

    def update_log(
        self, gc_code: str, log_ref: str, *, log_type_id: int, log_text: str,
        log_date: date | datetime, images: list | None = None, trackables: list | None = None,
        used_favorite_point: bool | None = None,
    ) -> dict:
        return update_log(
            gc_code, log_ref, log_type_id=log_type_id, log_text=log_text, log_date=log_date,
            images=images, trackables=trackables, used_favorite_point=used_favorite_point,
            session=self._session,
        )

    def delete_log(self, log_ref: str, *, reason_text: str = "") -> None:
        delete_log(log_ref, reason_text=reason_text, session=self._session)
