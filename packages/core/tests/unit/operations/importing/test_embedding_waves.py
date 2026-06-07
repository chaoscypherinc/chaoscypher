# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Embedding stage processes chunks in bounded waves.

Cost / resource-exhaustion fix (2026-05-25 review pass 2): the embedding stage
loaded every unembedded chunk (with its ``content``) into memory at once via
an unbounded ``list_unembedded_chunks().all()`` — a multi-GB document could OOM
the worker. ``_embed_unembedded_chunks`` now keyset-paginates and embeds in
waves, so peak memory is bounded by one wave rather than the whole document.

These tests pin: each ``embed_chunks`` call sees at most ``wave_size`` chunks,
every chunk is embedded exactly once across the waves, the marked-embedded
checkpoint advances per wave, and the loop terminates.
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
    """Adapter with all tables incl. the Alembic-managed llm_stage_progress."""
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
                "id": "src-wave",
                "database_name": "default",
                "filename": "doc.pdf",
                "filepath": str(tmp_path / "doc.pdf"),
                "status": "indexing",
            }
        )
        yield a
    finally:
        a.disconnect()


def _seed_chunks(adapter: SqliteAdapter, n: int) -> None:
    for i in range(n):
        adapter.create_chunk(
            {
                "id": f"chunk-{i}",
                "source_id": "src-wave",
                "database_name": "default",
                "chunk_index": i,
                "content": f"chunk content {i}",
                "embedded_at": None,
            }
        )


@pytest.mark.asyncio
async def test_embeds_in_bounded_waves(adapter: SqliteAdapter) -> None:
    """5 chunks + wave_size=2 -> 3 waves (2,2,1), each <= wave_size."""
    from chaoscypher_core.operations.importing.embedding_handler import (
        _embed_unembedded_chunks,
    )

    _seed_chunks(adapter, 5)

    wave_sizes_seen: list[int] = []

    async def _fake_embed(*, chunks, **_kwargs):
        wave_sizes_seen.append(len(chunks))
        return len(chunks)

    fake_indexing_service = MagicMock()
    fake_indexing_service.settings.search.vector_dimensions = 384
    fake_indexing_service.embed_chunks = AsyncMock(side_effect=_fake_embed)

    total = await _embed_unembedded_chunks(
        source_id="src-wave",
        database_name="default",
        adapter=adapter,
        indexing_service=fake_indexing_service,
        wave_size=2,
    )

    assert total == 5
    # Three waves, none larger than the wave size.
    assert wave_sizes_seen == [2, 2, 1]
    assert max(wave_sizes_seen) <= 2


@pytest.mark.asyncio
async def test_every_chunk_embedded_exactly_once(adapter: SqliteAdapter) -> None:
    """Across waves, each chunk is embedded once and all end up marked."""
    from chaoscypher_core.operations.importing.embedding_handler import (
        _embed_unembedded_chunks,
    )

    _seed_chunks(adapter, 5)

    embedded_ids: list[str] = []

    async def _fake_embed(*, chunks, **_kwargs):
        embedded_ids.extend(c["id"] for c in chunks)
        return len(chunks)

    fake_indexing_service = MagicMock()
    fake_indexing_service.settings.search.vector_dimensions = 384
    fake_indexing_service.embed_chunks = AsyncMock(side_effect=_fake_embed)

    await _embed_unembedded_chunks(
        source_id="src-wave",
        database_name="default",
        adapter=adapter,
        indexing_service=fake_indexing_service,
        wave_size=2,
    )

    # Each chunk embedded exactly once, in order, no duplicates.
    assert embedded_ids == [f"chunk-{i}" for i in range(5)]
    # And all are now marked embedded (the wave checkpoint advanced).
    assert adapter.count_unembedded_chunks(source_id="src-wave", database_name="default") == 0


@pytest.mark.asyncio
async def test_stage_progress_total_is_full_count(adapter: SqliteAdapter) -> None:
    """StageProgress total reflects the whole document, not one wave."""
    from chaoscypher_core.operations.importing.embedding_handler import (
        _embed_unembedded_chunks,
    )

    _seed_chunks(adapter, 5)

    fake_indexing_service = MagicMock()
    fake_indexing_service.settings.search.vector_dimensions = 384
    fake_indexing_service.embed_chunks = AsyncMock(side_effect=lambda *, chunks, **_k: len(chunks))

    await _embed_unembedded_chunks(
        source_id="src-wave",
        database_name="default",
        adapter=adapter,
        indexing_service=fake_indexing_service,
        wave_size=2,
    )

    progress = adapter._fetch_stage_progress("src-wave")
    assert progress["embedding"]["total"] == 5
    assert progress["embedding"]["processed"] == 5
    assert progress["embedding"]["completed_at"] is not None
