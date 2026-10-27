# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A fresh install is not an upgrade; an upgrade still is.

Building a new database's schema is invisible: no pre-upgrade backup, no upgrade
record, so neither the web app's post-upgrade notice nor a rollback is offered and MCP's
``upgrade_status`` reports nothing. That holds for every way a database is first built
(the startup run every open takes, and the explicit apply of ``chaoscypher upgrade``,
``db migrate apply``, the Maintenance page and MCP ``apply_upgrade``), and for a first
build that did not finish: SQLite commits the baseline's ``CREATE TABLE`` statements as they
run, while the version row is written only when the whole run commits, so an
interrupted build leaves the tables and no version. A database at an older revision
still gets the backup, the record, the notice and the rollback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.database.engine import get_db_path, prepare_database
from chaoscypher_core.database.migrations.runner import (
    current_revision,
    head_revision,
    pending_revisions,
    upgrade_to,
)
from chaoscypher_core.database.migrations.startup import run_startup_migrations
from chaoscypher_core.database.migrations.state import UpgradeState, get_upgrade_state
from chaoscypher_core.database.migrations.upgrade import UpgradeService
from chaoscypher_core.mcp import maintenance


if TYPE_CHECKING:
    from pathlib import Path


NAME = "brandnew"

# The revision a real upgrade starts from: data-changing (needs_confirmation)
# migrations lie between it and head.
OLD_REVISION = "0018"

QUIET = UpgradeState(ready=True)


def _db() -> Path:
    """The path ``get_db_path`` resolves for ``NAME`` under the test's data directory."""
    return get_db_path(NAME)


def _backups(db: Path) -> list[Path]:
    folder = db.parent / "backups"
    return sorted(folder.iterdir()) if folder.exists() else []


def _tables(db: Path) -> set[str]:
    with get_engine(db).connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return {row[0] for row in rows}


def _unstamp(db: Path) -> None:
    """Drop the version row, as an interrupted first build leaves it (never committed)."""
    with get_engine(db).begin() as conn:
        conn.exec_driver_sql("DELETE FROM alembic_version")


def _add_chat(db: Path) -> None:
    """One row of user data in a table the baseline makes."""
    with get_engine(db).begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chats (id, database_name, title, status, created_at, "
                "updated_at, message_count) VALUES ('c1', :db, 'kept', 'active', "
                "'2026-01-01', '2026-01-01', 0)"
            ),
            {"db": NAME},
        )


def _chat_count(db: Path) -> int:
    with get_engine(db).connect() as conn:
        return conn.exec_driver_sql("SELECT count(*) FROM chats").scalar_one()


def _assert_quiet_build(db: Path) -> None:
    """The database is at head and nothing about its build is an upgrade."""
    assert current_revision(db) == head_revision()
    assert get_upgrade_state(db) == QUIET
    assert _backups(db) == []
    pending = UpgradeService(NAME).pending()
    assert pending.ready is True
    assert pending.blocked_on == []
    assert pending.last_backup is None
    assert pending.last_applied == []
    assert pending.data_changing is False


# --- a fresh install ---------------------------------------------------------


@pytest.mark.parametrize("auto_apply_destructive", [True, False])
def test_opening_a_new_database_leaves_no_upgrade_record_and_no_backup(
    auto_apply_destructive: bool,
) -> None:
    db = _db()
    assert not db.exists()

    assert prepare_database(db, NAME, auto_apply_destructive=auto_apply_destructive)

    _assert_quiet_build(db)


def test_a_new_database_probed_before_it_is_opened_is_still_fresh() -> None:
    """The CLI's upgrade guard reads the state first, which makes the file and its row."""
    db = _db()
    get_upgrade_state(db)

    run_startup_migrations(db)

    _assert_quiet_build(db)


@pytest.mark.parametrize("surface", ["upgrade_service", "mcp_apply_upgrade"])
def test_applying_to_a_new_database_builds_it_without_a_backup(surface: str) -> None:
    """``chaoscypher upgrade``, ``db migrate apply``, /upgrade/apply and MCP apply_upgrade."""
    db = _db()

    if surface == "upgrade_service":
        result = UpgradeService(NAME).apply()
        assert result.backup_path is None
        assert result.current_revision == head_revision()
    else:
        result = maintenance._handle_apply_upgrade(NAME, confirm_destructive=True)
        assert result["success"] is True
        assert result["backup_path"] is None

    _assert_quiet_build(db)


def test_mcp_upgrade_status_reports_nothing_for_a_new_database() -> None:
    db = _db()
    prepare_database(db, NAME)

    status = maintenance._handle_upgrade_status(NAME)

    assert status["ready"] is True
    assert status["blocked_on"] == []
    assert status["last_backup"] is None
    assert status["last_applied"] == []


# --- a first build that did not finish ---------------------------------------


def test_an_interrupted_first_build_is_finished_quietly() -> None:
    """The baseline's tables were committed, the version row was not."""
    db = _db()
    upgrade_to(db, "0001")
    _unstamp(db)
    assert current_revision(db) is None

    run_startup_migrations(db)

    _assert_quiet_build(db)


def test_a_half_built_schema_is_rebuilt_quietly() -> None:
    """Killed partway through the baseline: some of its tables exist, empty."""
    db = _db()
    run_startup_migrations(db)
    built = _tables(db)
    with get_engine(db).begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for table in ("graph_edges", "graph_nodes", "chats", "chat_messages", "alembic_version"):
            conn.exec_driver_sql(f"DROP TABLE {table}")
    assert "chats" not in _tables(db)

    run_startup_migrations(db)

    _assert_quiet_build(db)
    assert _tables(db) == built


def test_a_search_index_made_before_the_schema_does_not_make_an_upgrade() -> None:
    """A worker that opens the search index before the schema is built makes its tables."""
    from chaoscypher_core.adapters.sqlite.repos import SearchRepository

    db = _db()
    SearchRepository(engine=get_engine(db), vector_dim=8, embedding_model="test")
    assert current_revision(db) is None

    run_startup_migrations(db)

    _assert_quiet_build(db)


def test_an_unfinished_build_is_rebuilt_by_an_explicit_apply_too() -> None:
    db = _db()
    upgrade_to(db, "0001")
    _unstamp(db)

    UpgradeService(NAME).apply()

    _assert_quiet_build(db)


def test_a_database_with_rows_and_no_version_is_not_rebuilt() -> None:
    """Rows mean data: it is upgraded as before, after a backup, and nothing is dropped."""
    db = _db()
    upgrade_to(db, "0001")
    _add_chat(db)
    _unstamp(db)

    run_startup_migrations(db)

    state = get_upgrade_state(db)
    assert state.last_backup is not None
    assert _backups(db) != []
    assert _chat_count(db) == 1
    assert current_revision(db) == head_revision()
    assert state.data_changing is True


# --- a real upgrade keeps its backup, record, notice and rollback ------------


def test_upgrading_an_old_database_still_backs_up_and_records_it() -> None:
    db = _db()
    upgrade_to(db, OLD_REVISION)
    _add_chat(db)
    expected = pending_revisions(db)
    assert expected

    run_startup_migrations(db, auto_apply_destructive=True)

    assert current_revision(db) == head_revision()
    state = get_upgrade_state(db)
    assert state.ready is True
    assert state.data_changing is True
    assert state.last_applied == expected
    assert state.last_backup is not None
    assert [str(path) for path in _backups(db)] == [state.last_backup]
    assert _backups(db)[0].name.startswith(f"pre-{expected[0]}-")

    pending = UpgradeService(NAME).pending()
    assert pending.last_backup == state.last_backup
    assert pending.last_applied == expected
    assert pending.data_changing is True
    status = maintenance._handle_upgrade_status(NAME)
    assert status["last_backup"] == state.last_backup
    assert status["last_applied"] == expected

    restored = UpgradeService(NAME).rollback()
    assert restored.revision == OLD_REVISION
    assert _chat_count(db) == 1


def test_applying_to_an_old_database_still_backs_up_and_records_it() -> None:
    db = _db()
    upgrade_to(db, OLD_REVISION)
    expected = pending_revisions(db)

    result = UpgradeService(NAME).apply()

    assert result.applied == expected
    assert result.backup_path is not None
    state = get_upgrade_state(db)
    assert state.last_backup == result.backup_path
    assert state.last_applied == expected
    assert state.data_changing is True
