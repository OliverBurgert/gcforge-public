"""
Management command: fix_lab_codes

Retroactively applies canonical LC code logic to all Adventure Lab caches.

For each Adventure with an adventure_guid, the canonical code is computed via
uuid_to_lc_code().  If the current code differs, the Adventure.code, the
parent Geocache.gc_code, and all stage Geocache.gc_code values are updated.

Adventures without a GUID are flagged for manual inspection since no canonical
code can be derived.
"""

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Fix Adventure Lab codes to use canonical UUID-derived LC codes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be changed without touching the database.",
        )

    def handle(self, *args, **options):
        from geocaches.lc_code import uuid_to_lc_code
        from geocaches.models import Adventure, Geocache

        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be saved.\n"))

        adventures = Adventure.objects.all().order_by("code")
        fixed = 0
        skipped = 0
        warnings = []

        for adv in adventures:
            if not adv.adventure_guid:
                warnings.append(
                    f"  {adv.code} — no adventure_guid, cannot derive canonical code"
                )
                skipped += 1
                continue

            canonical = uuid_to_lc_code(adv.adventure_guid)
            if adv.code == canonical:
                continue  # already correct

            old_code = adv.code

            # Check for conflicts: does a different adventure already own the canonical code?
            conflict = Adventure.objects.filter(code=canonical).exclude(pk=adv.pk).first()
            if conflict:
                warnings.append(
                    f"  {old_code} -> {canonical}  CONFLICT with existing "
                    f"adventure pk={conflict.pk} ({conflict.code}), skipped"
                )
                skipped += 1
                continue

            # Check for al_code conflict on the parent Geocache
            parent_conflict = (
                Geocache.objects.filter(al_code=canonical)
                .exclude(adventure=adv)
                .first()
            )
            if parent_conflict:
                warnings.append(
                    f"  {old_code} -> {canonical}  CONFLICT: al_code {canonical} "
                    f"already used by {parent_conflict.al_code} (pk={parent_conflict.pk}), skipped"
                )
                skipped += 1
                continue

            # Find parent and stages
            parent = Geocache.objects.filter(adventure=adv, al_detail__isnull=True).first()
            stages = list(
                Geocache.objects.filter(adventure=adv, al_detail__isnull=False)
                .select_related("al_detail")
                .order_by("al_detail__stage_number")
            )

            # Check for stage gc_code conflicts
            stage_conflict = False
            stage_renames = []
            for stage in stages:
                sn = stage.al_detail.stage_number if stage.al_detail else None
                new_code = f"{canonical}-{sn}"
                existing = (
                    Geocache.objects.filter(al_code=new_code)
                    .exclude(pk=stage.pk)
                    .first()
                )
                if existing:
                    warnings.append(
                        f"  {old_code} -> {canonical}  CONFLICT: stage al_code {new_code} "
                        f"already used by pk={existing.pk}, skipped"
                    )
                    stage_conflict = True
                    break
                stage_renames.append((stage, new_code))

            if stage_conflict:
                skipped += 1
                continue

            # Report
            self.stdout.write(f"  {old_code} -> {canonical}")
            if parent:
                self.stdout.write(f"    parent: {parent.gc_code} -> {canonical}")
            else:
                warnings.append(
                    f"  {old_code} -> {canonical}  WARNING: no parent Geocache found"
                )
            for stage, new_code in stage_renames:
                self.stdout.write(f"    stage:  {stage.al_code} -> {new_code}")

            if not dry_run:
                with transaction.atomic():
                    # Update stages first (avoid unique constraint issues with parent)
                    for stage, new_code in stage_renames:
                        stage.al_code = new_code
                        stage.save(update_fields=["al_code"])

                    if parent:
                        parent.al_code = canonical
                        parent.save(update_fields=["al_code"])

                    adv.code = canonical
                    adv.save(update_fields=["code"])

            fixed += 1

        # Summary
        self.stdout.write("")
        if fixed:
            action = "Would fix" if dry_run else "Fixed"
            self.stdout.write(self.style.SUCCESS(f"{action} {fixed} adventure(s)."))
        else:
            self.stdout.write("No adventures needed fixing.")

        if skipped:
            self.stdout.write(self.style.WARNING(f"\n{skipped} adventure(s) need manual inspection:"))
            for w in warnings:
                self.stdout.write(self.style.WARNING(w))
