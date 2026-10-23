# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The ``question_parts`` lever: a two-part question searches the chunk index whole and part by part."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    _interleave_hits,
)
from tests.fakes.graphrag_world import make_chunk, walk_handler


TWO_PART = (
    "Which guest at Anna Pávlovna's called the execution a political necessity, "
    "and what was that guest's first breach of manners earlier in the evening?"
)
CHUNKS = [make_chunk(f"c{i}", f"Sentence {i}.") for i in range(1, 7)]


def test_interleave_hits_takes_every_lists_first_hit_then_the_seconds_each_id_once() -> None:
    whole = [("chunk:c1", 0.9), ("chunk:c2", 0.8), ("chunk:c3", 0.7)]
    first = [("chunk:c2", 0.95), ("chunk:c4", 0.6)]
    second = [("chunk:c5", 0.5), ("chunk:c1", 0.4), ("chunk:c6", 0.3)]
    assert [rid for rid, _ in _interleave_hits([whole, first, second])] == [
        "chunk:c1",
        "chunk:c2",
        "chunk:c5",
        "chunk:c4",
        "chunk:c3",
        "chunk:c6",
    ]
    assert _interleave_hits([whole]) == whole
    assert _interleave_hits([]) == []


@pytest.mark.asyncio
async def test_a_two_part_question_is_searched_whole_and_by_part_with_the_lever() -> None:
    """Three searches (the whole, each part), the hits interleaved by rank, the count on the stats."""
    h = walk_handler(chunks=CHUNKS, question_parts=True, chunk_limit=6)
    h.search.hybrid_search = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [("chunk:c1", 0.9), ("chunk:c2", 0.8)],  # the whole question
            [("chunk:c2", 0.9), ("chunk:c3", 0.8)],  # part one
            [("chunk:c4", 0.9), ("chunk:c5", 0.8)],  # part two
        ]
    )
    result = await h.graphrag_search(TWO_PART)
    searched = [call.args[0] for call in h.search.hybrid_search.await_args_list]
    assert searched == [
        TWO_PART,
        "Which guest at Anna Pávlovna's called the execution a political necessity?",
        "what was that guest's first breach of manners earlier in the evening?",
    ]
    vector = [c["chunk_id"] for c in result["chunks"] if c["retrieval_origin"] == "vector"]
    assert vector == ["c1", "c2", "c4", "c3", "c5"]
    assert result["retrieval_stats"]["question_parts"] == 2
    assert result["retrieval_stats"]["faults"] == []


@pytest.mark.asyncio
async def test_a_one_part_question_and_the_lever_off_search_once() -> None:
    for levers, query in (({"question_parts": True}, "Who did Lise marry?"), ({}, TWO_PART)):
        h = walk_handler(chunks=CHUNKS[:2], **levers)
        result = await h.graphrag_search(query)
        assert h.search.hybrid_search.await_count == 1
        assert "question_parts" not in result["retrieval_stats"]
