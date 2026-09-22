"""Verify that the geocaches.models package re-exports every public name."""

from django.test import TestCase


class ModelsPackageImportTest(TestCase):
    def test_all_model_classes_importable(self):
        from geocaches.models import (
            Adventure,
            Attribute,
            CacheFusionRecord,
            CacheMapState,
            CacheSize,
            CacheStatus,
            CacheType,
            CorrectedCoordinates,
            CustomField,
            DistanceCache,
            Geocache,
            Image,
            Log,
            LogType,
            Note,
            NoteFormat,
            NoteType,
            OCExtension,
            SavedAreaFilter,
            SavedFilter,
            SavedWhereClause,
            SyncQuota,
            SyncState,
            Tag,
            Waypoint,
            WaypointType,
        )
        classes = [
            Adventure, Attribute, CacheFusionRecord, CacheMapState,
            CacheSize, CacheStatus, CacheType, CorrectedCoordinates,
            CustomField, DistanceCache, Geocache, Image, Log, LogType,
            Note, NoteFormat, NoteType, OCExtension, SavedAreaFilter,
            SavedFilter, SavedWhereClause, SyncQuota, SyncState, Tag,
            Waypoint, WaypointType,
        ]
        for cls in classes:
            self.assertTrue(hasattr(cls, '__name__'), f"{cls} not importable")

    def test_all_matches_actual_exports(self):
        import geocaches.models as m
        for name in m.__all__:
            self.assertTrue(
                hasattr(m, name),
                f"__all__ lists {name!r} but it is not in the namespace",
            )
