# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

# packages/cortex/tests/unit/shared/database/test_init_database.py
"""Tests for ``init_database()`` (Alembic-driven schema bring-up).

Alembic is now the single source of truth for the schema (see the
project's user-memory note "Schema migrations use Alembic"); the older
reflective auto-migrator was retired in April 2026. ``init_database``
applies pending revisions (or stamps a fresh DB to head) and then seeds
default rows.

The seed step opens an adapter on the *current* database
(``settings.app_db_path``), so we exercise the seeded database rather
than a side-name DB to keep the test deterministic.
"""

from chaoscypher_core.database.engine import get_db_path, init_database


def _reset_settings_cache() -> None:
    r"""Reset settings + engine caches so this test starts from a clean slate.

    ``init_database`` resolves the DB path via
    ``get_settings().paths.data_dir``. Two layers of cached state survive
    across tests in a full-suite run and must both be cleared, or this
    test silently operates on the REAL user data dir (e.g.
    ``%LOCALAPPDATA%\chaoscypher``) instead of the per-test ``tmp_path``:

    1. ``get_settings`` is ``@lru_cache``-decorated AND backed by a
       module-level ``_settings`` singleton. ``get_settings.cache_clear()``
       alone only drops the lru_cache entry — on the next call the function
       re-enters but short-circuits on the still-populated ``_settings``
       global, returning stale settings pointing at whatever ``data_dir``
       an earlier test loaded. ``reload_settings()`` clears the lru_cache
       AND nulls ``_settings`` so the new ``CHAOSCYPHER_DATA_DIR`` env is
       honoured.
    2. The SQLite engine cache (``adapters.sqlite.engine._engines``) holds
       engines keyed by resolved DB path for the worker's lifetime. Dispose
       them so a prior test's pooled connections / WAL handles don't leak
       into this one.

    Standalone this is a no-op (nothing cached yet); it only matters when a
    prior test in the suite has populated either cache.
    """
    from chaoscypher_core.adapters.sqlite.engine import dispose_all_engines
    from chaoscypher_core.app_config import reload_settings

    dispose_all_engines()
    reload_settings()


def test_init_database_creates_tables_on_fresh_db(tmp_path, monkeypatch):
    """init_database() should create all tables on a fresh DB."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(data_dir))
    _reset_settings_cache()

    init_database("default")

    db_path = get_db_path("default")
    assert db_path.exists()
    assert db_path.stat().st_size > 1024

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "workflows" in tables
        assert "graph_nodes" in tables
        # Alembic is the active migration system, so its bookkeeping table
        # is expected.
        assert "alembic_version" in tables
    finally:
        conn.close()


def test_init_database_is_idempotent(tmp_path, monkeypatch):
    """Calling init_database twice must not error."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(data_dir))
    _reset_settings_cache()

    init_database("default")
    init_database("default")
