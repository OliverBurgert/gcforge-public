from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from geocaches.importers.gsak import import_gsak_db

GSAK_DATA_DIR = Path.home() / "AppData/Roaming/gsak/data"


class Command(BaseCommand):
    help = "Import a GSAK sqlite.db3 database file into GCForge."

    def add_arguments(self, parser):
        parser.add_argument(
            "db_path",
            nargs="?",
            default=None,
            help="Path to a GSAK sqlite.db3 file. Omit to list available GSAK databases.",
        )
        parser.add_argument(
            "--tag",
            dest="tag_names",
            action="append",
            default=[],
            metavar="TAG",
            help="Tag to apply to all imported caches (repeatable: --tag A --tag B). "
                 "Defaults to the GSAK database directory name if omitted.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            dest="list_dbs",
            help="List available GSAK databases and exit.",
        )

    def handle(self, *args, **options):
        if options["list_dbs"] or options["db_path"] is None:
            self._list_databases()
            return

        db_path = Path(options["db_path"])
        if not db_path.exists():
            raise CommandError(f"File not found: {db_path}")

        tag_names = options["tag_names"] or None  # pass None → importer uses dir name default
        display_tags = ", ".join(tag_names) if tag_names else db_path.parent.name
        self.stdout.write(f"Importing {db_path} (tags: {display_tags!r}) …")

        stats = import_gsak_db(str(db_path), tag_names=tag_names)

        self.stdout.write(self.style.SUCCESS(
            f"Done — created: {stats.created}, updated: {stats.updated}, "
            f"locked: {stats.locked}"
        ))
        for error in stats.errors:
            self.stderr.write(self.style.ERROR(f"  Error: {error}"))
        if stats.errors:
            raise CommandError(f"{len(stats.errors)} cache(s) failed to import.")

    def _list_databases(self):
        if not GSAK_DATA_DIR.exists():
            self.stderr.write(f"GSAK data directory not found: {GSAK_DATA_DIR}")
            return
        dbs = sorted(
            p for p in GSAK_DATA_DIR.iterdir()
            if p.is_dir() and (p / "sqlite.db3").exists()
        )
        if not dbs:
            self.stdout.write("No GSAK databases found.")
            return
        self.stdout.write("Available GSAK databases:")
        for p in dbs:
            self.stdout.write(f"  {p.name:30s}  {p / 'sqlite.db3'}")
