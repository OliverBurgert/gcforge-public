"""Process-wide write serializer for SQLite.

Background tasks run on a small ThreadPoolExecutor and a few ad-hoc threads.
SQLite's WAL mode allows many concurrent readers, but only one writer at a
time.  When two writers contend for the database the second one waits up to
``busy_timeout`` ms before raising ``OperationalError: database is locked``.

A 30 s busy_timeout is normally enough, but a long-running PQ import (which
opens many short transactions in quick succession) plus a concurrent enrichment
or update task can exhaust it.  To reduce that contention we funnel the
high-volume / background write paths through a single re-entrant Python lock.

What this guarantees — and what it doesn't:

  - Writers that *take this lock* are serialized against each other in-process,
    so they never collide on the SQLite writer.  The importers, enrichment,
    dedup, save_alc, the delete task, and the image cache all go through it.
  - It is NOT a global mutex over every write.  Foreground single-statement
    saves in ordinary views deliberately skip the lock (single-user,
    low-contention) and rely on WAL + busy_timeout.  A "database is locked"
    error is therefore still *possible* in theory if a lock-taking writer and a
    bare foreground save contend for longer than busy_timeout — it is bounded,
    not eliminated.

Because of that, the key invariant is: KEEP TRANSACTIONS SHORT.  Wrap a single
statement or a small batch, never a long loop over many rows in one
``db_write_atomic`` — holding the lock for a long time is exactly the stall we
are trying to avoid.

Usage
-----
For most call sites use ``db_write_atomic()`` which both takes the lock and
opens a Django ``transaction.atomic()`` block::

    from geocaches.db_lock import db_write_atomic
    with db_write_atomic():
        save_geocache(...)

For code that performs writes without an explicit atomic block (e.g. a single
``model.save()``), use ``db_write()`` to take just the lock::

    with db_write():
        cache.save(update_fields=[...])

The lock is re-entrant: nested ``with db_write*():`` blocks on the same thread
are no-ops past the outermost one, so code can defensively wrap helpers without
worrying about deadlocking with a caller that already holds the lock.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

_db_write_lock = threading.RLock()


@contextmanager
def db_write():
    """Acquire the global write lock for the duration of the block."""
    with _db_write_lock:
        yield


@contextmanager
def db_write_atomic(using: str | None = None):
    """Acquire the global write lock and open a Django atomic transaction."""
    from django.db import transaction
    with _db_write_lock:
        with transaction.atomic(using=using):
            yield
