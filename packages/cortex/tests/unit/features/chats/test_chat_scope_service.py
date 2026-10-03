# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Behavioural tests for chat source-scope orchestration.

Runs ``ChatFeatureService`` + ``ChatScopeRepository`` over a real,
file-backed ``SqliteAdapter`` and the real engine ``ChatService`` (the same
wiring as ``get_chat_feature_service``) so tag-to-source resolution, scope
persistence and the "scope changed" system message are all executed rather
than mocked.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.services.chat import ChatService
from chaoscypher_cortex.features.chats.models import ChatScopeUpdate
from chaoscypher_cortex.features.chats.repository import ChatScopeRepository
from chaoscypher_cortex.features.chats.service import ChatFeatureService


DB_NAME = "test"


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "test.db"
    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name=DB_NAME)
    a.connect()
    yield a
    a.disconnect()


@pytest.fixture
def service(adapter: SqliteAdapter) -> ChatFeatureService:
    return ChatFeatureService(
        ChatService(storage=adapter, database_name=DB_NAME),
        ChatScopeRepository(adapter, DB_NAME),
    )


def _seed_source(adapter: SqliteAdapter, source_id: str) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": DB_NAME,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "indexed",
        }
    )


def _seed_tag(adapter: SqliteAdapter, tag_id: str, source_ids: list[str]) -> None:
    adapter.create_tag({"id": tag_id, "database_name": DB_NAME, "name": f"name-{tag_id}"})
    for sid in source_ids:
        adapter.assign_tag(sid, tag_id, DB_NAME)


def _new_chat(service: ChatFeatureService) -> str:
    chat = service.engine_chat_service.create_chat(chat_id="chat-1", title="Scoped")
    return str(chat["id"])


def _system_messages(chat: dict[str, Any]) -> list[str]:
    return [m["content"] for m in chat["messages"] if m["role"] == "system"]


def test_update_scope_unions_tag_sources_persists_and_announces(
    adapter: SqliteAdapter, service: ChatFeatureService
) -> None:
    for sid in ("s1", "s2", "s3"):
        _seed_source(adapter, sid)
    # t1 covers s1 (also given explicitly -> must dedupe) and s2; s3 is out of scope.
    _seed_tag(adapter, "t1", ["s1", "s2"])
    chat_id = _new_chat(service)

    result = service.update_scope_with_message(
        chat_id, ChatScopeUpdate(source_ids=["s1"], tag_ids=["t1"])
    )

    assert result is not None
    assert sorted(result["source_ids"]) == ["s1", "s2"]

    # Read back through a fresh lookup: the scope is persisted, not just echoed.
    persisted = service.engine_chat_service.get_chat(chat_id)
    assert persisted is not None
    assert sorted(persisted["source_ids"]) == ["s1", "s2"]

    (msg,) = _system_messages(persisted)
    assert msg.startswith("Source scope updated. Now scoped to: ")
    assert "s1.pdf" in msg and "s2.pdf" in msg
    assert "s3.pdf" not in msg


def test_clear_scope_after_set_removes_scope_and_announces(
    adapter: SqliteAdapter, service: ChatFeatureService
) -> None:
    _seed_source(adapter, "s1")
    chat_id = _new_chat(service)
    service.update_scope_with_message(chat_id, ChatScopeUpdate(source_ids=["s1"]))

    result = service.clear_scope_with_message(chat_id)

    assert result is not None
    persisted = service.engine_chat_service.get_chat(chat_id)
    assert persisted is not None
    assert not persisted["source_ids"]
    messages = _system_messages(persisted)
    assert len(messages) == 2
    assert messages[-1] == "Source scope removed. All sources are now accessible."


def test_scope_operations_on_missing_chat_return_none(service: ChatFeatureService) -> None:
    assert service.update_scope_with_message("nope", ChatScopeUpdate(source_ids=["s1"])) is None
    assert service.clear_scope_with_message("nope") is None
