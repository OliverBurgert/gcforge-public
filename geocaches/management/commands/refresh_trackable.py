"""Refresh one or more trackables from the GC API.

Wraps ``geocaches.services.trackable_sync`` so the sync layer can be exercised
without UI. Used during Phase 2 dev to validate the upsert + denorm path.

Examples:
    uv run python manage.py refresh_trackable TB7WZ44
    uv run python manage.py refresh_trackable TB7WZ44 TBABCDE --full
    uv run python manage.py refresh_trackable --inventory --full
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from geocaches.services.trackable_sync import (
    sync_trackable,
    sync_trackable_logs,
)


class Command(BaseCommand):
    help = "Sync a trackable's metadata + log history into the local DB"

    def add_arguments(self, parser):
        parser.add_argument(
            "refs",
            nargs="*",
            metavar="TB_CODE",
            help="One or more TB reference codes (TB#######)",
        )
        parser.add_argument(
            "--inventory",
            action="store_true",
            help="Refresh every TB currently in the authenticated user's inventory",
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help="Walk the entire log history (default: incremental)",
        )
        parser.add_argument(
            "--no-logs",
            action="store_true",
            help="Sync only the trackable metadata, skip log history",
        )

    def handle(self, *args, **options):
        from geocaches.feature_flags import gc_api_available
        if not gc_api_available():
            raise CommandError("GC integration is not available in this build.")
        from gcprivate.trackable_client import TrackableClient

        refs: list[str] = [r.strip().upper() for r in options["refs"] if r.strip()]
        client = TrackableClient()

        if options["inventory"]:
            try:
                inv = client.get_my_inventory()
            except Exception as exc:  # noqa: BLE001
                raise CommandError(f"Inventory fetch failed: {exc}") from exc
            refs += [
                (item.get("reference_code") or "").upper()
                for item in inv
                if item.get("reference_code")
            ]

        refs = sorted(set(r for r in refs if r))
        if not refs:
            raise CommandError("No trackable refs to sync. Pass codes or use --inventory.")

        full = options["full"]
        skip_logs = options["no_logs"]
        for ref in refs:
            self.stdout.write(f"Syncing {ref} …")
            try:
                tb = sync_trackable(ref, client=client)
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(self.style.ERROR(f"  metadata failed: {exc}"))
                continue
            self.stdout.write(
                f"  metadata: name={tb.name!r}, kind={tb.kind}, "
                f"holder_state={tb.holder_state}, "
                f"current_geocache_code={tb.current_geocache_code or '—'}"
            )
            if skip_logs:
                continue
            try:
                new_logs = sync_trackable_logs(ref, full=full, client=client)
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(self.style.ERROR(f"  logs failed: {exc}"))
                continue
            tb.refresh_from_db()
            self.stdout.write(
                f"  logs: +{new_logs} new, last_log_date={tb.last_log_date}, "
                f"total_visits={tb.total_visits}"
            )
        self.stdout.write(self.style.SUCCESS(f"Done — {len(refs)} trackable(s)"))
