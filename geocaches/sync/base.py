"""
Base classes for the API sync layer.

BasePlatformClient defines the interface that GC and OC clients implement.
SyncResult tracks the outcome of a sync operation.
SyncMode controls field depth (light = metadata only, full = everything).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum


class SyncMode(StrEnum):
    LIGHT = "light"
    FULL = "full"


@dataclass
class SyncResult:
    """Outcome of a sync operation."""
    created: int = 0
    updated: int = 0
    skipped: int = 0       # import-locked or unchanged
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.created + self.updated + self.skipped + self.failed

    def merge(self, other: "SyncResult") -> None:
        self.created += other.created
        self.updated += other.updated
        self.skipped += other.skipped
        self.failed += other.failed
        self.errors.extend(other.errors)


class BasePlatformClient(ABC):
    """
    Common interface for GC and OC API clients.

    Each platform client wraps its own auth mechanism and API format,
    but exposes the same operations. The normalize() method transforms
    platform-specific responses into save_geocache() kwargs.
    """

    platform: str  # "gc", "oc_de", "oc_pl", etc.

    @abstractmethod
    def search_by_bbox(
        self,
        south: float, west: float, north: float, east: float,
        *,
        max_results: int = 500,
    ) -> list[str]:
        """
        Return cache codes within a bounding box.

        Handles pagination internally. Returns a list of code strings
        (e.g. ["GC12345", "GC67890"] or ["OC1A2B3"]).
        """
        ...

    def search_by_center(
        self,
        lat: float, lon: float, radius_m: float,
        *,
        max_results: int = 500,
    ) -> list[str]:
        """
        Return cache codes within a circle (center + radius in metres).

        Default implementation converts to bounding box. Subclasses may
        override with a native radius-search API for better precision.
        """
        import math
        # Approximate bounding box from center + radius
        r_km = radius_m / 1000
        d_lat = r_km / 111.32
        d_lon = r_km / (111.32 * math.cos(math.radians(lat)))
        return self.search_by_bbox(
            lat - d_lat, lon - d_lon, lat + d_lat, lon + d_lon,
            max_results=max_results,
        )

    @abstractmethod
    def get_caches(
        self,
        codes: list[str],
        mode: SyncMode = SyncMode.LIGHT,
        *,
        log_count: int = 5,
    ) -> list[dict]:
        """
        Fetch and normalize cache data for multiple codes.

        Returns a list of dicts, each compatible with save_geocache() kwargs.
        Mode controls field depth: LIGHT for metadata, FULL for everything.
        Handles batch splitting internally (respects batch_size).
        log_count controls max logs per cache in FULL mode (ignored in LIGHT).
        """
        ...

    @abstractmethod
    def get_cache(
        self,
        code: str,
        mode: SyncMode = SyncMode.FULL,
    ) -> dict:
        """
        Fetch and normalize cache data for a single code.

        Returns a dict compatible with save_geocache() kwargs.
        Defaults to FULL mode for single-cache requests (detail view use case).
        """
        ...

    @abstractmethod
    def normalize(self, raw: dict, mode: SyncMode) -> dict:
        """
        Transform a platform-specific API response into save_geocache() kwargs.

        The returned dict must contain keys matching save_geocache() parameters:
            gc_code or oc_code  (str)
            fields              (dict of model field name → value)
            found               (bool | None)
            found_date          (date | None)
            update_source       (str: "gc" or "oc")

        And optionally (only populated in FULL mode):
            logs                (list[dict] | None)
            waypoints           (list[dict] | None)
            attributes          (list[dict] | None)
            corrected_coords    (dict | None)
            notes               (list[dict] | None)
        """
        ...

    @property
    @abstractmethod
    def batch_size(self) -> int:
        """Max codes per get_caches() API call (GC=50, OC=500)."""
        ...
