# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A first build racing another process never loses a row and never becomes an upgrade.

Cortex and the worker start together, and ``chaoscypher upgrade`` or MCP
``apply_upgrade`` can run during a first start. Each scenario holds one process
at the point the race needs, from a second thread: ``flock`` locks one open file
description, so a second open in this process contends as another process would.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import TYPE_CHECKING

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos import SearchRepository
from chaoscypher_core.database.engine import get_db_path, init_database
from chaoscypher_core.database.migrations import startup as startup_mod
from chaoscypher_core.database.migrations.runner import (
    SchemaStart,
    current_revision,
    head_revision,
    pending_revisions,
    schema_start,
    upgrade_to,
)
from chaoscypher_core.database.migrations.startup import run_startup_migrations
from chaoscypher_core.database.migrations.state import UpgradeState, get_upgrade_state
from chaoscypher_core.database.migrations.upgrade import UpgradeService
from chaoscypher_core.utils.filelock import lock_file, unlock_file


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest


NAME = "racy"

# Long enough for the other thread to reach the lock it waits on.
SETTLE_SECONDS = 0.5

CHAT_ROW = (
    "INSERT INTO chats (id, database_name, title, status, created_at, updated_at, "
    "message_count) VALUES ('c1', 'racy', 'kept', 'active', '2026-01-01', "
    "'2026-01-01', 0)"
)


def _unfinished(db: Path) -> None:
    """The baseline's tables with no version row: a first build that was cut short."""
    upgrade_to(db, "0001")
    with get_engine(db).begin() as conn:
        conn.exec_driver_sql("DELETE FROM alembic_version")
    assert schema_start(db) is SchemaStart.UNFINISHED


def _chats(db: Path) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT count(*) FROM chats").fetchone()[0]


def _backups(db: Path) -> list[Path]:
    folder = db.parent / "backups"
    return sorted(folder.iterdir()) if folder.exists() else []


def _in_thread(target: Callable[[], object]) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            target()
        except BaseException as exc:  # recorded for the assertion
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, errors


def test_a_row_committed_while_an_unfinished_schema_is_dropped_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A writer commits a row after the run classed the database unfinished, before the drop.

    The drop re-checks under its own write lock, so it finds the row and refuses;
    the run then upgrades the database as the unversioned install it now is.
    """
    db = get_db_path(NAME)
    _unfinished(db)
    classified = threading.Event()
    real_schema_start = startup_mod.schema_start

    def schema_start_then_signal(path: Path) -> SchemaStart:
        start = real_schema_start(path)
        classified.set()
        return start

    monkeypatch.setattr(startup_mod, "schema_start", schema_start_then_signal)
    writer = sqlite3.connect(db, isolation_level=None, timeout=30)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(CHAT_ROW)

    thread, errors = _in_thread(lambda: run_startup_migrations(db, auto_apply_destructive=True))
    assert classified.wait(30)
    time.sleep(SETTLE_SECONDS)
    writer.execute("COMMIT")
    writer.close()
    thread.join(60)

    assert errors == []
    assert _chats(db) == 1
    assert current_revision(db) == head_revision()
    state = get_upgrade_state(db)
    assert state.last_backup is not None
    assert [str(path) for path in _backups(db)] == [state.last_backup]


def _waiter_after(holder_work: Callable[[], None]) -> None:
    """Run ``init_database`` while the init lock is held, then release it after ``holder_work``."""
    db = get_db_path(NAME)
    db.parent.mkdir(parents=True, exist_ok=True)
    with open(db.parent / ".init.lock", "w") as held:
        lock_file(held, blocking=False)
        holder_work()
        thread, errors = _in_thread(lambda: init_database(NAME))
        time.sleep(SETTLE_SECONDS)
        assert thread.is_alive(), "init_database did not wait on the held init lock"
        unlock_file(held)
    thread.join(60)
    assert errors == []


def test_a_waiter_after_a_killed_first_build_gets_a_built_database() -> None:
    db = get_db_path(NAME)

    _waiter_after(lambda: _unfinished(db))

    assert current_revision(db) == head_revision()
    assert get_upgrade_state(db) == UpgradeState(ready=True)
    assert _backups(db) == []


def test_a_waiter_after_a_real_upgrade_keeps_the_notice() -> None:
    db = get_db_path(NAME)

    def real_upgrade() -> None:
        upgrade_to(db, "0018")
        run_startup_migrations(db, auto_apply_destructive=True)

    _waiter_after(real_upgrade)

    state = get_upgrade_state(db)
    assert state.data_changing is True
    assert state.last_applied
    assert state.last_backup is not None
    assert UpgradeService(NAME).pending().last_backup == state.last_backup


def test_a_run_with_nothing_pending_keeps_a_real_upgrades_record() -> None:
    db = get_db_path(NAME)
    upgrade_to(db, "0018")
    run_startup_migrations(db, auto_apply_destructive=True)
    recorded = get_upgrade_state(db)
    assert recorded.data_changing is True

    run_startup_migrations(db)

    assert get_upgrade_state(db) == recorded


def test_a_run_with_nothing_pending_clears_a_stale_blocked_state() -> None:
    """A blocked record whose migrations were since applied elsewhere must not block forever."""
    from chaoscypher_core.database.migrations.state import set_upgrade_state

    db = get_db_path(NAME)
    run_startup_migrations(db)
    set_upgrade_state(db, ready=False, blocked_on=["0019"], last_backup=None, message="stale")

    run_startup_migrations(db)

    assert get_upgrade_state(db).ready is True


def test_apply_racing_a_first_start_neither_backs_up_nor_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``chaoscypher upgrade`` during Cortex's first build waits, then finds nothing to do."""
    db = get_db_path(NAME)
    building = threading.Event()
    release = threading.Event()
    real_upgrade = startup_mod.upgrade_to_head

    def held_upgrade(path: Path) -> None:
        building.set()
        assert release.wait(30)
        real_upgrade(path)

    monkeypatch.setattr(startup_mod, "upgrade_to_head", held_upgrade)
    first_start, start_errors = _in_thread(lambda: run_startup_migrations(db))
    assert building.wait(30)
    results: list[object] = []
    applier, apply_errors = _in_thread(lambda: results.append(UpgradeService(NAME).apply()))
    time.sleep(SETTLE_SECONDS)
    release.set()
    first_start.join(60)
    applier.join(60)

    assert start_errors == []
    assert apply_errors == []
    assert results[0].backup_path is None  # type: ignore[attr-defined]
    assert current_revision(db) == head_revision()
    assert get_upgrade_state(db) == UpgradeState(ready=True)
    assert _backups(db) == []


def test_search_triggers_survive_rebuilding_an_unfinished_database() -> None:
    """A search index opened on the unfinished database keeps feeding from document_chunks."""
    db = get_db_path(NAME)
    _unfinished(db)
    SearchRepository(engine=get_engine(db), vector_dim=8, embedding_model="test")

    run_startup_migrations(db)

    with get_engine(db).connect() as conn:
        triggers = {
            row[0]
            for row in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='trigger' "
                "AND name LIKE 'fulltext_chunks_from_document_%'"
            )
        }
    assert triggers == {
        "fulltext_chunks_from_document_ai",
        "fulltext_chunks_from_document_au",
        "fulltext_chunks_from_document_ad",
    }


def test_apply_to_an_unversioned_database_stamps_it_before_upgrading() -> None:
    """Rows with no version: apply stamps at the baseline, backs up and keeps the rows."""
    db = get_db_path(NAME)
    upgrade_to(db, "0001")
    with get_engine(db).begin() as conn:
        conn.exec_driver_sql(CHAT_ROW)
        conn.exec_driver_sql("DELETE FROM alembic_version")
    expected = [rev for rev in pending_revisions(db) if rev != "0001"]

    result = UpgradeService(NAME).apply()

    assert result.applied == expected
    assert result.backup_path is not None
    assert current_revision(db) == head_revision()
    assert _chats(db) == 1


def test_apply_after_an_earlier_upgrade_takes_its_own_backup() -> None:
    """The record an earlier upgrade keeps (for its notice) never stands in for a new backup."""
    from chaoscypher_core.database.migrations.runner import downgrade_to

    db = get_db_path(NAME)
    upgrade_to(db, "0018")
    run_startup_migrations(db, auto_apply_destructive=True)
    earlier = get_upgrade_state(db).last_backup
    assert earlier is not None
    downgrade_to(db, "0024")  # a release later: one migration pending again

    result = UpgradeService(NAME).apply()

    assert result.applied == ["0025"]
    assert result.backup_path is not None
    assert result.backup_path != earlier
    assert get_upgrade_state(db).last_backup == result.backup_path
