# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Embedding integration: chunk-embedding loop reports progress via StageProgress.

Verifies that ``_embed_unembedded_chunks`` writes an ``embedding`` row to
``llm_stage_progress`` with a non-null ``completed_at`` after the embedding
wave finishes.  The ``indexing_service.embed_chunks`` is mocked so no real LLM
call happens — the test exercises the StageProgress wiring, not the model.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Adapter with all tables including llm_stage_progress.

    Mirrors the pattern from test_apply_vision_processing_progress.py:
    SQLModel.metadata creates the SQLModel-managed tables; the Alembic-managed
    ``llm_stage_progress`` is created inline.
    """
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    with engine.connect() as conn:
        conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS llm_stage_progress (
                source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                stage_name TEXT NOT NULL,
                total INTEGER NOT NULL DEFAULT 0,
                processed INTEGER NOT NULL DEFAULT 0,
                avg_ms INTEGER,
                started_at DATETIME,
                last_activity DATETIME,
                completed_at DATETIME,
                extras_json TEXT,
                PRIMARY KEY (source_id, stage_name)
            )
        """)
        )
        conn.commit()

    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    try:
        a.create_source(
            {
                "id": "src-embed",
                "database_name": "default",
                "filename": "doc.pdf",
                "filepath": str(tmp_path / "fake-source-files" / "doc.pdf"),
                "status": "indexing",
            }
        )
        yield a
    finally:
        a.disconnect()


@pytest.mark.asyncio
async def test_chunk_embedding_writes_stage_rows(adapter: SqliteAdapter) -> None:
    """After the chunk-embedding loop runs, llm_stage_progress has an
    'embedding' row with processed == embedded_count and a non-null
    completed_at.
    """
    from chaoscypher_core.operations.importing.embedding_handler import (
        _embed_unembedded_chunks,
    )

    # Seed two unembedded chunks directly in the DB.
    adapter.create_chunk(
        {
            "id": "chunk-1",
            "source_id": "src-embed",
            "database_name": "default",
            "chunk_index": 0,
            "content": "first chunk text",
            "embedded_at": None,
        }
    )
    adapter.create_chunk(
        {
            "id": "chunk-2",
            "source_id": "src-embed",
            "database_name": "default",
            "chunk_index": 1,
            "content": "second chunk text",
            "embedded_at": None,
        }
    )

    # Build a minimal indexing service mock: embed_chunks returns 2 (both
    # chunks embedded) without touching a real embedding model.
    fake_indexing_service = MagicMock()
    fake_indexing_service.settings.search.vector_dimensions = 384
    fake_indexing_service.embed_chunks = AsyncMock(return_value=2)

    await _embed_unembedded_chunks(
        source_id="src-embed",
        database_name="default",
        adapter=adapter,
        indexing_service=fake_indexing_service,
    )

    progress = adapter._fetch_stage_progress("src-embed")
    assert "embedding" in progress, (
        f"expected 'embedding' key in progress, got: {list(progress.keys())}"
    )
    assert progress["embedding"]["total"] == 2, (
        f"expected total=2, got {progress['embedding']['total']}"
    )
    assert progress["embedding"]["processed"] == 2, (
        f"expected processed=2, got {progress['embedding']['processed']}"
    )
    assert progress["embedding"]["completed_at"] is not None, (
        "expected completed_at to be set after embedding completes"
    )


@pytest.mark.asyncio
async def test_chunk_embedding_progress_zero_chunks_skipped(adapter: SqliteAdapter) -> None:
    """When there are no unembedded chunks, _embed_unembedded_chunks returns 0
    and no stage row is written (StageProgress is never entered).
    """
    from chaoscypher_core.operations.importing.embedding_handler import (
        _embed_unembedded_chunks,
    )

    # No chunks seeded — list_unembedded_chunks returns [].  embed_chunks
    # mock is wired but should NEVER fire (early-exit guards it); the
    # assert_not_called below pins that contract.
    fake_indexing_service = MagicMock()
    fake_indexing_service.settings.search.vector_dimensions = 384
    fake_indexing_service.embed_chunks = AsyncMock(return_value=0)

    count = await _embed_unembedded_chunks(
        source_id="src-embed",
        database_name="default",
        adapter=adapter,
        indexing_service=fake_indexing_service,
    )

    assert count == 0
    fake_indexing_service.embed_chunks.assert_not_called()
    # No stage row should be present — the early-exit path does not open StageProgress.
    progress = adapter._fetch_stage_progress("src-embed")
    assert "embedding" not in progress, (
        f"expected no 'embedding' row for zero-chunk path, got: {progress}"
    )
