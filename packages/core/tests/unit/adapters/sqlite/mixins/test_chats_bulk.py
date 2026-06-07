# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR2a Task 7 — bulk chat/message delete methods on ChatStorageProtocol."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import Chat, ChatMessage


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


def test_delete_messages_by_chat_ids(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        adapter.session.add(Chat(id="c1", database_name="test", title="t1"))
        adapter.session.add(Chat(id="c2", database_name="test", title="t2"))
    with adapter.transaction():
        adapter.session.add(ChatMessage(id="m1", chat_id="c1", role="user", content="x"))
        adapter.session.add(ChatMessage(id="m2", chat_id="c1", role="user", content="y"))
        adapter.session.add(ChatMessage(id="m3", chat_id="c2", role="user", content="z"))
    assert adapter.delete_messages_by_chat_ids(chat_ids=["c1"]) == 2


def test_delete_messages_by_empty_list_is_noop(adapter: SqliteAdapter) -> None:
    assert adapter.delete_messages_by_chat_ids(chat_ids=[]) == 0


def test_delete_all_chats_scoped(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        adapter.session.add(Chat(id="c1", database_name="a", title="t1"))
        adapter.session.add(Chat(id="c2", database_name="a", title="t2"))
        adapter.session.add(Chat(id="c3", database_name="b", title="t3"))
    assert adapter.delete_all_chats(database_name="a") == 2


def test_delete_all_chats_empty_noop(adapter: SqliteAdapter) -> None:
    assert adapter.delete_all_chats(database_name="test") == 0
