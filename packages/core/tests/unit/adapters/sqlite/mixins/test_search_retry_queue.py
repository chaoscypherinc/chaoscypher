# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR2a Task 13 - SearchRetryQueueProtocol + SQLite mixin."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel, select

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import PendingSearchIndex
from chaoscypher_core.ports.search_retry import SearchRetryQueueProtocol


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def test_sqlite_adapter_satisfies_search_retry_protocol(adapter: SqliteAdapter) -> None:
    """SqliteAdapter should satisfy SearchRetryQueueProtocol structurally."""
    assert isinstance(adapter, SearchRetryQueueProtocol)


def test_enqueue_inserts_rows(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        adapter.enqueue_pending_search_index(
            rows=[
                {"item_id": "n1", "kind": "node", "source_id": "s1"},
                {"item_id": "c1", "kind": "chunk", "source_id": "s1"},
            ]
        )
    rows = list(adapter.session.exec(select(PendingSearchIndex)))
    assert len(rows) == 2
    ids = {r.id for r in rows}
    assert ids == {"node:n1", "chunk:c1"}


def test_enqueue_is_idempotent(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        adapter.enqueue_pending_search_index(
            rows=[{"item_id": "n1", "kind": "node", "source_id": "s1"}]
        )
    with adapter.transaction():
        adapter.enqueue_pending_search_index(
            rows=[{"item_id": "n1", "kind": "node", "source_id": "s1"}]
        )
    rows = list(adapter.session.exec(select(PendingSearchIndex)))
    assert len(rows) == 1


def test_enqueue_empty_list_is_noop(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        adapter.enqueue_pending_search_index(rows=[])
    rows = list(adapter.session.exec(select(PendingSearchIndex)))
    assert rows == []


def test_enqueue_without_source_id(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        adapter.enqueue_pending_search_index(rows=[{"item_id": "tpl1", "kind": "template"}])
    rows = list(adapter.session.exec(select(PendingSearchIndex)))
    assert len(rows) == 1
    assert rows[0].source_id is None
