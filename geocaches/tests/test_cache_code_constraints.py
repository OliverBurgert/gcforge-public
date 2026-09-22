"""Conditional unique constraints on the live cache codes.

See docs/architecture-review-2026-09-workplan.md WP-06.  Live rows (those not
in the Trash) may not share a gc_code / oc_code / al_code; trashed rows keep
their codes so a re-import can build a fresh record beside them.
"""

from importlib import import_module
from types import SimpleNamespace

from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils.timezone import now

from geocaches.models import ALStageDetail, Adventure, CacheType, Geocache, Log, LogType
from geocaches.services import merge_duplicate, save_geocache
from geocaches.services.codes import find_duplicate_live_codes, format_duplicate_report
from geocaches.services.dedup import _merge_into
from geocaches.services.trash import RestoreConflict, restore_cache, trash_cache

_migration = import_module("geocaches.migrations.0044_unique_live_cache_codes")


def _cache(**kwargs):
    defaults = dict(
        name="Cache", cache_type=CacheType.TRADITIONAL,
        latitude=48.5, longitude=9.1,
    )
    defaults.update(kwargs)
    return Geocache.objects.create(**defaults)


def _drop_constraint(name):
    """Remove one unique index so a duplicate can be planted for the scanner.

    Raw DDL rather than the schema editor: SQLite refuses the latter inside the
    transaction a TestCase runs in.  Returns the CREATE statement for
    _restore_constraint().
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = %s", [name]
        )
        (sql,) = cursor.fetchone()
        cursor.execute(f'DROP INDEX "{name}"')
    return sql


def _restore_constraint(create_sql):
    with connection.cursor() as cursor:
        cursor.execute(create_sql)


class LiveCodeUniquenessTests(TestCase):
    def test_two_live_caches_cannot_share_a_gc_code(self):
        _cache(gc_code="GC1111")
        with self.assertRaises(IntegrityError), transaction.atomic():
            _cache(gc_code="GC1111")

    def test_two_live_caches_cannot_share_an_oc_code(self):
        _cache(oc_code="OC1111")
        with self.assertRaises(IntegrityError), transaction.atomic():
            _cache(oc_code="OC1111")

    def test_two_live_caches_cannot_share_an_al_code(self):
        _cache(al_code="LC1111")
        with self.assertRaises(IntegrityError), transaction.atomic():
            _cache(al_code="LC1111")

    def test_a_trashed_cache_may_keep_a_code_a_live_cache_uses(self):
        trashed = _cache(gc_code="GC2222")
        trash_cache(trashed)
        fresh = _cache(gc_code="GC2222")
        self.assertNotEqual(fresh.pk, trashed.pk)
        self.assertEqual(Geocache.all_objects.filter(gc_code="GC2222").count(), 2)

    def test_empty_codes_are_not_constrained(self):
        _cache(gc_code="GC3333")
        _cache(gc_code="GC4444")
        # Both have oc_code="" and al_code="" — no conflict.
        self.assertEqual(Geocache.objects.filter(oc_code="").count(), 2)

    def test_a_code_can_move_between_the_three_fields(self):
        cache = _cache(gc_code="OC5555")
        other = _cache(oc_code="OC5555")
        self.assertNotEqual(cache.pk, other.pk)


class DuplicateScanTests(TestCase):
    def test_clean_database_reports_nothing(self):
        _cache(gc_code="GC1111", oc_code="OC1111")
        self.assertEqual(find_duplicate_live_codes(), {})

    def test_duplicate_is_found_with_both_pks(self):
        constraint = _drop_constraint("uniq_live_oc_code")
        try:
            first = _cache(oc_code="OC9999")
            second = _cache(oc_code="OC9999")
            duplicates = find_duplicate_live_codes()
        finally:
            Geocache.objects.filter(oc_code="OC9999").exclude(pk=first.pk).delete()
            _restore_constraint(constraint)

        self.assertEqual(
            duplicates, {"oc_code": [("OC9999", sorted([first.pk, second.pk]))]}
        )

    def test_trashed_rows_are_not_duplicates(self):
        trashed = _cache(oc_code="OC8888")
        trash_cache(trashed)
        _cache(oc_code="OC8888")
        self.assertEqual(find_duplicate_live_codes(), {})

    def test_report_names_field_code_and_ids(self):
        report = format_duplicate_report({"oc_code": [("OC9999", [3, 17])]})
        self.assertEqual(report, "  oc_code=OC9999: ids 3, 17")


class MigrationPreCheckTests(TestCase):
    def _run_check(self):
        _migration.check_no_duplicate_codes(
            None, SimpleNamespace(connection=connection)
        )

    def test_passes_on_a_clean_database(self):
        _cache(gc_code="GC1111")
        self._run_check()  # must not raise

    def test_refuses_and_names_the_duplicate(self):
        constraint = _drop_constraint("uniq_live_gc_code")
        try:
            first = _cache(gc_code="GC7777")
            second = _cache(gc_code="GC7777")
            with self.assertRaises(RuntimeError) as ctx:
                self._run_check()
        finally:
            Geocache.objects.filter(gc_code="GC7777").exclude(pk=first.pk).delete()
            _restore_constraint(constraint)

        message = str(ctx.exception)
        self.assertIn("gc_code=GC7777", message)
        self.assertIn(str(first.pk), message)
        self.assertIn(str(second.pk), message)
        self.assertIn("find_duplicate_codes", message)
        self.assertIn("Find duplicates", message)  # the Tools page it points at


class MergeOrderTests(TestCase):
    """The losing row must be gone before its code lands on the winner."""

    def test_merge_into_moves_the_oc_code_of_a_live_record(self):
        dest = _cache(gc_code="GC1111", name="GC record")
        source = _cache(oc_code="OC1111", name="OC record")
        source_pk = source.pk

        _merge_into(source=source, dest=dest, oc_code="OC1111")

        dest.refresh_from_db()
        self.assertEqual(dest.oc_code, "OC1111")
        self.assertFalse(Geocache.all_objects.filter(pk=source_pk).exists())

    def test_merge_into_keeps_the_related_rows(self):
        dest = _cache(gc_code="GC1111")
        source = _cache(oc_code="OC1111", county="Tübingen")
        Log.objects.create(
            geocache=source, log_type=LogType.FOUND, user_name="me",
            logged_date="2026-01-01", text="found", source="oc_de",
        )

        _merge_into(source=source, dest=dest, oc_code="OC1111")

        dest.refresh_from_db()
        self.assertEqual(dest.logs.count(), 1)
        self.assertEqual(dest.county, "Tübingen")

    def test_merge_duplicate_under_the_constraint(self):
        gc = _cache(gc_code="GC1111")
        oc = _cache(oc_code="OC1111")

        merge_duplicate(gc.pk, oc.pk)

        gc.refresh_from_db()
        self.assertEqual(gc.oc_code, "OC1111")
        self.assertFalse(Geocache.all_objects.filter(pk=oc.pk).exists())


class SaveGeocacheLookupTests(TestCase):
    """save_geocache still finds an existing record by each of the codes."""

    def _fields(self, **extra):
        fields = {
            "name": "Updated", "cache_type": CacheType.TRADITIONAL,
            "latitude": 48.5, "longitude": 9.1,
        }
        fields.update(extra)
        return fields

    def test_updates_by_gc_code(self):
        cache = _cache(gc_code="GC1111", name="Original")
        result = save_geocache(gc_code="GC1111", fields=self._fields())
        self.assertFalse(result.created)
        self.assertEqual(result.geocache.pk, cache.pk)
        self.assertEqual(result.geocache.name, "Updated")

    def test_updates_by_oc_code(self):
        cache = _cache(oc_code="OC1111", name="Original")
        result = save_geocache(oc_code="OC1111", fields=self._fields())
        self.assertFalse(result.created)
        self.assertEqual(result.geocache.pk, cache.pk)

    def test_updates_by_al_code(self):
        cache = _cache(al_code="LC1111", name="Original", cache_type=CacheType.LAB)
        result = save_geocache(al_code="LC1111", fields=self._fields(cache_type=CacheType.LAB))
        self.assertFalse(result.created)
        self.assertEqual(result.geocache.pk, cache.pk)

    def test_creates_when_no_record_matches(self):
        result = save_geocache(gc_code="GC5555", fields=self._fields())
        self.assertTrue(result.created)

    def test_a_trashed_record_is_replaced_not_reused(self):
        trashed = _cache(gc_code="GC1111")
        trash_cache(trashed)
        result = save_geocache(gc_code="GC1111", fields=self._fields())
        self.assertTrue(result.created)
        self.assertNotEqual(result.geocache.pk, trashed.pk)

    def test_a_cross_referenced_oc_code_is_written_when_free(self):
        cache = _cache(gc_code="GC1111")
        result = save_geocache(
            gc_code="GC1111", oc_code="OC1111",
            fields=self._fields(gc_code="GC1111", oc_code="OC1111"),
        )
        self.assertEqual(result.geocache.pk, cache.pk)
        cache.refresh_from_db()
        self.assertEqual(cache.oc_code, "OC1111")

    def test_a_cross_referenced_oc_record_is_folded_in(self):
        gc = _cache(gc_code="GC1111")
        oc = _cache(oc_code="OC1111", county="Tübingen")
        Log.objects.create(
            geocache=oc, log_type=LogType.FOUND, user_name="me",
            logged_date="2026-01-01", text="found", source="oc_de",
        )

        result = save_geocache(
            gc_code="GC1111", oc_code="OC1111",
            fields=self._fields(gc_code="GC1111", oc_code="OC1111"),
            update_source="oc",
        )

        self.assertEqual(result.geocache.pk, gc.pk)
        self.assertEqual(result.merged_from, "OC1111")
        gc.refresh_from_db()
        self.assertEqual(gc.oc_code, "OC1111")
        self.assertEqual(gc.logs.count(), 1)
        self.assertFalse(Geocache.all_objects.filter(pk=oc.pk).exists())

    def test_a_conflicting_al_code_is_left_alone(self):
        holder = _cache(al_code="LC1111", cache_type=CacheType.LAB)
        stage = _cache(al_code="LC2222", cache_type=CacheType.LAB)

        save_geocache(
            al_code="LC2222",
            fields=self._fields(al_code="LC1111", cache_type=CacheType.LAB),
        )

        stage.refresh_from_db()
        holder.refresh_from_db()
        self.assertEqual(stage.al_code, "LC2222")
        self.assertEqual(holder.al_code, "LC1111")


class RestoreCollisionTests(TestCase):
    def test_restore_is_refused_when_the_code_is_taken(self):
        trashed = _cache(gc_code="GC1111")
        trash_cache(trashed)
        _cache(gc_code="GC1111")

        with self.assertRaises(RestoreConflict) as ctx:
            restore_cache(trashed)

        self.assertEqual([code for code, _pk in ctx.exception.conflicts], ["GC1111"])
        trashed.refresh_from_db()
        self.assertIsNotNone(trashed.deleted_at)

    def test_restore_is_refused_on_a_secondary_code(self):
        trashed = _cache(gc_code="GC1111", oc_code="OC1111")
        trash_cache(trashed)
        _cache(oc_code="OC1111")

        with self.assertRaises(RestoreConflict):
            restore_cache(trashed)

    def test_restore_succeeds_when_no_code_is_taken(self):
        trashed = _cache(gc_code="GC1111", oc_code="OC1111")
        trash_cache(trashed)

        restore_cache(trashed)

        trashed.refresh_from_db()
        self.assertIsNone(trashed.deleted_at)

    def test_restore_is_refused_when_a_stage_code_is_taken(self):
        adv = Adventure.objects.create(
            code="LC1TEST", title="Adventure", owner="me",
            latitude=52.5, longitude=13.4,
        )
        parent = _cache(al_code="LC1TEST", cache_type=CacheType.LAB, adventure=adv, is_al_parent=True)
        stage = _cache(al_code="LC1TEST-1", cache_type=CacheType.LAB, adventure=adv)
        ALStageDetail.objects.create(geocache=stage, stage_number=1)
        trash_cache(parent)
        _cache(al_code="LC1TEST-1", cache_type=CacheType.LAB)

        with self.assertRaises(RestoreConflict) as ctx:
            restore_cache(parent)

        self.assertEqual([code for code, _pk in ctx.exception.conflicts], ["LC1TEST-1"])
        parent.refresh_from_db()
        self.assertIsNotNone(parent.deleted_at)

    def test_restore_view_reports_the_conflict_and_links_to_the_tool(self):
        trashed = _cache(gc_code="GC1111", name="Trashed")
        trash_cache(trashed)
        _cache(gc_code="GC1111", name="Fresh")

        response = self.client.post(
            reverse("geocaches:trash_restore", args=[trashed.pk]), follow=True
        )

        texts = [str(m) for m in response.context["messages"]]
        self.assertEqual(len(texts), 1)
        self.assertIn("GC1111", texts[0])
        self.assertIn(reverse("geocaches:tools_duplicate_caches"), texts[0])
        trashed.refresh_from_db()
        self.assertIsNotNone(trashed.deleted_at)

    def test_restore_view_restores_when_there_is_no_conflict(self):
        trashed = _cache(gc_code="GC1111")
        trash_cache(trashed)

        self.client.post(
            reverse("geocaches:trash_restore", args=[trashed.pk]), follow=True
        )

        trashed.refresh_from_db()
        self.assertIsNone(trashed.deleted_at)

    def test_trash_cache_leaves_a_timestamp(self):
        cache = _cache(gc_code="GC1111")
        stamp = trash_cache(cache)
        self.assertLessEqual(stamp, now())
