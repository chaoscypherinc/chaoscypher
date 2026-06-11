# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat list pagination + scoped count at the adapter layer.

2026-06-10 audit P1: the service applied ``offset`` by slicing a top-N
fetch in Python (``list_chats(limit=page_size)[offset:]``) so page 2 was
always empty, and ``count_chats`` ignored the ``scoped`` filter so the
pagination envelope lied whenever ``?scoped=`` was used. Offset and the
scoped count now live in SQL.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import Chat


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


@pytest.fixture
def three_chats(adapter: SqliteAdapter) -> SqliteAdapter:
    """c1 (oldest, scoped) .. c3 (newest, unscoped) in database 'test'."""
    base = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)
    with adapter.transaction():
        adapter.session.add(
            Chat(id="c1", database_name="test", title="t1", created_at=base, source_ids=["s1"])
        )
        adapter.session.add(
            Chat(id="c2", database_name="test", title="t2", created_at=base + timedelta(minutes=1))
        )
        adapter.session.add(
            Chat(id="c3", database_name="test", title="t3", created_at=base + timedelta(minutes=2))
        )
    return adapter


def test_list_chats_offset_in_sql(three_chats: SqliteAdapter) -> None:
    """Offset skips newest-first rows in SQL — page 2 is no longer empty."""
    page2 = three_chats.list_chats(database_name="test", limit=1, offset=1)
    assert [c["id"] for c in page2] == ["c2"]
    page3 = three_chats.list_chats(database_name="test", limit=1, offset=2)
    assert [c["id"] for c in page3] == ["c1"]


def test_list_chats_offset_default_zero(three_chats: SqliteAdapter) -> None:
    rows = three_chats.list_chats(database_name="test", limit=2)
    assert [c["id"] for c in rows] == ["c3", "c2"]


def test_count_chats_scoped(three_chats: SqliteAdapter) -> None:
    assert three_chats.count_chats(database_name="test") == 3
    assert three_chats.count_chats(database_name="test", scoped=True) == 1
    assert three_chats.count_chats(database_name="test", scoped=False) == 2


def test_list_chats_scoped_handles_json_null(three_chats: SqliteAdapter) -> None:
    """source_ids=None persists as JSON 'null' (not SQL NULL) — the scoped
    filter must treat both as unscoped.
    """
    scoped = three_chats.list_chats(database_name="test", scoped=True)
    assert [c["id"] for c in scoped] == ["c1"]
    unscoped = three_chats.list_chats(database_name="test", scoped=False)
    assert {c["id"] for c in unscoped} == {"c2", "c3"}


def test_create_message_maintains_message_count(adapter: SqliteAdapter) -> None:
    """message_count was never written (always 0) despite being serialized
    and publicly documented (2026-06-10 audit P2).
    """
    with adapter.transaction():
        adapter.session.add(Chat(id="mc1", database_name="test", title="t"))
    adapter.create_message({"id": "m1", "chat_id": "mc1", "role": "user", "content": "a"})
    adapter.create_message({"id": "m2", "chat_id": "mc1", "role": "assistant", "content": "b"})
    chat = adapter.get_chat("mc1", "test")
    assert chat is not None
    assert chat["message_count"] == 2
