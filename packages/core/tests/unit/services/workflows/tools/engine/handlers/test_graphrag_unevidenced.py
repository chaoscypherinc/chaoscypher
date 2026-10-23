# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""An unevidenced edge stays out of the chat context by default; the summary is cut by rank.

Keep, flag, never delete (2026-10-07): the evidence filter keeps a
relationship no cited sentence states, flagged ``properties.unevidenced``.
Retrieval reads the flag through ``name_identity.is_unevidenced_record``
(``_edge_in_context``, the one decision): PageRank, the paths and the
relationship lines leave such an edge out unless
``graphrag.include_unevidenced_edges`` lets it in, and then it ranks after
every evidenced line before the ``max_triples`` cap. The triple summary
orders its lines by standing (named before reference, connected before
orphan, evidenced before unevidenced) and its budget cuts the tail, never
the selection order by bytes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from chaoscypher_core.benchmark.chat_run import parse_sweep_set, run_provenance
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    DESCRIPTOR_REFERENCE_KEY,
    EVIDENCE_REASON_KEY,
    PIPELINE_EDGE_FLAG_KEYS,
    UNEVIDENCED_KEY,
    TripleStanding,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
    _edge_in_context,
    _line_standing,
)
from chaoscypher_core.settings import EngineSettings, GraphRAGSettings
from tests.fakes.domain_tables import domain_conventions
from tests.fakes.graph_edges import EdgesByNode


LITERARY = domain_conventions("literary")
REASON = '"Prince Vasíli" is named in S3 but "Anna Pávlovna" is not'


def _node(nid: str, label: str, **props: Any) -> SimpleNamespace:
    return SimpleNamespace(
        id=nid, label=label, template_id="person", entity_type="Character", properties=props
    )


NODES = {
    "vasili": _node("vasili", "Prince Vasíli"),
    "anna": _node("anna", "Anna Pávlovna"),
    "pierre": _node("pierre", "Pierre"),
    "count": _node("count", "the count", **{DESCRIPTOR_REFERENCE_KEY: True}),
}


def _edge(eid: str, src: str, label: str, tgt: str, **props: Any) -> SimpleNamespace:
    return SimpleNamespace(
        id=eid, source_node_id=src, label=label, target_node_id=tgt, properties=props
    )


EDGES = [
    _edge("e1", "vasili", "hosts", "anna", **{UNEVIDENCED_KEY: True, EVIDENCE_REASON_KEY: REASON}),
    _edge("e2", "count", "parent_of", "pierre"),
    _edge("e3", "anna", "interacts_with", "pierre"),
]


class _Graph(EdgesByNode):
    """The two per-node listings the context reads, the minimal listings PageRank reads, the flag scan."""

    def list_edges(self, **kw: Any) -> list[Any]:
        if "source_node_id" in kw:
            return [e for e in EDGES if e.source_node_id == kw["source_node_id"]]
        return [e for e in EDGES if e.target_node_id == kw["target_node_id"]]

    def get_nodes_batch(self, ids: list[str], **_: Any) -> list[Any]:
        return [NODES[i] for i in ids if i in NODES]

    def list_nodes_minimal(self, **_: Any) -> list[Any]:
        return [SimpleNamespace(id=n.id, source_id=None) for n in NODES.values()]

    def list_edges_minimal(self, **_: Any) -> list[Any]:
        # as the SQLite listing: no properties but the pipeline's edge flags
        return [
            SimpleNamespace(
                id=e.id, source_node_id=e.source_node_id, target_node_id=e.target_node_id,
                label=e.label, template_id="t",
                properties={k: True for k in PIPELINE_EDGE_FLAG_KEYS if e.properties.get(k) is True},
            )
            for e in EDGES
        ]  # fmt: skip


def _handler(**graphrag: Any) -> GraphRAGToolHandlers:
    settings = EngineSettings()
    for k, v in graphrag.items():
        setattr(settings.graphrag, k, v)
    h = GraphRAGToolHandlers(
        graph_repository=_Graph(),  # type: ignore[arg-type]
        search_repository=MagicMock(),
        indexing_repository=MagicMock(),
        source_storage=MagicMock(),
        embedding_callback=AsyncMock(),
        settings=settings,
        database_name="db",
        name_conventions=LITERARY,
    )
    h._resolve_template_names = lambda nodes: {}  # type: ignore[method-assign]
    return h


PPR = {"vasili": 0.5, "anna": 0.3, "pierre": 0.15, "count": 0.05}
SEEDS = [("vasili", 1.0), ("anna", 0.9)]


def _lines(ctx: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(t["source_id"], t["label"], t["target_id"]) for t in ctx["relationships"]]


def test_the_one_decision_reads_the_flag_on_the_edge() -> None:
    flagged, plain = EDGES[0], EDGES[2]
    assert not _edge_in_context(flagged, include_unevidenced=False)
    assert _edge_in_context(flagged, include_unevidenced=True)
    assert _edge_in_context(plain, include_unevidenced=False)
    minimal = {e.id: e for e in _Graph().list_edges_minimal()}  # the flags only
    assert not _edge_in_context(minimal["e1"], include_unevidenced=False)
    assert _edge_in_context(minimal["e3"], include_unevidenced=False)
    assert _edge_in_context(SimpleNamespace(id="e3"), include_unevidenced=False)  # no properties
    text_value = _edge("e9", "a", "x", "b", **{UNEVIDENCED_KEY: "no"})  # the text's, no flag
    assert _edge_in_context(text_value, include_unevidenced=False)


def test_an_unevidenced_edge_is_out_of_the_context_by_default() -> None:
    ctx = _handler(question_gate=False)._assemble_graph_context(PPR, SEEDS)
    assert ("vasili", "hosts", "anna") not in _lines(ctx)
    assert "hosts" not in ctx["summary"]
    assert all("hosts" not in str(p) for p in ctx.get("paths", []))


def test_the_lever_lets_it_in_after_every_evidenced_line() -> None:
    ctx = _handler(question_gate=False, include_unevidenced_edges=True)._assemble_graph_context(
        PPR, SEEDS
    )
    # The selection ranks by the full line standing before the cap, the summary's order
    # (2026-10-08): the top PageRank edge after every evidenced named line, and a line
    # touching a reference (``the count``) after every named one.
    assert _lines(ctx) == [
        ("anna", "interacts_with", "pierre"),
        ("vasili", "hosts", "anna"),
        ("count", "parent_of", "pierre"),
    ]
    assert [line.rstrip(".") for line in ctx["summary"].splitlines()] == [
        "Anna Pávlovna interacts_with Pierre",
        "Prince Vasíli hosts Anna Pávlovna",
        "the count parent_of Pierre",
    ]


def test_pagerank_spreads_no_mass_over_an_unevidenced_edge(monkeypatch: Any) -> None:
    from chaoscypher_core.services.workflows.tools.engine.handlers import graphrag_handlers

    seen: list[list[str]] = []

    def fake_pagerank(nodes: Any, edges: Any, **_: Any) -> dict[str, Any]:
        seen.append([e.id for e in edges])
        return {"personalized": True, "pagerank_scores": {n.id: 1.0 for n in nodes}}

    monkeypatch.setattr(graphrag_handlers, "calculate_pagerank", fake_pagerank)
    for h in (_handler(), _handler(include_unevidenced_edges=True)):
        h._run_ppr(SEEDS, *h._load_graph(None), None)
    assert seen == [["e2", "e3"], ["e1", "e2", "e3"]]


def test_the_summary_is_cut_by_rank_and_counts_what_it_leaves_out(monkeypatch: Any) -> None:
    """The summary cuts the tail of the selection's order, which is ranked by line standing."""
    from chaoscypher_core.services.workflows.tools.engine.handlers import graphrag_handlers

    by_id = {e.id: e for e in EDGES}
    assert _line_standing(by_id["e2"], NODES) == TripleStanding(True, False, False, False, False)
    assert _line_standing(by_id["e1"], NODES) == TripleStanding(False, False, False, True, False)

    def summary() -> str:
        handler = _handler(question_gate=False, include_unevidenced_edges=True)
        return str(handler._assemble_graph_context(PPR, SEEDS)["summary"])

    full = summary()
    assert full.splitlines() == [
        "Anna Pávlovna interacts_with Pierre.",  # named, evidenced
        "Prince Vasíli hosts Anna Pávlovna.",  # named, unevidenced
        "the count parent_of Pierre.",  # a reference
    ]
    # a budget of two lines keeps the top two by rank and counts the third
    monkeypatch.setattr(graphrag_handlers, "_SUMMARY_MAX_CHARS", len(full) - 5)
    assert summary().splitlines() == [
        "Anna Pávlovna interacts_with Pierre.",
        "Prince Vasíli hosts Anna Pávlovna.",
        "... (1 more triples omitted)",
    ]
    # a line never cut mid-way: a budget below the first line keeps none and counts all
    monkeypatch.setattr(graphrag_handlers, "_SUMMARY_MAX_CHARS", 5)
    assert summary() == "\n... (3 more triples omitted)"


def test_the_lever_is_recorded_and_sweepable() -> None:
    """A benchmark lever: in the row's ``run_settings`` levers (so comparability sees it) and SWEEP_SET."""
    assert GraphRAGSettings().include_unevidenced_edges is False
    assert parse_sweep_set("graphrag.include_unevidenced_edges=true") == {
        "include_unevidenced_edges": True
    }
    on = GraphRAGSettings(include_unevidenced_edges=True)
    off_run, on_run = run_provenance(GraphRAGSettings()), run_provenance(on)
    assert off_run["levers"]["include_unevidenced_edges"] is False
    assert on_run["levers"]["include_unevidenced_edges"] is True
    assert off_run["lever_signature"] != on_run["lever_signature"]


def test_a_line_ranks_by_its_endpoints_in_the_node_order_then_its_own_flags() -> None:
    """One order for nodes and lines, the endpoints' standing then the edge's own flags.

    Named > reference, connected > orphan, undoubted > doubted; then evidenced >
    unevidenced, uncapped > capped (read through ``pipeline_flag``: a text value is no flag).
    """
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        CAPPED_KEY,
        LOW_PLAUSIBILITY_KEY,
        ORPHAN_KEY,
        UNEVIDENCED_KEY,
        node_standing,
        triple_standing,
    )

    plain = node_standing({})
    doubted = node_standing({LOW_PLAUSIBILITY_KEY: True})
    orphan = node_standing({ORPHAN_KEY: True})
    lines = {
        "plain": triple_standing({}, plain, plain),
        "capped": triple_standing({CAPPED_KEY: True}, plain, plain),
        "unevidenced": triple_standing({UNEVIDENCED_KEY: True}, plain, plain),
        "doubted": triple_standing({}, plain, doubted),
        "orphan": triple_standing({}, orphan, plain),
        "text": triple_standing({CAPPED_KEY: "true", UNEVIDENCED_KEY: 1}, plain, plain),
    }
    assert lines["text"] == lines["plain"]
    assert sorted(lines, key=lambda k: (lines[k], k)) == [
        "plain",
        "text",
        "capped",
        "unevidenced",
        "doubted",
        "orphan",
    ]
    assert lines["capped"].doubted_edge and not lines["capped"].unevidenced
