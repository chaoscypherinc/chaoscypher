# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``graphrag_search`` under a tag scope: every graph-context node is attested by a tagged document.

Over a real SQLite graph (:mod:`tests.fakes.tag_scope_world`): the seeds,
the graph PageRank reads and the neighbours the context expands to. With
``tags=["alpha"]`` only ``node-a`` may appear, though it links to a
person-made node and to ``doc-b``'s node. With ``source_ids=["doc-a"]`` (a
plain source scope) the person-made and orphaned nodes still answer, as
before (owner decision 2026-11-05).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos.graph import GraphRepository
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository
from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
from chaoscypher_core.models import Node
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
)
from chaoscypher_core.settings import EngineSettings
from tests.fakes.tag_scope_world import (
    DATABASE,
    NODES,
    QUERY_VECTOR,
    SHARED_RELATION,
    UNTAGGED_RELATION,
    build_world,
)


QUESTION = "Where is the Falcon Aerie?"
AGGREGATE_QUESTION = "List every place near the Falcon Aerie."
DOC_A_NODES = {"node-a", "node-a2"}
"""The nodes ``doc-a`` (tag ``alpha``) attests."""


class _NoChunks:
    """An indexing store holding no chunk: the graph context is what is under test."""

    def get_chunks_by_ids_batch(self, _ids: list[str], **_: Any) -> list[dict[str, Any]]:
        return []

    def get_source_filenames_by_ids(self, ids: list[str], *_: Any) -> dict[str, str]:
        return dict.fromkeys(ids, "doc.txt")

    def get_source_titles_by_ids(self, ids: list[str], *_: Any) -> dict[str, str]:
        return dict.fromkeys(ids, "doc")


async def _embed(_text: str) -> dict[str, list[float]]:
    return {"embedding": QUERY_VECTOR}


@pytest.fixture
def handlers(tmp_path: Path) -> Iterator[GraphRAGToolHandlers]:
    path = tmp_path / "app.db"
    build_world(path)
    engine = get_engine(str(path))
    search = SearchRepository(engine, vector_dim=4, embedding_model="m")
    search.index_nodes_batch(
        [
            Node(id=nid, label=str(spec["label"]), template_id="place", properties={})
            for nid, spec in NODES.items()
        ]
    )
    search.index_embeddings_batch(
        [(nid, list(spec["vector"])) for nid, spec in NODES.items()],  # type: ignore[call-overload]
        item_type="node",
    )
    settings = EngineSettings()
    settings.graphrag.include_unevidenced_edges = True  # the world's edges cite no chunk
    settings.graphrag.router = "rules"
    with SafeSession(engine) as session:
        yield GraphRAGToolHandlers(
            graph_repository=GraphRepository(session, DATABASE),
            search_repository=search,
            indexing_repository=_NoChunks(),  # type: ignore[arg-type]
            embedding_callback=_embed,
            settings=settings,
            database_name=DATABASE,
        )
    evict_engine(path)


def _node_ids(value: Any) -> set[str]:
    """Every node id of the world named anywhere in ``value`` (a graph context)."""
    if isinstance(value, str):
        return {value} & NODES.keys()
    if isinstance(value, dict):
        return set().union(*(_node_ids(v) for v in value.values()), set())
    if isinstance(value, list | tuple):
        return set().union(*(_node_ids(v) for v in value), set())
    return set()


def _labels(text: str) -> set[str]:
    return {nid for nid, spec in NODES.items() if str(spec["label"]) in text}


async def test_a_tag_scope_keeps_every_context_node_to_the_tagged_documents(
    handlers: GraphRAGToolHandlers,
) -> None:
    result = await handlers.graphrag_search(QUESTION, tags=["alpha"])
    assert result["success"] is True
    assert result["retrieval_stats"]["mode"] == "full_graphrag"
    assert "node-a" in _node_ids(result["graph_context"])
    assert _node_ids(result["graph_context"]) <= DOC_A_NODES
    assert _labels(result["context_text"]) <= DOC_A_NODES
    assert UNTAGGED_RELATION not in repr(result["graph_context"])
    assert UNTAGGED_RELATION not in result["context_text"]


async def test_a_tag_scope_drops_a_relationship_only_an_untagged_document_states(
    handlers: GraphRAGToolHandlers,
) -> None:
    """Both ends of ``edge-a-a2`` are ``doc-a``'s; only ``doc-b`` states it: out under ``alpha``."""
    plain = await handlers.graphrag_search(QUESTION, source_ids=["doc-a", "doc-b"])
    assert UNTAGGED_RELATION in repr(plain["graph_context"])  # the world does reach it
    tagged = await handlers.graphrag_search(QUESTION, tags=["alpha"])
    # a merged edge homed at doc-b that doc-a states too is read by its members: kept
    assert SHARED_RELATION in repr(tagged["graph_context"])
    for question in (
        QUESTION,
        AGGREGATE_QUESTION,
        "How is the Falcon Aerie connected to the Falcon Aspen?",
    ):
        result = await handlers.graphrag_search(question, tags=["alpha"])
        assert UNTAGGED_RELATION not in repr(result["graph_context"]), question
        assert UNTAGGED_RELATION not in result["context_text"], question


async def test_an_aggregate_question_under_a_tag_scope_lists_only_tagged_nodes(
    handlers: GraphRAGToolHandlers,
) -> None:
    result = await handlers.graphrag_search(AGGREGATE_QUESTION, tags=["alpha"])
    assert result["shape"] == "aggregate"
    assert "node-a" in _node_ids(result["graph_context"])
    assert _node_ids(result["graph_context"]) <= DOC_A_NODES
    assert _labels(result["context_text"]) <= DOC_A_NODES


async def test_a_plain_source_scope_still_reads_person_made_and_orphaned_nodes(
    handlers: GraphRAGToolHandlers,
) -> None:
    result = await handlers.graphrag_search(QUESTION, source_ids=["doc-a"])
    ids = _node_ids(result["graph_context"])
    assert {"node-a", "node-person", "node-orphan"} <= ids
    assert "node-b" not in ids
