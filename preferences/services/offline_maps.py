import gzip
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timezone

import requests
from pmtiles.writer import write as pmtiles_write
from pmtiles.tile import (
    deserialize_header,
    deserialize_directory,
    zxy_to_tileid,
    find_tile,
    Compression,
    TileType,
)

PROTOMAPS_METADATA_URL = 'https://build-metadata.protomaps.dev/builds.json'
PROTOMAPS_DOWNLOAD_BASE = 'https://build.protomaps.com/'

# Tile ranges within this many bytes of each other are merged into one request.
# Larger value = fewer requests but more wasted bandwidth.
_COALESCE_GAP = 64 * 1024


def get_latest_protomaps_url() -> str | None:
    """Return the download URL of the most recent Protomaps planet build, or None on failure."""
    try:
        r = requests.get(PROTOMAPS_METADATA_URL, timeout=5)
        r.raise_for_status()
        builds = r.json()
        if builds:
            return PROTOMAPS_DOWNLOAD_BASE + builds[0]['key']
    except Exception:
        pass
    return None


def _lon_to_tile_x(lon, zoom):
    return int((lon + 180) / 360 * 2**zoom)


def _lat_to_tile_y(lat, zoom):
    lat_r = math.radians(lat)
    return int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * 2**zoom)


def estimate_tile_count(bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat, min_zoom, max_zoom) -> dict:
    """Return estimated tile count and byte size for the given bbox and zoom range."""
    tile_count = 0
    for z in range(min_zoom, max_zoom + 1):
        x_min = _lon_to_tile_x(bbox_min_lon, z)
        x_max = _lon_to_tile_x(bbox_max_lon, z)
        y_min = _lat_to_tile_y(bbox_max_lat, z)
        y_max = _lat_to_tile_y(bbox_min_lat, z)
        tile_count += (x_max - x_min + 1) * (y_max - y_min + 1)
    estimated_bytes = tile_count * 8192
    return {"tile_count": tile_count, "estimated_bytes": estimated_bytes}


class _HttpSource:
    """HTTP range-request source."""

    def __init__(self, url):
        self.url = url
        self._session = requests.Session()

    def get_bytes(self, offset, length):
        headers = {"Range": f"bytes={offset}-{offset + length - 1}"}
        r = self._session.get(self.url, headers=headers, timeout=30)
        r.raise_for_status()
        return r.content


class _CachingSource:
    """Wraps _HttpSource and caches every response by (offset, length).

    Used during directory traversal so the root directory and each leaf
    directory are fetched at most once regardless of how many tiles reference
    them.  Tile data is never passed through this source — only directory
    pages (header + root dir + leaf dirs), which are small and reusable.
    """

    def __init__(self, url):
        self._inner = _HttpSource(url)
        self._cache: dict[tuple[int, int], bytes] = {}

    def get_bytes(self, offset: int, length: int) -> bytes:
        key = (offset, length)
        if key not in self._cache:
            self._cache[key] = self._inner.get_bytes(offset, length)
        return self._cache[key]


def _enumerate_bbox_tiles(bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat, min_zoom, max_zoom):
    """Yield (z, x, y) for every tile slot that intersects the bbox."""
    for z in range(min_zoom, max_zoom + 1):
        x_min = _lon_to_tile_x(bbox_min_lon, z)
        x_max = _lon_to_tile_x(bbox_max_lon, z)
        y_min = _lat_to_tile_y(bbox_max_lat, z)
        y_max = _lat_to_tile_y(bbox_min_lat, z)
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                yield z, x, y


def _resolve_tile_ranges(caching_source, header, slots) -> dict[int, tuple[int, int]]:
    """Walk PMTiles directories to find the file offset for each tile.

    Returns {tile_id: (absolute_byte_offset, length)}.
    Each directory page is fetched AND deserialized only once — both the raw
    bytes and the parsed entry list are cached.
    """
    parsed_dirs: dict[tuple[int, int], list] = {}

    def get_dir(offset, length):
        key = (offset, length)
        if key not in parsed_dirs:
            parsed_dirs[key] = deserialize_directory(caching_source.get_bytes(offset, length))
        return parsed_dirs[key]

    result: dict[int, tuple[int, int]] = {}
    for z, x, y in slots:
        tile_id = zxy_to_tileid(z, x, y)
        dir_offset = header["root_offset"]
        dir_length = header["root_length"]
        for _ in range(4):  # PMTiles spec: max 4 directory levels
            directory = get_dir(dir_offset, dir_length)
            entry = find_tile(directory, tile_id)
            if entry is None:
                break
            if entry.run_length == 0:
                dir_offset = header["leaf_directory_offset"] + entry.offset
                dir_length = entry.length
            else:
                result[tile_id] = (header["tile_data_offset"] + entry.offset, entry.length)
                break
    return result


def _fetch_ranges_parallel(
    source_url: str,
    tile_ranges: dict[int, tuple[int, int]],
    max_gap: int = _COALESCE_GAP,
    max_workers: int = 8,
    progress_callback=None,
) -> dict[int, bytes]:
    """Coalesce adjacent tile byte ranges and download in parallel.

    Tiles whose byte ranges are within max_gap of each other in the file are
    merged into a single HTTP request.  This turns hundreds of small tile
    requests into a handful of larger ones.
    """
    if not tile_ranges:
        return {}

    sorted_items = sorted(tile_ranges.items(), key=lambda kv: kv[1][0])

    # Build coalesced chunks: each is [chunk_start, chunk_end, {tile_id: (offset, length)}]
    chunks: list[list] = []
    for tile_id, (offset, length) in sorted_items:
        end = offset + length
        if chunks and offset <= chunks[-1][1] + max_gap:
            chunks[-1][2][tile_id] = (offset, length)
            if end > chunks[-1][1]:
                chunks[-1][1] = end
        else:
            chunks.append([offset, end, {tile_id: (offset, length)}])

    total_chunks = len(chunks)
    completed_chunks = [0]
    last_pct = [-1]

    import threading as _threading
    _local = _threading.local()

    def _get_session():
        if not hasattr(_local, 'session'):
            _local.session = requests.Session()
        return _local.session

    def fetch_chunk(chunk):
        chunk_start, chunk_end, tiles = chunk
        r = _get_session().get(
            source_url,
            headers={"Range": f"bytes={chunk_start}-{chunk_end - 1}"},
            timeout=120,
        )
        r.raise_for_status()
        data = r.content
        out = {}
        for tid, (tile_offset, tile_length) in tiles.items():
            rel = tile_offset - chunk_start
            out[tid] = data[rel: rel + tile_length]
        return out

    tile_data: dict[int, bytes] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_chunk, c) for c in chunks]
        for future in as_completed(futures):
            tile_data.update(future.result())
            completed_chunks[0] += 1
            if progress_callback and total_chunks > 0:
                pct = int(completed_chunks[0] / total_chunks * 100)
                if pct != last_pct[0]:
                    last_pct[0] = pct
                    progress_callback(pct)

    return tile_data


def _extract_pmtiles(source_url, dest_path, bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat,
                     min_zoom, max_zoom, progress_callback):
    # Phase 0 — header + metadata (tiny, sequential).
    src = _CachingSource(source_url)
    header = deserialize_header(src.get_bytes(0, 127))
    try:
        raw_meta = src.get_bytes(header["metadata_offset"], header["metadata_length"])
        if header.get("internal_compression") == Compression.GZIP:
            raw_meta = gzip.decompress(raw_meta)
        import json
        metadata = json.loads(raw_meta)
    except Exception:
        metadata = {}

    slots = list(_enumerate_bbox_tiles(
        bbox_min_lon, bbox_min_lat, bbox_max_lon, bbox_max_lat, min_zoom, max_zoom
    ))

    if progress_callback:
        progress_callback(2)

    # Phase 1 — directory traversal.
    # All directory pages cached in src; each unique page fetched once.
    tile_ranges = _resolve_tile_ranges(src, header, slots)

    if progress_callback:
        progress_callback(10)

    # Phase 2 — parallel coalesced data fetch.
    def _data_progress(pct):
        if progress_callback:
            progress_callback(10 + int(pct * 0.85))

    tile_data = _fetch_ranges_parallel(
        source_url, tile_ranges, progress_callback=_data_progress
    )

    if progress_callback:
        progress_callback(96)

    # Phase 3 — write output (tiles must be in tile_id order for valid PMTiles).
    with pmtiles_write(dest_path) as writer:
        for tile_id, data in sorted(tile_data.items()):
            writer.write_tile(tile_id, data)

        output_header = {
            "tile_type": header.get("tile_type", TileType.MVT),
            "tile_compression": header.get("tile_compression", Compression.GZIP),
            "min_lon_e7": int(bbox_min_lon * 10_000_000),
            "min_lat_e7": int(bbox_min_lat * 10_000_000),
            "max_lon_e7": int(bbox_max_lon * 10_000_000),
            "max_lat_e7": int(bbox_max_lat * 10_000_000),
            "center_zoom": (min_zoom + max_zoom) // 2,
            "center_lon_e7": int((bbox_min_lon + bbox_max_lon) / 2 * 10_000_000),
            "center_lat_e7": int((bbox_min_lat + bbox_max_lat) / 2 * 10_000_000),
        }
        writer.finalize(output_header, metadata)

    if progress_callback:
        progress_callback(100)


def _get_offline_maps_dir() -> Path:
    from django.conf import settings as django_settings
    from preferences.models import UserPreference
    offline_maps_dir = UserPreference.get("offline_maps_dir", "")
    if offline_maps_dir:
        return Path(offline_maps_dir)
    db_path = Path(django_settings.DATABASES["default"]["NAME"])
    return db_path.parent / "offline_maps"


def download_area(area_id, progress_callback=None) -> None:
    """Download and extract a PMTiles subset for the given OfflineMapArea id."""
    from preferences.models import OfflineMapArea

    area = OfflineMapArea.objects.get(pk=area_id)
    area.status = "downloading"
    area.progress = 0
    area.error_message = ""
    area.save(update_fields=["status", "progress", "error_message"])

    maps_dir = _get_offline_maps_dir()
    maps_dir.mkdir(parents=True, exist_ok=True)

    area.filename = f"area_{area.id}.pmtiles"
    area.save(update_fields=["filename"])

    dest_path = str(maps_dir / area.filename)

    def _wrapped_progress(pct):
        area.progress = pct
        area.save(update_fields=["progress"])
        if progress_callback:
            progress_callback(pct)

    try:
        _extract_pmtiles(
            source_url=area.source_url,
            dest_path=dest_path,
            bbox_min_lon=area.bbox_min_lon,
            bbox_min_lat=area.bbox_min_lat,
            bbox_max_lon=area.bbox_max_lon,
            bbox_max_lat=area.bbox_max_lat,
            min_zoom=area.min_zoom,
            max_zoom=area.max_zoom,
            progress_callback=_wrapped_progress,
        )
        area.status = "ready"
        area.progress = 100
        area.file_size_bytes = os.path.getsize(dest_path)
        area.last_downloaded_at = datetime.now(timezone.utc)
        area.save(update_fields=["status", "progress", "file_size_bytes", "last_downloaded_at"])
    except Exception as e:
        area.status = "error"
        area.error_message = str(e)
        area.save(update_fields=["status", "error_message"])
        raise
