# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CAS guards on ``set_system_paused`` (``expect_unpaused`` / ``expect_paused_by``).

The health-monitor evaluator decides from a snapshot; these guards make its
writes lose cleanly (rowcount 0, no state change, no audit event) when a
manual pause landed in between — instead of relabelling or lifting a pause
the monitor does not own.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_dir = tmp_path / "cc-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def _pause_events(adapter: SqliteAdapter) -> int:
    return len(adapter.list_system_events(event_type="pause"))


def test_unguarded_write_still_returns_rowcount(adapter: SqliteAdapter) -> None:
    assert adapter.set_system_paused(is_paused=True, reason="x", paused_by="user") == 1
    assert adapter.set_system_paused(is_paused=False) == 1


def test_expect_unpaused_succeeds_on_unpaused_row(adapter: SqliteAdapter) -> None:
    updated = adapter.set_system_paused(
        is_paused=True, reason="Auto-paused: db", paused_by="health_monitor", expect_unpaused=True
    )
    assert updated == 1
    state = adapter.get_system_state()
    assert state["processing_paused"] is True
    assert state["paused_by"] == "health_monitor"


def test_expect_unpaused_loses_to_concurrent_user_pause(adapter: SqliteAdapter) -> None:
    """A user pause that landed first must survive a guarded trip write intact."""
    adapter.set_system_paused(is_paused=True, reason="maintenance", paused_by="user")
    events_before = _pause_events(adapter)

    updated = adapter.set_system_paused(
        is_paused=True, reason="Auto-paused: db", paused_by="health_monitor", expect_unpaused=True
    )

    assert updated == 0
    state = adapter.get_system_state()
    assert state["paused_by"] == "user"
    assert state["processing_paused_reason"] == "maintenance"
    # No phantom audit event for the write that matched zero rows.
    assert _pause_events(adapter) == events_before


def test_expect_paused_by_lifts_only_own_pause(adapter: SqliteAdapter) -> None:
    adapter.set_system_paused(is_paused=True, reason="Auto-paused: db", paused_by="health_monitor")
    updated = adapter.set_system_paused(is_paused=False, expect_paused_by="health_monitor")
    assert updated == 1
    assert adapter.get_system_state()["processing_paused"] is False


def test_expect_paused_by_refuses_foreign_pause(adapter: SqliteAdapter) -> None:
    """A guarded clear must never lift a pause owned by the user."""
    adapter.set_system_paused(is_paused=True, reason="maintenance", paused_by="user")

    updated = adapter.set_system_paused(is_paused=False, expect_paused_by="health_monitor")

    assert updated == 0
    state = adapter.get_system_state()
    assert state["processing_paused"] is True
    assert state["paused_by"] == "user"
