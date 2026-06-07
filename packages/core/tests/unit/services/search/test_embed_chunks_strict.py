# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Strict count + per-vector shape validation for IndexingService.embed_chunks.

Pins the F35 contract: the embedding provider must return exactly N vectors
for N chunks, each vector must have the configured dimension, no NaN/Inf.
On any validation failure the method must raise ``ValidationError`` BEFORE
persisting anything — no chunk gets ``embedded_at`` set, no vector gets
written to the search backend. This guarantees the queue handler can retry
the entire wave cleanly.
"""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core import EngineSettings
from chaoscypher_core.exceptions import ChaosCypherException, ValidationError
from chaoscypher_core.services.search.engine.index import IndexingService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunks(n: int) -> list[dict[str, Any]]:
    """Build n minimal chunk dicts with id and content fields."""
    return [{"id": f"chunk-{i}", "content": f"text-{i}"} for i in range(n)]


def _make_vector(dim: int, fill: float = 0.1) -> list[float]:
    """Build a vector of the given dimension."""
    return [fill] * dim


def _make_indexing_service(
    *, embedding_service: Any = None, vector_dimensions: int = 4
) -> IndexingService:
    """Return an IndexingService with a MagicMock repo and configurable dimensions."""
    repo = MagicMock()
    # Seed the 2026-05-22 race-guard probe so embed_chunks doesn't
    # short-circuit on the source-deleted path.
    repo.get_chunks_by_source.return_value = ([], 1)
    settings = EngineSettings()
    settings.search.vector_dimensions = vector_dimensions
    # Make batching predictable: one batch of all chunks at concurrency 1.
    settings.batching.embedding_batch_size = 1000
    settings.batching.embedding_concurrency = 1
    return IndexingService(
        repository=repo,
        settings=settings,
        embedding_service=embedding_service,
    )


def _fake_provider_returning(vectors: list[list[float]]) -> AsyncMock:
    """Build an AsyncMock embedding provider whose batch_embed returns ``vectors``.

    The IndexingService's ``_generate_chunk_embeddings`` calls
    ``provider.batch_embed(batch, batch_size=...)`` and reads
    ``result.embeddings`` + ``result.total``. We return everything in one
    batch by configuring a large batch_size on the service.
    """
    embedding_svc = AsyncMock()
    batch_result = MagicMock()
    batch_result.embeddings = vectors
    batch_result.total = len(vectors)
    embedding_svc.batch_embed = AsyncMock(return_value=batch_result)
    return embedding_svc


# ---------------------------------------------------------------------------
# F35.a — Count mismatch (provider returns N-1 for N chunks)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmbedChunksCountMismatch:
    """Provider returning fewer vectors than chunks must raise ValidationError."""

    @pytest.mark.asyncio
    async def test_99_vectors_for_100_chunks_raises_and_persists_nothing(self) -> None:
        chunks = _make_chunks(100)
        # Provider returns 99 vectors instead of 100.
        vectors = [_make_vector(4) for _ in range(99)]

        provider = _fake_provider_returning(vectors)
        service = _make_indexing_service(embedding_service=provider, vector_dimensions=4)

        with pytest.raises(ValidationError) as exc_info:
            await service.embed_chunks(
                chunks=chunks,
                source_id="src-test",
                database_name="default",
            )

        exc = exc_info.value
        assert isinstance(exc, ChaosCypherException)
        assert exc.code == "VALIDATION_ERROR"
        # Structured details should expose expected vs actual.
        assert exc.details.get("expected") == 100
        assert exc.details.get("actual") == 99
        # And — critically — no persistence occurred.
        assert service.repository.update_chunk_embedding.call_count == 0


# ---------------------------------------------------------------------------
# F35.b — NaN in vector
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmbedChunksNaNVector:
    """A vector containing NaN must raise ValidationError before persistence."""

    @pytest.mark.asyncio
    async def test_nan_in_one_vector_raises_and_persists_nothing(self) -> None:
        chunks = _make_chunks(100)
        vectors = [_make_vector(4) for _ in range(100)]
        # Inject a NaN into one of the middle vectors.
        vectors[50] = [0.1, float("nan"), 0.3, 0.4]

        provider = _fake_provider_returning(vectors)
        service = _make_indexing_service(embedding_service=provider, vector_dimensions=4)

        with pytest.raises(ValidationError) as exc_info:
            await service.embed_chunks(
                chunks=chunks,
                source_id="src-nan",
                database_name="default",
            )

        exc = exc_info.value
        assert exc.code == "VALIDATION_ERROR"
        assert service.repository.update_chunk_embedding.call_count == 0

    @pytest.mark.asyncio
    async def test_inf_in_one_vector_raises_and_persists_nothing(self) -> None:
        """Inf must be rejected by the same finite-check that rejects NaN."""
        chunks = _make_chunks(10)
        vectors = [_make_vector(4) for _ in range(10)]
        vectors[3] = [0.1, 0.2, float("inf"), 0.4]

        provider = _fake_provider_returning(vectors)
        service = _make_indexing_service(embedding_service=provider, vector_dimensions=4)

        with pytest.raises(ValidationError):
            await service.embed_chunks(
                chunks=chunks,
                source_id="src-inf",
                database_name="default",
            )

        assert service.repository.update_chunk_embedding.call_count == 0


# ---------------------------------------------------------------------------
# F35.c — Wrong dimension
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmbedChunksWrongDimension:
    """A vector with the wrong dimension must raise ValidationError."""

    @pytest.mark.asyncio
    async def test_wrong_dimension_raises_and_persists_nothing(self) -> None:
        chunks = _make_chunks(100)
        # Configure expected dimension = 1536; provider returns 1536-dim
        # vectors except for one that is 768-dim.
        vectors = [_make_vector(1536) for _ in range(100)]
        vectors[42] = _make_vector(768)

        provider = _fake_provider_returning(vectors)
        service = _make_indexing_service(embedding_service=provider, vector_dimensions=1536)

        with pytest.raises(ValidationError) as exc_info:
            await service.embed_chunks(
                chunks=chunks,
                source_id="src-wrong-dim",
                database_name="default",
            )

        exc = exc_info.value
        assert exc.code == "VALIDATION_ERROR"
        assert service.repository.update_chunk_embedding.call_count == 0


# ---------------------------------------------------------------------------
# Sanity check — happy path still persists every chunk.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmbedChunksHappyPath:
    """When all vectors validate, every chunk must be persisted exactly once."""

    @pytest.mark.asyncio
    async def test_all_valid_vectors_persist_all_chunks(self) -> None:
        chunks = _make_chunks(10)
        vectors = [_make_vector(4, fill=float(i) / 10.0) for i in range(10)]
        # Sanity: every value is finite.
        for v in vectors:
            assert all(math.isfinite(x) for x in v)

        provider = _fake_provider_returning(vectors)
        service = _make_indexing_service(embedding_service=provider, vector_dimensions=4)

        result = await service.embed_chunks(
            chunks=chunks,
            source_id="src-happy",
            database_name="default",
        )

        assert result == 10
        assert service.repository.update_chunk_embedding.call_count == 10


# ---------------------------------------------------------------------------
# Race guard — source deleted while neuron was in the Ollama call.
# Added 2026-05-22 after operator log review surfaced 419 warning logs +
# an error from a single mid-flight delete. The probe at the top of the
# per-chunk write loop turns this into one info-level log.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmbedChunksSourceDeletedMidFlight:
    """When CASCADE wipes the source between Ollama returning and persistence."""

    @pytest.mark.asyncio
    async def test_zero_live_chunks_short_circuits_with_zero_writes(self) -> None:
        """Probe returning total=0 must skip the per-chunk loop entirely."""
        chunks = _make_chunks(419)
        vectors = [_make_vector(4) for _ in range(419)]

        provider = _fake_provider_returning(vectors)
        service = _make_indexing_service(embedding_service=provider, vector_dimensions=4)
        # Simulate the CASCADE delete: probe finds no live chunks.
        service.repository.get_chunks_by_source.return_value = ([], 0)

        result = await service.embed_chunks(
            chunks=chunks,
            source_id="src-deleted-mid-flight",
            database_name="default",
        )

        assert result == 0
        # No update_chunk_embedding call would mean no 419 NotFoundError
        # warnings in the operator log.
        service.repository.update_chunk_embedding.assert_not_called()
