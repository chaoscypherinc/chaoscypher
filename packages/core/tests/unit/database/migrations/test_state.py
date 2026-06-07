# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the upgrade-state table."""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.database.migrations.runner import upgrade_to_head
from chaoscypher_core.database.migrations.state import (
    UpgradeState,
    clear_upgrade_state,
    get_upgrade_state,
    set_upgrade_state,
)


def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "app.db"
    upgrade_to_head(db)
    return db


def test_initial_state_is_ready(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    state = get_upgrade_state(db)
    assert state == UpgradeState(ready=True, blocked_on=[], last_backup=None, message="")


def test_set_and_get_round_trip(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    set_upgrade_state(
        db,
        ready=False,
        blocked_on=["0002", "0003"],
        last_backup="/data/backups/x.db",
        message="2 migrations need confirmation",
    )
    state = get_upgrade_state(db)
    assert state.ready is False
    assert state.blocked_on == ["0002", "0003"]
    assert state.last_backup == "/data/backups/x.db"
    assert state.message == "2 migrations need confirmation"


def test_clear_resets_to_ready(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    set_upgrade_state(db, ready=False, blocked_on=["0002"], last_backup=None, message="x")
    clear_upgrade_state(db)
    state = get_upgrade_state(db)
    assert state.ready is True
    assert state.blocked_on == []
    assert state.message == ""


def test_table_auto_created_on_first_read(tmp_path: Path) -> None:
    """get_upgrade_state must create the table + default row on first call."""
    db = _fresh_db(tmp_path)
    # Tables are empty-by-default aside from alembic_version + our own.
    state = get_upgrade_state(db)
    # Default row → ready=True.
    assert state.ready is True


def test_record_successful_upgrade_retains_backup(tmp_path) -> None:
    from chaoscypher_core.database.migrations.state import (
        get_upgrade_state,
        record_successful_upgrade,
    )

    db = tmp_path / "app.db"
    record_successful_upgrade(
        db,
        applied=["0042", "0043"],
        last_backup="/tmp/backups/pre-0042-x.db",
        data_changing=True,
    )
    state = get_upgrade_state(db)
    assert state.ready is True
    assert state.blocked_on == []
    assert state.last_applied == ["0042", "0043"]
    assert state.last_backup == "/tmp/backups/pre-0042-x.db"
    assert state.data_changing is True


def test_clear_resets_record(tmp_path) -> None:
    from chaoscypher_core.database.migrations.state import (
        clear_upgrade_state,
        get_upgrade_state,
        record_successful_upgrade,
    )

    db = tmp_path / "app.db"
    record_successful_upgrade(
        db, applied=["0010"], last_backup="/tmp/x.db", data_changing=True
    )
    clear_upgrade_state(db)
    state = get_upgrade_state(db)
    assert state.last_applied == []
    assert state.last_backup is None
    assert state.data_changing is False
