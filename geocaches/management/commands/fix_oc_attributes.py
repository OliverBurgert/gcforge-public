"""
Management command: fix_oc_attributes

Repairs OC attribute rows that accumulated from earlier import bugs:

  1. **A-code names** — OKAPI sync used to store the raw A-code ("A1", "A62")
     as the attribute name.  Resolve those to full display names via the
     OKAPI attribute dictionary.
  2. **OC attrs stranded in the GC bucket** — a legacy fix renamed some OC
     attributes to "OC - …" but left them at source="gc", so they show up
     under the GC header in the filter dialog.  Migrate each to source="oc"
     (re-pointing every cache link to the canonical OC row, then deleting
     the GC duplicate).

Run once after upgrading; the OKAPI sync now resolves names at fetch time
so new rows arrive correct.
"""

import re

from django.core.management.base import BaseCommand
from django.db import transaction

_ACODE_RE = re.compile(r"^A\d+$")
_OC_PREFIX = "OC - "


class Command(BaseCommand):
    help = "Resolve OC A-code attribute names and move OC attrs out of the GC bucket."

    def add_arguments(self, parser):
        parser.add_argument(
            "--platform", default="oc_de",
            help="OC platform to fetch the attribute dictionary from (default: oc_de).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print what would change without touching the database.",
        )

    def handle(self, *args, **options):
        from geocaches.models import Attribute

        platform = options["platform"]
        dry = options["dry_run"]
        if dry:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be saved.\n"))

        names = self._fetch_names(platform)
        if names:
            self.stdout.write(f"Fetched {len(names)} attribute name(s) from {platform}.")
        else:
            self.stdout.write(self.style.WARNING(
                "No attribute dictionary available - A-code rows can't be renamed; "
                "GC->OC migration will use the stripped 'OC - ' name as fallback.\n"
            ))

        with transaction.atomic():
            self._rename_acodes(Attribute, names, dry)
            self._migrate_gc_oc_rows(Attribute, names, dry)

    # ------------------------------------------------------------------ #

    def _fetch_names(self, platform):
        try:
            from geocaches.sync.oc_client import OCClient
            return OCClient(platform=platform).get_attribute_names()
        except Exception as exc:  # pragma: no cover - network/credentials
            self.stdout.write(self.style.WARNING(f"Could not fetch names: {exc}"))
            return {}

    def _rename_acodes(self, Attribute, names, dry):
        renamed = 0
        for a in Attribute.objects.filter(source="oc"):
            if _ACODE_RE.match(a.name or "") and a.attribute_id in names:
                new = names[a.attribute_id]
                self.stdout.write(f'  rename oc/{a.attribute_id}: "{a.name}" -> "{new}"')
                if not dry:
                    a.name = new
                    a.save(update_fields=["name"])
                renamed += 1
        self.stdout.write(self.style.SUCCESS(
            f"{'Would rename' if dry else 'Renamed'} {renamed} A-code attribute(s)."
        ))

    def _migrate_gc_oc_rows(self, Attribute, names, dry):
        stranded = Attribute.objects.filter(source="gc", name__startswith=_OC_PREFIX)
        migrated = 0
        for gc_attr in stranded:
            oc_name = names.get(gc_attr.attribute_id) or gc_attr.name[len(_OC_PREFIX):]
            n_caches = gc_attr.geocaches.count()
            self.stdout.write(
                f'  migrate gc/{gc_attr.attribute_id} (pos={gc_attr.is_positive}) '
                f'"{gc_attr.name}" -> oc/"{oc_name}"  ({n_caches} cache link(s))'
            )
            if dry:
                migrated += 1
                continue

            oc_attr, _ = Attribute.objects.get_or_create(
                source="oc",
                attribute_id=gc_attr.attribute_id,
                is_positive=gc_attr.is_positive,
                defaults={"name": oc_name},
            )
            # Re-point every cache link, then drop the GC duplicate.
            for cache in gc_attr.geocaches.all():
                cache.attributes.add(oc_attr)
                cache.attributes.remove(gc_attr)
            gc_attr.delete()
            migrated += 1

        self.stdout.write(self.style.SUCCESS(
            f"{'Would migrate' if dry else 'Migrated'} {migrated} stranded GC->OC attribute(s)."
        ))
