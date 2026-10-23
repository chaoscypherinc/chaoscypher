# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The five-node Book One world the GraphRAG handler tests retrieve over, and its handler fakes.

War and Peace, Book One: Lise (a seed) -spouse_of-> Prince Andrew -serves->
General Kutúzov (a seed); Hélène is a top node that touches Andrew; Moscow is
a neighbour outside the top nodes. Two views of it:

- the context view (:data:`NODES`, :data:`EDGES`, :class:`Graph`,
  :func:`handler`): bare edges through each node's listings, for the
  graph-context assembly;
- the walk view (:data:`WALK_EDGES`, :class:`WalkGraph`, :func:`walk_handler`):
  the same graph with edge ids, Kutúzov's synthetic inverse of ``serves``, and
  the whole-graph minimal listings a walk loads, behind a whole
  ``graphrag_search`` pipeline (keyword seeds, chunks, citations).

Both handlers are built by one builder, their levers set as a sweep
override sets them (validated), so the shape, walk and aggregate tests all
retrieve through the same wiring.

The chunk and PageRank helpers (:func:`make_chunk`, :func:`batch_lookup`,
:func:`pin_pagerank`) stand in for indexing storage and the PageRank run of
any handler under test.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from chaoscypher_core.services.sources.engine.deduplication.name_identity import INVERSE_OF_KEY
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
)
from chaoscypher_core.settings import EngineSettings
from tests.fakes.domain_tables import domain_conventions
from tests.fakes.graph_edges import EdgesByNode


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        NameConventions,
    )


__all__ = [
    "EDGES",
    "LITERARY",
    "NODES",
    "OLD",
    "PPR",
    "SEEDS",
    "WALK_EDGES",
    "Graph",
    "WalkGraph",
    "batch_lookup",
    "edge",
    "handler",
    "make_chunk",
    "node",
    "pin_pagerank",
    "walk_edge",
    "walk_handler",
]


LITERARY: NameConventions = domain_conventions("literary")
"""The literary domain's naming conventions: Book One's names are read by them."""


def node(nid: str, label: str, **props: Any) -> SimpleNamespace:
    """A person node ``nid`` named ``label`` with ``props`` as its properties."""
    return SimpleNamespace(id=nid, label=label, template_id="person", properties=dict(props))


def edge(src: str, label: str, tgt: str) -> SimpleNamespace:
    """A bare edge (no id, no properties): what the context assembly reads."""
    return SimpleNamespace(source_node_id=src, label=label, target_node_id=tgt)


def walk_edge(eid: str, src: str, label: str, tgt: str, **props: Any) -> SimpleNamespace:
    """An edge with its id and properties: what a walk loads, folds and cites."""
    return SimpleNamespace(
        id=eid, source_node_id=src, label=label, target_node_id=tgt, properties=dict(props)
    )


NODES: dict[str, SimpleNamespace] = {
    "L": node("L", "Lise Meinen", definition="the little princess, Prince Andrew's wife"),
    "A": node("A", "Prince Andrew", definition="Entity: Prince Andrew"),  # the commit placeholder
    "K": node("K", "General Kutúzov", descriptors=["commander-in-chief", "old general"]),
    "X": node("X", "Hélène"),
    "Z": node("Z", "Moscow"),
}
"""The world's nodes by id: Lise, Andrew, Kutúzov, Hélène and Moscow."""

EDGES: list[SimpleNamespace] = [
    edge("L", "spouse_of", "A"),
    edge("A", "serves", "K"),
    edge("X", "interacts_with", "A"),
    edge("A", "references", "Z"),
]
"""The context view's edges."""

WALK_EDGES: list[SimpleNamespace] = [
    walk_edge("e1", "L", "spouse_of", "A"),
    walk_edge("e2", "A", "serves", "K"),
    walk_edge("e2i", "K", "commands", "A", **{INVERSE_OF_KEY: "serves"}),
    walk_edge("e3", "X", "interacts_with", "A"),
    walk_edge("e4", "A", "references", "Z"),
]
"""The walk view's edges: :data:`EDGES` with ids; Kutúzov's synthetic inverse of ``serves``
folds onto its twin."""

PPR: dict[str, float] = {"L": 0.5, "A": 0.4, "K": 0.3, "X": 0.2}
"""Fixed PageRank scores: Moscow is outside the top nodes."""

SEEDS: list[tuple[str, float]] = [("L", 0.9), ("K", 0.8)]
"""The seeds and their scores: Lise and Kutúzov."""

OLD: dict[str, Any] = {
    "triple_selection": "all_edges",
    "max_triples": 200,
    "entity_descriptions": False,
    "seed_paths": False,
    "question_gate": False,
}
"""The pre-review (2026-10-02) graph-context levers, before the defaults flipped."""


class Graph(EdgesByNode):
    """The context view through each node's edge listings and the node fetch."""

    def list_edges(self, **kw: Any) -> list[Any]:
        """The edges out of ``source_node_id``, or into ``target_node_id``."""
        if "source_node_id" in kw:
            return [e for e in EDGES if e.source_node_id == kw["source_node_id"]]
        return [e for e in EDGES if e.target_node_id == kw["target_node_id"]]

    def get_nodes_batch(self, ids: list[str], **_: Any) -> list[Any]:
        """The known nodes among ``ids``, in order."""
        return [NODES[i] for i in ids if i in NODES]


class WalkGraph(EdgesByNode):
    """The whole graph through the minimal listings, each node's edges, and the node fetch."""

    def __init__(
        self,
        edges: list[Any] | None = None,
        loaded: list[str] | None = None,
        minimal_labels: bool = True,
    ) -> None:
        self.edges = list(WALK_EDGES if edges is None else edges)
        self.loaded = list(NODES if loaded is None else loaded)
        self.minimal_labels = minimal_labels
        self.fetched: list[list[str]] = []

    def list_nodes_minimal(self, **_: Any) -> list[Any]:
        """The loaded nodes, with their labels unless ``minimal_labels`` is off."""
        if self.minimal_labels:
            return [NODES[i] for i in self.loaded]
        return [SimpleNamespace(id=i) for i in self.loaded]

    def list_edges_minimal(self, **_: Any) -> list[Any]:
        """Every edge of the graph."""
        return list(self.edges)

    def list_edges(self, *, limit: int, **kw: Any) -> list[Any]:
        """Up to ``limit`` edges out of ``source_node_id``, or into ``target_node_id``."""
        if "source_node_id" in kw:
            return [e for e in self.edges if e.source_node_id == kw["source_node_id"]][:limit]
        return [e for e in self.edges if e.target_node_id == kw["target_node_id"]][:limit]

    def get_nodes_batch(self, ids: list[str], **_: Any) -> list[Any]:
        """The known nodes among ``ids``, in order; each fetch is recorded in ``fetched``."""
        self.fetched.append(list(ids))
        return [NODES[i] for i in ids if i in NODES]


def _settings(graphrag: dict[str, Any]) -> EngineSettings:
    """Engine settings with ``graphrag``'s levers set as a sweep override sets them (validated)."""
    settings = EngineSettings()
    # The cap tests use max_triples below the product floor (10) to see the cap
    # bite on this five-node graph; every other lever is set (and validated) as
    # a sweep override would set it.
    floor_free = {k: graphrag.pop(k) for k in ("max_triples",) if k in graphrag}
    for k, v in graphrag.items():
        setattr(settings.graphrag, k, v)
    if floor_free:
        settings.graphrag = settings.graphrag.model_copy(update=floor_free)
    return settings


def _build(
    graph: Any,
    *,
    search: Any,
    indexing: Any,
    source_storage: Any,
    embedding: Any,
    graphrag: dict[str, Any],
    chat: Any = None,
) -> GraphRAGToolHandlers:
    """The one handler builder of the world: the repositories given, the literary conventions, no templates."""
    h = GraphRAGToolHandlers(
        graph_repository=graph,
        search_repository=search,
        indexing_repository=indexing,
        source_storage=source_storage,
        embedding_callback=embedding,
        settings=_settings(graphrag),
        database_name="db",
        name_conventions=LITERARY,
        chat_callback=chat,
    )
    h._resolve_template_names = lambda nodes: {}  # type: ignore[method-assign]
    return h


def handler(**graphrag: Any) -> GraphRAGToolHandlers:
    """A handler over :class:`Graph` with ``graphrag``'s levers set as a sweep override sets them."""
    return _build(
        Graph(),  # the listings the assembly reads
        search=MagicMock(),
        indexing=MagicMock(),
        source_storage=MagicMock(),
        embedding=AsyncMock(),
        graphrag=graphrag,
    )


def walk_handler(
    chat: Any = None,
    *,
    ppr: dict[str, float] | None = PPR,
    keyword: Sequence[tuple[str, float]] = (("L", 1.0), ("K", 0.9)),
    edges: list[Any] | None = None,
    graph: Any = None,
    chunks: Sequence[dict[str, Any]] = (),
    citations: Sequence[dict[str, Any]] = (),
    **graphrag: Any,
) -> GraphRAGToolHandlers:
    """A whole ``graphrag_search`` over :class:`WalkGraph` (or ``graph``): keyword seeds, the hybrid arm, chunks and citations.

    ``keyword`` are the keyword search's hits (the seeds, by name); the
    hybrid arm returns ``chunks``' ids and the indexing serves them;
    ``citations`` are the relationship citations; ``ppr`` fixes PageRank's
    scores (:func:`pin_pagerank`, recorded on ``h.loaded``; None runs the
    real one). ``graphrag``'s levers are set as a sweep override sets them.
    """
    search = MagicMock()
    search.needs_full_reindex = False
    search.vector_search.return_value = []
    search.keyword_search.return_value = list(keyword)
    search.hybrid_search = AsyncMock(return_value=[(f"chunk:{c['id']}", 0.9) for c in chunks])
    indexing = MagicMock()
    indexing.get_chunks_by_ids_batch.side_effect = batch_lookup(*chunks)
    source_storage = MagicMock()
    source_storage.get_citations_batch.return_value = []
    source_storage.get_relationship_citations_batch.return_value = list(citations)
    h = _build(
        graph or WalkGraph(edges),
        search=search,
        indexing=indexing,
        source_storage=source_storage,
        embedding=AsyncMock(return_value=[0.1, 0.2]),
        graphrag=graphrag,
        chat=chat,
    )
    if ppr is not None:
        h.loaded = pin_pagerank(h, ppr)  # type: ignore[attr-defined]
    return h


def make_chunk(
    chunk_id: str,
    content: str = "Test content.",
    source_id: str = "src1",
) -> dict[str, Any]:
    """Create a minimal chunk dict matching the storage protocol output.

    Args:
        chunk_id: Chunk identifier.
        content: Text content of the chunk.
        source_id: Owning source identifier.

    Returns:
        A dict matching the shape returned by indexing storage protocols.

    """
    return {
        "id": chunk_id,
        "content": content,
        "source_id": source_id,
        "database_name": "default",
        "chunk_index": 0,
        "page_number": 1,
        "section": None,
        "chunk_metadata": None,
    }


def batch_lookup(*chunks: dict[str, Any]) -> Callable[..., list[dict[str, Any]]]:
    """side_effect emulating get_chunks_by_ids_batch over the given chunks.

    Mirrors the adapter contract: results follow input order and missing
    ids are silently absent.
    """
    by_id = {c["id"]: c for c in chunks}

    def _fetch(chunk_ids: list[str], **_kwargs: Any) -> list[dict[str, Any]]:
        return [by_id[cid] for cid in chunk_ids if cid in by_id]

    return _fetch


def pin_pagerank(handler: GraphRAGToolHandlers, scores: dict[str, float]) -> list[tuple[int, int]]:
    """Fix ``handler``'s PageRank to ``scores``; the returned list records each graph it is handed.

    The pipeline still loads the graph from the handler's repository
    (``_load_graph``) and hands it over: each call appends the
    ``(nodes, edges)`` counts it received.
    """
    loaded: list[tuple[int, int]] = []

    def run_ppr(
        seeds: Any, nodes: list[Any], edges: list[Any], source_ids: Any
    ) -> dict[str, float]:
        loaded.append((len(nodes), len(edges)))
        return dict(scores)

    handler._run_ppr = run_ppr  # type: ignore[method-assign]
    return loaded
