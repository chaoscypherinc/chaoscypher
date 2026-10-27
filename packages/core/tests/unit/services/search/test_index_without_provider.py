# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""IndexingService with no embedding provider: the keyword-only mode (vector search off)."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.services.search.engine.index import IndexingService
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.embedding import FakeEmbeddingProvider
from chaoscypher_core.utils.chunk import ChunkingService


SOURCE = "src_keyword_only"


@pytest.fixture
def settings() -> EngineSettings:
    s = EngineSettings()
    s.search.vector_dimensions = 8
    return s


@pytest.fixture
def adapter(tmp_path: Path, settings: EngineSettings) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    adapter.create_source(
        {
            "id": SOURCE,
            "database_name": "default",
            "filename": "note.txt",
            "filepath": "note.txt",
            "file_type": "txt",
            "status": "pending",
        }
    )
    yield adapter
    adapter.disconnect()


async def _store_chunks(adapter: SqliteAdapter, settings: EngineSettings) -> None:
    await ChunkingService(settings=settings, repository=adapter).create_chunks(
        full_text="Pierre Bezukhov moved to Lyon.", source_id=SOURCE
    )


@pytest.mark.asyncio
async def test_create_index_with_no_provider_keeps_the_chunks_keyword_only(
    adapter: SqliteAdapter, settings: EngineSettings
) -> None:
    await _store_chunks(adapter, settings)
    service = IndexingService(repository=adapter, settings=settings, embedding_service=None)

    result = await service.create_index(source_id=SOURCE)

    assert result == {"chunks_count": 1, "embedding_model": "", "embedding_dimensions": 0}
    (chunk,), _ = adapter.get_chunks_by_source(SOURCE, include_embeddings=True)
    assert chunk["embedding"] is None
    assert adapter.list_unembedded_chunks(source_id=SOURCE, database_name="default")


def test_the_provider_is_a_required_argument() -> None:
    with pytest.raises(TypeError):
        IndexingService(repository=MagicMock(), settings=EngineSettings())  # type: ignore[call-arg]


def test_from_engine_wires_the_engines_provider() -> None:
    engine = SimpleNamespace(
        storage_adapter=MagicMock(), settings=EngineSettings(), embedding_service=object()
    )
    assert IndexingService.from_engine(engine).embedding_service is engine.embedding_service


@pytest.mark.asyncio
async def test_embed_chunks_can_leave_the_chunk_status_alone(
    adapter: SqliteAdapter, settings: EngineSettings
) -> None:
    await _store_chunks(adapter, settings)
    adapter.update_chunk_status(SOURCE, "committed")
    chunks, _ = adapter.get_chunks_by_source(SOURCE)
    service = IndexingService(
        repository=adapter, settings=settings, embedding_service=FakeEmbeddingProvider(8)
    )

    embedded = await service.embed_chunks(
        chunks=chunks, source_id=SOURCE, database_name="default", chunk_status=None
    )

    assert embedded == 1
    adapter.session.expire_all()
    (chunk,), _ = adapter.get_chunks_by_source(SOURCE, include_embeddings=True)
    assert chunk["status"] == "committed"
    assert chunk["embedding"] is not None
