"""
Opencaching (OKAPI) sync client.

Implements BasePlatformClient for any OKAPI-based OC node (oc_de, oc_pl, etc.).
Uses Level 1 (consumer key) for public queries and Level 3 (OAuth) for
user-specific fields (found status, personal notes).
"""

import logging

from accounts.keyring_util import get_oauth_token
from accounts.okapi_client import (
    _get_level1,
    _get_level3,
    get_consumer_credentials,
    get_node_url,
)

from .base import BasePlatformClient, SyncMode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapping constants (OC API value → GCForge enum value)
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "Traditional": "Traditional",
    "Multi": "Multi-Cache",
    "Quiz": "Mystery",
    "Moving": "Moving",
    "Virtual": "Virtual",
    "Webcam": "Webcam",
    "Event": "Event",
    "Own": "Own",
    "Podcast": "Podcast",
    "Drive-In": "Drive-In",
    "Math/Physics": "Math/Physics",
    "Other": "Unknown",
}

_SIZE_MAP = {
    "none": "None",
    "nano": "Nano",
    "micro": "Micro",
    "small": "Small",
    "regular": "Regular",
    "large": "Large",
    "xlarge": "XLarge",
    "other": "Other",
}

_STATUS_MAP = {
    "Available": "Active",
    "Temporarily unavailable": "Disabled",
    "Archived": "Archived",
}

# Reverse maps (GCForge value → OKAPI search value), for criteria search.
# First OKAPI key wins per value; the maps above are 1:1 so order is irrelevant.
_REVERSE_TYPE_MAP: dict[str, str] = {}
for _ok, _val in _TYPE_MAP.items():
    _REVERSE_TYPE_MAP.setdefault(_val, _ok)

_REVERSE_SIZE_MAP: dict[str, str] = {}
for _ok, _val in _SIZE_MAP.items():
    _REVERSE_SIZE_MAP.setdefault(_val, _ok)

_LOG_TYPE_MAP = {
    "Found it": "Found it",
    "Didn't find it": "Didn't find it",
    "Comment": "Write note",
    "Will attend": "Will Attend",
    "Attended": "Attended",
    "Ready to search": "Enable Listing",
    "Temporarily unavailable": "Temporarily Disable Listing",
    "Archived": "Archive",
}

# OC waypoint type_name → WaypointType.value
_WP_TYPE_MAP = {
    "Parking Area": "Parking",
    "parking": "Parking",
    "Stage": "Stage",
    "stage": "Stage",
    "Physical Stage": "Stage",
    "Virtual Stage": "Question",
    "Question to Answer": "Question",
    "Final Location": "Final",
    "final": "Final",
    "Trailhead": "Trailhead",
    "Reference Point": "Reference",
    "User coords": "Final",
}


# ---------------------------------------------------------------------------
# Field sets for OKAPI requests (pipe-separated)
# ---------------------------------------------------------------------------

_LIGHT_FIELDS = "|".join([
    "code", "name", "location", "type", "status",
    "size2", "difficulty", "terrain",
    "founds", "recommendations",
    "country", "state",
    "date_hidden", "last_found", "last_modified",
    "gc_code", "owner", "needs_maintenance", "req_passwd",
])

_FULL_FIELDS = _LIGHT_FIELDS + "|" + "|".join([
    "description", "descriptions",
    "hint2", "hints2",
    "short_description", "short_descriptions",
    "images", "latest_logs",
    "trackables_count", "alt_wpts", "attr_acodes",
])

# Appended when Level 3 OAuth credentials are available
_USER_FIELDS = "|".join([
    "is_found", "my_notes", "is_recommended",
])


# ---------------------------------------------------------------------------
# Public client (implements BasePlatformClient)
# ---------------------------------------------------------------------------

# GCForge LogType value → OKAPI logtype string (for submission)
_REVERSE_OC_LOG_TYPE_MAP = {
    "Found it": "Found it",
    "Didn't find it": "Didn't find it",
    "Write note": "Comment",
    "Will Attend": "Will attend",
    "Attended": "Attended",
    # OC has no dedicated webcam log type; it's logged as a normal find.
    "Webcam Photo Taken": "Found it",
    "Temporarily Disable Listing": "Temporarily unavailable",
    "Enable Listing": "Ready to search",
    "Needs Maintenance": "Needs maintenance",
}


# Process-lifetime cache of OKAPI attribute dictionaries, keyed by platform.
# Maps numeric attribute id → display name (resolved from A-codes).
_ATTR_NAME_CACHE: dict[str, dict[int, str]] = {}


class OCClient(BasePlatformClient):
    """Opencaching OKAPI client implementing the sync interface.

    Args:
        platform: OC node identifier (e.g. "oc_de", "oc_pl").
        user_id: Optional user_id for Level 3 (OAuth) access.
                 If provided, found status and personal notes are fetched.
    """

    def __init__(self, platform: str = "oc_de", user_id: str = "") -> None:
        self.platform = platform

        base = get_node_url(platform)
        if not base:
            raise ValueError(f"Unknown OC platform: {platform!r}")
        self._base_url = base + "/okapi"

        creds = get_consumer_credentials(platform)
        if not creds:
            raise ValueError(f"No consumer key for platform: {platform!r}")
        self._consumer_key, self._consumer_secret = creds

        # Level 3 credentials (optional)
        self._oauth_token = ""
        self._oauth_token_secret = ""
        if user_id:
            tokens = get_oauth_token(platform, user_id)
            if tokens:
                self._oauth_token, self._oauth_token_secret = tokens

    @property
    def _has_level3(self) -> bool:
        return bool(self._oauth_token)

    @property
    def batch_size(self) -> int:
        return 50

    def _get(self, path: str, params: dict) -> dict | list:
        """Make a GET request, using Level 3 if available, Level 1 otherwise."""
        url = self._base_url + path
        if self._has_level3:
            return _get_level3(
                url, params,
                self._consumer_key, self._consumer_secret,
                self._oauth_token, self._oauth_token_secret,
            )
        return _get_level1(url, params, self._consumer_key)

    def get_attribute_names(self, langpref: str = "en|de") -> dict[int, str]:
        """Resolve OKAPI A-codes to display names → ``{numeric_id: name}``.

        Calls ``services/attrs/attributes`` (Level 1) once per platform and
        caches the result for the process lifetime.  Returns ``{}`` on any
        failure so callers fall back to the raw A-code.
        """
        cached = _ATTR_NAME_CACHE.get(self.platform)
        if cached is not None:
            return cached

        names: dict[int, str] = {}
        try:
            raw = self._get("/services/attrs/attribute_index", {
                "fields": "name",
                "langpref": langpref,
            })
            if isinstance(raw, dict):
                for acode, info in raw.items():
                    if not (acode.startswith("A") and acode[1:].isdigit()):
                        continue
                    name = info.get("name") if isinstance(info, dict) else None
                    if name:
                        names[int(acode[1:])] = name
        except Exception:
            names = {}

        _ATTR_NAME_CACHE[self.platform] = names
        return names

    def _post(self, path: str, params: dict) -> dict:
        """Level 3 OAuth POST request."""
        if not self._has_level3:
            raise ValueError("OC log submission requires Level 3 OAuth credentials")
        url = self._base_url + path
        from accounts.okapi_client import _post_level3
        return _post_level3(
            url, params,
            self._consumer_key, self._consumer_secret,
            self._oauth_token, self._oauth_token_secret,
        )

    def submit_log(
        self, cache_code: str, log_type: str, when_iso: str, comment: str,
        password: str = "", recommend: bool = False,
    ) -> dict:
        """Submit a log via OKAPI services/logs/submit. Returns response dict.

        Raises ValueError if the API reports success=false (e.g. wrong passphrase).
        """
        oc_logtype = _REVERSE_OC_LOG_TYPE_MAP.get(log_type)
        if oc_logtype is None:
            raise ValueError(f"Unsupported log type for OC: {log_type!r}")
        params = {
            "cache_code": cache_code,
            "logtype": oc_logtype,
            "comment": comment,
            "when": when_iso,
        }
        if password:
            params["password"] = password
        if recommend and oc_logtype == "Found it":
            params["recommend"] = "true"
        resp = self._post("/services/logs/submit", params)
        if not resp.get("success"):
            msg = resp.get("message") or "Log submission rejected by OKAPI"
            raise ValueError(msg)
        resp["log_uuid"] = resp.get("log_uuid") or ""
        return resp

    def add_recommendation(self, cache_code: str) -> None:
        """Add an OC recommendation for a cache (OKAPI services/recommendations/add)."""
        resp = self._post("/services/recommendations/add", {"cache_code": cache_code})
        if not resp.get("success"):
            raise ValueError(resp.get("message") or "Failed to add recommendation")

    def remove_recommendation(self, cache_code: str) -> None:
        """Remove an OC recommendation for a cache (OKAPI services/recommendations/delete)."""
        resp = self._post("/services/recommendations/delete", {"cache_code": cache_code})
        if not resp.get("success"):
            raise ValueError(resp.get("message") or "Failed to remove recommendation")

    def upload_log_image(
        self,
        log_uuid: str,
        image_bytes: bytes,
        mime_type: str,
        *,
        caption: str = "",
        is_spoiler: bool = False,
    ) -> tuple[bool, str]:
        """Upload an image to an OC log via OKAPI services/logs/images/add.

        Returns (success, error_message).
        """
        import base64
        b64 = base64.b64encode(image_bytes).decode("ascii")
        params: dict = {
            "log_uuid": log_uuid,
            "image": b64,
            "is_spoiler": "true" if is_spoiler else "false",
        }
        if caption:
            params["caption"] = caption
        try:
            resp = self._post("/services/logs/images/add", params)
            if resp.get("success"):
                return True, ""
            return False, resp.get("message") or "OKAPI rejected image upload"
        except Exception as exc:
            return False, str(exc)

    def _fields_for_mode(self, mode: SyncMode) -> str:
        """Return the pipe-separated field list for the given mode."""
        fields = _FULL_FIELDS if mode == SyncMode.FULL else _LIGHT_FIELDS
        if self._has_level3:
            fields += "|" + _USER_FIELDS
        return fields

    def search_by_bbox(
        self,
        south: float, west: float, north: float, east: float,
        *,
        max_results: int = 500,
    ) -> list[str]:
        codes: list[str] = []
        offset = 0
        limit = min(500, max_results)
        while len(codes) < max_results:
            result = self._get(
                "/services/caches/search/bbox",
                {
                    "bbox": f"{south}|{west}|{north}|{east}",
                    "limit": str(limit),
                    "offset": str(offset),
                },
            )
            batch = result.get("results", []) if isinstance(result, dict) else []
            if not batch:
                break
            codes.extend(batch)
            offset += len(batch)
            if len(batch) < limit:
                break
        return codes[:max_results]

    def search_by_center(
        self,
        lat: float, lon: float, radius_m: float,
        *,
        max_results: int = 500,
    ) -> list[str]:
        """Search by center + radius using OKAPI search/nearest."""
        radius_km = radius_m / 1000
        codes: list[str] = []
        offset = 0
        limit = min(500, max_results)
        while len(codes) < max_results:
            result = self._get(
                "/services/caches/search/nearest",
                {
                    "center": f"{lat}|{lon}",
                    "radius": str(radius_km),
                    "limit": str(limit),
                    "offset": str(offset),
                },
            )
            batch = result.get("results", []) if isinstance(result, dict) else []
            if not batch:
                break
            codes.extend(batch)
            offset += len(batch)
            if len(batch) < limit:
                break
        return codes[:max_results]

    def _resolve_uuid(self, username: str) -> str:
        """Resolve an OC username to its UUID via OKAPI (Level 1). '' on miss."""
        username = (username or "").strip()
        if not username:
            return ""
        try:
            from accounts.okapi_client import lookup_user_by_username
            info = lookup_user_by_username(self.platform, self._consumer_key, username)
            return info.get("uuid", "") if isinstance(info, dict) else ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("OC user lookup failed for %r on %s: %s", username, self.platform, exc)
            return ""

    def search_criteria(
        self,
        criteria: dict,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        max_results: int = 500,
    ) -> list[str]:
        """Return cache codes matching ``criteria`` via OKAPI search/all.

        ``criteria`` keys (all optional): owner, types (GCForge cache_type values),
        sizes (GCForge size values), d_min/d_max, t_min/t_max, name, found_by,
        not_found_by, found_status, exclude_disabled, include_archived.
        ``min_fav`` is GC-only and ignored here (OC has no favourite points).

        A specified owner/found_by that can't be resolved to a UUID yields an
        empty result rather than silently widening the scope.
        """
        params: dict[str, str] = {
            "limit": str(min(500, max_results)),
            "offset": "0",
        }

        types = [_REVERSE_TYPE_MAP[t] for t in criteria.get("types", []) if t in _REVERSE_TYPE_MAP]
        if types:
            params["type"] = "|".join(types)

        sizes = [_REVERSE_SIZE_MAP[s] for s in criteria.get("sizes", []) if s in _REVERSE_SIZE_MAP]
        if sizes:
            params["size2"] = "|".join(sizes)

        lo = int(round(criteria.get("d_min") or 1))
        hi = int(round(criteria.get("d_max") or 5))
        if (lo, hi) != (1, 5):
            params["difficulty"] = f"{lo}-{hi}"
        t_lo = int(round(criteria.get("t_min") or 1))
        t_hi = int(round(criteria.get("t_max") or 5))
        if (t_lo, t_hi) != (1, 5):
            params["terrain"] = f"{t_lo}-{t_hi}"

        name = (criteria.get("name") or "").strip()
        if name:
            params["name"] = f"*{name}*"

        statuses = ["Available"]
        if not criteria.get("exclude_disabled"):
            statuses.append("Temporarily unavailable")
        if criteria.get("include_archived"):
            statuses.append("Archived")
        params["status"] = "|".join(statuses)

        # Owner / found-by need UUIDs. Positive filters that fail to resolve
        # must not silently widen the scope → return no results.
        owner = (criteria.get("owner") or "").strip()
        if owner:
            uuid = self._resolve_uuid(owner)
            if not uuid:
                return []
            params["owner_uuid"] = uuid

        found_by = (criteria.get("found_by") or "").strip()
        if found_by:
            uuid = self._resolve_uuid(found_by)
            if not uuid:
                return []
            params["found_by"] = uuid

        not_found_by = (criteria.get("not_found_by") or "").strip()
        if not_found_by:
            uuid = self._resolve_uuid(not_found_by)
            if uuid:  # unresolved exclusion is harmless — just skip it
                params["not_found_by"] = uuid

        fs = criteria.get("found_status")
        if fs in ("found_only", "notfound_only") and self._has_level3:
            params["found_status"] = fs

        if bbox:
            s, w, n, e = bbox
            params["bbox"] = f"{s}|{w}|{n}|{e}"

        result = self._get("/services/caches/search/all", params)
        codes = result.get("results", []) if isinstance(result, dict) else []
        return codes[:max_results]

    def get_caches(
        self,
        codes: list[str],
        mode: SyncMode = SyncMode.LIGHT,
        *,
        log_count: int = 5,
    ) -> list[dict]:
        fields = self._fields_for_mode(mode)
        results = []
        for i in range(0, len(codes), self.batch_size):
            batch_codes = codes[i:i + self.batch_size]
            params = {
                "cache_codes": "|".join(batch_codes),
                "fields": fields,
            }
            if mode == SyncMode.FULL and log_count:
                params["lpc"] = str(log_count)
            raw = self._get(
                "/services/caches/geocaches",
                params,
            )
            # raw is a dict keyed by cache code
            if isinstance(raw, dict):
                for code in batch_codes:
                    cache_data = raw.get(code)
                    if cache_data:
                        results.append(self.normalize(cache_data, mode))
        return results

    def get_cache(
        self,
        code: str,
        mode: SyncMode = SyncMode.FULL,
    ) -> dict:
        fields = self._fields_for_mode(mode)
        raw = self._get(
            "/services/caches/geocache",
            {
                "cache_code": code,
                "fields": fields,
            },
        )
        return self.normalize(raw, mode)

    def normalize(self, raw: dict, mode: SyncMode) -> dict:
        oc_code = raw.get("code", "")

        # Location: "lat|lon" string
        loc = raw.get("location", "")
        lat, lon = 0.0, 0.0
        if loc and "|" in loc:
            parts = loc.split("|")
            try:
                lat, lon = float(parts[0]), float(parts[1])
            except (ValueError, IndexError):
                pass

        # Core fields
        fields = {
            "name": raw.get("name") or "",
            "cache_type": _TYPE_MAP.get(raw.get("type") or "", "Unknown"),
            "size": _SIZE_MAP.get(raw.get("size2") or "", "Unknown"),
            "difficulty": raw.get("difficulty"),
            "terrain": raw.get("terrain"),
            "status": _STATUS_MAP.get(raw.get("status", ""), "Active"),
            "latitude": lat,
            "longitude": lon,
            "recommendations": raw.get("recommendations"),
            "found_count": raw.get("founds"),
            "country": raw.get("country") or "",
            "state": raw.get("state") or "",
            "needs_maintenance": raw.get("needs_maintenance", False),
            "primary_source": "oc",
        }

        # Owner
        owner = raw.get("owner", {})
        if isinstance(owner, dict):
            fields["owner"] = owner.get("username", "")

        # Hidden date
        date_hidden = raw.get("date_hidden")
        if date_hidden:
            fields["hidden_date"] = date_hidden[:10]

        # Last found date
        last_found = raw.get("last_found")
        if last_found:
            fields["last_found_date"] = last_found[:10]

        # GC code cross-reference
        gc_code = raw.get("gc_code", "")

        # Found status (Level 3 only)
        found = None
        if raw.get("is_found") is not None:
            found = raw["is_found"]

        # OC-extension fields (stored in OCExtension, not Geocache)
        oc_ext: dict = {}
        if raw.get("req_passwd") is not None:
            oc_ext["req_passwd"] = bool(raw["req_passwd"])
        if raw.get("is_recommended") is not None:
            oc_ext["user_recommended"] = bool(raw["is_recommended"])
        # Persist the owner-stated GC cross-reference even when the pair isn't fused yet
        if gc_code:
            oc_ext["related_gc_code"] = gc_code

        result = {
            "oc_code": oc_code,
            "fields": fields,
            "found": found,
            "found_date": None,  # OKAPI doesn't provide found_date directly
            "corrected_coords": None,
            "update_source": "oc",
            "oc_ext": oc_ext or None,
        }

        # Cross-reference GC code
        if gc_code:
            result["gc_code"] = gc_code

        # Full mode extras
        if mode == SyncMode.FULL:
            # Descriptions — prefer language-specific dicts, fall back to single field
            descriptions = raw.get("descriptions", {}) or {}
            short_descriptions = raw.get("short_descriptions", {}) or {}
            # Pick best language: en > de > first available
            for lang in ("en", "de"):
                if lang in descriptions:
                    fields["long_description"] = descriptions[lang]
                    break
            else:
                if descriptions:
                    fields["long_description"] = next(iter(descriptions.values()))
                else:
                    fields["long_description"] = raw.get("description", "")

            for lang in ("en", "de"):
                if lang in short_descriptions:
                    fields["short_description"] = short_descriptions[lang]
                    break
            else:
                if short_descriptions:
                    fields["short_description"] = next(iter(short_descriptions.values()))
                else:
                    fields["short_description"] = raw.get("short_description", "")

            # Hints
            hints = raw.get("hints2", {}) or {}
            for lang in ("en", "de"):
                if lang in hints:
                    fields["hint"] = hints[lang]
                    break
            else:
                if hints:
                    fields["hint"] = next(iter(hints.values()))
                else:
                    fields["hint"] = raw.get("hint2", "")

            # Logs — keys must match Log model fields
            raw_logs = raw.get("latest_logs", []) or []
            if raw_logs:
                logs = []
                for log in raw_logs:
                    log_type = _LOG_TYPE_MAP.get(log.get("type", ""), "Write note")
                    date_str = (
                        log.get("date", "")[:10]
                        if log.get("date") else ""
                    )
                    if not date_str:
                        continue
                    user = log.get("user", {})
                    logs.append({
                        "log_type": log_type,
                        "logged_date": date_str,
                        "user_name": user.get("username", ""),
                        "user_id": user.get("uuid", ""),
                        "text": log.get("comment", ""),
                        "source_id": log.get("uuid", ""),
                        "source": self.platform,
                    })
                result["logs"] = logs

            # Waypoints — keys must match Waypoint model fields
            raw_wps = raw.get("alt_wpts", []) or []
            if raw_wps:
                waypoints = []
                for idx, wp in enumerate(raw_wps):
                    wp_loc = wp.get("location", "")
                    wp_lat, wp_lon = None, None
                    if wp_loc and "|" in wp_loc:
                        parts = wp_loc.split("|")
                        try:
                            wp_lat, wp_lon = float(parts[0]), float(parts[1])
                        except (ValueError, IndexError):
                            pass
                    wp_type = _WP_TYPE_MAP.get(
                        wp.get("type_name", ""), "Other",
                    )
                    waypoints.append({
                        "lookup": f"{oc_code}-WP{idx}",
                        "prefix": "",
                        "name": wp.get("name", ""),
                        "waypoint_type": wp_type,
                        "latitude": wp_lat,
                        "longitude": wp_lon,
                        "note": wp.get("description", ""),
                    })
                result["waypoints"] = waypoints

            # Attributes (OKAPI uses A-codes like "A1", "A62")
            raw_attrs = raw.get("attr_acodes", []) or []
            if raw_attrs:
                attr_names = self.get_attribute_names()
                attributes = []
                for acode in raw_attrs:
                    # Extract numeric ID from A-code (e.g. "A62" → 62)
                    attr_id = int(acode[1:]) if acode.startswith("A") and acode[1:].isdigit() else 0
                    attributes.append({
                        "source": "oc",
                        "attribute_id": attr_id,
                        "is_positive": True,
                        # Resolve to the full display name; fall back to the
                        # raw A-code if the dictionary lookup misses.
                        "name": attr_names.get(attr_id) or acode,
                    })
                result["attributes"] = attributes

            # Trackable count
            tc = raw.get("trackables_count")
            if tc is not None:
                fields["has_trackable"] = tc > 0

            # Personal note (Level 3 only)
            my_notes = raw.get("my_notes", "")
            if my_notes:
                fields["gc_note"] = my_notes  # reuse gc_note field for OC notes too

        return result

    def get_ignored_caches(self) -> list[dict]:
        """Return all caches on the user's OC ignored list with name and status."""
        if not self._has_level3:
            raise ValueError("get_ignored_caches requires Level 3 OAuth credentials")
        codes: list[str] = []
        offset = 0
        limit = 500
        while True:
            result = self._get("/services/caches/search/all", {
                "ignored_status": "ignored_only",
                "limit": str(limit),
                "offset": str(offset),
            })
            batch = result.get("results", []) if isinstance(result, dict) else []
            codes.extend(batch)
            if not result.get("more"):
                break
            offset += len(batch)

        if not codes:
            return []

        entries: list[dict] = []
        for i in range(0, len(codes), self.batch_size):
            batch = codes[i:i + self.batch_size]
            raw = self._get("/services/caches/geocaches", {
                "cache_codes": "|".join(batch),
                "fields": "code|name|status",
            })
            if isinstance(raw, dict):
                for code in batch:
                    item = raw.get(code) or {}
                    entries.append({
                        "code": code,
                        "name": item.get("name", ""),
                        "status": _STATUS_MAP.get(item.get("status", ""), ""),
                    })
        return entries

    def set_ignored(self, oc_code: str, ignored: bool) -> None:
        """Mark or unmark a cache as ignored via OKAPI services/caches/mark."""
        resp = self._post("/services/caches/mark", {
            "cache_code": oc_code,
            "ignored": "true" if ignored else "false",
        })
        if not resp.get("success"):
            raise ValueError(resp.get("developer_message") or "OKAPI mark failed")
