"""Drop ``SavedFilter.params`` — v2 phase 4d-ii destructive cleanup.

After this migration, every ``SavedFilter`` carries only ``tree`` (the v2
filter-expression payload).  Migration 0023 added ``tree`` and backfilled
it from ``params`` via ``legacy_params_to_tree`` — but that shim was
incomplete at the time and skipped exotic ``flag=alc_*`` values.  Here
we:

  1. Recompute ``tree`` for every row using the *now-complete*
     ``legacy_params_to_tree``.
  2. For the two built-in filters (``ALC stages loggable at center point``,
     ``ALC started but incomplete``), write canonical tree payloads
     explicitly — their ``params`` carried exotic ALC flag values that the
     shim still doesn't translate to tree conditions.
  3. Drop the ``params`` column.
  4. Make ``tree`` ``NOT NULL`` (every row now has one).
"""

from django.db import migrations, models


# Hand-curated trees for the built-in filters.  ``loggable_from_ref``
# resolves the active ReferencePoint inside the compiler; ``in_progress``
# uses ``apply_alc_in_progress_filter`` via a pk-in subquery.
_BUILTIN_TREES = {
    "ALC stages loggable at center point": {
        "g": "and",
        "c": [{"f": "alc", "op": "loggable_from_ref", "v": None}],
    },
    "ALC started but incomplete": {
        "g": "and",
        "c": [{"f": "alc", "op": "in_progress", "v": True}],
    },
    "Not updated today": {
        "g": "and",
        "c": [{"f": "updated_at", "op": "not_today", "v": None}],
    },
    # To-go: NOT(Mystery|Math/Physics without corrected coords) via De Morgan
    "To-go": {
        "g": "and",
        "c": [
            {"f": "found", "op": "is_false", "v": True},
            {"f": "completed", "op": "is_false", "v": True},
            {"f": "status", "op": "in", "v": ["Active"]},
            {"g": "or", "c": [
                {"f": "cache_type", "op": "not_in", "v": ["Mystery", "Math/Physics"]},
                {"f": "has_corrected_coordinates", "op": "is_true", "v": True},
            ]},
        ],
    },
    "FTF possible": {
        "g": "and",
        "c": [{"f": "ftf_possible", "op": "is_true", "v": None}],
    },
    "Fused (GC+OC)": {
        "g": "and",
        "c": [
            {"f": "gc_code", "op": "is_not_empty", "v": None},
            {"f": "oc_code", "op": "is_not_empty", "v": None},
        ],
    },
}


def _migrate_savedfilters_to_tree(apps, schema_editor):
    SavedFilter = apps.get_model("geocaches", "SavedFilter")

    # Import the shim lazily — it lives in app code, not in migration state.
    from geocaches.filter_expr import legacy_params_to_tree

    for sf in SavedFilter.objects.all():
        if sf.name in _BUILTIN_TREES:
            sf.tree = _BUILTIN_TREES[sf.name]
        else:
            params = sf.params or {}
            try:
                sf.tree = legacy_params_to_tree(params).to_dict()
            except Exception:
                # Preserve any existing tree; otherwise default to an empty
                # AND so the column can be made NOT NULL.
                sf.tree = sf.tree or {"g": "and", "c": []}
        sf.save(update_fields=["tree"])


def _noop_reverse(apps, schema_editor):
    """No reverse — ``params`` is gone after this migration; recovering it
    would require ``tree_to_legacy_params`` which doesn't exist."""


class Migration(migrations.Migration):

    dependencies = [
        ("geocaches", "0023_savedfilter_tree"),
    ]

    operations = [
        # 1) Backfill every row with a complete tree.
        migrations.RunPython(_migrate_savedfilters_to_tree, _noop_reverse),
        # 2) tree is now always present → make it NOT NULL.
        migrations.AlterField(
            model_name="savedfilter",
            name="tree",
            field=models.JSONField(),
        ),
        # 3) Drop the legacy params column.
        migrations.RemoveField(
            model_name="savedfilter",
            name="params",
        ),
    ]
