"""
Management command: backfill iso_country_code from existing country name values.

Usage:
    uv run python manage.py normalize_country_codes [--dry-run]

For each Geocache where iso_country_code is blank but country is set, tries to
resolve the country name to an ISO 3166-1 alpha-2 code via pycountry.
"""
from django.core.management.base import BaseCommand

from geocaches.countries import name_to_iso
from geocaches.models import Geocache


class Command(BaseCommand):
    help = "Backfill iso_country_code from existing country name values via pycountry."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without writing to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        qs = Geocache.objects.filter(iso_country_code="").exclude(country="")
        total = qs.count()
        self.stdout.write(f"Caches with country set but no iso_country_code: {total}")

        updated = 0
        unresolved = 0
        unresolved_names: set[str] = set()

        for cache in qs.iterator(chunk_size=500):
            iso = name_to_iso(cache.country)
            if iso:
                updated += 1
                if not dry_run:
                    cache.iso_country_code = iso
                    cache.save(update_fields=["iso_country_code"])
            else:
                unresolved += 1
                unresolved_names.add(cache.country)

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(f"{prefix}Resolved: {updated}, Unresolved: {unresolved}")
        if unresolved_names:
            self.stdout.write("Unresolved country names:")
            for name in sorted(unresolved_names):
                self.stdout.write(f"  {name!r}")
