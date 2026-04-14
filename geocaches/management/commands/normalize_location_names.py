"""
Management command: validate and normalize state/county names using Nominatim.

For each distinct state or county value in the database one sample cache is
chosen and its coordinates are reverse-geocoded via Nominatim.  If the name
returned by Nominatim differs from the stored value the command prints the
diff and (unless --dry-run) bulk-replaces every occurrence of the old value
with the correct one.

Requests are minimised: one Nominatim call per distinct
(iso_country_code, field_value) pair — not one per cache.

This is a one-time cleanup tool.  Future imports and the location enrichment
pipeline produce correct values automatically.

Usage:
    uv run python manage.py normalize_location_names [--dry-run] [--field state|county|both]
"""
import time

from django.core.management.base import BaseCommand
from django.db import transaction

from geocaches.countries import is_latin, strip_admin_suffix
from geocaches.enrichment import (
    _extract_address_fields,
    _nominatim_request,
)
from geocaches.models import Geocache


def _fetch_for_coords(lat: float, lon: float, iso_country_code: str) -> dict[str, str]:
    """Return {state, county} from Nominatim with Latin fallback and suffix stripping.

    Returns empty dict on any error or when rate-limited.
    """
    import geocaches.enrichment as _enr
    if _enr._nominatim_blocked:
        return {}
    try:
        data = _nominatim_request(lat, lon)
        addr = data.get("address", {})
        fields = _extract_address_fields(addr)
        state  = fields["state"]
        county = fields["county"]

        if (state and not is_latin(state)) or (county and not is_latin(county)):
            try:
                data_en = _nominatim_request(lat, lon, accept_language="en")
                addr_en = data_en.get("address", {})
                fields_en = _extract_address_fields(addr_en)
                if fields_en["state"]:
                    state = fields_en["state"]
                if fields_en["county"]:
                    county = fields_en["county"]
            except Exception:
                pass

        iso = iso_country_code.upper()
        return {
            "state":  strip_admin_suffix(state,  iso, "state"),
            "county": strip_admin_suffix(county, iso, "county"),
        }
    except Exception:
        return {}


class Command(BaseCommand):
    help = (
        "Validate state/county names against Nominatim and bulk-replace "
        "incorrect values.  Makes one request per distinct value, not per cache."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show differences without writing to the database.",
        )
        parser.add_argument(
            "--field",
            choices=["state", "county", "both"],
            default="both",
            help="Which field(s) to check (default: both).",
        )

    def handle(self, *args, **options):
        dry_run  = options["dry_run"]
        field    = options["field"]
        prefix   = "[DRY RUN] " if dry_run else ""

        check_state  = field in ("state", "both")
        check_county = field in ("county", "both")

        if check_state:
            self._process_field("state", dry_run, prefix)
        if check_county:
            self._process_field("county", dry_run, prefix)

    def _process_field(self, field_name: str, dry_run: bool, prefix: str) -> None:
        import geocaches.enrichment as _enr

        self.stdout.write(f"\n=== Checking {field_name} ===")

        # Distinct (iso_country_code, field_value) pairs, each with one
        # representative cache lat/lon.
        pairs = (
            Geocache.objects
            .exclude(**{field_name: ""})
            .values("iso_country_code", field_name, "latitude", "longitude")
            .order_by("iso_country_code", field_name)
            .distinct()
        )

        # Collapse to one representative lat/lon per (country, value) pair.
        seen: dict[tuple[str, str], tuple[float, float]] = {}
        for row in pairs:
            key = (row["iso_country_code"], row[field_name])
            if key not in seen:
                seen[key] = (row["latitude"], row["longitude"])

        total = len(seen)
        self.stdout.write(f"Distinct (country, {field_name}) pairs: {total}")

        replacements: list[tuple[str, str, str, int]] = []  # (country, old, new, count)
        errors = 0

        for i, ((iso_code, old_value), (lat, lon)) in enumerate(seen.items(), 1):
            if _enr._nominatim_blocked:
                self.stdout.write(
                    self.style.ERROR(
                        "Nominatim rate-limited — stopping early. "
                        f"Processed {i - 1}/{total}."
                    )
                )
                break

            result = _fetch_for_coords(lat, lon, iso_code)
            if not result:
                errors += 1
                self.stdout.write(
                    f"  [{i}/{total}] {iso_code!r} {old_value!r} — lookup failed, skipped"
                )
                continue

            new_value = result[field_name]

            if not new_value:
                self.stdout.write(
                    f"  [{i}/{total}] {iso_code!r} {old_value!r} — Nominatim returned empty, skipped"
                )
                continue

            if new_value == old_value:
                self.stdout.write(f"  [{i}/{total}] {iso_code!r} {old_value!r} — OK")
                continue

            count = Geocache.objects.filter(
                iso_country_code=iso_code, **{field_name: old_value}
            ).count()
            replacements.append((iso_code, old_value, new_value, count))
            self.stdout.write(
                self.style.WARNING(
                    f"  [{i}/{total}] {iso_code!r}  {old_value!r}  →  {new_value!r}  ({count} caches)"
                )
            )

        self.stdout.write(
            f"\n{prefix}Replacements found: {len(replacements)}, lookup errors: {errors}"
        )

        if not replacements or dry_run:
            return

        self.stdout.write(f"Applying {len(replacements)} replacement(s)…")
        with transaction.atomic():
            for iso_code, old_value, new_value, count in replacements:
                updated = Geocache.objects.filter(
                    iso_country_code=iso_code, **{field_name: old_value}
                ).update(**{field_name: new_value})
                self.stdout.write(f"  {iso_code!r}  {old_value!r}  →  {new_value!r}  ({updated} rows)")
        self.stdout.write(self.style.SUCCESS("Done."))
