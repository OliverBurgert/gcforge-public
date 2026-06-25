"""
Management command: fix_oc_codes

Finds caches where an OC* code is stored in the gc_code field (legacy import
artefact), then:
  1. Moves the code to oc_code, clears gc_code.
  2. Sets primary_source based on the OC network prefix.
  3. Updates source on all logs for those caches.
  4. Fixes attribute names that were imported as raw IDs ("Attribute #NNN").
"""

from django.core.management.base import BaseCommand
from django.db import transaction

# OC code prefix → primary_source value + log source
OC_PREFIX_MAP = {
    "OC": "oc_de",
    "OP": "oc_pl",
    "OU": "oc_us",
    "OB": "oc_nl",
    "OK": "oc_uk",
    "OR": "oc_ro",
}

# Attribute IDs (stored as "Attribute #NNN") → correct display names
ATTRIBUTE_FIX_MAP = {
    16:  "Cacti nearby",
    106: "OC - Only loggable at Opencaching",
    130: "OC - Point of interest",
    134: "OC - In the water",
    135: "OC - No GPS required",
    147: "OC - Compass",
}


def _oc_prefix(code: str) -> str | None:
    """Return the two-letter OC prefix if this looks like an OC code."""
    prefix = code[:2].upper()
    return prefix if prefix in OC_PREFIX_MAP else None


class Command(BaseCommand):
    help = "Move OC* codes from gc_code to oc_code and fix related data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be changed without touching the database.",
        )

    def handle(self, *args, **options):
        from geocaches.models import Attribute, Geocache, Log

        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be saved.\n"))

        # ------------------------------------------------------------------ #
        # 1. Find caches with OC* codes in gc_code                            #
        # ------------------------------------------------------------------ #
        candidates = Geocache.objects.exclude(gc_code="").filter(oc_code="")
        to_fix = []
        for cache in candidates:
            prefix = _oc_prefix(cache.gc_code)
            if prefix:
                to_fix.append((cache, prefix))

        if not to_fix:
            self.stdout.write("No caches with OC* codes in gc_code — nothing to do.")
            return

        self.stdout.write(f"Found {len(to_fix)} cache(s) to fix:")
        for cache, prefix in to_fix:
            source = OC_PREFIX_MAP[prefix]
            log_count = Log.objects.filter(geocache=cache).count()
            self.stdout.write(
                f"  {cache.gc_code} -> oc_code={cache.gc_code}  "
                f"primary_source={source}  logs={log_count}"
            )

        if dry_run:
            self._fix_attributes(Attribute, dry_run=True)
            return

        # ------------------------------------------------------------------ #
        # 2. Apply fixes in a single transaction                              #
        # ------------------------------------------------------------------ #
        with transaction.atomic():
            for cache, prefix in to_fix:
                old_code = cache.gc_code
                source = OC_PREFIX_MAP[prefix]

                Log.objects.filter(geocache=cache).update(source=source)

                cache.oc_code = old_code
                cache.gc_code = ""
                cache.primary_source = source
                cache.save(update_fields=["gc_code", "oc_code", "primary_source"])

            self.stdout.write(
                self.style.SUCCESS(f"Updated {len(to_fix)} cache(s) and their logs.")
            )

            self._fix_attributes(Attribute, dry_run=False)

    def _fix_attributes(self, Attribute, *, dry_run: bool):
        fixed = 0
        for attr_id, correct_name in ATTRIBUTE_FIX_MAP.items():
            raw_name = f"Attribute #{attr_id}"
            qs = Attribute.objects.filter(name=raw_name)
            count = qs.count()
            if count:
                self.stdout.write(
                    f"  {'[dry-run] ' if dry_run else ''}Rename {count}x "
                    f'"{raw_name}" -> "{correct_name}"'
                )
                if not dry_run:
                    qs.update(name=correct_name)
                fixed += count

        if fixed:
            action = "Would rename" if dry_run else "Renamed"
            self.stdout.write(
                self.style.SUCCESS(f"{action} {fixed} attribute row(s).")
            )
        else:
            self.stdout.write("No attribute names needed fixing.")
