# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for incremental embedding resume semantics."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.operations.importing.embedding_handler import (
    _embed_unembedded_chunks,
)


@pytest.mark.asyncio
async def test_embedding_skips_already_embedded_chunks() -> None:
    """Unembedded chunks are embedded in keyset waves that drain and terminate.

    The adapter mock must honour the ``after_chunk_index`` cursor: the first
    page returns the work, the next (cursor advanced) returns empty. A mock
    that returns the same page regardless of the cursor would spin the wave
    loop forever — the regression this guards (see _embed_unembedded_chunks's
    keyset loop).
    """
    unembedded = [
        {"id": "c2", "chunk_index": 1, "content": "b", "embedded_at": None},
        {"id": "c3", "chunk_index": 2, "content": "c", "embedded_at": None},
    ]

    adapter = MagicMock()
    adapter.count_unembedded_chunks = MagicMock(return_value=len(unembedded))
    # First-pass embedding: no recorded source row, so no dimension cross-check.
    adapter.get_source = MagicMock(return_value=None)

    def _list_unembedded(*, source_id, database_name, after_chunk_index, limit):
        # Honour the keyset cursor so the wave loop terminates.
        return unembedded if after_chunk_index is None else []

    adapter.list_unembedded_chunks = MagicMock(side_effect=_list_unembedded)
    adapter.mark_chunks_embedded = MagicMock(return_value=2)

    indexing_service = MagicMock()
    indexing_service.embed_chunks = AsyncMock(return_value=2)

    count = await _embed_unembedded_chunks(
        source_id="s-1",
        database_name="default",
        adapter=adapter,
        indexing_service=indexing_service,
    )

    assert count == 2
    indexing_service.embed_chunks.assert_awaited_once()
    assert indexing_service.embed_chunks.await_args.kwargs["chunks"] == unembedded
    adapter.mark_chunks_embedded.assert_called_once()
    # One page of work + one empty page that terminates the keyset loop.
    assert adapter.list_unembedded_chunks.call_count == 2
    first_call = adapter.list_unembedded_chunks.call_args_list[0]
    assert first_call.kwargs["source_id"] == "s-1"
    assert first_call.kwargs["after_chunk_index"] is None


@pytest.mark.asyncio
async def test_embedding_is_noop_when_all_chunks_embedded() -> None:
    """No embedding work when count_unembedded_chunks reports zero."""
    adapter = MagicMock()
    adapter.count_unembedded_chunks = MagicMock(return_value=0)
    adapter.list_unembedded_chunks = MagicMock(return_value=[])
    indexing_service = MagicMock()
    indexing_service.embed_chunks = AsyncMock()

    count = await _embed_unembedded_chunks(
        source_id="s-1",
        database_name="default",
        adapter=adapter,
        indexing_service=indexing_service,
    )

    assert count == 0
    indexing_service.embed_chunks.assert_not_awaited()
    # count==0 short-circuits before the wave loop ever lists chunks.
    adapter.list_unembedded_chunks.assert_not_called()


@pytest.mark.asyncio
async def test_wave_loop_breaks_when_keyset_cursor_does_not_advance() -> None:
    """A misbehaving adapter whose keyset never advances must not spin forever.

    Defense-in-depth: a correct adapter returns chunks strictly past
    ``after_chunk_index``, so the cursor always advances. If one ever returns
    a page that does not move the cursor forward, the loop must break (logged)
    rather than re-fetch the same wave indefinitely — the failure mode that
    burned ~5 GB / 86 min under a stale test mock.
    """
    same_chunk = [{"id": "c1", "chunk_index": 1, "content": "a", "embedded_at": None}]

    adapter = MagicMock()
    adapter.count_unembedded_chunks = MagicMock(return_value=1)
    adapter.get_source = MagicMock(return_value=None)
    # Bug simulation: ignores after_chunk_index, always returns the same chunk.
    adapter.list_unembedded_chunks = MagicMock(return_value=same_chunk)
    adapter.mark_chunks_embedded = MagicMock(return_value=1)

    indexing_service = MagicMock()
    indexing_service.embed_chunks = AsyncMock(return_value=1)

    # Must return rather than hang. The first wave is processed (cursor
    # None -> 1); the second fetch returns chunk_index 1 again (<= cursor),
    # so the stall guard breaks the loop.
    count = await _embed_unembedded_chunks(
        source_id="s-1",
        database_name="default",
        adapter=adapter,
        indexing_service=indexing_service,
    )

    assert count == 1
    indexing_service.embed_chunks.assert_awaited_once()
