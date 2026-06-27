"""Bulk-logging service.

The bulk-logging view currently submits one field note at a time via the
"Submit now" action. This service owns the non-HTTP steps: calling
submit_log, classifying the result, and persisting the resulting
state back onto the Note row.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone as _tz
from typing import Optional

_bulk_logging_logger = logging.getLogger("geocaches.bulk_logging")


@dataclass
class BulkLogResult:
    success: bool
    submit_error: str = ""
    image_errors_only: bool = False

    @property
    def had_image_errors(self) -> bool:
        return self.image_errors_only or "Images:" in self.submit_error


def submit_field_note(
    note,
    *,
    log_type: str,
    logged_at_utc: datetime,
    text: str,
    sequence_number: Optional[int],
    platforms: list,
    passphrase: str,
    images: list,
    give_favourite: bool,
    recommend: bool,
) -> BulkLogResult:
    """Submit a single field note as a log and update the Note row.

    Mirrors the old inline logic in bulk_logging's "submit_now" branch:
    calls sync.log_submit.submit_log, then writes the outcome back to
    the note's bookkeeping fields.
    """
    from geocaches.sync.log_submit import submit_log

    result = submit_log(
        note.geocache, log_type, logged_at_utc, text, platforms,
        sequence_number=sequence_number, passphrase=passphrase,
        images=images,
        give_favourite=give_favourite, recommend=recommend,
    )

    errors = []
    if result.gc_success is False:
        errors.append(f"GC: {result.gc_error}")
    if result.oc_success is False:
        errors.append(f"OC: {result.oc_error}")
    errors.extend(result.image_errors)

    hard_failure = result.gc_success is False or result.oc_success is False

    if errors and hard_failure:
        submit_error = "; ".join(
            e for e in errors if not e.startswith("GC image") and not e.startswith("OC image")
        )
        if result.image_errors:
            submit_error = (submit_error + " | Images: " + "; ".join(result.image_errors)).strip(" |")
        note.submit_error = submit_error
        note.save(update_fields=["submit_error"])
        _bulk_logging_logger.warning("Bulk log submit error for note %s: %s", note.pk, submit_error)
        return BulkLogResult(success=False, submit_error=submit_error)

    note.submitted_at = datetime.now(_tz.utc)
    note.bulk_draft = False
    note.submit_error = " | ".join(result.image_errors) if result.image_errors else ""
    note.log_type = log_type
    note.logged_at = logged_at_utc
    note.sequence_number = sequence_number
    note.draft_body = ""
    note.save(update_fields=[
        "submitted_at", "bulk_draft", "submit_error",
        "log_type", "logged_at", "sequence_number", "draft_body",
    ])
    return BulkLogResult(
        success=True,
        submit_error=note.submit_error,
        image_errors_only=bool(result.image_errors),
    )
