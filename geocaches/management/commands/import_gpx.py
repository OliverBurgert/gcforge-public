from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from geocaches.importers import import_gc_gpx


class Command(BaseCommand):
    help = "Import a Groundspeak Pocket Query GPX or ZIP file into the database."

    def add_arguments(self, parser):
        parser.add_argument("gpx_file", type=str, help="Path to a .gpx file or .zip archive")
        parser.add_argument(
            "--wpts",
            dest="wpts_file",
            default=None,
            help="Path to the companion -wpts.gpx file (auto-detected if omitted)",
        )
        parser.add_argument(
            "--tag",
            dest="tag_names",
            action="append",
            default=[],
            metavar="TAG",
            help="Tag to apply to all imported caches (repeatable: --tag A --tag B)",
        )

    def handle(self, *args, **options):
        main_path = options["gpx_file"]
        if not Path(main_path).exists():
            raise CommandError(f"File not found: {main_path}")

        wpts_path = options["wpts_file"]
        if wpts_path and not Path(wpts_path).exists():
            raise CommandError(f"Wpts file not found: {wpts_path}")

        self.stdout.write(f"Importing {main_path} …")
        stats = import_gc_gpx(main_path, wpts_path=wpts_path, tag_names=options["tag_names"])

        self.stdout.write(self.style.SUCCESS(
            f"Done — created: {stats.created}, updated: {stats.updated}, "
            f"locked: {stats.locked}"
        ))
        for error in stats.errors:
            self.stderr.write(self.style.ERROR(f"  Error: {error}"))
        if stats.errors:
            raise CommandError(f"{len(stats.errors)} cache(s) failed to import.")
