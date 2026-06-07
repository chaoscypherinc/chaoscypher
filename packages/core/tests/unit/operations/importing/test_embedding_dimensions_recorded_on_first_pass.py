# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 7 audit-remediation (2026-05-09): P1 #7 — dim recorded at chunk-write time.

Closes audit finding P1 #7 (embedding-dimension drift).

Before this fix, ``embedding_dimensions`` was only written on
``complete_indexing`` (post-completion).  Between first-pass embedding
success and ``complete_indexing``, if the operator changed
``settings.search.vector_dimensions``, the re-embed path would read the
stale settings value and allow dimension-incompatible chunks to be
written silently.

The fix: record ``embedding_dimensions`` on the ``SourceRow`` immediately
after the first chunk's embedding wave succeeds — inside
``_embed_unembedded_chunks``, before ``mark_chunks_embedded``.

Tests in this file pin the contract:

1. First-pass embedding (``source_row.embedding_dimensions is None``) →
   ``update_source_columns`` is called with the current dimension before the
   chunks are marked embedded.
2. Re-embed path (``source_row.embedding_dimensions`` already set) →
   ``update_source_columns`` is NOT called (idempotent: never overwrite).
3. Failure to record is logged and swallowed — it must not block the pipeline.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.operations.importing.embedding_handler import (
    _embed_unembedded_chunks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(
    *,
    source_row: dict[str, Any] | None,
    unembedded: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Build a minimal adapter stub for ``_embed_unembedded_chunks``.

    The handler keyset-paginates in waves, so the stub mimics that contract:
    ``count_unembedded_chunks`` drives the early-exit, and
    ``list_unembedded_chunks`` returns the chunks once then ``[]`` so the wave
    loop terminates (a static return_value would spin forever).
    """
    adapter = MagicMock()
    chunks = (
        unembedded
        if unembedded is not None
        else [{"id": "c1", "chunk_index": 0, "content": "hello"}]
    )
    adapter.count_unembedded_chunks.return_value = len(chunks)
    adapter.list_unembedded_chunks.side_effect = [chunks, []]
    adapter.get_source.return_value = source_row
    adapter.mark_chunks_embedded.return_value = None
    # update_source_columns default: succeeds silently.
    adapter.update_source_columns.return_value = None
    return adapter


def _make_indexing_service(*, vector_dimensions: int = 384) -> MagicMock:
    """Build an indexing-service stub whose embed_chunks returns 1 (success)."""
    svc = MagicMock()
    svc.settings.search.vector_dimensions = vector_dimensions
    svc.embed_chunks = AsyncMock(return_value=1)
    return svc


# ---------------------------------------------------------------------------
# P1 #7 — dim recorded at first-pass write time
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmbeddingDimensionsRecordedOnFirstPass:
    """``_embed_unembedded_chunks`` writes ``embedding_dimensions`` on first pass."""

    @pytest.mark.asyncio
    async def test_first_pass_records_dimensions_before_mark_embedded(self) -> None:
        """First-pass embedding (no prior dim) → update_source_columns fires BEFORE
        mark_chunks_embedded with the dimension from settings.

        This is the key ordering guarantee: the dim is on the row before the
        chunks are stamped ``embedded_at``, so a crash between dim-write and
        mark-embedded leaves the source in "re-embed me" state with a dim already
        recorded — the next attempt can cross-check rather than blindly accepting
        whatever the settings say at that point.
        """
        adapter = _make_adapter(
            source_row={"id": "src-first", "embedding_dimensions": None},
        )
        service = _make_indexing_service(vector_dimensions=1536)

        await _embed_unembedded_chunks(
            source_id="src-first",
            database_name="default",
            adapter=adapter,
            indexing_service=service,
        )

        # update_source_columns must have been called with the dimension.
        adapter.update_source_columns.assert_called_once_with(
            source_id="src-first",
            database_name="default",
            updates={"embedding_dimensions": 1536},
        )

        # Ordering: update_source_columns BEFORE mark_chunks_embedded.
        update_call_order = [
            i for i, c in enumerate(adapter.mock_calls) if "update_source_columns" in str(c)
        ]
        mark_call_order = [
            i for i, c in enumerate(adapter.mock_calls) if "mark_chunks_embedded" in str(c)
        ]
        assert update_call_order, "update_source_columns was not called"
        assert mark_call_order, "mark_chunks_embedded was not called"
        assert max(update_call_order) < min(mark_call_order), (
            "update_source_columns must fire BEFORE mark_chunks_embedded"
        )

    @pytest.mark.asyncio
    async def test_re_embed_does_not_overwrite_existing_dimensions(self) -> None:
        """Re-embed path (dim already set) → update_source_columns NOT called.

        Idempotency: once the dimension is recorded it is authoritative.
        A re-embed wave does not reset it.
        """
        adapter = _make_adapter(
            source_row={"id": "src-re", "embedding_dimensions": 1536},
        )
        service = _make_indexing_service(vector_dimensions=1536)

        await _embed_unembedded_chunks(
            source_id="src-re",
            database_name="default",
            adapter=adapter,
            indexing_service=service,
        )

        adapter.update_source_columns.assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_to_record_is_logged_and_swallowed(self) -> None:
        """If update_source_columns raises, the pipeline continues (best-effort)."""
        adapter = _make_adapter(
            source_row={"id": "src-fail", "embedding_dimensions": None},
        )
        adapter.update_source_columns.side_effect = RuntimeError("db gone")
        service = _make_indexing_service(vector_dimensions=384)

        # Must NOT raise — swallowed.
        count = await _embed_unembedded_chunks(
            source_id="src-fail",
            database_name="default",
            adapter=adapter,
            indexing_service=service,
        )

        # Pipeline continues: chunks are still marked embedded.
        assert count == 1
        adapter.mark_chunks_embedded.assert_called_once()

    @pytest.mark.asyncio
    async def test_source_row_missing_does_not_record_dimensions(self) -> None:
        """If get_source returns None, update_source_columns is not called.

        No source row → nothing to update; first-pass logic is skipped
        gracefully (same behaviour as the existing expected_dimensions=None path).
        """
        adapter = _make_adapter(source_row=None)
        service = _make_indexing_service(vector_dimensions=384)

        await _embed_unembedded_chunks(
            source_id="src-missing",
            database_name="default",
            adapter=adapter,
            indexing_service=service,
        )

        adapter.update_source_columns.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_chunks_to_embed_skips_dimension_recording(self) -> None:
        """If there are no unembedded chunks, update_source_columns must not be called.

        Zero-chunk path returns early before embed_chunks is even called, so
        there is no dimension to record.
        """
        adapter = _make_adapter(
            source_row={"id": "src-empty", "embedding_dimensions": None},
            unembedded=[],  # nothing to embed
        )
        service = _make_indexing_service(vector_dimensions=384)

        count = await _embed_unembedded_chunks(
            source_id="src-empty",
            database_name="default",
            adapter=adapter,
            indexing_service=service,
        )

        assert count == 0
        adapter.update_source_columns.assert_not_called()
