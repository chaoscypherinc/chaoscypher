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


# ---------------------------------------------------------------------------
# delete_messages_after — tail truncation for regenerate / edit-and-resend
# ---------------------------------------------------------------------------


def _seed_turn(adapter: SqliteAdapter) -> None:
    """One chat with user → tool → assistant → user → assistant history."""
    from datetime import UTC, datetime

    base = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)
    with adapter.transaction():
        adapter.session.add(Chat(id="c1", database_name="test", title="t1", message_count=5))
    rows = [
        ("m1", "user", "first question"),
        ("m2", "tool", "{}"),
        ("m3", "assistant", "first answer"),
        ("m4", "user", "second question"),
        ("m5", "assistant", "second answer"),
    ]
    with adapter.transaction():
        for i, (mid, role, content) in enumerate(rows):
            adapter.session.add(
                ChatMessage(
                    id=mid,
                    chat_id="c1",
                    role=role,
                    content=content,
                    timestamp=base.replace(minute=i),
                )
            )


def test_delete_messages_after_exclusive_keeps_anchor(adapter: SqliteAdapter) -> None:
    """Regenerate case: truncate AFTER the last user message keeps it."""
    _seed_turn(adapter)
    deleted = adapter.delete_messages_after("c1", "m4")
    assert deleted == 1  # only m5
    remaining = [m["id"] for m in adapter.get_messages("c1")]
    assert remaining == ["m1", "m2", "m3", "m4"]
    assert adapter.get_chat("c1", "test")["message_count"] == 4


def test_delete_messages_after_inclusive_removes_anchor(adapter: SqliteAdapter) -> None:
    """Edit-and-resend case: the edited message itself is replaced."""
    _seed_turn(adapter)
    deleted = adapter.delete_messages_after("c1", "m4", inclusive=True)
    assert deleted == 2  # m4 + m5
    remaining = [m["id"] for m in adapter.get_messages("c1")]
    assert remaining == ["m1", "m2", "m3"]
    assert adapter.get_chat("c1", "test")["message_count"] == 3


def test_delete_messages_after_unknown_anchor_is_noop(adapter: SqliteAdapter) -> None:
    _seed_turn(adapter)
    assert adapter.delete_messages_after("c1", "nope") == 0
    assert len(adapter.get_messages("c1")) == 5


def test_delete_messages_after_wrong_chat_is_noop(adapter: SqliteAdapter) -> None:
    """An anchor belonging to another chat must not truncate this one."""
    _seed_turn(adapter)
    with adapter.transaction():
        adapter.session.add(Chat(id="c2", database_name="test", title="t2"))
        adapter.session.add(ChatMessage(id="other", chat_id="c2", role="user", content="x"))
    assert adapter.delete_messages_after("c1", "other") == 0
    assert len(adapter.get_messages("c1")) == 5


# ---------------------------------------------------------------------------
# Title search (server-side chat switcher search)
# ---------------------------------------------------------------------------


def test_list_chats_search_filters_by_title_substring(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        adapter.session.add(Chat(id="c1", database_name="test", title="War and Peace notes"))
        adapter.session.add(Chat(id="c2", database_name="test", title="Grocery planning"))
        adapter.session.add(Chat(id="c3", database_name="test", title="peace treaty research"))
    titles = [c["title"] for c in adapter.list_chats("test", search="peace")]
    assert sorted(titles) == ["War and Peace notes", "peace treaty research"]
    assert adapter.count_chats("test", search="peace") == 2
    assert adapter.count_chats("test", search="zzz") == 0
