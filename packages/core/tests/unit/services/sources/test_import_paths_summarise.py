# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Both Core paths that finish indexing a document write its summary, after the indexing commits.

``Engine.chunk_and_index`` (``process_document``, the MCP index-only path) on a
real engine, and the queue's embed handler (``_run_embedding``) on a real
adapter. ``import_summary_chat`` is replaced by a fake model; a fake that
raises leaves the document indexed with no summary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.models import DocumentChunk, SourceRow
from chaoscypher_core.operations.importing.embedding_handler import _run_embedding
from chaoscypher_core.settings import EngineSettings
from tests.unit.mcp.memory_world import DOCUMENT_TEXT, build_world


SUMMARY = "A note about where Pierre Bezukhov was born."
PATHS = ["engine_chunk_and_index", "embed_handler"]

# A path indexes one document and returns (its storage, its database, its id).
Indexer = Callable[[], Awaitable[tuple[SqliteAdapter, str, str]]]


class FakeChat:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def __call__(self, messages: list[dict[str, str]]) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SUMMARY


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch)
    yield w
    w.engine.close()


def _engine_path(world: SimpleNamespace) -> Indexer:
    async def index() -> tuple[SqliteAdapter, str, str]:
        result = await world.engine.process_document(DOCUMENT_TEXT, extract=False)
        return world.storage, world.engine.settings.current_database, result.source_id

    return index


def _embed_handler_path(adapter: SqliteAdapter) -> Indexer:
    async def index() -> tuple[SqliteAdapter, str, str]:
        session = adapter.session
        assert session is not None
        session.add(
            SourceRow(
                id="doc-a",
                database_name="default",
                filename="doc-a.txt",
                filepath="",
                source_type="text",
                status="indexing",
                full_text=DOCUMENT_TEXT,
            )
        )
        session.flush()
        session.add(
            DocumentChunk(
                id="doc-a-c0",
                database_name="default",
                source_id="doc-a",
                chunk_index=0,
                content=DOCUMENT_TEXT,
            )
        )
        session.commit()
        indexing_service = SimpleNamespace(settings=EngineSettings())
        handler = "chaoscypher_core.operations.importing.embedding_handler"
        with (
            patch(f"{handler}._embed_unembedded_chunks", new=AsyncMock(return_value=1)),
            patch(f"{handler}._queue_post_indexing_analysis", new=AsyncMock()),
            patch(f"{handler}.event_bus"),
        ):
            result = await _run_embedding(
                source_id="doc-a",
                file_info={"filename": "doc-a.txt"},
                adapter=adapter,
                indexing_service=indexing_service,
                settings=SimpleNamespace(),
                database_name="default",
                task_id=None,
            )
        assert result["status"] == "indexed"
        return adapter, "default", "doc-a"

    return index


@pytest.fixture
def indexer(
    request: pytest.FixtureRequest, world: SimpleNamespace, in_memory_adapter: SqliteAdapter
) -> Indexer:
    if request.param == "engine_chunk_and_index":
        return _engine_path(world)
    return _embed_handler_path(in_memory_adapter)


def _patched_chat(chat: FakeChat) -> Any:
    return patch(
        "chaoscypher_core.services.sources.summaries.import_summary_chat",
        return_value=chat,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("indexer", PATHS, indirect=True)
async def test_the_indexed_document_gets_the_model_summary(indexer: Indexer) -> None:
    chat = FakeChat()
    with _patched_chat(chat):
        storage, database, source_id = await indexer()

    row = storage.get_source(source_id, database)
    assert row is not None
    assert row["status"] == "indexed"
    assert row["summary"] == SUMMARY
    assert chat.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("indexer", PATHS, indirect=True)
async def test_a_failed_summary_still_completes_indexing(indexer: Indexer) -> None:
    chat = FakeChat(error=ConnectionError("model down"))
    with _patched_chat(chat):
        storage, database, source_id = await indexer()

    row = storage.get_source(source_id, database)
    assert row is not None
    assert row["status"] == "indexed"
    assert row["summary"] is None
    assert chat.calls == 1
