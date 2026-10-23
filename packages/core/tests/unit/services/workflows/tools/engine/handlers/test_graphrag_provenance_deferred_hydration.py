# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The provenance arm hydrates only the chunks that survive the merge (2026-10-12 hunt).

The top-20 PageRank entities cite most of a corpus (168 of Book One's 179
chunks), and rank fusion reads ids and ranks alone, yet every cited chunk
was numbered and built before the budget kept ``chunk_limit`` of them. The
arm now returns stubs and the merge builds the survivors; the merged chunk
list is the eager one, key for key, on the fused path and on the fallback.
"""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.workflows.tools.engine.handlers import graphrag_handlers
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
)
from chaoscypher_core.settings import EngineSettings


CITED = [f"c{i:02d}" for i in range(30)]


class _Chunks:
    """Chunk rows for any id across two sources, plus the source lookup the hydration reads."""

    def get_chunks_by_ids_batch(self, ids: list[str], **_: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": i,
                "content": f"Sentence one of {i}. Sentence two of {i}.",
                "source_id": "s1" if int(i[1:]) % 2 else "s2",
                "database_name": "db",
                "chunk_index": int(i[1:]),
            }
            for i in sorted(ids)
        ]

    def get_source_filenames_by_ids(self, source_ids: list[str], _db: str) -> dict[str, str]:
        return {source_id: f"{source_id}.txt" for source_id in source_ids}


def _handler() -> GraphRAGToolHandlers:
    storage = MagicMock()
    storage.get_citations_batch.return_value = [
        {"entity_uri": "top", "chunk_id": cid, "confidence": 1.0 - n / 100}
        for n, cid in enumerate(CITED)
    ]
    return GraphRAGToolHandlers(
        MagicMock(),
        MagicMock(),
        indexing_repository=_Chunks(),  # type: ignore[arg-type]
        source_storage=storage,
        settings=EngineSettings(),
        database_name="db",
    )


def _eager(handler: GraphRAGToolHandlers, source_ids: list[str] | None) -> list[dict[str, Any]]:
    """The arm as it was: every cited chunk hydrated, in lookup order."""
    rows = handler.indexing.get_chunks_by_ids_batch(CITED)
    return handler._hydrate_chunks(((row, 1.0) for row in rows), source_ids, origin="provenance")


def _vector(handler: GraphRAGToolHandlers) -> list[dict[str, Any]]:
    rows = handler.indexing.get_chunks_by_ids_batch(["c05", "c29", "h1", "h2"])
    return handler._hydrate_chunks(
        ((row, 0.9 - n / 10) for n, row in enumerate(rows)), None, origin="vector"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source_ids", [None, ["s1"]])
async def test_the_merge_of_the_stubs_is_the_eager_merge(source_ids: list[str] | None) -> None:
    handler = _handler()
    stubs = await handler._retrieve_provenance_chunks(["top"], source_ids)
    eager = _eager(handler, source_ids)
    assert [s["chunk_id"] for s in stubs] == [c["chunk_id"] for c in eager]
    vector = _vector(handler)
    deferred = handler._merge_and_rank([], stubs, copy.deepcopy(vector), 6, seed_labels=["x"])
    assert deferred == handler._merge_and_rank([], eager, copy.deepcopy(vector), 6, ["x"])
    assert all(graphrag_handlers._UNHYDRATED_ROW not in c for c in deferred)
    assert any(c["retrieval_origin"] == "both" for c in deferred)


@pytest.mark.asyncio
async def test_the_fallback_hydrates_its_stubs_too(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _handler()
    stubs = await handler._retrieve_provenance_chunks(["top"], None)
    eager = _eager(handler, None)
    vector = _vector(handler)

    def broken(*_: Any) -> Any:
        raise RuntimeError("fusion down")

    monkeypatch.setattr(graphrag_handlers, "reciprocal_rank_fusion", broken)
    deferred = handler._merge_and_rank([], stubs, copy.deepcopy(vector), 4)
    assert deferred == handler._merge_and_rank([], eager, copy.deepcopy(vector), 4)
    assert all(graphrag_handlers._UNHYDRATED_ROW not in c for c in deferred)


@pytest.mark.asyncio
async def test_only_the_survivors_are_numbered(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _handler()
    vector = _vector(handler)
    numbered: list[str] = []
    real = graphrag_handlers.format_chunk_content

    def counting(content: str, filename: str, alias: str) -> tuple[str, int]:
        numbered.append(content)
        return real(content, filename, alias)

    monkeypatch.setattr(graphrag_handlers, "format_chunk_content", counting)
    stubs = await handler._retrieve_provenance_chunks(["top"], None)
    assert numbered == []  # the arm itself numbers nothing
    merged = handler._merge_and_rank([], stubs, vector, 4)
    assert len(merged) == 4
    assert len(numbered) <= 4 < len(CITED)
