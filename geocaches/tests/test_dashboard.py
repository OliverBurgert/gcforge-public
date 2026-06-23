"""Dashboard page + stats service smoke/behaviour tests."""

import datetime as _dt

from django.test import TestCase
from django.urls import reverse

from geocaches.models import CacheSize, CacheStatus, CacheType, Geocache
from geocaches.services import stats
from preferences import dashboard_maps


def _cache(gc_code, **kw):
    defaults = dict(
        name="C", cache_type=CacheType.TRADITIONAL, size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE, latitude=48.0, longitude=9.0,
        difficulty=2.0, terrain=1.5,
    )
    defaults.update(kw)
    return Geocache.objects.create(gc_code=gc_code, **defaults)


class DashboardViewTests(TestCase):
    def test_dashboard_renders(self):
        resp = self.client.get(reverse("geocaches:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Statistics")
        # The heavy tabs load their bodies in the background via HTMX, so the
        # shell ships only the loaders + their partial URLs.
        self.assertContains(resp, 'id="dash-stats-body"')
        self.assertContains(resp, reverse("geocaches:dashboard_statistics"))
        self.assertContains(resp, reverse("geocaches:dashboard_maps_panel"))

    def test_statistics_partial_renders(self):
        resp = self.client.get(reverse("geocaches:dashboard_statistics"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Total finds")

    def test_maps_partial_renders(self):
        resp = self.client.get(reverse("geocaches:dashboard_maps_panel"))
        self.assertEqual(resp.status_code, 200)

    def test_stat_tables_partial_renders(self):
        resp = self.client.get(
            reverse("geocaches:dashboard_stat_tables"), {"stat_type": "Traditional"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Finds by found date")
        self.assertContains(resp, "Difficulty / Terrain chart")


class StatsServiceTests(TestCase):
    def setUp(self):
        # Two Traditional finds + one Multi find; one unfound cache.
        _cache("GC1", found=True, found_date=_dt.date(2021, 5, 10),
               hidden_date=_dt.date(2010, 3, 1), difficulty=1.0, terrain=1.0)
        _cache("GC2", found=True, found_date=_dt.date(2021, 5, 10),
               hidden_date=_dt.date(2010, 3, 15), difficulty=1.0, terrain=1.0)
        _cache("GC3", found=True, found_date=_dt.date(2022, 7, 1),
               cache_type=CacheType.MULTI, hidden_date=_dt.date(2012, 8, 20),
               difficulty=3.0, terrain=2.5)
        _cache("GC4", found=False)

    def test_summary(self):
        s = stats.summary_stats()
        self.assertEqual(s["total_finds"], 3)
        self.assertEqual(s["distinct_days"], 2)        # 2021-05-10, 2022-07-01
        self.assertEqual(s["best_day_count"], 2)       # two finds on 2021-05-10
        self.assertEqual(s["first_find"], _dt.date(2021, 5, 10))
        self.assertEqual(s["last_find"], _dt.date(2022, 7, 1))

    def test_type_filter(self):
        self.assertEqual(stats.find_queryset().count(), 3)
        self.assertEqual(stats.find_queryset(CacheType.MULTI).count(), 1)

    def test_dt_matrix_counts_and_totals(self):
        m = stats.dt_matrix()
        # D=1.0 (row 0), T=1.0 (col 0) -> two finds.
        self.assertEqual(m["rows"][0]["cells"][0]["count"], 2)
        self.assertEqual(m["grand_total"], 3)
        # Filtered to Multi: only the D3/T2.5 cell.
        mm = stats.dt_matrix(CacheType.MULTI)
        self.assertEqual(mm["grand_total"], 1)

    def test_calendar_found_date(self):
        cal = stats.finds_by_found_date()
        # May (month 5) row, day 10 -> 2 finds.
        may = cal["rows"][4]
        self.assertEqual(may["cells"][9]["count"], 2)
        self.assertEqual(cal["grand_total"], 3)

    def test_placed_month(self):
        pm = stats.finds_by_placed_month()
        years = [r["year"] for r in pm["rows"]]
        self.assertEqual(years, list(range(2010, 2013)))   # 2010..2012 inclusive
        self.assertEqual(pm["grand_total"], 3)


class AdventureLabStatsTests(TestCase):
    """The Adventure Lab tab counts found lab *stages*, always — independent of
    the dashboard "Include Adventure Lab caches" preference."""

    def setUp(self):
        from geocaches.models import Adventure

        # Adventure A — themes [History, Nature], parent in DE; 2 of 3 stages found.
        self.adv_a = Adventure.objects.create(
            code="LCAAA", adventure_guid="a", title="A",
            themes=["History", "Nature"],
        )
        Geocache.objects.create(  # parent (no al_detail) carries the country
            al_code="LCAAA", name="A", cache_type=CacheType.LAB,
            adventure=self.adv_a, iso_country_code="DE", completed=False,
            latitude=48.0, longitude=9.0,
        )
        self._stage(self.adv_a, 1, found=True, found_date=_dt.date(2023, 6, 1),
                    iso="DE")
        self._stage(self.adv_a, 2, found=True, found_date=_dt.date(2023, 6, 1),
                    iso="")  # no stage country → falls back to parent DE
        self._stage(self.adv_a, 3, found=False, iso="DE")

        # Adventure B — no themes, parent in US; 1 stage found.
        self.adv_b = Adventure.objects.create(
            code="LCBBB", adventure_guid="b", title="B", themes=[],
        )
        Geocache.objects.create(
            al_code="LCBBB", name="B", cache_type=CacheType.LAB,
            adventure=self.adv_b, iso_country_code="US",
            latitude=40.0, longitude=-74.0,
        )
        self._stage(self.adv_b, 1, found=True, found_date=_dt.date(2024, 1, 2),
                    iso="US")

    def _stage(self, adv, n, *, found, iso, found_date=None):
        from geocaches.models import ALStageDetail

        gc = Geocache.objects.create(
            al_code=f"{adv.code}-{n}", name=f"{adv.code} stage {n}",
            cache_type=CacheType.LAB, adventure=adv, found=found,
            found_date=found_date, iso_country_code=iso,
            latitude=48.0, longitude=9.0,
        )
        ALStageDetail.objects.create(geocache=gc, stage_number=n)
        return gc

    def test_summary_counts_found_stages_and_adventures(self):
        s = stats.alc_summary()
        self.assertEqual(s["total_finds"], 3)        # 2 from A + 1 from B
        self.assertEqual(s["adventures"], 2)
        self.assertEqual(s["distinct_days"], 2)      # 2023-06-01, 2024-01-02

    def test_finds_by_country_uses_parent_fallback(self):
        rows = {r["iso"]: r for r in stats.alc_finds_by_country()}
        self.assertEqual(rows["DE"]["count"], 2)     # one stage falls back to parent DE
        self.assertEqual(rows["US"]["count"], 1)
        # adv_a (DE): 2 of 3 stages found -> incomplete. adv_b (US): 1/1 -> completed.
        self.assertEqual(rows["DE"]["completed"], 0)
        self.assertEqual(rows["DE"]["incomplete"], 1)
        self.assertEqual(rows["US"]["completed"], 1)
        self.assertEqual(rows["US"]["incomplete"], 0)

    def test_theme_breakdown_counts_adventures_and_stages(self):
        rows = {r["label"]: r for r in stats.alc_theme_breakdown()}
        # adv_a (History+Nature): 2 of 3 stages found -> incomplete; 2 found stages.
        self.assertEqual(rows["History"]["completed"], 0)
        self.assertEqual(rows["History"]["incomplete"], 1)
        self.assertEqual(rows["History"]["not_started"], 0)
        self.assertEqual(rows["History"]["stages"], 2)
        self.assertEqual(rows["Nature"]["incomplete"], 1)
        self.assertEqual(rows["Nature"]["stages"], 2)
        # adv_b (no themes): 1/1 -> completed, in the "No theme" bucket; 1 stage.
        self.assertEqual(rows["No theme"]["completed"], 1)
        self.assertEqual(rows["No theme"]["incomplete"], 0)
        self.assertEqual(rows["No theme"]["stages"], 1)

    def test_unstarted_adventure_counts_as_not_started(self):
        from geocaches.models import Adventure
        adv = Adventure.objects.create(
            code="LCUNS", adventure_guid="uns", title="U", themes=["Travel"],
        )
        Geocache.objects.create(
            al_code="LCUNS", name="U", cache_type=CacheType.LAB,
            adventure=adv, latitude=48.0, longitude=9.0,
        )
        self._stage(adv, 1, found=False, iso="DE")  # no found stage -> not started
        rows = {r["label"]: r for r in stats.alc_theme_breakdown()}
        self.assertEqual(rows["Travel"]["not_started"], 1)
        self.assertEqual(rows["Travel"]["completed"], 0)
        self.assertEqual(rows["Travel"]["incomplete"], 0)
        self.assertEqual(rows["Travel"]["stages"], 0)
        self.assertEqual(
            stats.alc_theme_parent_ids("Travel", "not_started"),
            [Geocache.objects.get(al_code="LCUNS").id],
        )

    def test_theme_parent_ids_and_filter_endpoint(self):
        parent_a = Geocache.objects.get(al_code="LCAAA")
        self.assertEqual(
            stats.alc_theme_parent_ids("History", "incomplete"), [parent_a.id]
        )
        self.assertEqual(stats.alc_theme_parent_ids("History", "completed"), [])
        # 'stages' lists the found stage geocaches (adv_a stages 1 & 2).
        self.assertEqual(len(stats.alc_theme_stage_ids("History")), 2)
        resp = self.client.get(
            reverse("geocaches:dashboard_alc_theme"),
            {"theme": "History", "status": "incomplete"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("where_sql=", resp["Location"])
        self.assertIn(str(parent_a.id), resp["Location"])

    def test_found_date_calendar_total(self):
        cal = stats.alc_finds_by_found_date()
        self.assertEqual(cal["grand_total"], 3)

    def test_independent_of_include_al_preference(self):
        from preferences.models import UserPreference

        UserPreference.set("stats_include_al", False)
        self.assertEqual(stats.alc_summary()["total_finds"], 3)
        UserPreference.set("stats_include_al", True)
        self.assertEqual(stats.alc_summary()["total_finds"], 3)

    def test_tab_renders_with_lab_finds(self):
        # The shell ships the ALC tab + loader; its body comes from the partial.
        shell = self.client.get(reverse("geocaches:dashboard"))
        self.assertContains(shell, 'id="dash-alc"')
        self.assertContains(shell, reverse("geocaches:dashboard_alc"))
        resp = self.client.get(reverse("geocaches:dashboard_alc"))
        self.assertContains(resp, "Lab stage finds")
        self.assertContains(resp, "chart-alc-cumulative-data")


class AdventureThemeDisplayTests(TestCase):
    def test_known_token_prettified_with_icon(self):
        from geocaches.al_themes import theme_display
        label, icon = theme_display("FoodDrink")
        self.assertEqual(label, "Food & Drink")
        self.assertTrue(icon)

    def test_unknown_token_split_pascalcase_with_fallback_icon(self):
        from geocaches.al_themes import FALLBACK_ICON, theme_display
        label, icon = theme_display("WaterActivities")
        self.assertEqual(label, "Water Activities")
        self.assertEqual(icon, FALLBACK_ICON)

    def test_badges_skip_empties_and_preserve_order(self):
        from geocaches.al_themes import theme_badges
        badges = theme_badges(["History", "", "WalkingTour"])
        self.assertEqual([b["value"] for b in badges], ["History", "WalkingTour"])
        self.assertEqual(badges[1]["label"], "Walking Tour")


class CountryMapTests(TestCase):
    def setUp(self):
        _cache("GC1", found=True, iso_country_code="DE")
        _cache("GC2", found=True, iso_country_code="de")     # case-folded to DE
        _cache("GC3", completed=True, iso_country_code="US")  # completed counts as find
        _cache("GC4", found=False, iso_country_code="FR")     # not a find → excluded
        _cache("GC5", found=True, iso_country_code="")        # no country → skipped

    def test_counts_keyed_by_uppercase_iso(self):
        counts = stats.finds_by_country_iso()
        self.assertEqual(counts["DE"], 2)
        self.assertEqual(counts["US"], 1)
        self.assertNotIn("FR", counts)
        self.assertNotIn("", counts)


class RegionMapDataTests(TestCase):
    def setUp(self):
        _cache("R1", found=True, iso_country_code="DE", state="Bayern")
        _cache("R2", found=True, iso_country_code="DE", state="Bayern")
        _cache("R3", found=True, iso_country_code="DE", state="Sachsen")
        _cache("R4", found=True, iso_country_code="DE", state="Atlantis")  # no polygon

    def test_finds_by_state_county_disambiguates_collisions(self):
        # Two distinct (state, county) pairs that share a county name —
        # exactly the "San Juan, Utah" vs "San Juan, Puerto Rico" case in the
        # US.  finds_by_state_county must keep them separate.
        _cache("R5", found=True, iso_country_code="US", state="Utah",
               county="San Juan")
        _cache("R6", found=True, iso_country_code="US", state="Utah",
               county="San Juan")
        _cache("R7", found=True, iso_country_code="US", state="Puerto Rico",
               county="San Juan")
        counts = stats.finds_by_state_county("US")
        self.assertEqual(counts[("Utah", "San Juan")], 2)
        self.assertEqual(counts[("Puerto Rico", "San Juan")], 1)

    def test_finds_by_state(self):
        counts = stats.finds_by_state("DE")
        self.assertEqual(counts["Bayern"], 2)
        self.assertEqual(counts["Sachsen"], 1)
        self.assertEqual(counts["Atlantis"], 1)

    def test_normalize_name_rules(self):
        from preferences.services import boundaries
        n = boundaries.normalize_name
        # Diacritics + hyphen become a single space-joined lowercase key.
        self.assertEqual(n("Baden-Württemberg"), "baden wurttemberg")
        # SE "län" suffix (incl. genitive "s lan") strips so DB "Västra Götaland"
        # matches the geoBoundaries "Västra Götalands län" polygon.
        self.assertEqual(n("Västra Götalands län"), n("Västra Götaland"))
        # FR poly "Grand Est" matches DB "Grand-Est" (hyphen → space).
        self.assertEqual(n("Grand Est"), n("Grand-Est"))
        # ES poly slash-aliases — first half joined to bare DB name.
        self.assertEqual(n("Cataluña/Catalunya"), n("Cataluña"))
        # ES "Islas " prefix stripped on DB side.
        self.assertEqual(n("Islas Canarias"), n("Canarias"))
        # IS English↔Icelandic alias.
        self.assertEqual(n("Capital Region", "IS"),
                         n("Höfudborgarsvaedi", "IS"))
        self.assertEqual(n("Eastern Region", "IS"),
                         n("Austurland", "IS"))
        # JP — geoBoundaries inconsistently appends " Prefecture".
        self.assertEqual(n("Aichi Prefecture"), n("Aichi"))
        # GB — Nominatim's finer "London"/"South Wales" roll up to the
        # constituent country (only level gbOpen ADM1 offers for the UK).
        self.assertEqual(n("London", "GB"), n("England", "GB"))
        self.assertEqual(n("South Wales", "GB"), n("Wales", "GB"))
        # Trailing parenthetical disambiguator stripped on either side, so a
        # bare DB name joins the polygon's "(DE)" variant and vice-versa.
        self.assertEqual(n("Friesland (DE)"), n("Friesland"))
        self.assertEqual(n("Leer (Ostfriesland)"), n("Leer"))
        # …even when the parenthetical sat in front of a stripped suffix, as in
        # geoBoundaries' "Halle (Saale), Kreisfreie Stadt".
        self.assertEqual(n("Halle (Saale), Kreisfreie Stadt"), n("Halle (Saale)"))
        # Lusatian bilingual "German - Sorbian" names join on the German half;
        # a compound hyphen (no surrounding spaces) is NOT split.
        self.assertEqual(n("Bautzen - Budyšin", "DE"), n("Bautzen", "DE"))
        self.assertEqual(n("Mayen-Koblenz"), "mayen koblenz")
        # DE county aliases reconcile abbreviated Landkreis spellings.
        self.assertEqual(n("Sächs. Schweiz-Osterzgebirge", "DE"),
                         n("Sächsische Schweiz-Osterzgebirge", "DE"))
        self.assertEqual(n("Wunsiedel i. Fichtelgebirge", "DE"),
                         n("Wunsiedel im Fichtelgebirge", "DE"))

    def test_effective_level_override(self):
        # Italy's gbOpen ADM1 is 5 NUTS-1 macro-regions; ADM2 has the 20
        # real regioni, so the service quietly uses ADM2 for IT.
        from preferences.services import boundaries
        self.assertEqual(boundaries.effective_level("IT"), "ADM2")
        self.assertEqual(boundaries.effective_level("DE"), "ADM1")
        self.assertEqual(boundaries.effective_level("us"), "ADM1")  # case-insensitive
        # County tier: DE Landkreise live in ADM3 (gbOpen DE-ADM2 is the 38
        # Regierungsbezirke, not the 401 Landkreise).  Most countries → ADM2.
        self.assertEqual(boundaries.effective_county_level("DE"), "ADM3")
        self.assertEqual(boundaries.effective_county_level("IT"), "ADM3")
        self.assertEqual(boundaries.effective_county_level("US"), "ADM2")

    def test_resolve_iso_3166_2_via_pycountry(self):
        # geoBoundaries ITA ADM2 leaves shapeISO blank; the pycountry fallback
        # is what gets the flag lookup working.
        from preferences.services import boundaries
        self.assertEqual(boundaries._resolve_iso_3166_2("IT", "Lombardia"), "IT-25")
        self.assertEqual(boundaries._resolve_iso_3166_2("DE", "Bayern"), "DE-BY")
        self.assertEqual(boundaries._resolve_iso_3166_2("DE", "Atlantis"), "")
        self.assertEqual(boundaries._resolve_iso_3166_2("", "Bayern"), "")

    def test_region_map_data_joins_and_reports_unmatched(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        from preferences.services import boundaries

        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"name": "Bayern"},
             "geometry": {"type": "Point", "coordinates": [11, 48]}},
            {"type": "Feature", "properties": {"name": "Sachsen"},
             "geometry": {"type": "Point", "coordinates": [13, 51]}},
            {"type": "Feature", "properties": {"name": "Hessen"},
             "geometry": {"type": "Point", "coordinates": [9, 50]}},
        ]}
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "DEU_ADM1.geojson").write_text(json.dumps(fc), encoding="utf-8")
            with mock.patch.object(boundaries, "boundaries_dir", return_value=Path(td)):
                data = boundaries.region_map_data("DE")

        by = {f["properties"]["name"]: f["properties"]["count"] for f in data["features"]}
        self.assertEqual(by["Bayern"], 2)
        self.assertEqual(by["Sachsen"], 1)
        self.assertEqual(by["Hessen"], 0)
        self.assertEqual(data["meta"]["total"], 4)       # includes "Atlantis"
        self.assertEqual(data["meta"]["unmatched"], 1)   # the unmatched "Atlantis"

    def test_county_map_data_city_state_rollup(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        from preferences.services import boundaries

        # Berlin has a single county polygon → its Bezirk finds roll up.
        # Bayern has several → an unmatched Bayern find must NOT be swallowed.
        _cache("C1", found=True, iso_country_code="DE",
               state="Berlin", county="Berlin Mitte")
        _cache("C2", found=True, iso_country_code="DE",
               state="Berlin", county="Charlottenburg-Wilmersdorf")
        _cache("C3", found=True, iso_country_code="DE",
               state="Bayern", county="München")
        _cache("C4", found=True, iso_country_code="DE",
               state="Bayern", county="Nirgendwo")  # no polygon — stays unmatched

        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "properties": {"name": "Berlin", "parent_state": "Berlin"},
             "geometry": {"type": "Point", "coordinates": [13, 52]}},
            {"type": "Feature",
             "properties": {"name": "München, Kreisfreie Stadt",
                            "parent_state": "Bayern"},
             "geometry": {"type": "Point", "coordinates": [11, 48]}},
            {"type": "Feature",
             "properties": {"name": "Augsburg, Kreisfreie Stadt",
                            "parent_state": "Bayern"},
             "geometry": {"type": "Point", "coordinates": [10, 48]}},
        ]}
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "DEU_ADM3.geojson").write_text(json.dumps(fc), encoding="utf-8")
            with mock.patch.object(boundaries, "boundaries_dir", return_value=Path(td)):
                data = boundaries.county_map_data("DE")

        by = {f["properties"]["name"]: f["properties"]["count"] for f in data["features"]}
        # Both Berlin Bezirke roll up onto the single Berlin polygon.
        self.assertEqual(by["Berlin"], 2)
        # Bayern is multi-county: München joins directly, Nirgendwo does NOT get
        # swallowed by some Bayern polygon.
        self.assertEqual(by["München, Kreisfreie Stadt"], 1)
        self.assertEqual(by["Augsburg, Kreisfreie Stadt"], 0)
        self.assertEqual(data["meta"]["total"], 4)
        self.assertEqual(data["meta"]["unmatched"], 1)  # C4 Nirgendwo
        self.assertEqual(data["meta"]["country"], "Germany")
        unmatched = data["meta"]["unmatched_caches"]
        self.assertEqual(
            unmatched,
            [{"code": "C4", "name": "C", "state": "Bayern", "county": "Nirgendwo"}],
        )

    def test_county_map_data_dc_rollup_not_other_washingtons(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        from preferences.services import boundaries

        # Washington DC is a single-county federal district (one polygon), so
        # finds Nominatim labels county="Washington" roll up to it.  Maryland is
        # multi-county, so it must NOT swallow an unmatched find — and its real
        # "Washington" county still joins directly.
        _cache("W1", found=True, iso_country_code="US",
               state="District of Columbia", county="Washington")
        _cache("W2", found=True, iso_country_code="US",
               state="District of Columbia", county="District of Columbia")
        _cache("W3", found=True, iso_country_code="US",
               state="Maryland", county="Washington")
        _cache("W4", found=True, iso_country_code="US",
               state="Maryland", county="Nowhere")  # no polygon — stays unmatched

        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature",
             "properties": {"name": "District of Columbia",
                            "parent_state": "District of Columbia"},
             "geometry": {"type": "Point", "coordinates": [-77, 38.9]}},
            {"type": "Feature",
             "properties": {"name": "Washington", "parent_state": "Maryland"},
             "geometry": {"type": "Point", "coordinates": [-77.7, 39.6]}},
            {"type": "Feature",
             "properties": {"name": "Montgomery", "parent_state": "Maryland"},
             "geometry": {"type": "Point", "coordinates": [-77.2, 39.1]}},
        ]}
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "USA_ADM2.geojson").write_text(json.dumps(fc), encoding="utf-8")
            with mock.patch.object(boundaries, "boundaries_dir", return_value=Path(td)):
                data = boundaries.county_map_data("US")

        by = {(f["properties"]["parent_state"], f["properties"]["name"]):
              f["properties"]["count"] for f in data["features"]}
        # DC: both W1 (Washington) and W2 roll onto the single DC polygon.
        self.assertEqual(by[("District of Columbia", "District of Columbia")], 2)
        # Maryland (multi-county): real Washington joins; Nowhere is unmatched.
        self.assertEqual(by[("Maryland", "Washington")], 1)
        self.assertEqual(by[("Maryland", "Montgomery")], 0)
        self.assertEqual(data["meta"]["unmatched"], 1)  # W4 Nowhere

    def test_finds_in_state_county_keys_matches_state_or_pair(self):
        # Hessen isn't used by setUp, so the state-key query is isolated.
        _cache("K1", found=True, iso_country_code="DE",
               state="Hessen", county="Hof")
        _cache("K2", found=True, iso_country_code="DE",
               state="Hessen", county="Other")
        _cache("K3", found=True, iso_country_code="DE",
               state="Sachsen", county="Hof")
        # Tuple key matches the exact (state, county); string key matches state.
        pair = stats.finds_in_state_county_keys("DE", [("Hessen", "Hof")])
        self.assertEqual([r["code"] for r in pair], ["K1"])
        whole = stats.finds_in_state_county_keys("DE", ["Hessen"])
        self.assertEqual({r["code"] for r in whole}, {"K1", "K2"})

    def test_merge_duplicate_features_folds_same_name(self):
        from preferences.services import boundaries
        feats = [
            {"type": "Feature",
             "properties": {"name": "Aurich", "parent_state": "Niedersachsen"},
             "geometry": {"type": "Point", "coordinates": [7, 53]}},
            {"type": "Feature",
             "properties": {"name": "Aurich", "parent_state": "Niedersachsen"},
             "geometry": {"type": "Polygon", "coordinates": [[[7, 53], [8, 53], [8, 54]]]}},
            {"type": "Feature",
             "properties": {"name": "Leer", "parent_state": "Niedersachsen"},
             "geometry": {"type": "Point", "coordinates": [7, 53]}},
        ]
        merged = boundaries._merge_duplicate_features(feats)
        names = [f["properties"]["name"] for f in merged]
        self.assertEqual(names, ["Aurich", "Leer"])  # Aurich folded to one
        # …and its geometry kept both parts as a MultiPolygon.
        self.assertEqual(merged[0]["geometry"]["type"], "MultiPolygon")
        self.assertEqual(len(merged[0]["geometry"]["coordinates"]), 1)

    def test_stitch_rings_and_relation_geometry(self):
        from preferences.services import boundaries
        # A square split into two ways (second reversed) stitches into one
        # closed ring.
        ways = [[(0, 0), (1, 0), (1, 1)], [(0, 0), (0, 1), (1, 1)]]
        rings = boundaries._stitch_rings(ways)
        self.assertEqual(len(rings), 1)
        self.assertEqual(rings[0][0], rings[0][-1])
        # A relation with one already-closed outer way → a Polygon.
        rel = {"members": [{"type": "way", "role": "outer", "geometry": [
            {"lon": 0, "lat": 0}, {"lon": 1, "lat": 0},
            {"lon": 1, "lat": 1}, {"lon": 0, "lat": 0}]}]}
        geom = boundaries._relation_geometry(rel)
        self.assertEqual(geom["type"], "Polygon")
        self.assertEqual(geom["coordinates"][0][0], geom["coordinates"][0][-1])

    def test_single_county_states_uses_merged_polygons(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        from preferences.services import boundaries

        fc = {"type": "FeatureCollection", "features": [
            {"properties": {"name": "Berlin", "parent_state": "Berlin"}},
            {"properties": {"name": "Hamburg", "parent_state": "Hamburg"}},
            {"properties": {"name": "Hamburg", "parent_state": "Hamburg"}},  # dup
            {"properties": {"name": "München", "parent_state": "Bayern"}},
            {"properties": {"name": "Augsburg", "parent_state": "Bayern"}},
        ]}
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "DEU_ADM3.geojson").write_text(json.dumps(fc), encoding="utf-8")
            with mock.patch.object(boundaries, "boundaries_dir", return_value=Path(td)):
                self.assertEqual(boundaries.single_county_states("DE"),
                                 ["Berlin", "Hamburg"])  # not multi-county Bayern

    def test_district_endpoint(self):
        from unittest import mock

        from preferences.services import boundaries
        # 200 with data (and the spaced state name routes correctly)…
        with mock.patch.object(boundaries, "district_map_data",
                               return_value={"features": [], "meta": {}}):
            resp = self.client.get(reverse(
                "geocaches:dashboard_district_data",
                args=["US", "District of Columbia"]))
        self.assertEqual(resp.status_code, 200)
        # …404 when the districts aren't fetched yet.
        with mock.patch.object(boundaries, "district_map_data", return_value=None):
            resp = self.client.get(reverse(
                "geocaches:dashboard_district_data", args=["DE", "Berlin"]))
        self.assertEqual(resp.status_code, 404)

    def test_region_endpoint_404_when_not_downloaded(self):
        from unittest import mock

        from preferences.services import boundaries

        with mock.patch.object(boundaries, "is_downloaded", return_value=False), \
             mock.patch.object(boundaries, "region_map_data", return_value=None):
            resp = self.client.get(reverse("geocaches:dashboard_region_data", args=["ZZ"]))
        self.assertEqual(resp.status_code, 404)


class DashboardMapsConfigTests(TestCase):
    def test_default_is_world_only(self):
        cfg = {m["type"]: m for m in dashboard_maps.default_config()}
        self.assertTrue(cfg["world"]["visible"])
        self.assertFalse(cfg["continent"]["visible"])
        self.assertFalse(cfg["country"]["visible"])
        self.assertFalse(cfg["county"]["visible"])

    def test_save_normalizes_and_orders(self):
        dashboard_maps.save_config([
            {"type": "world", "visible": False, "order": 5},
            {"type": "continent", "visible": True, "order": 1},
        ])
        cfg = dashboard_maps.get_config()
        # All known levels are always present, even those not supplied.
        self.assertEqual({m["type"] for m in cfg}, set(dashboard_maps.MAP_LEVELS))
        by = {m["type"]: m for m in cfg}
        self.assertFalse(by["world"]["visible"])
        self.assertTrue(by["continent"]["visible"])
        # Ordered by `order`: continent (1) precedes world (5).
        self.assertLess(
            [m["type"] for m in cfg].index("continent"),
            [m["type"] for m in cfg].index("world"),
        )

    def test_view_renders_world_section_and_counts(self):
        # The Maps tab body is loaded in the background via its own partial.
        resp = self.client.get(reverse("geocaches:dashboard_maps_panel"))
        self.assertContains(resp, 'data-level="world"')
        self.assertContains(resp, 'id="dashboard-country-counts"')

    def test_save_view_persists_visibility(self):
        resp = self.client.post(
            reverse("preferences:save_dashboard_maps"),
            {"vis_world": "1", "order_world": "2", "order_continent": "0"},
        )
        self.assertEqual(resp.status_code, 302)
        by = {m["type"]: m for m in dashboard_maps.get_config()}
        self.assertTrue(by["world"]["visible"])
        self.assertFalse(by["continent"]["visible"])  # checkbox absent → off


class DashboardMapsCountryConfigTests(TestCase):
    """Tests for the countries field on the country level config entry."""

    def test_countries_defaults_to_none(self):
        cfg = {m["type"]: m for m in dashboard_maps.default_config()}
        self.assertIsNone(cfg["country"]["countries"])

    def test_countries_round_trips_through_normalize(self):
        dashboard_maps.save_config([
            {"type": "country", "visible": True, "order": 0, "countries": ["DE", "US"]},
        ])
        cfg = {m["type"]: m for m in dashboard_maps.get_config()}
        self.assertEqual(cfg["country"]["countries"], ["DE", "US"])

    def test_countries_preserved_when_absent(self):
        # Save without countries — should default to None.
        dashboard_maps.save_config([
            {"type": "country", "visible": True, "order": 0},
        ])
        cfg = {m["type"]: m for m in dashboard_maps.get_config()}
        self.assertIsNone(cfg["country"]["countries"])

    def test_countries_none_when_non_list(self):
        # If somehow a non-list is stored it should normalise to None.
        from preferences.models import UserPreference
        from preferences.dashboard_maps import DASHBOARD_MAPS_KEY
        UserPreference.set(DASHBOARD_MAPS_KEY, {"maps": [
            {"type": "country", "visible": True, "order": 0, "countries": "DE"},
        ]})
        cfg = {m["type"]: m for m in dashboard_maps.get_config()}
        self.assertIsNone(cfg["country"]["countries"])

    def test_save_view_persists_country_visibility_and_selection(self):
        _cache("GC10", found=True, iso_country_code="DE")
        resp = self.client.post(
            reverse("preferences:save_dashboard_maps"),
            {
                "vis_world": "1", "order_world": "0",
                "order_continent": "1",
                "vis_country": "1", "order_country": "2",
                "country_iso": ["DE"],
            },
        )
        self.assertEqual(resp.status_code, 302)
        cfg = {m["type"]: m for m in dashboard_maps.get_config()}
        self.assertTrue(cfg["country"]["visible"])
        self.assertEqual(cfg["country"]["countries"], ["DE"])

    def test_save_view_country_empty_selection_stored_as_none(self):
        resp = self.client.post(
            reverse("preferences:save_dashboard_maps"),
            {
                "vis_world": "1", "order_world": "0",
                "order_continent": "1",
                "vis_country": "1", "order_country": "2",
                # no country_iso checkboxes
            },
        )
        self.assertEqual(resp.status_code, 302)
        cfg = {m["type"]: m for m in dashboard_maps.get_config()}
        self.assertIsNone(cfg["country"]["countries"])

    def test_download_boundary_endpoint_exists_and_redirects(self):
        from unittest import mock
        from preferences.services import boundaries

        with mock.patch.object(boundaries, "download_boundary", return_value=42):
            resp = self.client.post(
                reverse("preferences:download_boundary"),
                {"iso2": "DE"},
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("dashboard", resp["Location"])

    def test_download_boundary_rejects_invalid_iso(self):
        resp = self.client.post(
            reverse("preferences:download_boundary"),
            {"iso2": "INVALID"},
        )
        self.assertEqual(resp.status_code, 302)

    def test_update_all_boundaries_endpoint_exists_and_redirects(self):
        from unittest import mock
        from preferences.services import boundaries

        with mock.patch.object(boundaries, "update_all", return_value={}):
            resp = self.client.post(
                reverse("preferences:update_all_boundaries"),
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("dashboard", resp["Location"])

    def test_download_boundary_get_returns_405(self):
        resp = self.client.get(reverse("preferences:download_boundary"))
        self.assertEqual(resp.status_code, 405)

    def test_update_all_boundaries_get_returns_405(self):
        resp = self.client.get(reverse("preferences:update_all_boundaries"))
        self.assertEqual(resp.status_code, 405)


class MissingClauseTests(TestCase):
    def setUp(self):
        # One found Traditional at D1/T1.
        _cache("GC1", found=True, found_date=_dt.date(2021, 5, 10),
               hidden_date=_dt.date(2010, 3, 1), difficulty=1.0, terrain=1.0)

    def test_dt_clause_excludes_found_cell(self):
        sql = stats.build_missing_where_sql("dt", None, minimum=1)
        # D1/T1 is found -> must NOT appear; a missing combo must.
        self.assertNotIn("difficulty = 1.0 AND terrain IN (1.0)", sql)
        self.assertIn("difficulty = 5.0", sql)
        self.assertIn("found = 0", sql)
        self.assertIn("completed = 0", sql)

    def test_dt_minimum_includes_underfilled_cell(self):
        # minimum=2 -> the D1/T1 cell (count 1 < 2) is now "missing".
        sql = stats.build_missing_where_sql("dt", None, minimum=2)
        self.assertIn("difficulty = 1.0 AND terrain IN (1.0", sql)

    def test_placed_date_uses_strftime(self):
        # %% (doubled) is required for RawSQL — see _pd_sql note.
        sql = stats.build_missing_where_sql("placed_date", None, minimum=1)
        self.assertIn("strftime('%%m-%%d', hidden_date) IN (", sql)

    def test_type_filter_validated(self):
        sql = stats.build_missing_where_sql("dt", CacheType.TRADITIONAL, minimum=1)
        self.assertIn(f"cache_type = '{CacheType.TRADITIONAL}'", sql)
        # An unknown/forged type is ignored (no cache_type clause).
        sql2 = stats.build_missing_where_sql("dt", "'; DROP TABLE x; --", minimum=1)
        self.assertNotIn("DROP TABLE", sql2)

    def test_endpoint_redirects_to_list_with_where_sql(self):
        resp = self.client.get(
            reverse("geocaches:dashboard_missing"), {"which": "all"}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("where_sql=", resp["Location"])

    def test_clause_applies_via_full_view_path_and_excludes_found(self):
        # Must run through apply_all (the list view's path), which carries
        # distance-annotation params — that's what triggers Django's %-format
        # substitution and exposed the un-doubled strftime('%Y-%m') bug.  The
        # round-trip through a QueryDict mirrors the redirect.
        from urllib.parse import urlencode
        from django.db.models import Q
        from django.http import QueryDict
        from geocaches.query import apply_all

        # GC1 (found, placed 2010-03) makes 2010-03 NOT missing.
        # GC2 (unfound, placed 2010-05) sits in a missing month.
        _cache("GC2", found=False, hidden_date=_dt.date(2010, 5, 1),
               difficulty=2.0, terrain=2.0)

        for which in ("all", "placed_month", "placed_date"):
            sql = stats.build_missing_where_sql(which, None, minimum=1)
            qd = QueryDict(urlencode({"where_sql": sql}))
            qs, fv = apply_all(Geocache.objects.all(), qd)
            self.assertEqual(fv.get("where_error"), "", f"{which} where_error")
            found = qs.filter(Q(found=True) | Q(completed=True)).count()
            self.assertEqual(found, 0, f"{which} leaked found caches")

        # placed_month specifically returns the unfound GC2, not the found GC1.
        sql = stats.build_missing_where_sql("placed_month", None, minimum=1)
        qd = QueryDict(urlencode({"where_sql": sql}))
        codes = set(apply_all(Geocache.objects.all(), qd)[0].values_list("gc_code", flat=True))
        self.assertIn("GC2", codes)
        self.assertNotIn("GC1", codes)
