# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""One ``source_citations`` row per chunk an entity is cited in (audit W2-13).

The commit wrote one row per entity (its ``cited_chunk_id``), so the
provenance arm reached one chunk for an entity the deduplication had merged
from mentions in many groups. ``_create_source_citations`` now writes a row
for each distinct evidence chunk, each with that chunk's sentences.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.engine.commit.service import entity_citation_chunks


def _service() -> Any:
    """A SourceCommitService whose sources repository records the citation batch."""
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService
    from chaoscypher_core.settings import EngineSettings

    repo = MagicMock()
    repo.create_citations_batch = MagicMock(return_value=None)
    svc = SourceCommitService(
        graph_repository=MagicMock(spec=GraphRepository),
        source_repository=repo,
        sources_repository=repo,
        indexing_repository=MagicMock(),
        search_repository=MagicMock(spec=SearchRepository),
        settings=EngineSettings(),
    )
    svc.database_name = "default"
    return svc, repo


def _ev(text: str, chunk: str | None, idx: int = 0) -> dict[str, Any]:
    """One resolved evidence sentence."""
    return {"text": text, "chunk_id": chunk, "local_index": idx}


@pytest.mark.asyncio
async def test_an_entity_cited_in_three_groups_gets_three_rows() -> None:
    """Three evidence chunks, three rows, each with its own chunk's sentence and stable id."""
    svc, repo = _service()
    entity = {
        "name": "Anna Pávlovna",
        "type": "Character",
        "sent_ref": "S2",
        "cited_chunk_id": "c1",
        "evidence": [
            _ev("Anna Pávlovna greeted him.", "c1"),
            _ev("Anna Pávlovna frowned.", "c4", 2),
            _ev("Anna Pávlovna smiled.", "c9", 1),
            _ev("She said so.", "c4", 3),
        ],
    }
    await svc._create_source_citations(
        file_id="src",
        source_id="src",
        entity_index_to_node_id={0: "node-anna"},
        entity_index_to_node={0: MagicMock(label="Anna Pávlovna")},
        commit_data={"entities": [entity]},
        stored_chunk_ids={"c1", "c4", "c9"},
    )
    (rows,) = repo.create_citations_batch.call_args.args
    assert [r["chunk_id"] for r in rows] == ["c1", "c4", "c9"]
    assert len({r["id"] for r in rows}) == 3
    by_chunk = {r["chunk_id"]: r for r in rows}
    assert by_chunk["c4"]["context_snippet"] == "Anna Pávlovna frowned. She said so."
    assert [s["text"] for s in by_chunk["c9"]["citation_metadata"]["sentences"]] == [
        "Anna Pávlovna smiled."
    ]
    # The group-level sent_ref belongs to the cited chunk's row only.
    assert by_chunk["c1"]["citation_metadata"]["sent_ref"] == "S2"
    assert by_chunk["c4"]["citation_metadata"]["sent_ref"] is None


@pytest.mark.asyncio
async def test_an_unstored_evidence_chunk_is_skipped_and_the_rest_written() -> None:
    """A pruned chunk costs its own row, not the entity's other rows."""
    svc, repo = _service()
    entity = {
        "name": "Pierre",
        "cited_chunk_id": "c1",
        "evidence": [_ev("Pierre came.", "c1"), _ev("Pierre left.", "gone")],
    }
    await svc._create_source_citations(
        file_id="src",
        source_id="src",
        entity_index_to_node_id={0: "node-pierre"},
        entity_index_to_node={0: MagicMock(label="Pierre")},
        commit_data={"entities": [entity]},
        stored_chunk_ids={"c1"},
    )
    (rows,) = repo.create_citations_batch.call_args.args
    assert [r["chunk_id"] for r in rows] == ["c1"]


def test_entity_citation_chunks_orders_the_cited_chunk_first_without_repeats() -> None:
    """``cited_chunk_id`` first (it may be the group fallback with no evidence), then evidence."""
    entity = {"cited_chunk_id": "c3", "evidence": [_ev("a", "c1"), _ev("b", "c3"), _ev("c", None)]}
    assert entity_citation_chunks(entity) == ["c3", "c1"]
    assert entity_citation_chunks({"name": "x"}) == []
