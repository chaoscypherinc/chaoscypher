# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``_ensure_chunk_embeddings`` — the finalize-time backfill.

Steady state the chunk handler eager-writes ``raw_entity_embeddings``
alongside ``raw_entities``, so the backfill is a no-op.  When chunk rows
pre-date the schema change (or the embedding service was unavailable
at extract time), the backfill computes and persists embeddings before
dedup runs so retry never re-pays the embedding cost.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.operations.extraction import extraction_finalizer


def _entity(name: str) -> dict[str, Any]:
    return {"name": name, "type": "Person", "description": f"about {name}"}


class TestEnsureChunkEmbeddings:
    """Backfill behavior across the cache hit / miss / partial paths."""

    @pytest.mark.asyncio
    async def test_steady_state_is_no_op_when_all_rows_have_embeddings(self) -> None:
        """Every chunk has embeddings → backfill returns aggregated, no embed calls."""
        adapter = MagicMock()
        adapter.set_chunk_task_embeddings = MagicMock()
        embedding_service = MagicMock()
        embedding_service.batch_embed = AsyncMock(
            side_effect=AssertionError("batch_embed should not be called on steady state")
        )

        completed_tasks = [
            {
                "id": "ct1",
                "raw_entities": [_entity("A"), _entity("B")],
                "raw_entity_embeddings": [[0.1] * 4, [0.2] * 4],
            },
            {
                "id": "ct2",
                "raw_entities": [_entity("C")],
                "raw_entity_embeddings": [[0.3] * 4],
            },
        ]

        result = await extraction_finalizer._ensure_chunk_embeddings(
            adapter=adapter,
            completed_tasks=completed_tasks,
            embedding_service=embedding_service,
            job_id="job1",
        )

        assert result == [[0.1] * 4, [0.2] * 4, [0.3] * 4]
        adapter.set_chunk_task_embeddings.assert_not_called()

    @pytest.mark.asyncio
    async def test_backfills_null_rows_and_persists(self) -> None:
        """Row with NULL embeddings → compute, persist, and aggregate."""
        adapter = MagicMock()
        adapter.set_chunk_task_embeddings = MagicMock()

        # Fake embedding service: returns one zero-vector per text
        async def _fake_batch_embed(texts: list[str]) -> Any:
            return MagicMock(embeddings=[[0.0] * 4 for _ in texts])

        embedding_service = MagicMock()
        embedding_service.batch_embed = AsyncMock(side_effect=_fake_batch_embed)

        completed_tasks = [
            {
                "id": "ct1",
                "raw_entities": [_entity("A"), _entity("B")],
                "raw_entity_embeddings": None,  # legacy row, needs backfill
            },
        ]

        result = await extraction_finalizer._ensure_chunk_embeddings(
            adapter=adapter,
            completed_tasks=completed_tasks,
            embedding_service=embedding_service,
            job_id="job1",
        )

        assert result == [[0.0] * 4, [0.0] * 4]
        # Backfill persisted to the chunk task row
        adapter.set_chunk_task_embeddings.assert_called_once_with("ct1", [[0.0] * 4, [0.0] * 4])

    @pytest.mark.asyncio
    async def test_returns_none_when_embedding_service_unavailable(self) -> None:
        """Embedding service down during backfill → return None (dedup will recompute)."""
        adapter = MagicMock()
        adapter.set_chunk_task_embeddings = MagicMock()

        async def _failing_batch_embed(_texts: list[str]) -> Any:
            raise RuntimeError("embedding service down")

        embedding_service = MagicMock()
        embedding_service.batch_embed = AsyncMock(side_effect=_failing_batch_embed)

        completed_tasks = [
            {
                "id": "ct1",
                "raw_entities": [_entity("A")],
                "raw_entity_embeddings": None,
            },
        ]

        result = await extraction_finalizer._ensure_chunk_embeddings(
            adapter=adapter,
            completed_tasks=completed_tasks,
            embedding_service=embedding_service,
            job_id="job1",
        )

        assert result is None
        adapter.set_chunk_task_embeddings.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_rows_with_empty_entities(self) -> None:
        """Chunks with no raw_entities (filtered chunks) are skipped silently."""
        adapter = MagicMock()
        adapter.set_chunk_task_embeddings = MagicMock()

        async def _fake_batch_embed(texts: list[str]) -> Any:
            return MagicMock(embeddings=[[0.0] * 4 for _ in texts])

        embedding_service = MagicMock()
        embedding_service.batch_embed = AsyncMock(side_effect=_fake_batch_embed)

        completed_tasks = [
            {"id": "ct1", "raw_entities": [], "raw_entity_embeddings": None},
            {
                "id": "ct2",
                "raw_entities": [_entity("A")],
                "raw_entity_embeddings": [[0.5] * 4],
            },
        ]

        result = await extraction_finalizer._ensure_chunk_embeddings(
            adapter=adapter,
            completed_tasks=completed_tasks,
            embedding_service=embedding_service,
            job_id="job1",
        )

        assert result == [[0.5] * 4]
        embedding_service.batch_embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_backfill_persist_failure_is_non_fatal(self) -> None:
        """If set_chunk_task_embeddings raises, return aggregated anyway (best-effort persist)."""
        adapter = MagicMock()
        adapter.set_chunk_task_embeddings = MagicMock(side_effect=RuntimeError("DB locked"))

        async def _fake_batch_embed(texts: list[str]) -> Any:
            return MagicMock(embeddings=[[0.0] * 4 for _ in texts])

        embedding_service = MagicMock()
        embedding_service.batch_embed = AsyncMock(side_effect=_fake_batch_embed)

        completed_tasks = [
            {
                "id": "ct1",
                "raw_entities": [_entity("A")],
                "raw_entity_embeddings": None,
            },
        ]

        result = await extraction_finalizer._ensure_chunk_embeddings(
            adapter=adapter,
            completed_tasks=completed_tasks,
            embedding_service=embedding_service,
            job_id="job1",
        )

        # Aggregated embeddings still returned despite persist failure;
        # dedup will use them in-memory for this finalize pass; next retry
        # will recompute (the persist failure is logged).
        assert result == [[0.0] * 4]
