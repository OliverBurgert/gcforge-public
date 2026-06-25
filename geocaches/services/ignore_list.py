from typing import Iterable

from geocaches.models import IgnoreListEntry, IgnoreSource


_LOCAL_SOURCES = (IgnoreSource.INTERNAL, IgnoreSource.GSAK)


def is_internally_ignored(code: str) -> bool:
    return IgnoreListEntry.objects.filter(source__in=_LOCAL_SOURCES, code=code).exists()


def internally_ignored_codes(codes: Iterable[str]) -> set[str]:
    code_list = list(codes)
    if not code_list:
        return set()
    return set(
        IgnoreListEntry.objects.filter(source__in=_LOCAL_SOURCES, code__in=code_list)
        .values_list("code", flat=True)
    )


def _local_cache_fields(code: str) -> tuple[str, str]:
    """Return (name, status) from a local Geocache row matching this code, or ('','')."""
    from geocaches.models import Geocache
    g = (
        Geocache.objects.filter(gc_code=code).only("name", "status").first()
        or Geocache.objects.filter(oc_code=code).only("name", "status").first()
    )
    return (g.name, g.status) if g else ("", "")


def add_internal(code: str, *, notes: str = "") -> IgnoreListEntry:
    name, status = _local_cache_fields(code)
    entry, _ = IgnoreListEntry.objects.get_or_create(
        source=IgnoreSource.INTERNAL,
        oc_platform="",
        code=code,
        defaults={"notes": notes, "name": name, "status": status},
    )
    return entry


def remove_internal(code: str) -> bool:
    deleted, _ = IgnoreListEntry.objects.filter(
        source__in=_LOCAL_SOURCES, code=code
    ).delete()
    return deleted > 0


def upsert_remote(
    source: str,
    code: str,
    *,
    oc_platform: str = "",
    name: str = "",
    status: str = "",
) -> IgnoreListEntry:
    entry, _ = IgnoreListEntry.objects.update_or_create(
        source=source,
        oc_platform=oc_platform,
        code=code,
        defaults={"name": name, "status": status},
    )
    return entry


def remove_remote(source: str, code: str, *, oc_platform: str = "") -> bool:
    deleted, _ = IgnoreListEntry.objects.filter(
        source=source, oc_platform=oc_platform, code=code
    ).delete()
    return deleted > 0


def remove_archived(source: str | None = None, oc_platform: str | None = None) -> int:
    from geocaches.models import CacheStatus
    qs = IgnoreListEntry.objects.filter(status=CacheStatus.ARCHIVED)
    if source is not None:
        qs = qs.filter(source=source)
    if oc_platform is not None:
        qs = qs.filter(oc_platform=oc_platform)
    deleted, _ = qs.delete()
    return deleted


def _make_oc_client(platform: str):
    from accounts.models import UserAccount
    from geocaches.sync.oc_client import OCClient
    acct = UserAccount.objects.filter(platform=platform).first()
    user_id = acct.user_id if acct else ""
    return OCClient(platform=platform, user_id=user_id)


def sync_oc_ignore_list(platform: str) -> int:
    from geocaches.db_lock import db_write_atomic
    client = _make_oc_client(platform)
    remote = client.get_ignored_caches()
    with db_write_atomic():
        IgnoreListEntry.objects.filter(source=IgnoreSource.OC, oc_platform=platform).delete()
        IgnoreListEntry.objects.bulk_create([
            IgnoreListEntry(
                source=IgnoreSource.OC,
                oc_platform=platform,
                code=e["code"],
                name=e.get("name", ""),
                status=e.get("status", ""),
            )
            for e in remote
            if e.get("code")
        ])
    return len(remote)


def add_oc(oc_code: str, platform: str) -> IgnoreListEntry:
    _make_oc_client(platform).set_ignored(oc_code, True)
    name, status = _local_cache_fields(oc_code)
    entry, _ = IgnoreListEntry.objects.get_or_create(
        source=IgnoreSource.OC,
        oc_platform=platform,
        code=oc_code,
        defaults={"name": name, "status": status},
    )
    return entry


def remove_oc(oc_code: str, platform: str) -> bool:
    _make_oc_client(platform).set_ignored(oc_code, False)
    return remove_remote(IgnoreSource.OC, oc_code, oc_platform=platform)


def sync_gc_ignore_list() -> int:
    from geocaches.db_lock import db_write_atomic
    from gcprivate.gc_client import GCClient
    client = GCClient()
    remote = client.get_ignore_list()
    with db_write_atomic():
        IgnoreListEntry.objects.filter(source=IgnoreSource.GC).delete()
        IgnoreListEntry.objects.bulk_create([
            IgnoreListEntry(
                source=IgnoreSource.GC,
                oc_platform="",
                code=entry["referenceCode"],
                name=entry.get("name", ""),
                status=entry.get("status", ""),
            )
            for entry in remote
            if entry.get("referenceCode")
        ])
    return len(remote)


def add_gc(gc_code: str) -> IgnoreListEntry:
    from gcprivate.gc_client import GCClient
    GCClient().add_to_ignore_list(gc_code)
    name, status = _local_cache_fields(gc_code)
    entry, _ = IgnoreListEntry.objects.get_or_create(
        source=IgnoreSource.GC,
        oc_platform="",
        code=gc_code,
        defaults={"name": name, "status": status},
    )
    return entry


def remove_gc(gc_code: str) -> bool:
    from gcprivate.gc_client import GCClient
    GCClient().remove_from_ignore_list(gc_code)
    return remove_remote(IgnoreSource.GC, gc_code)


def refresh_statuses(entries, *, task_info=None) -> None:
    import logging
    from django.utils import timezone
    from geocaches.db_lock import db_write
    logger = logging.getLogger(__name__)

    rows = list(entries.values_list("pk", "code"))
    gc_rows = [(pk, code) for pk, code in rows if code.upper().startswith("GC")]
    oc_rows = [(pk, code) for pk, code in rows if not code.upper().startswith("GC")]
    completed = 0

    if gc_rows:
        if task_info:
            task_info.phase = f"Refreshing internal GC entries (0/{len(gc_rows)})"
        try:
            from gcprivate.gc_client import GCClient
            from geocaches.sync.base import SyncMode
            client = GCClient()
            codes = [code for _, code in gc_rows]
            results = client.get_caches(codes, SyncMode.LIGHT)
            status_map = {r["gc_code"]: r["fields"] for r in results if "gc_code" in r}
            now = timezone.now()
            for pk, code in gc_rows:
                if task_info and task_info.cancel_event.is_set():
                    return
                flds = status_map.get(code, {})
                with db_write():
                    IgnoreListEntry.objects.filter(pk=pk).update(
                        name=flds.get("name", ""),
                        status=flds.get("status", ""),
                        last_status_refresh=now,
                    )
                completed += 1
                if task_info:
                    task_info.completed = completed
                    task_info.phase = f"Refreshing internal GC entries ({completed}/{len(gc_rows)})"
        except Exception as exc:
            logger.error("GC internal status refresh failed: %s", exc)
            completed += len(gc_rows)
            if task_info:
                task_info.completed = completed

    if oc_rows and not (task_info and task_info.cancel_event.is_set()):
        from geocaches.oc_platforms import group_by_platform
        from geocaches.sync.oc_client import OCClient, _STATUS_MAP
        from accounts.models import UserAccount
        pk_by_code = {code: pk for pk, code in oc_rows}
        platform_codes = group_by_platform([code for _, code in oc_rows])
        for platform, codes in platform_codes.items():
            if task_info and task_info.cancel_event.is_set():
                return
            if task_info:
                task_info.phase = f"Refreshing internal {platform} entries ({len(codes)})"
            try:
                acct = UserAccount.objects.filter(platform=platform).first()
                user_id = acct.user_id if acct else ""
                client = OCClient(platform=platform, user_id=user_id)
                raw = client._get("/services/caches/geocaches", {
                    "cache_codes": "|".join(codes),
                    "fields": "code|name|status",
                })
                now = timezone.now()
                for code in codes:
                    item = (raw.get(code) or {}) if isinstance(raw, dict) else {}
                    with db_write():
                        IgnoreListEntry.objects.filter(pk=pk_by_code[code]).update(
                            name=item.get("name", ""),
                            status=_STATUS_MAP.get(item.get("status", ""), ""),
                            last_status_refresh=now,
                        )
                    completed += 1
                    if task_info:
                        task_info.completed = completed
            except Exception as exc:
                logger.error("OC internal status refresh failed for %s: %s", platform, exc)
                completed += len(codes)
                if task_info:
                    task_info.completed = completed
