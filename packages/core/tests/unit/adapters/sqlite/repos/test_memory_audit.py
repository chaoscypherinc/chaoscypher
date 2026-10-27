# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A forget's audit row is written inside the transaction that deletes the memory, or not at all.

``memory_audit.record`` adds the row and leaves the commit to the transaction it joins; called
outside one, the row would sit uncommitted in the session until some later commit, or be
lost with the next rollback, so it refuses instead.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos import memory_audit
from chaoscypher_core.exceptions import InvalidStateError


@pytest.fixture
def adapter(tmp_path: Path) -> Iterator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(db_path), checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    try:
        yield a
    finally:
        a.disconnect()


def _record(adapter: SqliteAdapter) -> None:
    assert adapter.session is not None
    memory_audit.record(
        adapter.session,
        "test",
        action=memory_audit.FORGET,
        handle="a1b2c3",
        by="alice",
        scope=None,
        facts_removed=1,
        facts_kept=0,
    )


def _rows_on_disk(adapter: SqliteAdapter) -> int:
    with sqlite3.connect(adapter.db_path) as db:
        [(n,)] = db.execute("SELECT count(*) FROM memory_audit")
    return int(n)


def test_an_audit_row_outside_a_transaction_is_refused(adapter: SqliteAdapter) -> None:
    with pytest.raises(InvalidStateError, match="inside the transaction"):
        _record(adapter)
    assert adapter.session is not None and not adapter.session.new


def test_an_audit_row_commits_with_its_transaction(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        _record(adapter)
        assert _rows_on_disk(adapter) == 0
    assert _rows_on_disk(adapter) == 1
