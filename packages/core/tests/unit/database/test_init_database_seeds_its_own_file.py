# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``init_database`` seeds the file it built, whatever the settings singleton says.

It builds the database at the path its arguments (or ``PathSettings``) give, and
used to seed the default tools, workflows and triggers through the app-config
singleton's data directory instead: a caller passing ``data_dir``, or a process
whose settings were read before its data directory changed, seeded another file,
a new empty one, and failed on ``no such table: system_tools``.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from chaoscypher_core import app_config
from chaoscypher_core.database.engine import get_db_path, init_database


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _reset_settings() -> None:
    app_config.get_config_manager.cache_clear()
    app_config.get_settings.cache_clear()
    app_config._settings = None


@pytest.fixture
def settings_elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The settings singleton, read while the data directory was another one."""
    elsewhere = tmp_path / "settings-data-dir"
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(elsewhere))
    _reset_settings()
    assert str(app_config.get_settings().paths.data_dir) == str(elsewhere)
    yield elsewhere
    _reset_settings()


def _count(db: Path, table: str) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_an_explicit_data_dir_is_seeded_not_the_settings_one(
    tmp_path: Path, settings_elsewhere: Path
) -> None:
    data_dir = tmp_path / "explicit"

    init_database("seeded", data_dir=str(data_dir))

    db = get_db_path("seeded", data_dir=str(data_dir))
    assert _count(db, "system_tools") > 0
    assert _count(db, "workflows") > 0
    assert _count(db, "triggers") > 0
    assert not (settings_elsewhere / "databases" / "seeded").exists()


def test_a_data_dir_changed_after_the_settings_were_read_is_seeded(
    tmp_path: Path, settings_elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The way the shard failed: the singleton was read earlier, under another directory."""
    current = tmp_path / "current"
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(current))

    init_database("seeded")

    assert _count(get_db_path("seeded"), "system_tools") > 0
    assert not (settings_elsewhere / "databases" / "seeded").exists()
