# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The SQLite engines a test opens are released when it ends.

Test infrastructure only. ``chaoscypher_core.adapters.sqlite.engine.get_engine`` caches one
SQLAlchemy engine per database path for the life of the process, and ``Engine.close()`` leaves it
there for the next Engine on the same path: right for an app with one database, a leak for a test
process where every test opens a database under its own ``tmp_path``. An xdist worker kept every
test's engine, pooled connections and page caches: about 35 MB a test in the MCP memory tests, and
on 2026-10-26 a worker of a 2-worker shard grew from 1.7 to 4.7 GB in its last minute, on CI
runners that killed such workers. Every package's ``conftest.py`` imports
:func:`sqlite_engines_released` the way it imports ``data_dir_in_tmp``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from chaoscypher_core.adapters.sqlite import engine as _sqlite_engine


def _cached_paths() -> set[str]:
    with _sqlite_engine._engines_lock:  # noqa: SLF001 - the cache's own lock
        return set(_sqlite_engine._engines)  # noqa: SLF001 - which engines exist is the point


@pytest.fixture(autouse=True)
def sqlite_engines_released() -> Iterator[None]:
    """Evict (dispose and uncache) every engine first opened during the test, after it.

    Engines cached before the test (a module- or session-scoped database) are left alone.
    """
    before = _cached_paths()
    yield
    for path in _cached_paths() - before:
        _sqlite_engine.evict_engine(Path(path))
