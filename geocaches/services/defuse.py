"""De-fuse service: split a fused GC+OC cache record back into two."""
import logging
from dataclasses import dataclass
from typing import Optional

_defuse_logger = logging.getLogger("geocaches.defuse")


@dataclass
class DefuseResult:
    gc_code: str
    oc_code: str
    oc_logs_deleted: int
    oc_import_error: Optional[str] = None
    gc_refresh_error: Optional[str] = None

    @property
    def has_errors(self) -> bool:
        return bool(self.oc_import_error or self.gc_refresh_error)


def defuse_cache(cache, gc_client, oc_client) -> DefuseResult:
    """De-fuse a cache that has both a gc_code and an oc_code.

    Steps 1-3 from the old inline view logic:
      1. Strip OC data from the GC record (logs, extension, attributes, oc_code).
      2. Re-import the OC cache as a fresh standalone record.
      3. Full GC refresh to restore GC-only attributes.

    The caller is responsible for permission checks, writing the note, and
    recording the fusion decision.
    """
    from geocaches.sync.base import SyncMode
    from geocaches.sync.log_fetch import ensure_my_gc_logs
    from geocaches.services import save_geocache

    gc_code = cache.gc_code
    oc_code = cache.oc_code

    # 1. Strip OC data from the GC cache record
    oc_logs_deleted, _ = cache.logs.filter(source__startswith="oc_").delete()

    from geocaches.models import OCExtension
    try:
        cache.oc_extension.delete()
    except OCExtension.DoesNotExist:
        pass

    cache.attributes.clear()  # re-fetched from both APIs below
    cache.oc_code = ""
    cache.save(update_fields=["oc_code"])

    _defuse_logger.info(
        "De-fused %s: removed OC code %s, deleted %d OC logs",
        gc_code, oc_code, oc_logs_deleted,
    )

    # 2. Re-import OC cache as a fresh standalone record
    oc_import_error = None
    try:
        oc_data = oc_client.get_cache(oc_code, SyncMode.FULL)
        oc_kwargs = dict(oc_data)
        oc_kwargs["fields"] = dict(oc_data["fields"])
        save_geocache(**oc_kwargs)
    except Exception as exc:
        oc_import_error = str(exc)
        _defuse_logger.error("De-fuse: OC re-import failed for %s: %s", oc_code, exc)

    # 3. Full GC refresh
    gc_refresh_error = None
    try:
        gc_data = gc_client.get_cache(gc_code, SyncMode.FULL, log_count=5)
        gc_kwargs = dict(gc_data)
        gc_kwargs["fields"] = dict(gc_data["fields"])
        save_geocache(**gc_kwargs)
        ensure_my_gc_logs(gc_client, gc_code)
    except Exception as exc:
        gc_refresh_error = str(exc)
        _defuse_logger.error("De-fuse: GC refresh failed for %s: %s", gc_code, exc)

    _defuse_logger.info("De-fuse complete: %s / %s", gc_code, oc_code)

    return DefuseResult(
        gc_code=gc_code,
        oc_code=oc_code,
        oc_logs_deleted=oc_logs_deleted,
        oc_import_error=oc_import_error,
        gc_refresh_error=gc_refresh_error,
    )
