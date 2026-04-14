"""
Opencaching (OKAPI) sync client.

Implements BasePlatformClient for any OKAPI-based OC node (oc_de, oc_pl, etc.).
Uses Level 1 (consumer key) for public queries and Level 3 (OAuth) for
user-specific fields (found status, personal notes).
"""

import logging
from datetime import date as date_cls

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
    "Virtual Stage": "Stage",
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
    "Temporarily Disable Listing": "Temporarily unavailable",
    "Enable Listing": "Ready to search",
    "Needs Maintenance": "Needs maintenance",
}


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
            "image_base64": b64,
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
                attributes = []
                for acode in raw_attrs:
                    # Extract numeric ID from A-code (e.g. "A62" → 62)
                    attr_id = int(acode[1:]) if acode.startswith("A") and acode[1:].isdigit() else 0
                    attributes.append({
                        "source": "oc",
                        "attribute_id": attr_id,
                        "is_positive": True,
                        "name": acode,
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
