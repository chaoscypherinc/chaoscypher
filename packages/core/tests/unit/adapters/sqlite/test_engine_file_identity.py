# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The engine cache is keyed by path *and file*: a deleted or replaced file gets a fresh engine.

A cached engine keeps pooled connections open on the file it first opened.
When that file is deleted (a clean ``compose build`` removes the database
folder) or replaced (a restore, an ``os.replace``), those connections still
point at the old inode, so every write through the cached engine landed in a
file nobody could read any more, and the new file on disk stayed empty.
"""

from __future__ import annotations

import errno
import os
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
import structlog
from sqlalchemy import text

from chaoscypher_core.adapters.sqlite import engine as engine_module
from chaoscypher_core.adapters.sqlite.engine import (
    dispose_all_engines,
    evict_engine,
    get_engine,
)


_USED: list[Path] = []


@pytest.fixture(autouse=True)
def _evict_used_paths() -> Iterator[None]:
    """Dispose every engine a test opened, so no pooled connection outlives it."""
    yield
    for path in _USED:
        evict_engine(path)
    _USED.clear()


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "db" / "app.db"
    _USED.append(path)
    return path


def _create_table(path: Path, name: str) -> None:
    with get_engine(path).begin() as conn:
        conn.execute(text(f"CREATE TABLE {name}(x)"))


def _tables_through_engine(path: Path) -> set[str]:
    with get_engine(path).connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))
        return {row[0] for row in rows}


def _tables_on_disk(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {row[0] for row in rows}


def test_unchanged_file_keeps_the_same_engine(tmp_path: Path) -> None:
    path = _db(tmp_path)
    first = get_engine(path)
    with first.connect() as conn:
        conn.execute(text("SELECT 1"))

    assert get_engine(path) is first


def test_deleted_file_gets_a_fresh_engine(tmp_path: Path) -> None:
    path = _db(tmp_path)
    first = get_engine(path)
    _create_table(path, "t")
    shutil.rmtree(path.parent)
    path.parent.mkdir()

    second = get_engine(path)

    assert second is not first
    with second.begin() as conn:
        conn.execute(text("CREATE TABLE u(x)"))
    assert "u" in _tables_on_disk(path)


def test_replaced_file_gets_a_fresh_engine(tmp_path: Path) -> None:
    path = _db(tmp_path)
    first = get_engine(path)
    _create_table(path, "mine")
    other = tmp_path / "other.db"
    with closing(sqlite3.connect(other)) as conn:
        conn.execute("CREATE TABLE other(x)")
        conn.commit()
    other.replace(path)
    # Every real replacement drops the WAL sidecars too (``UpgradeService``
    # rollback, the database reset): SQLite would otherwise replay the old
    # file's ``-wal`` onto the new one, which no engine cache can prevent.
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)

    second = get_engine(path)

    assert second is not first
    assert "other" in _tables_through_engine(path)


def test_directory_moved_back_gets_engine_for_the_file_now_there(tmp_path: Path) -> None:
    path = _db(tmp_path)
    folder = path.parent
    _create_table(path, "first")
    aside = tmp_path / "aside"
    shutil.move(folder, aside)
    _create_table(path, "second")  # a fresh engine makes a new file in a new folder
    shutil.move(folder, tmp_path / "away")
    shutil.move(aside, folder)

    tables = _tables_through_engine(path)

    assert "first" in tables
    assert "second" not in tables


def test_never_connected_engine_is_kept(tmp_path: Path) -> None:
    path = _db(tmp_path)
    first = get_engine(path)
    assert not path.exists()

    assert get_engine(path) is first


def test_evict_and_dispose_forget_the_identity(tmp_path: Path) -> None:
    path = _db(tmp_path)
    key = str(path.resolve())
    _create_table(path, "t")
    assert key in engine_module._identities

    evict_engine(path)
    assert key not in engine_module._identities

    _create_table(path, "u")
    assert key in engine_module._identities
    # dispose_all_engines clears the whole process cache: set every other test's
    # engine (this xdist worker's) aside and put it back afterwards.
    other_engines = {k: v for k, v in engine_module._engines.items() if k != key}
    other_identities = {k: v for k, v in engine_module._identities.items() if k != key}
    for other in other_engines:
        engine_module._engines.pop(other)
        engine_module._identities.pop(other, None)
    try:
        dispose_all_engines()
        assert key not in engine_module._identities
    finally:
        engine_module._engines.update(other_engines)
        engine_module._identities.update(other_identities)


def _deny_stat_of(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Make ``os.stat`` of ``path`` raise PermissionError (an unreadable folder); others pass."""
    real_stat = os.stat
    denied = str(path.resolve())

    def _stat(target: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if str(target) == denied:
            raise PermissionError(errno.EACCES, "Permission denied", denied)
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(os, "stat", _stat)


def test_an_unreadable_file_identity_keeps_the_cached_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown is not "changed": a stat that fails for another reason than a missing file."""
    path = _db(tmp_path)
    first = get_engine(path)
    _create_table(path, "t")
    _deny_stat_of(monkeypatch, path)

    with structlog.testing.capture_logs() as logs:
        again = get_engine(path)

    assert again is first
    records = [r for r in logs if r["event"] == "database_engine_identity_unknown"]
    assert len(records) == 1, logs
    assert records[0]["log_level"] == "debug"
    assert records[0]["errno"] == errno.EACCES


def test_an_unreadable_file_on_first_connect_records_nothing_and_still_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _db(tmp_path)
    path.parent.mkdir(parents=True)
    _deny_stat_of(monkeypatch, path)

    _create_table(path, "t")

    assert str(path.resolve()) not in engine_module._identities
    monkeypatch.undo()
    assert "t" in _tables_on_disk(path)


def test_a_stale_engine_is_disposed_outside_the_cache_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As ``evict_engine`` does: a slow pool teardown must not hold every other caller."""
    path = _db(tmp_path)
    first = get_engine(path)
    _create_table(path, "t")
    lock_held_during_dispose: list[bool] = []
    real_dispose = first.dispose

    def _dispose(*args: Any, **kwargs: Any) -> None:
        lock_held_during_dispose.append(engine_module._engines_lock.locked())
        real_dispose(*args, **kwargs)

    monkeypatch.setattr(first, "dispose", _dispose)
    shutil.rmtree(path.parent)
    path.parent.mkdir()

    assert get_engine(path) is not first
    assert lock_held_during_dispose == [False]
