"""
Management command: reset_oc_attributes

Deletes every ``source="oc"`` Attribute row (and, via the M2M cascade, all
its cache links).  Used to wipe the historical OC-attribute mess caused by
two incompatible numbering schemes (GPX Groundspeak-equivalent IDs vs OKAPI
A-codes that overlapped in the 1-89 range) and rebuild cleanly.

After running this, re-sync OC caches with a **FULL** update — the OKAPI
sync now resolves A-codes to display names, so rebuilt rows are correct and
icon lookups (keyed by A-code) line up.

Caveat: any OC cache that is *not* re-synced via OKAPI (e.g. GPX-only or
archived/unreachable) will have no attributes until it is.
"""

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Delete all source='oc' attribute rows so a FULL OKAPI re-sync can rebuild them cleanly."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be deleted without touching the database.",
        )

    def handle(self, *args, **options):
        from geocaches.models import Attribute

        dry = options["dry_run"]
        qs = Attribute.objects.filter(source="oc")
        row_count = qs.count()

        # Total cache links across all OC attribute rows (through-table rows).
        link_count = sum(a.geocaches.count() for a in qs)

        self.stdout.write(
            f"source='oc' attribute rows: {row_count}  (cache links: {link_count})"
        )

        if row_count == 0:
            self.stdout.write("Nothing to delete.")
            return

        if dry:
            self.stdout.write(self.style.WARNING(
                "DRY RUN - no changes made. Re-run without --dry-run to delete, "
                "then FULL-update OC caches to rebuild attributes."
            ))
            return

        with transaction.atomic():
            deleted, _ = qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f"Deleted {row_count} OC attribute row(s) and {link_count} cache link(s). "
            "Now run a FULL update on your OC caches to rebuild them."
        ))
