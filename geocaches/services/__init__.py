"""Service layer for the geocaches app.

This package root is re-exports only — every name below is defined in a
sibling module and imported here so that the historical
``from geocaches.services import <name>`` call sites (and the tests that patch
``geocaches.services.<name>``) keep working:

  * ``save``           — save_geocache and the single-cache persistence funnel
  * ``dedup``          — merging two records, plus the duplicate scan
  * ``fusion``         — dual-listing decisions (GC/OC pairs)
  * ``importing``      — import_and_enrich / export_caches
  * ``gsak_locations`` — GSAK saved locations → ReferencePoint
  * ``tags``           — manage_tags

New code should import from the sibling module directly.
"""
from .dedup import _merge_into, find_potential_duplicates, merge_duplicate
from .fusion import _record_auto_link, set_fusion_decision
from .gsak_locations import import_gsak_location_candidates, parse_and_import_gsak_locations
from .importing import _start_auto_enrich, export_caches, import_and_enrich
from .save import (
    _GC_OWNED_FIELDS,
    _apply_extra_codes,
    _find_proximity_match,
    _save_geocache_inner,
    SaveResult,
    save_geocache,
)
from .tags import manage_tags

__all__ = [
    "SaveResult",
    "save_geocache",
    "find_potential_duplicates",
    "merge_duplicate",
    "set_fusion_decision",
    "import_and_enrich",
    "export_caches",
    "parse_and_import_gsak_locations",
    "import_gsak_location_candidates",
    "manage_tags",
]
