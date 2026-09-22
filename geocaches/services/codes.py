"""External cache codes and their uniqueness.

A ``Geocache`` carries up to three external codes — ``gc_code``, ``oc_code``
and ``al_code`` — and each of them may identify at most one record that is not
in the Trash.  The partial unique constraints on the model
(``uniq_live_gc_code`` and friends) enforce that; soft-deleted rows keep their
codes so a re-import can build a fresh record next to them.

``find_duplicate_live_codes()`` runs as raw SQL against a given connection so
that the pre-check in migration 0044, the ``find_duplicate_codes`` management
command and the tests all share one implementation.
"""

CODE_FIELDS = ("gc_code", "oc_code", "al_code")


def find_duplicate_live_codes(connection=None):
    """Return ``{field: [(code, [pk, …]), …]}`` for every duplicated live code.

    Fields without duplicates are absent, so an empty dict means "clean".
    """
    if connection is None:
        from django.db import connection as default_connection

        connection = default_connection

    duplicates: dict[str, list[tuple[str, list[int]]]] = {}
    with connection.cursor() as cursor:
        for field in CODE_FIELDS:
            cursor.execute(
                f"SELECT {field}, GROUP_CONCAT(id) FROM geocaches_geocache "
                f"WHERE deleted_at IS NULL AND {field} <> '' "
                f"GROUP BY {field} HAVING COUNT(*) > 1 ORDER BY {field}"
            )
            rows = [
                (code, sorted(int(pk) for pk in pks.split(",")))
                for code, pks in cursor.fetchall()
            ]
            if rows:
                duplicates[field] = rows
    return duplicates


def format_duplicate_report(duplicates):
    """Render ``find_duplicate_live_codes()`` output as one indented line per code."""
    lines = []
    for field in CODE_FIELDS:
        for code, pks in duplicates.get(field, []):
            ids = ", ".join(str(pk) for pk in pks)
            lines.append(f"  {field}={code}: ids {ids}")
    return "\n".join(lines)


def live_code_conflict(field, value, exclude_pk=None):
    """The cache outside the Trash that already holds *value* in *field*, or None."""
    from geocaches.models import Geocache

    if not value:
        return None
    qs = Geocache.objects.filter(**{field: value})
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()
