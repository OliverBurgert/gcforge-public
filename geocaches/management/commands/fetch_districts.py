"""
Fetch sub-county district boundaries (OSM admin_level 9) for single-county
states that have finds — the Bezirke / wards of city-states like Berlin,
Hamburg and Washington DC, which geoBoundaries doesn't carry.

The districts are assembled from OpenStreetMap and cached next to the other
dashboard boundaries; the Maps tab then renders a district choropleth for
those states.

Usage:
    uv run python manage.py fetch_districts [--country DE]
"""

from django.core.management.base import BaseCommand

from preferences.services import boundaries


class Command(BaseCommand):
    help = (
        "Fetch OSM admin_level-9 districts for single-county states with finds "
        "(Berlin Bezirke, DC wards, …) and cache them for the dashboard map."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--country",
            default="",
            help="Limit to one ISO 3166-1 alpha-2 code (default: every country "
                 "whose county boundary is already downloaded).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-fetch even states whose districts are already cached.",
        )

    def handle(self, *args, **options):
        from geocaches.services import stats

        country = (options["country"] or "").upper()
        force = options["force"]
        if country:
            isos = [country]
        else:
            isos = sorted({info["iso2"] for info in boundaries.status().values()})

        any_work = False
        for iso in isos:
            for state in boundaries.single_county_states(iso):
                if not stats.finds_by_district(iso, state):
                    continue
                any_work = True
                if not force and boundaries.districts_downloaded(iso, state):
                    self.stdout.write(f"  {iso} {state}: already cached (skip)")
                    continue
                try:
                    n = boundaries.download_districts(iso, state)
                    self.stdout.write(
                        self.style.SUCCESS(f"  {iso} {state}: {n} districts")
                    )
                except (RuntimeError, ValueError, OSError) as exc:
                    self.stdout.write(self.style.ERROR(f"  {iso} {state}: {exc}"))

        if not any_work:
            self.stdout.write(
                "No single-county states with finds found. Download the county "
                "boundary for the relevant countries first (Settings → Dashboard)."
            )
