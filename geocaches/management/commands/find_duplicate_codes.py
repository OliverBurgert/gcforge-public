"""
Management command: find_duplicate_codes

Lists every external cache code (gc_code / oc_code / al_code) that is used by
more than one cache outside the Trash.  Read-only — it never changes anything.

Migration 0044 adds the unique constraints that forbid such duplicates and
refuses to run while any exist; this command prints the same list so they can
be resolved first.
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "List external cache codes shared by more than one cache outside the Trash."

    def handle(self, *args, **options):
        from geocaches.services.codes import find_duplicate_live_codes, format_duplicate_report

        duplicates = find_duplicate_live_codes()
        if not duplicates:
            self.stdout.write(self.style.SUCCESS("No duplicate cache codes — nothing to do."))
            return

        total = sum(len(rows) for rows in duplicates.values())
        self.stdout.write(self.style.WARNING(
            f"{total} code(s) are used by more than one cache outside the Trash:"
        ))
        self.stdout.write(format_duplicate_report(duplicates))
        self.stdout.write(
            "\nResolve each one, then run the migrations again:\n"
            "  * Tools -> Find duplicates merges a GC/OC pair into one record.\n"
            "  * Or delete the surplus record in the cache list — moving it to the\n"
            "    Trash is enough, trashed caches may keep a code a live cache uses."
        )
