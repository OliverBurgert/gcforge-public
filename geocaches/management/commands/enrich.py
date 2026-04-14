from django.core.management.base import BaseCommand

from geocaches.enrichment import enrich_queryset
from geocaches.models import Geocache


class Command(BaseCommand):
    help = "Fill missing elevation and location data for geocaches"

    def add_arguments(self, parser):
        parser.add_argument(
            "--fields",
            nargs="+",
            choices=["elevation", "location"],
            default=["elevation", "location"],
            metavar="FIELD",
            help="Fields to enrich (default: elevation location)",
        )
        parser.add_argument(
            "--code",
            nargs="+",
            dest="codes",
            metavar="GC_CODE",
            help="Restrict enrichment to specific GC codes",
        )

    def handle(self, *args, **options):
        qs = Geocache.objects.all()
        if options["codes"]:
            qs = qs.filter(gc_code__in=options["codes"])

        fields = set(options["fields"])
        self.stdout.write(
            f"Enriching up to {qs.count()} caches — fields: {', '.join(sorted(fields))} …"
        )

        stats = enrich_queryset(qs, fields)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done — updated: {stats.updated}, "
                f"already complete: {stats.skipped}, "
                f"errors: {len(stats.errors)}"
            )
        )
        for err in stats.errors:
            self.stderr.write(f"  {err}")
