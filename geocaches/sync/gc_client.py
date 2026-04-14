"""
Geocaching.com API sync client — stub for public build.

GC API support is not included in this release.
"""
from .base import BasePlatformClient, SyncMode


class GCClient(BasePlatformClient):
    platform = "gc"

    def __init__(self) -> None:
        raise NotImplementedError("GC API support not yet available")

    @property
    def batch_size(self) -> int:
        return 50

    def search_by_bbox(self, south, west, north, east, *, max_results=500):
        raise NotImplementedError("GC API support not yet available")

    def search_by_center(self, lat, lon, radius_m, *, max_results=500):
        raise NotImplementedError("GC API support not yet available")

    def get_caches(self, codes, mode=SyncMode.LIGHT, *, log_count=5):
        raise NotImplementedError("GC API support not yet available")

    def get_cache(self, code, mode=SyncMode.FULL, *, log_count=5):
        raise NotImplementedError("GC API support not yet available")

    def normalize(self, raw, mode):
        raise NotImplementedError("GC API support not yet available")

    def get_pocket_queries(self):
        raise NotImplementedError("GC API support not yet available")

    def download_pocket_query(self, reference_code):
        raise NotImplementedError("GC API support not yet available")

    def get_log_drafts(self):
        raise NotImplementedError("GC API support not yet available")

    def submit_log(self, gc_code, log_type, logged_at_iso, text, *, use_favourite_point=False):
        raise NotImplementedError("GC API support not yet available")

    def set_log_favourite(self, log_ref, value):
        raise NotImplementedError("GC API support not yet available")

    def upload_log_image(self, log_ref, image_bytes, mime_type, *, name="", description=""):
        raise NotImplementedError("GC API support not yet available")
