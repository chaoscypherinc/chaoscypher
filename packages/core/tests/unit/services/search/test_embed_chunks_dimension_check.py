# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cross-check tests: embed_chunks rejects dimension mismatch against SourceRow.

F28 — Embedding dimension cross-check.

When a source has been embedded once (``SourceRow.embedding_dimensions``
is set) and the user later switches to an embedding model that returns
a different vector dimension, the next re-embedding wave must NOT
silently persist mis-shaped vectors. ``IndexingService.embed_chunks``
accepts an optional ``expected_dimensions`` parameter that, when set,
asserts every returned embedding matches before any persistence happens.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.models import BatchEmbedResult
from chaoscypher_core.services.search.engine.index import IndexingService


def _make_settings(*, vector_dimensions: int = 1536) -> MagicMock:
    """Build a settings stand-in matching ``EngineSettings`` shape."""
    settings = MagicMock()
    settings.batching.embedding_batch_size = 16
    settings.batching.embedding_concurrency = 1
    settings.batching.chunk_fetch_limit = 1000
    settings.search.vector_dimensions = vector_dimensions
    settings.embedding.model = "test-model"
    return settings


def _make_repository() -> MagicMock:
    """Build a repository stub satisfying IndexingProtocol surface used here."""
    repo = MagicMock()
    repo.database_name = "default"
    # The 2026-05-22 race-guard in embed_chunks probes get_chunks_by_source
    # before the write loop to detect a CASCADE-deleted source. Seed the
    # default to "1 chunk live" so existing dimension-check tests don't
    # accidentally hit the source-deleted short-circuit. Tests that want
    # the deleted-mid-flight path override this to (_, 0).
    repo.get_chunks_by_source.return_value = ([], 1)
    return repo


def _batch_embed_returning(vector: list[float], provider: str = "test"):
    """Return an async stub for ``EmbeddingProviderProtocol.batch_embed``."""

    async def _stub(texts, batch_size: int | None = None):
        del batch_size
        return BatchEmbedResult(
            embeddings=[vector for _ in texts],
            total=len(texts),
            failed=0,
            provider=provider,
        )

    return _stub


class TestEmbedChunksDimensionCrossCheck:
    """``embed_chunks`` enforces the SourceRow-recorded dimension when supplied."""

    @pytest.mark.asyncio
    async def test_dimension_mismatch_raises_validation_error(self) -> None:
        """Provider returns 768-dim, SourceRow says 1536-dim → ValidationError."""
        embedding_service = MagicMock()
        embedding_service.batch_embed = _batch_embed_returning([0.1] * 768)

        service = IndexingService(
            repository=_make_repository(),
            settings=_make_settings(vector_dimensions=1536),
            embedding_service=embedding_service,
        )

        chunks = [{"id": "c1", "content": "hello"}]

        with pytest.raises(ValidationError) as exc_info:
            await service.embed_chunks(
                chunks=chunks,
                source_id="src-mismatch",
                database_name="default",
                expected_dimensions=1536,
            )

        assert exc_info.value.field == "embedding_dimensions"
        # Both expected and actual dimensions must appear in the message
        # so on-call can spot the misconfiguration without enabling debug.
        assert "1536" in exc_info.value.message
        assert "768" in exc_info.value.message
        assert exc_info.value.details["expected"] == 1536
        assert exc_info.value.details["actual"] == 768
        assert exc_info.value.details["source_id"] == "src-mismatch"

        # Persistence must be skipped on dimension mismatch — the chunk
        # row should never receive the mis-shaped vector.
        service.repository.update_chunk_embedding.assert_not_called()

    @pytest.mark.asyncio
    async def test_matching_dimensions_succeed(self) -> None:
        """Provider returns 1536-dim, SourceRow says 1536-dim → persists normally."""
        embedding_service = MagicMock()
        embedding_service.batch_embed = _batch_embed_returning([0.5] * 1536)

        repo = _make_repository()
        service = IndexingService(
            repository=repo,
            settings=_make_settings(vector_dimensions=1536),
            embedding_service=embedding_service,
        )

        chunks = [{"id": "c1", "content": "hello"}, {"id": "c2", "content": "world"}]

        count = await service.embed_chunks(
            chunks=chunks,
            source_id="src-ok",
            database_name="default",
            expected_dimensions=1536,
        )

        assert count == 2
        assert repo.update_chunk_embedding.call_count == 2

    @pytest.mark.asyncio
    async def test_expected_dimensions_none_falls_back_to_settings(self) -> None:
        """No SourceRow dimension yet → fall back to settings.vector_dimensions.

        Post-merge with F35: when the caller passes ``expected_dimensions=None``
        (first-pass embedding, no prior SourceRow.embedding_dimensions on
        record), the service still validates against the configured
        ``settings.search.vector_dimensions`` — F35's settings-based check is
        the floor and cannot be bypassed by passing None. This test confirms
        the fall-back: settings is 1536 but provider returns 768 → still
        rejected.
        """
        embedding_service = MagicMock()
        embedding_service.batch_embed = _batch_embed_returning([0.5] * 768)

        repo = _make_repository()
        service = IndexingService(
            repository=repo,
            settings=_make_settings(vector_dimensions=1536),
            embedding_service=embedding_service,
        )

        chunks = [{"id": "c1", "content": "hello"}]

        with pytest.raises(ValidationError) as exc_info:
            await service.embed_chunks(
                chunks=chunks,
                source_id="src-firstpass",
                database_name="default",
                expected_dimensions=None,
            )

        # Falls back to settings dim (1536) for the comparison.
        assert exc_info.value.details["expected"] == 1536
        assert exc_info.value.details["actual"] == 768
        # Persistence skipped — same all-or-nothing guarantee as the
        # explicit-expected_dimensions path.
        repo.update_chunk_embedding.assert_not_called()


class TestEmbeddingHandlerCrossCheck:
    """``_embed_unembedded_chunks`` forwards SourceRow dimension to embed_chunks."""

    @pytest.mark.asyncio
    async def test_handler_passes_sourcerow_dimension(self) -> None:
        """Handler reads SourceRow.embedding_dimensions and forwards it."""
        from chaoscypher_core.operations.importing.embedding_handler import (
            _embed_unembedded_chunks,
        )

        adapter = MagicMock()
        # The handler keyset-paginates in waves: count drives the early-exit,
        # and list returns the chunk once then [] so the loop terminates.
        _wave = [{"id": "c1", "chunk_index": 0, "content": "x"}]
        adapter.count_unembedded_chunks.return_value = len(_wave)
        adapter.list_unembedded_chunks.side_effect = [_wave, []]
        adapter.get_source.return_value = {
            "id": "src-1",
            "embedding_dimensions": 1536,
        }

        indexing_service = MagicMock()
        indexing_service.embed_chunks = AsyncMock(return_value=1)

        await _embed_unembedded_chunks(
            source_id="src-1",
            database_name="default",
            adapter=adapter,
            indexing_service=indexing_service,
        )

        indexing_service.embed_chunks.assert_awaited_once()
        kwargs = indexing_service.embed_chunks.await_args.kwargs
        assert kwargs["expected_dimensions"] == 1536

    @pytest.mark.asyncio
    async def test_handler_passes_none_when_sourcerow_missing(self) -> None:
        """First-pass embedding (no recorded dim) → expected_dimensions=None."""
        from chaoscypher_core.operations.importing.embedding_handler import (
            _embed_unembedded_chunks,
        )

        adapter = MagicMock()
        # The handler keyset-paginates in waves: count drives the early-exit,
        # and list returns the chunk once then [] so the loop terminates.
        _wave = [{"id": "c1", "chunk_index": 0, "content": "x"}]
        adapter.count_unembedded_chunks.return_value = len(_wave)
        adapter.list_unembedded_chunks.side_effect = [_wave, []]
        adapter.get_source.return_value = {
            "id": "src-1",
            "embedding_dimensions": None,
        }

        indexing_service = MagicMock()
        indexing_service.embed_chunks = AsyncMock(return_value=1)

        await _embed_unembedded_chunks(
            source_id="src-1",
            database_name="default",
            adapter=adapter,
            indexing_service=indexing_service,
        )

        kwargs = indexing_service.embed_chunks.await_args.kwargs
        assert kwargs["expected_dimensions"] is None

    @pytest.mark.asyncio
    async def test_handler_passes_none_when_source_missing(self) -> None:
        """Source not found at all → expected_dimensions=None (no crash)."""
        from chaoscypher_core.operations.importing.embedding_handler import (
            _embed_unembedded_chunks,
        )

        adapter = MagicMock()
        # The handler keyset-paginates in waves: count drives the early-exit,
        # and list returns the chunk once then [] so the loop terminates.
        _wave = [{"id": "c1", "chunk_index": 0, "content": "x"}]
        adapter.count_unembedded_chunks.return_value = len(_wave)
        adapter.list_unembedded_chunks.side_effect = [_wave, []]
        adapter.get_source.return_value = None

        indexing_service = MagicMock()
        indexing_service.embed_chunks = AsyncMock(return_value=1)

        await _embed_unembedded_chunks(
            source_id="src-1",
            database_name="default",
            adapter=adapter,
            indexing_service=indexing_service,
        )

        kwargs = indexing_service.embed_chunks.await_args.kwargs
        assert kwargs["expected_dimensions"] is None
