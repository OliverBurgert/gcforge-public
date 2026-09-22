"""
Management command: split_gsak_notes

Splits fused GSAK UserNote data stored in Note.body into separate records:

  - Text before the '$~' separator  → Note (note_type="note")
  - Each '--Field Note Start/End--' block after '$~' → Note (note_type="field_note")

Rules:
  - Empty user-note text → not created
  - Field note with no text AND no date → not created
  - Field note with a date but empty text → created with empty body
  - Notes that were already split (note_type != "note" or no '$~' in body) → skipped
  - Existing notes whose body contains no '$~' and no Field Note markers → left untouched

Usage:
  uv run python manage.py split_gsak_notes
  uv run python manage.py split_gsak_notes --dry-run
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from geocaches.importers.gsak import _GSAK_NOTE_SPLIT as SPLIT_MARKER, split_gsak_note


def _split_note(note) -> list[dict]:
    """Wrap split_gsak_note for an existing Note object."""
    return split_gsak_note(note.body or "")


class Command(BaseCommand):
    help = "Split fused GSAK UserNote data into separate note and field_note records."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without writing to the database.",
        )

    def handle(self, *args, **options):
        from geocaches.models import Note

        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes will be written.\n"))

        candidates = Note.objects.filter(
            note_type="note",
            body__contains=SPLIT_MARKER,
        ).select_related("geocache")

        total = candidates.count()
        self.stdout.write(f"Found {total} note(s) with '$~' separator to process.\n")

        split_count = 0
        created_count = 0
        skipped_count = 0

        for note in candidates:
            records = _split_note(note)
            if not records:
                skipped_count += 1
                continue

            cache_label = note.geocache.gc_code or note.geocache.oc_code or f"id={note.geocache_id}"
            self.stdout.write(
                f"  {cache_label}: 1 note -> {len(records)} record(s)"
            )
            for r in records:
                label = r["note_type"]
                dt_str = f" @ {r['logged_at']}" if r["logged_at"] else ""
                text_preview = (r["body"][:60] + "…") if len(r["body"]) > 60 else r["body"] or "(empty)"
                self.stdout.write(f"    [{label}{dt_str}] {text_preview!r}")

            if not dry_run:
                with transaction.atomic():
                    note.delete()
                    for r in records:
                        Note.objects.create(geocache_id=note.geocache_id, **r)
                created_count += len(records)
                split_count += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"\nDry run complete. {total} note(s) would be processed."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone. {split_count} note(s) split into {created_count} record(s). "
                f"{skipped_count} skipped (no split needed)."
            ))
            # Nullify created_at / updated_at on all notes — all existing notes
            # are from GSAK imports and have no meaningful timestamp.
            nullified = Note.objects.filter(
                created_at__isnull=False
            ).update(created_at=None, updated_at=None)
            self.stdout.write(f"Nullified timestamps on {nullified} note(s).")
