"""
Import-level smoke test for the geocaches.views package.

Catches broken imports (e.g. wrong-depth relative imports inside
geocaches/views/*.py) without spinning up Django's test client or DB —
fast feedback for the kind of bug a simple HTTP smoke test would also
catch, but cheaper to run during refactors.
"""

import importlib
import pkgutil

from django.test import SimpleTestCase


class ViewsPackageImportsCleanly(SimpleTestCase):
    def test_every_submodule_imports(self):
        import geocaches.views as views_pkg

        failures = []
        for mod_info in pkgutil.walk_packages(views_pkg.__path__, prefix=f"{views_pkg.__name__}."):
            try:
                importlib.import_module(mod_info.name)
            except Exception as exc:  # noqa: BLE001 — surface the root cause
                failures.append(f"{mod_info.name}: {exc!r}")

        self.assertFalse(
            failures,
            msg="Broken imports in geocaches.views:\n  " + "\n  ".join(failures),
        )
