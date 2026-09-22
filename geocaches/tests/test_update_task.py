"""Tests for geocaches/tasks/update.py helpers."""
from django.test import TestCase

from geocaches.tasks.update import _clone_save_payload


class TestCloneSavePayload(TestCase):
    def test_returns_shallow_clone(self):
        data = {"gc_code": "GC123", "fields": {"name": "Test", "difficulty": 2.0}}
        result = _clone_save_payload(data)
        self.assertEqual(result["gc_code"], "GC123")
        self.assertEqual(result["fields"]["name"], "Test")

    def test_mutations_do_not_affect_original(self):
        data = {"gc_code": "GC123", "fields": {"name": "Test"}}
        result = _clone_save_payload(data)
        result["gc_code"] = "GC999"
        result["fields"]["name"] = "Changed"
        self.assertEqual(data["gc_code"], "GC123")
        self.assertEqual(data["fields"]["name"], "Test")
