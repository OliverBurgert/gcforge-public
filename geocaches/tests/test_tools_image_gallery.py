"""Tests for the Image Gallery tool.

Covers:
- collect_cache_images: each source, exclusion enforcement
- config GET view
- generate POST view → task submitted
- gallery view (running vs completed states)
- each exporter (HTML zip, ODF)
"""
import json
import time
from datetime import date
from pathlib import Path

from django.test import TestCase, RequestFactory, override_settings

from geocaches.models import (
    CacheSize, CacheStatus, CacheType, Geocache, Image, Log, Note, NoteType,
)

D = date(2024, 6, 1)


def _make_cache(gc_code="GC12345", **kwargs):
    defaults = dict(
        name=f"Cache {gc_code}",
        cache_type=CacheType.TRADITIONAL,
        size=CacheSize.SMALL,
        status=CacheStatus.ACTIVE,
        latitude=48.0,
        longitude=9.0,
        difficulty=2.0,
        terrain=2.0,
        hidden_date=D,
        owner="testowner",
        primary_source="gc",
    )
    defaults.update(kwargs)
    return Geocache.objects.create(gc_code=gc_code, **defaults)


class CollectCacheImagesTests(TestCase):
    def setUp(self):
        self.cache = _make_cache()

    def test_background_image_included(self):
        self.cache.background_image_url = "https://img.geocaching.com/bg.jpg"
        self.cache.save()
        from geocaches.services.gallery import collect_cache_images
        items = collect_cache_images(self.cache, {})
        urls = [i["url"] for i in items]
        self.assertIn("https://img.geocaching.com/bg.jpg", urls)

    def test_image_model_rows_included(self):
        Image.objects.create(geocache=self.cache, url="https://img.geocaching.com/pic.jpg", name="A photo")
        from geocaches.services.gallery import collect_cache_images
        items = collect_cache_images(self.cache, {})
        urls = [i["url"] for i in items]
        self.assertIn("https://img.geocaching.com/pic.jpg", urls)

    def test_inline_description_images_included(self):
        self.cache.long_description = '<p><img src="https://example.com/desc.jpg"></p>'
        self.cache.save()
        from geocaches.services.gallery import collect_cache_images
        items = collect_cache_images(self.cache, {})
        urls = [i["url"] for i in items]
        self.assertIn("https://example.com/desc.jpg", urls)

    def test_log_images_included_when_enabled(self):
        Log.objects.create(
            geocache=self.cache, log_type="Found it", user_name="finder",
            logged_date=D, text='<img src="https://example.com/log.jpg">',
        )
        from geocaches.services.gallery import collect_cache_images
        items = collect_cache_images(self.cache, {"include_log_images": True})
        urls = [i["url"] for i in items]
        self.assertIn("https://example.com/log.jpg", urls)

    def test_log_images_excluded_when_disabled(self):
        Log.objects.create(
            geocache=self.cache, log_type="Found it", user_name="finder",
            logged_date=D, text='<img src="https://example.com/log2.jpg">',
        )
        from geocaches.services.gallery import collect_cache_images
        items = collect_cache_images(self.cache, {"include_log_images": False})
        urls = [i["url"] for i in items]
        self.assertNotIn("https://example.com/log2.jpg", urls)

    def test_note_images_included_when_user_notes_enabled(self):
        Note.objects.create(
            geocache=self.cache, note_type=NoteType.NOTE,
            body='<img src="https://example.com/note.jpg">',
        )
        from geocaches.services.gallery import collect_cache_images
        items = collect_cache_images(self.cache, {"user_notes": True})
        urls = [i["url"] for i in items]
        self.assertIn("https://example.com/note.jpg", urls)

    def test_note_images_excluded_when_user_notes_disabled(self):
        Note.objects.create(
            geocache=self.cache, note_type=NoteType.NOTE,
            body='<img src="https://example.com/note2.jpg">',
        )
        from geocaches.services.gallery import collect_cache_images
        items = collect_cache_images(self.cache, {"user_notes": False})
        urls = [i["url"] for i in items]
        self.assertNotIn("https://example.com/note2.jpg", urls)

    def test_deduplication_within_cache(self):
        from django.db import IntegrityError, transaction
        url = "https://img.geocaching.com/dup.jpg"
        Image.objects.create(geocache=self.cache, url=url, name="Pic 1")
        # The DB constraint now prevents duplicate (geocache, url) rows.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Image.objects.create(geocache=self.cache, url=url, name="Pic 2")
        from geocaches.services.gallery import collect_cache_images
        items = collect_cache_images(self.cache, {})
        found = [i for i in items if i["url"] == url]
        self.assertEqual(len(found), 1)

    def test_collect_cache_images_drops_excluded_urls(self):
        excl_url = "https://project-gc.com/badge.png"
        ok_url = "https://img.geocaching.com/ok.jpg"
        Image.objects.create(geocache=self.cache, url=excl_url, name="Badge")
        Image.objects.create(geocache=self.cache, url=ok_url, name="Photo")
        from geocaches.services.gallery import collect_cache_images
        items = collect_cache_images(self.cache, {})
        urls = [i["url"] for i in items]
        self.assertNotIn(excl_url, urls)
        self.assertIn(ok_url, urls)

    def test_collect_cache_images_drops_custom_exclusion(self):
        from preferences.models import UserPreference
        UserPreference.set("image_cache.exclusions", "example.com/badge")
        url = "https://example.com/badge.gif"
        ok_url = "https://img.geocaching.com/safe.jpg"
        Image.objects.create(geocache=self.cache, url=url, name="Badge")
        Image.objects.create(geocache=self.cache, url=ok_url, name="Safe")
        from geocaches.services.gallery import collect_cache_images
        items = collect_cache_images(self.cache, {})
        urls = [i["url"] for i in items]
        self.assertNotIn(url, urls)
        self.assertIn(ok_url, urls)


class GalleryConfigViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_config_get_returns_200(self):
        _make_cache()
        from geocaches.views.tools_image_gallery import tools_image_gallery_config
        req = self.factory.get("/tools/image-gallery/")
        resp = tools_image_gallery_config(req)
        self.assertEqual(resp.status_code, 200)

    def test_config_shows_cache_count(self):
        _make_cache("GC11111")
        _make_cache("GC22222")
        from geocaches.views.tools_image_gallery import tools_image_gallery_config
        req = self.factory.get("/tools/image-gallery/")
        resp = tools_image_gallery_config(req)
        self.assertContains(resp, "2")


class GalleryGenerateViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_generate_post_redirects(self):
        from geocaches.views.tools_image_gallery import tools_image_gallery_generate
        req = self.factory.post("/tools/image-gallery/generate/", {
            "query_string": "",
            "user_notes": "on",
            "include_log_images": "on",
            "image_size": "page_width",
            "max_width_px": "800",
        })
        resp = tools_image_gallery_generate(req)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/tools/image-gallery/", resp["Location"])

    def test_generate_get_redirects_to_config(self):
        from geocaches.views.tools_image_gallery import tools_image_gallery_generate
        req = self.factory.get("/tools/image-gallery/generate/")
        resp = tools_image_gallery_generate(req)
        self.assertEqual(resp.status_code, 302)


class GalleryViewTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _run_task_sync(self, cache):
        """Submit a gallery task and wait for it to finish (up to 5 s)."""
        from geocaches.tasks.image_gallery import start_gallery_build
        from geocaches.tasks import get_task
        task_id = start_gallery_build("", {"user_notes": True, "include_log_images": True})
        for _ in range(50):
            info = get_task(task_id)
            if info and info["state"] in ("completed", "failed", "cancelled"):
                break
            time.sleep(0.1)
        return task_id

    def test_view_shows_running_state(self):
        from geocaches.tasks.runner import _lock, _registry, TaskInfo, TaskState
        from geocaches.views.tools_image_gallery import tools_image_gallery_view

        # Register a running task directly (no background thread) — under sync
        # task mode a real submit_task runs to completion inline, so we can't
        # observe the RUNNING state via the executor.
        info = TaskInfo(id="fakerunning01", name="Image gallery", state=TaskState.RUNNING)
        with _lock:
            _registry[info.id] = info
        req = self.factory.get(f"/tools/image-gallery/{info.id}/")
        resp = tools_image_gallery_view(req, info.id)
        self.assertEqual(resp.status_code, 200)

    def test_view_renders_gallery_after_completion(self):
        _make_cache("GC99999")
        task_id = self._run_task_sync(None)
        from geocaches.views.tools_image_gallery import tools_image_gallery_view
        req = self.factory.get(f"/tools/image-gallery/{task_id}/")
        resp = tools_image_gallery_view(req, task_id)
        self.assertEqual(resp.status_code, 200)

    def test_view_unknown_task_shows_error(self):
        from geocaches.views.tools_image_gallery import tools_image_gallery_view
        req = self.factory.get("/tools/image-gallery/doesnotexist/")
        resp = tools_image_gallery_view(req, "doesnotexist")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "not found")


class GalleryExportHtmlTests(TestCase):
    def _make_run(self, tmp_path: Path) -> str:
        cache = _make_cache("GC77777")
        run_dir = tmp_path / "gallery_runs"
        run_dir.mkdir(parents=True, exist_ok=True)
        task_id = "testhtmlexport"
        (run_dir / f"{task_id}.json").write_text(
            json.dumps({"query_string": "", "options": {}}), encoding="utf-8"
        )
        return task_id

    def test_html_export_returns_zip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with override_settings(DATA_DIR=tmp):
                task_id = self._make_run(tmp)
                from geocaches.views.tools_image_gallery import tools_image_gallery_export_html
                factory = RequestFactory()
                req = factory.get(f"/tools/image-gallery/{task_id}/export/html/")
                resp = tools_image_gallery_export_html(req, task_id)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp["Content-Type"], "application/zip")
                self.assertGreater(len(resp.content), 0)

    def test_html_export_zip_contains_index(self):
        import io
        import zipfile
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with override_settings(DATA_DIR=tmp):
                task_id = self._make_run(tmp)
                from geocaches.views.tools_image_gallery import tools_image_gallery_export_html
                factory = RequestFactory()
                req = factory.get(f"/tools/image-gallery/{task_id}/export/html/")
                resp = tools_image_gallery_export_html(req, task_id)
                zf = zipfile.ZipFile(io.BytesIO(resp.content))
                self.assertIn("index.html", zf.namelist())


class GalleryExportOdfTests(TestCase):
    def _make_run(self, tmp_path: Path) -> str:
        _make_cache("GC55555")
        run_dir = tmp_path / "gallery_runs"
        run_dir.mkdir(parents=True, exist_ok=True)
        task_id = "testodfexport"
        (run_dir / f"{task_id}.json").write_text(
            json.dumps({"query_string": "", "options": {}}), encoding="utf-8"
        )
        return task_id

    def test_odf_export_returns_odt(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with override_settings(DATA_DIR=tmp):
                task_id = self._make_run(tmp)
                from geocaches.views.tools_image_gallery import tools_image_gallery_export_odf
                factory = RequestFactory()
                req = factory.get(f"/tools/image-gallery/{task_id}/export/odf/")
                resp = tools_image_gallery_export_odf(req, task_id)
                self.assertEqual(resp.status_code, 200)
                self.assertIn("oasis", resp["Content-Type"])
                self.assertGreater(len(resp.content), 0)


class GalleryOdfHtmlStrippingTests(TestCase):
    def _odt_content_xml(self, data: bytes) -> str:
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return zf.read("content.xml").decode("utf-8")

    def test_html_tags_not_visible_in_odf_descriptions(self):
        cache = _make_cache(
            "GC88888",
            short_description="<p>First paragraph.</p><p>Second <em>paragraph</em>.</p>",
            long_description="<div>Long block.<br>With a line break.</div>",
        )
        from geocaches.exporters.gallery_odf import build_odf
        data = build_odf([cache], {
            "include_short_description": True,
            "include_long_description": True,
        })
        xml = self._odt_content_xml(data)
        self.assertNotIn("&lt;p&gt;", xml)
        self.assertNotIn("&lt;br&gt;", xml)
        self.assertNotIn("&lt;div&gt;", xml)
        self.assertNotIn("&lt;em&gt;", xml)
        self.assertIn("First paragraph.", xml)
        self.assertIn("Second paragraph.", xml)
        self.assertIn("Long block.", xml)
        self.assertIn("With a line break.", xml)

    def test_html_notes_stripped_in_odf(self):
        from geocaches.models import Note, NoteFormat, NoteType
        cache = _make_cache("GC88889")
        Note.objects.create(
            geocache=cache, note_type=NoteType.NOTE, format=NoteFormat.HTML,
            body="<p>Hidden tags should not appear.</p>",
        )
        from geocaches.exporters.gallery_odf import build_odf
        data = build_odf([cache], {"user_notes": True})
        xml = self._odt_content_xml(data)
        self.assertNotIn("&lt;p&gt;", xml)
        self.assertIn("Hidden tags should not appear.", xml)


class GalleryPageRenderTests(TestCase):
    def _make_run_and_get(self, options: dict, cache_kwargs: dict | None = None):
        import tempfile
        from geocaches.views.tools_image_gallery import tools_image_gallery_view

        cache_kwargs = cache_kwargs or {}
        cache = _make_cache("GC10001", **cache_kwargs)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with override_settings(DATA_DIR=tmp):
                run_dir = tmp / "gallery_runs"
                run_dir.mkdir(parents=True, exist_ok=True)
                task_id = "viewtest"
                (run_dir / f"{task_id}.json").write_text(
                    json.dumps({"query_string": "", "options": options}), encoding="utf-8"
                )
                # Fake a completed TaskInfo so the view renders the gallery branch.
                from geocaches.tasks import submit_task
                tid = submit_task("Image gallery", lambda *, task_info: None)
                # Wait briefly for completion
                from geocaches.tasks import get_task
                for _ in range(50):
                    info = get_task(tid)
                    if info and info["state"] in ("completed", "failed", "cancelled"):
                        break
                    time.sleep(0.05)
                # Write the run file under the real task id
                (run_dir / f"{tid}.json").write_text(
                    json.dumps({"query_string": "", "options": options}), encoding="utf-8"
                )
                factory = RequestFactory()
                req = factory.get(f"/tools/image-gallery/{tid}/")
                return tools_image_gallery_view(req, tid), cache

    def test_short_description_renders_when_enabled(self):
        resp, _ = self._make_run_and_get(
            {"include_short_description": True},
            {"short_description": "Hello short."},
        )
        self.assertContains(resp, "Hello short.")

    def test_short_description_hidden_when_disabled(self):
        resp, _ = self._make_run_and_get(
            {"include_short_description": False},
            {"short_description": "Hello short."},
        )
        self.assertNotContains(resp, "Hello short.")

    def test_long_description_renders_when_enabled(self):
        resp, _ = self._make_run_and_get(
            {"include_long_description": True},
            {"long_description": "Long story here."},
        )
        self.assertContains(resp, "Long story here.")

    def test_notes_render_when_enabled(self):
        from geocaches.models import Note, NoteType
        resp, cache = self._make_run_and_get({"user_notes": True})
        Note.objects.create(geocache=cache, note_type=NoteType.NOTE, body="My note body.")
        # Re-fetch via a second view call (the previous response was rendered before the note existed)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with override_settings(DATA_DIR=tmp):
                from geocaches.tasks import submit_task, get_task
                tid = submit_task("Image gallery", lambda *, task_info: None)
                for _ in range(50):
                    info = get_task(tid)
                    if info and info["state"] in ("completed", "failed", "cancelled"):
                        break
                    time.sleep(0.05)
                run_dir = tmp / "gallery_runs"
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / f"{tid}.json").write_text(
                    json.dumps({"query_string": "", "options": {"user_notes": True}}),
                    encoding="utf-8",
                )
                factory = RequestFactory()
                req = factory.get(f"/tools/image-gallery/{tid}/")
                from geocaches.views.tools_image_gallery import tools_image_gallery_view
                resp2 = tools_image_gallery_view(req, tid)
                self.assertContains(resp2, "My note body.")

    def test_map_container_rendered_when_enabled(self):
        resp, _ = self._make_run_and_get({"include_map": True})
        self.assertContains(resp, "data-gallery-map")

    def test_map_container_absent_when_disabled(self):
        resp, _ = self._make_run_and_get({"include_map": False})
        self.assertNotContains(resp, "data-gallery-map")
