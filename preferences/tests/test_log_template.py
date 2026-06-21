"""Tests for the LogTemplate model."""
from django.test import TestCase

from preferences.models import LogTemplate


class TestLogTemplateDefault(TestCase):
    def test_save_clears_other_defaults_in_same_scope(self):
        a = LogTemplate.objects.create(name="A", body="a", scope="Found it", is_default=True)
        b = LogTemplate.objects.create(name="B", body="b", scope="Found it", is_default=True)
        a.refresh_from_db()
        self.assertFalse(a.is_default)
        self.assertTrue(b.is_default)

    def test_default_isolated_per_scope(self):
        # A default in scope=Found it must not affect a default in scope=any.
        LogTemplate.objects.create(name="found-default", body="x",
                                   scope="Found it", is_default=True)
        any_default = LogTemplate.objects.create(
            name="any-default", body="y", scope="any", is_default=True,
        )
        self.assertTrue(any_default.is_default)
        # Original scope=Found it default is unaffected.
        found_default = LogTemplate.objects.get(name="found-default")
        self.assertTrue(found_default.is_default)

    def test_str_returns_name(self):
        t = LogTemplate.objects.create(name="Quick TFTC", body="TFTC")
        self.assertEqual(str(t), "Quick TFTC")
