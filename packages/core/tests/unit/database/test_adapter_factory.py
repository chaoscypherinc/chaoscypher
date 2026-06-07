# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for get_sqlite_adapter database-name resolution.

Regression coverage for the create-a-new-database bug: get_sqlite_adapter
must connect to the database named by its ``database_name`` argument, not
silently fall back to the active ``current_database``. The fallback caused
``create_database('X')`` to seed into the *current* database's app.db and
collide on the global ``graph_templates.id`` primary key.
"""

from __future__ import annotations

from types import SimpleNamespace

from chaoscypher_core import app_config
from chaoscypher_core.database import adapter_factory


def _fake_settings(tmp_path, current: str) -> SimpleNamespace:
    """Settings stand-in exposing both the old (app_db_path) and new (paths)
    resolution surfaces, so the test reproduces the bug on the old code path
    (which reads app_db_path → current db) and passes on the fixed path
    (which honours the requested name).
    """
    return SimpleNamespace(
        current_database=current,
        app_db_path=tmp_path / "databases" / current / "app.db",
        paths=SimpleNamespace(
            data_dir=str(tmp_path),
            databases_subdir="databases",
            app_db_filename="app.db",
        ),
    )


def test_get_sqlite_adapter_honors_explicit_database_name(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "get_settings", lambda: _fake_settings(tmp_path, "default"))

    adapter = adapter_factory.get_sqlite_adapter(database_name="other")
    try:
        assert adapter.db_path == tmp_path / "databases" / "other" / "app.db"
        assert adapter.database_name == "other"
    finally:
        adapter.disconnect()


def test_get_sqlite_adapter_defaults_to_current_database(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "get_settings", lambda: _fake_settings(tmp_path, "active"))

    adapter = adapter_factory.get_sqlite_adapter()
    try:
        assert adapter.db_path == tmp_path / "databases" / "active" / "app.db"
        assert adapter.database_name == "active"
    finally:
        adapter.disconnect()
