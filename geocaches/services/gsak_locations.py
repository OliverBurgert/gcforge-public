"""Import of GSAK's saved locations as ReferencePoint rows.

Reads the three places GSAK keeps coordinates a user named — the Locations
setting in ``gsak.db3``, the FindStatGen home history and each database's
``settings.ini`` centre point — and turns the ones the user picks into
``preferences.models.ReferencePoint`` objects.
"""
import configparser
import re
import sqlite3
from pathlib import Path


def parse_and_import_gsak_locations(gsak_path):
    from geocaches.geo.coords import parse_coordinate
    from preferences.models import ReferencePoint

    GSAK_DIR = Path(gsak_path)
    GSAK_DB = GSAK_DIR / "gsak.db3"

    def _parse_gsak_line(line):
        line = line.strip()
        if not line or line.startswith('#'):
            return None
        comma_idx = line.find(',')
        if comma_idx < 0:
            return None
        name = line[:comma_idx].strip()
        coord_part = line[comma_idx + 1:].strip()
        if not name or not coord_part:
            return None
        m = re.search(r'\s+([EWew]\s*\d)', coord_part)
        if m:
            lat = parse_coordinate(coord_part[:m.start()].strip())
            lon = parse_coordinate(coord_part[m.start():].strip())
            if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
                return name, lat, lon
        tokens = [t for t in re.split(r'[,\s]+', coord_part) if t]
        if len(tokens) == 2:
            lat = parse_coordinate(tokens[0])
            lon = parse_coordinate(tokens[1])
            if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
                return name, lat, lon
        return None

    candidates = []
    errors = []

    if GSAK_DB.exists():
        try:
            conn = sqlite3.connect(str(GSAK_DB))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT Data FROM Settings WHERE Type='LO' AND Description='Locations'"
            ).fetchone()
            conn.close()
            if row and row["Data"]:
                for line in row["Data"].splitlines():
                    parsed = _parse_gsak_line(line)
                    if parsed:
                        candidates.append({"name": parsed[0], "lat": parsed[1], "lon": parsed[2], "source": "GSAK Locations"})
        except Exception as exc:
            errors.append(f"Could not read GSAK Locations: {exc}")
    else:
        errors.append(f"GSAK database not found at {GSAK_DB}")

    FSG_DB = GSAK_DIR / "Macros" / "FoundStatsSQLLite.db3"
    if FSG_DB.exists():
        try:
            conn = sqlite3.connect(str(FSG_DB))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT hdate, hlat, hlon FROM Home WHERE hsettings=1 ORDER BY hdate"
            ).fetchall()
            conn.close()
            for row in rows:
                try:
                    lat = float(row["hlat"])
                    lon = float(row["hlon"])
                except (ValueError, TypeError):
                    continue
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
                hdate = str(row["hdate"])[:10]
                candidates.append({
                    "name": f"Home (from {hdate})",
                    "lat": lat, "lon": lon,
                    "source": "FindStatGen home history",
                    "valid_from": hdate,
                    "is_home": True,
                })
        except Exception as exc:
            errors.append(f"Could not read FindStatGen home history: {exc}")

    data_dir = GSAK_DIR / "data"
    if data_dir.exists():
        for db_dir in sorted(data_dir.iterdir()):
            ini_path = db_dir / "settings.ini"
            if not ini_path.exists():
                continue
            try:
                cfg = configparser.ConfigParser(strict=False)
                cfg.read(str(ini_path), encoding="cp1252")
                lat_str = cfg.get("General", "CentreLat", fallback="").strip()
                lon_str = cfg.get("General", "CentreLon", fallback="").strip()
                name_str = cfg.get("General", "CentreDes", fallback="").strip() or db_dir.name
                if lat_str and lon_str:
                    lat = parse_coordinate(lat_str)
                    lon = parse_coordinate(lon_str)
                    if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
                        candidates.append({
                            "name": name_str,
                            "lat": lat, "lon": lon,
                            "source": f"DB centre: {db_dir.name}",
                        })
            except Exception as exc:
                errors.append(f"Could not read {ini_path}: {exc}")

    seen = set()
    unique_candidates = []
    for c in candidates:
        key = (c["name"].lower(), round(c["lat"], 4), round(c["lon"], 4))
        if key not in seen:
            seen.add(key)
            unique_candidates.append(c)

    existing = list(ReferencePoint.objects.all())
    existing_keys = {(rp.name.lower(), round(rp.latitude, 4), round(rp.longitude, 4)) for rp in existing}
    existing_names = {rp.name.lower() for rp in existing}

    for c in unique_candidates:
        c_key = (c["name"].lower(), round(c["lat"], 4), round(c["lon"], 4))
        c["already_exists"] = c_key in existing_keys or c["name"].lower() in existing_names

    return unique_candidates, errors, existing


def import_gsak_location_candidates(selected_candidates):
    """
    Create ReferencePoint objects for a list of pre-selected location candidates.

    Args:
        selected_candidates: list of candidate dicts as returned by
                             parse_and_import_gsak_locations, already filtered
                             to only those the user chose to import.

    Returns:
        List of names of the created ReferencePoint objects.
    """
    from preferences.models import ReferencePoint

    imported = []
    for c in selected_candidates:
        ReferencePoint.objects.create(
            name=c["name"],
            latitude=c["lat"],
            longitude=c["lon"],
            note=c["source"],
            valid_from=c.get("valid_from"),
            is_home=c.get("is_home", False),
        )
        imported.append(c["name"])
    return imported
