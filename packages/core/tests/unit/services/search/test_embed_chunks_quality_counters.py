# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Quality counter wiring tests for IndexingService.embed_chunks.

Phase 7 audit-remediation (2026-05-09).

Pins two new counter sites:

- P1 #6 — EMBEDDING_CHUNK_FAILURES: incremented once per chunk that
  cannot be persisted because the chunk row was not found (NotFoundError).
- P2 #3 — EMBEDDING_DIMENSION_MISMATCHES: incremented when
  _validate_embeddings raises ValidationError with field="embedding_dimensions",
  BEFORE the exception propagates.

Both use the same ``increment_quality_counter`` helper used by every other
pipeline stage.  The helper is best-effort — a storage UPDATE failure logs
and returns — so the counter increment must never block the pipeline.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core import EngineSettings
from chaoscypher_core.exceptions import NotFoundError, ValidationError
from chaoscypher_core.services.quality.counters import QualityCounter
from chaoscypher_core.services.search.engine.index import IndexingService


# ---------------------------------------------------------------------------
# Helpers (mirror the pattern from test_embed_chunks_strict.py)
# ---------------------------------------------------------------------------


def _make_chunks(n: int) -> list[dict[str, Any]]:
    """Build n minimal chunk dicts with id and content fields."""
    return [{"id": f"chunk-{i}", "content": f"text-{i}"} for i in range(n)]


def _make_vector(dim: int, fill: float = 0.1) -> list[float]:
    """Build a valid vector of the given dimension."""
    return [fill] * dim


def _fake_provider_returning(vectors: list[list[float]]) -> AsyncMock:
    """Build an AsyncMock provider that returns ``vectors`` from batch_embed."""
    embedding_svc = AsyncMock()
    batch_result = MagicMock()
    batch_result.embeddings = vectors
    batch_result.total = len(vectors)
    embedding_svc.batch_embed = AsyncMock(return_value=batch_result)
    return embedding_svc


def _make_service(
    *,
    vectors: list[list[float]],
    vector_dimensions: int = 4,
) -> IndexingService:
    """Build an IndexingService with a controllable embedding provider and MagicMock repo."""
    repo = MagicMock()
    repo.database_name = "default"
    # Seed the 2026-05-22 race-guard probe so embed_chunks doesn't
    # short-circuit on the source-deleted path.
    repo.get_chunks_by_source.return_value = ([], 1)
    settings = EngineSettings()
    settings.search.vector_dimensions = vector_dimensions
    settings.batching.embedding_batch_size = 1000
    settings.batching.embedding_concurrency = 1
    provider = _fake_provider_returning(vectors)
    return IndexingService(repository=repo, settings=settings, embedding_service=provider)


# ---------------------------------------------------------------------------
# P1 #6 — EMBEDDING_CHUNK_FAILURES
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmbeddingChunkFailureCounter:
    """EMBEDDING_CHUNK_FAILURES is incremented per NotFoundError during persistence."""

    @pytest.mark.asyncio
    async def test_single_not_found_increments_counter_once(self) -> None:
        """When one of two chunks raises NotFoundError, the counter increments once."""
        chunks = _make_chunks(2)
        vectors = [_make_vector(4), _make_vector(4)]
        service = _make_service(vectors=vectors, vector_dimensions=4)

        # First chunk persists OK; second raises NotFoundError.
        service.repository.update_chunk_embedding.side_effect = [
            None,
            NotFoundError("chunk", "chunk-1"),
        ]

        with patch(
            "chaoscypher_core.services.search.engine.index.increment_quality_counter",
            new_callable=AsyncMock,
        ) as mock_increment:
            result = await service.embed_chunks(
                chunks=chunks,
                source_id="src-pfail",
                database_name="default",
            )

        # Counter incremented exactly once (for the one NotFoundError).
        mock_increment.assert_awaited_once_with(
            adapter=service.repository,
            source_id="src-pfail",
            database_name="default",
            counter=QualityCounter.EMBEDDING_CHUNK_FAILURES,
        )
        # The successful chunk is counted; the failed one is skipped.
        assert result == 1

    @pytest.mark.asyncio
    async def test_two_not_found_increments_counter_twice(self) -> None:
        """Two NotFoundError chunks → counter incremented twice, result = 0."""
        chunks = _make_chunks(2)
        vectors = [_make_vector(4), _make_vector(4)]
        service = _make_service(vectors=vectors, vector_dimensions=4)

        service.repository.update_chunk_embedding.side_effect = [
            NotFoundError("chunk", "chunk-0"),
            NotFoundError("chunk", "chunk-1"),
        ]

        with patch(
            "chaoscypher_core.services.search.engine.index.increment_quality_counter",
            new_callable=AsyncMock,
        ) as mock_increment:
            result = await service.embed_chunks(
                chunks=chunks,
                source_id="src-twofail",
                database_name="default",
            )

        assert mock_increment.await_count == 2
        # Both calls used EMBEDDING_CHUNK_FAILURES.
        for awaited_call in mock_increment.await_args_list:
            assert awaited_call.kwargs["counter"] == QualityCounter.EMBEDDING_CHUNK_FAILURES
        assert result == 0

    @pytest.mark.asyncio
    async def test_no_not_found_does_not_increment_failure_counter(self) -> None:
        """Happy-path persistence → EMBEDDING_CHUNK_FAILURES is never called."""
        chunks = _make_chunks(3)
        vectors = [_make_vector(4) for _ in range(3)]
        service = _make_service(vectors=vectors, vector_dimensions=4)
        service.repository.update_chunk_embedding.return_value = None

        with patch(
            "chaoscypher_core.services.search.engine.index.increment_quality_counter",
            new_callable=AsyncMock,
        ) as mock_increment:
            result = await service.embed_chunks(
                chunks=chunks,
                source_id="src-happy",
                database_name="default",
            )

        # Counter must not fire for a successful pass.
        failure_calls = [
            c
            for c in mock_increment.await_args_list
            if c.kwargs.get("counter") == QualityCounter.EMBEDDING_CHUNK_FAILURES
        ]
        assert failure_calls == []
        assert result == 3

    @pytest.mark.asyncio
    async def test_non_not_found_error_propagates_without_incrementing(self) -> None:
        """A non-NotFoundError exception is re-raised and the counter is NOT incremented."""
        chunks = _make_chunks(2)
        vectors = [_make_vector(4), _make_vector(4)]
        service = _make_service(vectors=vectors, vector_dimensions=4)

        # Second chunk raises a generic RuntimeError (not NotFoundError).
        service.repository.update_chunk_embedding.side_effect = [
            None,
            RuntimeError("unexpected DB error"),
        ]

        with patch(
            "chaoscypher_core.services.search.engine.index.increment_quality_counter",
            new_callable=AsyncMock,
        ) as mock_increment:
            with pytest.raises(RuntimeError, match="unexpected DB error"):
                await service.embed_chunks(
                    chunks=chunks,
                    source_id="src-rterr",
                    database_name="default",
                )

        # RuntimeError path does not increment the failure counter.
        failure_calls = [
            c
            for c in mock_increment.await_args_list
            if c.kwargs.get("counter") == QualityCounter.EMBEDDING_CHUNK_FAILURES
        ]
        assert failure_calls == []


# ---------------------------------------------------------------------------
# P2 #3 — EMBEDDING_DIMENSION_MISMATCHES
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmbeddingDimensionMismatchCounter:
    """EMBEDDING_DIMENSION_MISMATCHES is incremented on vector-dimension mismatch."""

    @pytest.mark.asyncio
    async def test_dim_mismatch_increments_counter_before_raising(self) -> None:
        """Provider returns 768-dim, expected 1536-dim → counter fires then ValidationError propagates."""
        chunks = _make_chunks(1)
        vectors = [_make_vector(768)]  # wrong dim
        service = _make_service(vectors=vectors, vector_dimensions=1536)

        with patch(
            "chaoscypher_core.services.search.engine.index.increment_quality_counter",
            new_callable=AsyncMock,
        ) as mock_increment:
            with pytest.raises(ValidationError) as exc_info:
                await service.embed_chunks(
                    chunks=chunks,
                    source_id="src-dimmatch",
                    database_name="default",
                    expected_dimensions=1536,
                )

        # Counter must fire before the raise.
        mock_increment.assert_awaited_once_with(
            adapter=service.repository,
            source_id="src-dimmatch",
            database_name="default",
            counter=QualityCounter.EMBEDDING_DIMENSION_MISMATCHES,
        )
        # The ValidationError itself must still propagate with correct details.
        assert exc_info.value.field == "embedding_dimensions"
        assert exc_info.value.details["expected"] == 1536
        assert exc_info.value.details["actual"] == 768
        # Persistence must be skipped.
        service.repository.update_chunk_embedding.assert_not_called()

    @pytest.mark.asyncio
    async def test_dim_mismatch_counter_uses_correct_source_id_and_db(self) -> None:
        """Counter call receives the source_id and database_name passed to embed_chunks."""
        chunks = _make_chunks(1)
        vectors = [_make_vector(512)]  # wrong dim (expected 1536)
        service = _make_service(vectors=vectors, vector_dimensions=1536)

        with patch(
            "chaoscypher_core.services.search.engine.index.increment_quality_counter",
            new_callable=AsyncMock,
        ) as mock_increment:
            with pytest.raises(ValidationError):
                await service.embed_chunks(
                    chunks=chunks,
                    source_id="src-abc",
                    database_name="mydb",
                )

        kwargs = mock_increment.await_args.kwargs
        assert kwargs["source_id"] == "src-abc"
        assert kwargs["database_name"] == "mydb"
        assert kwargs["counter"] == QualityCounter.EMBEDDING_DIMENSION_MISMATCHES

    @pytest.mark.asyncio
    async def test_count_mismatch_does_not_fire_dim_mismatch_counter(self) -> None:
        """A count mismatch (wrong number of vectors) must NOT fire the dim-mismatch counter."""
        chunks = _make_chunks(3)
        # Provider returns only 2 vectors for 3 chunks.
        vectors = [_make_vector(4), _make_vector(4)]
        service = _make_service(vectors=vectors, vector_dimensions=4)

        with patch(
            "chaoscypher_core.services.search.engine.index.increment_quality_counter",
            new_callable=AsyncMock,
        ) as mock_increment:
            with pytest.raises(ValidationError) as exc_info:
                await service.embed_chunks(
                    chunks=chunks,
                    source_id="src-countmiss",
                    database_name="default",
                )

        # ValidationError fires for count mismatch (field="embeddings") not dim.
        assert exc_info.value.field == "embeddings"
        # The dim-mismatch counter must NOT have been incremented.
        dim_mismatch_calls = [
            c
            for c in mock_increment.await_args_list
            if c.kwargs.get("counter") == QualityCounter.EMBEDDING_DIMENSION_MISMATCHES
        ]
        assert dim_mismatch_calls == []

    @pytest.mark.asyncio
    async def test_matching_dimensions_does_not_increment_dim_counter(self) -> None:
        """Correct dim → EMBEDDING_DIMENSION_MISMATCHES is never called."""
        chunks = _make_chunks(2)
        vectors = [_make_vector(4), _make_vector(4)]
        service = _make_service(vectors=vectors, vector_dimensions=4)
        service.repository.update_chunk_embedding.return_value = None

        with patch(
            "chaoscypher_core.services.search.engine.index.increment_quality_counter",
            new_callable=AsyncMock,
        ) as mock_increment:
            result = await service.embed_chunks(
                chunks=chunks,
                source_id="src-ok",
                database_name="default",
            )

        dim_mismatch_calls = [
            c
            for c in mock_increment.await_args_list
            if c.kwargs.get("counter") == QualityCounter.EMBEDDING_DIMENSION_MISMATCHES
        ]
        assert dim_mismatch_calls == []
        assert result == 2
