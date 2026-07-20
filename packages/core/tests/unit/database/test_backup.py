# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the auto-backup helper."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from chaoscypher_core.database.backup import BackupResult, backup_database


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO widgets (name) VALUES ('one'), ('two')")
    conn.commit()
    conn.close()


def test_backup_creates_copy_at_requested_path(tmp_path: Path) -> None:
    src = tmp_path / "app.db"
    _seed_db(src)

    result = backup_database(src, label="pre-test")

    assert isinstance(result, BackupResult)
    assert result.backup_path.exists()
    assert result.backup_path.parent == src.parent / "backups"
    assert result.backup_path.name.startswith("pre-test-")
    assert result.backup_path.suffix == ".db"
    assert result.source_bytes == result.backup_bytes
    assert result.source_bytes > 0


def test_backup_preserves_row_content(tmp_path: Path) -> None:
    src = tmp_path / "app.db"
    _seed_db(src)

    result = backup_database(src, label="integrity")

    conn = sqlite3.connect(str(result.backup_path))
    rows = conn.execute("SELECT name FROM widgets ORDER BY id").fetchall()
    conn.close()
    assert rows == [("one",), ("two",)]


def test_backup_raises_when_source_missing(tmp_path: Path) -> None:
    src = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        backup_database(src, label="whatever")


def test_backup_creates_nested_backup_dir(tmp_path: Path) -> None:
    src = tmp_path / "sub" / "app.db"
    src.parent.mkdir()
    _seed_db(src)

    # Explicitly nested backup_dir should be created recursively.
    target = tmp_path / "deeply" / "nested" / "backups"
    result = backup_database(src, label="nested", backup_dir=target)

    assert result.backup_path.exists()
    assert result.backup_path.parent == target


# ----- latest_backup() ------------------------------------------------------

import time

from chaoscypher_core.database.backup import latest_backup


def test_latest_backup_returns_most_recent(tmp_path: Path) -> None:
    src = tmp_path / "app.db"
    _seed_db(src)

    first = backup_database(src, label="v1")
    # Filenames carry seconds-granularity timestamps; one-second gap
    # ensures the two backup paths differ.
    time.sleep(1.1)
    second = backup_database(src, label="v2")

    found = latest_backup(src)
    assert found == second.backup_path
    assert found != first.backup_path


def test_latest_backup_returns_none_when_no_backups(tmp_path: Path) -> None:
    src = tmp_path / "app.db"
    _seed_db(src)
    assert latest_backup(src) is None


def test_latest_backup_ranks_by_timestamp_not_label(tmp_path: Path) -> None:
    """Regression: the label prefix must not dominate recency ordering.

    Filenames are ``<label>-<timestamp>.db``. A lexical sort of the whole
    filename lets a high-sorting label mask a chronologically-newer backup
    that happens to carry a low-sorting label — so ``latest_backup`` must rank
    by the embedded timestamp, not the full name.
    """
    src = tmp_path / "app.db"
    _seed_db(src)

    older = backup_database(src, label="zzz")
    # Seconds-granularity timestamps live in the filename; a >1s gap makes the
    # two backups' timestamps differ.
    time.sleep(1.1)
    newer = backup_database(src, label="aaa")

    # Precondition that makes this a real regression test: the OLDER backup's
    # filename sorts lexically AFTER the newer one, so a naive full-name sort
    # (the old behavior) would wrongly return the older backup.
    assert older.backup_path.name > newer.backup_path.name

    found = latest_backup(src)
    assert found == newer.backup_path
    assert found != older.backup_path
