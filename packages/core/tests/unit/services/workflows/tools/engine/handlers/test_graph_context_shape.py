# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""How the graph is handed to the chat model (2026-10-02): triple selection, descriptions,
seed paths, and the one rendering (``context_text``) the app loop and the benchmark share.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.services.workflows.tools.engine.context_render import EXPLANATION
from chaoscypher_core.settings import EngineSettings
from tests.fakes.graphrag_world import (
    EDGES,
    LITERARY,
    NODES,
    OLD,
    PPR,
    SEEDS,
    Graph,
    edge,
    handler,
    node,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        GraphRAGToolHandlers,
    )


def _triples(ctx: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(t["source"], t["label"], t["target"]) for t in ctx["relationships"]]


def test_all_edges_is_the_pre_review_behaviour_every_edge_in_node_order() -> None:
    """Opt-in since the defaults flipped (2026-10-02): the block the review found unusable."""
    ctx = handler(**OLD)._assemble_graph_context(PPR, SEEDS)
    # node order: Lise's edges, then Andrew's (outgoing before incoming), ...
    assert _triples(ctx) == [
        ("Lise Meinen", "spouse_of", "Prince Andrew"),
        ("Prince Andrew", "serves", "General Kutúzov"),
        ("Prince Andrew", "references", "Moscow"),
        ("Hélène", "interacts_with", "Prince Andrew"),
    ]
    assert "description" not in ctx["seed_entities"][0]
    assert "paths" not in ctx


def test_connected_keeps_edges_among_the_top_nodes_or_touching_a_seed_ranked_by_pagerank() -> None:
    """Andrew -> Moscow goes (Moscow is outside the top nodes, neither end is a seed); the
    seed's own edge ranks first.
    """
    ctx = handler(**{**OLD, "triple_selection": "connected"})._assemble_graph_context(PPR, SEEDS)
    assert _triples(ctx) == [
        ("Lise Meinen", "spouse_of", "Prince Andrew"),
        ("Prince Andrew", "serves", "General Kutúzov"),
        ("Hélène", "interacts_with", "Prince Andrew"),
    ]
    capped = handler(
        **{**OLD, "triple_selection": "connected", "max_triples": 1}
    )._assemble_graph_context(PPR, SEEDS)
    assert _triples(capped) == [("Lise Meinen", "spouse_of", "Prince Andrew")]


def test_entity_descriptions_come_from_the_stored_definition_or_descriptors_never_the_placeholder() -> (
    None
):
    ctx = handler(
        **{**OLD, "entity_descriptions": True, "entity_description_chars": 30}
    )._assemble_graph_context(PPR, SEEDS)
    by_label = {e["label"]: e for e in ctx["seed_entities"] + ctx["related_entities"]}
    # clean_descriptions is on by default (2026-10-09): a cut lands on a word boundary
    assert by_label["Lise Meinen"]["description"] == "the little princess, Prince…"
    assert by_label["General Kutúzov"]["description"] == "commander-in-chief; old…"
    assert "description" not in by_label["Prince Andrew"]  # "Entity: ..." is the commit's filler
    assert "description" not in by_label["Hélène"]


def test_seed_paths_lists_the_relation_chain_between_the_seeds() -> None:
    ctx = handler(**{**OLD, "seed_paths": True})._assemble_graph_context(PPR, SEEDS)
    assert ctx["paths"] == [
        {
            "steps": [
                {"source": "Lise Meinen", "label": "spouse_of", "target": "Prince Andrew"},
                {"source": "Prince Andrew", "label": "serves", "target": "General Kutúzov"},
            ]
        }
    ]


@pytest.mark.asyncio
async def test_graphrag_search_attaches_the_rendering_the_app_and_the_benchmark_both_read() -> None:
    h = handler(explain_lines=True, graph_block_last=False)
    h._graphrag_search_pipeline = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "success": True,
            "graph_context": {
                "seed_entities": [{"label": "Lise Meinen", "description": "the little princess"}],
                "related_entities": [],
                "paths": [
                    {
                        "steps": [
                            {
                                "source": "Lise Meinen",
                                "label": "spouse_of",
                                "target": "Prince Andrew",
                            }
                        ]
                    }
                ],
                "relationships": [
                    {"source": "Prince Andrew", "label": "serves", "target": "General Kutúzov"}
                ],
            },
            "chunks": [{"text": "[CHUNK C0 | book.txt]\nShe called him Uncle."}],
        }
    )
    result = await h.graphrag_search("who")
    assert result["context_text"].splitlines() == [
        EXPLANATION,
        "- Lise Meinen: the little princess",
        "- path: Lise Meinen spouse_of Prince Andrew",
        "- Prince Andrew serves General Kutúzov",
        "[CHUNK C0 | book.txt]",
        "She called him Uncle.",
    ]
    plain = handler()
    plain._graphrag_search_pipeline = AsyncMock(
        return_value={"success": True, "graph_context": {}, "chunks": []}
    )  # type: ignore[method-assign]
    assert (await plain.graphrag_search("who"))["context_text"] == "(empty)"


def test_question_gate_keeps_seeds_and_what_shares_a_word_with_the_query() -> None:
    """Moscow and Hélène are dropped for a question about the princess and Kutúzov.

    The seeds' own edges stay whatever the words.
    """
    ctx = handler(**{**OLD, "question_gate": True})._assemble_graph_context(
        PPR, SEEDS, query="What is the connection between the little princess and General Kutúzov?"
    )
    assert [e["label"] for e in ctx["seed_entities"]] == ["Lise Meinen", "General Kutúzov"]
    assert [e["label"] for e in ctx["related_entities"]] == []  # Andrew, Hélène: no shared word
    assert _triples(ctx) == [
        ("Lise Meinen", "spouse_of", "Prince Andrew"),
        ("Prince Andrew", "serves", "General Kutúzov"),
    ]


def test_paths_to_top_adds_one_hop_chains_from_the_seeds_to_the_other_top_nodes() -> None:
    ctx = handler(**{**OLD, "seed_paths": True, "paths_to_top": True})._assemble_graph_context(
        PPR, SEEDS
    )
    chains = [[(s["source"], s["label"], s["target"]) for s in p["steps"]] for p in ctx["paths"]]
    assert chains[0] == [
        ("Lise Meinen", "spouse_of", "Prince Andrew"),
        ("Prince Andrew", "serves", "General Kutúzov"),
    ]
    assert [("Lise Meinen", "spouse_of", "Prince Andrew")] in chains  # seed -> top node
    assert [("Prince Andrew", "serves", "General Kutúzov")] in chains


def test_keyword_seeds_come_first_and_never_include_chunks() -> None:
    """A keyword hit seeds when its name holds a word of the question; chunks and templates never.

    ``L`` matches the keyword search (its description could mention Kutúzov)
    but its name does not, so it keeps its vector score behind ``K``.
    """
    h = handler(keyword_seeds=True)
    h.search.keyword_search = MagicMock(
        return_value=[("chunk:7", 9.0), ("template:t", 8.0), ("K", 5.0), ("L", 4.0)]
    )
    seeds = h._add_keyword_seeds("Kutúzov", [("L", 0.6), ("X", 0.4)], seed_limit=3)
    assert seeds == [("K", 1.0), ("L", 0.6), ("X", 0.4)]
    assert h.search.keyword_search.call_args.kwargs["item_type"] == "node"
    h.search.keyword_search = MagicMock(side_effect=RuntimeError("fts down"))
    assert h._add_keyword_seeds("Kutúzov", [("L", 0.6)], seed_limit=3) == [("L", 0.6)]


def test_rank_chunks_by_seeds_puts_the_chunk_naming_both_people_first() -> None:
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        _rank_by_seed_mentions,
    )

    chunks = [
        {"chunk_id": "a", "text": "Kutúzov slept."},
        {"chunk_id": "b", "text": "Lise Meinen asked Kutuzov about her husband."},
        {"chunk_id": "c", "text": "It rained."},
    ]
    ranked = _rank_by_seed_mentions(chunks, ["Lise Meinen", "General Kutúzov"], LITERARY)
    assert [c["chunk_id"] for c in ranked] == ["b", "a", "c"]


class _GraphWith(Graph):
    """The module's graph plus ``extra`` edges."""

    def __init__(self, extra: list[Any]) -> None:
        self.edges = EDGES + extra

    def list_edges(self, **kw: Any) -> list[Any]:
        if "source_node_id" in kw:
            return [e for e in self.edges if e.source_node_id == kw["source_node_id"]]
        return [e for e in self.edges if e.target_node_id == kw["target_node_id"]]


def _handler_on(extra: list[Any], **graphrag: Any) -> GraphRAGToolHandlers:
    h = handler(**graphrag)
    h.graph = _GraphWith(extra)
    return h


CONNECTED = {**OLD, "triple_selection": "connected"}


def test_generic_relations_last_lets_the_cap_keep_the_specific_facts() -> None:
    """Lise interacts_with Hélène ranks with Lise's PageRank, ahead of Andrew serves Kutúzov;
    ranked last, the cap of two keeps spouse_of and serves.
    """
    extra = [edge("L", "interacts_with", "X")]
    plain = _handler_on(extra, **{**CONNECTED, "max_triples": 2})._assemble_graph_context(
        PPR, SEEDS
    )
    assert _triples(plain) == [
        ("Lise Meinen", "spouse_of", "Prince Andrew"),
        ("Lise Meinen", "interacts_with", "Hélène"),
    ]
    last = _handler_on(
        extra, **{**CONNECTED, "max_triples": 2, "generic_relations_last": True}
    )._assemble_graph_context(PPR, SEEDS)
    assert _triples(last) == [
        ("Lise Meinen", "spouse_of", "Prince Andrew"),
        ("Prince Andrew", "serves", "General Kutúzov"),
    ]


def test_dedupe_drops_what_a_path_states_and_one_side_of_a_symmetric_generic_pair() -> None:
    """The path already says spouse_of and serves; Hélène interacts_with Andrew both ways is one
    fact. A directed relation stated both ways (courts) stays as two lines.
    """
    extra = [
        edge("A", "interacts_with", "X"),
        edge("X", "courts", "A"),
        edge("A", "courts", "X"),
    ]
    plain = _handler_on(extra, **{**CONNECTED, "seed_paths": True})._assemble_graph_context(
        PPR, SEEDS
    )
    assert ("Lise Meinen", "spouse_of", "Prince Andrew") in _triples(plain)
    assert ("Prince Andrew", "interacts_with", "Hélène") in _triples(plain)
    deduped = _handler_on(
        extra, **{**CONNECTED, "seed_paths": True, "dedupe_graph_lines": True}
    )._assemble_graph_context(PPR, SEEDS)
    assert deduped["paths"] == plain["paths"]
    triples = _triples(deduped)
    assert ("Lise Meinen", "spouse_of", "Prince Andrew") not in triples
    assert ("Prince Andrew", "serves", "General Kutúzov") not in triples
    interacts = [t for t in triples if t[1] == "interacts_with"]
    assert len(interacts) == 1
    assert ("Hélène", "courts", "Prince Andrew") in triples
    assert ("Prince Andrew", "courts", "Hélène") in triples


def test_both_shapes_are_off_by_default() -> None:
    graphrag = EngineSettings().graphrag
    assert graphrag.dedupe_graph_lines is False and graphrag.generic_relations_last is False


# ---------------------------------------------------------------------------
# v10: aliases, clean descriptions, sentence evidence, question-named entities first (2026-10-04)
# ---------------------------------------------------------------------------


def test_entity_aliases_appear_on_the_entity_line() -> None:
    from chaoscypher_core.services.workflows.tools.engine.context_render import (
        render_retrieved_context,
    )

    class _AliasGraph(Graph):
        def get_nodes_batch(self, ids: list[str], **_: Any) -> list[Any]:
            nodes = {
                **NODES,
                "L": node(
                    "L", "Lise Meinen", aliases=["Lise", "the little princess", "Lise Meinen"]
                ),
            }
            return [nodes[i] for i in ids if i in nodes]

    h = handler(**{**OLD, "entity_aliases": True})
    h.graph = _AliasGraph()
    ctx = h._assemble_graph_context(PPR, SEEDS)
    lise = next(e for e in ctx["seed_entities"] if e["label"] == "Lise Meinen")
    assert lise["aliases"] == ["Lise", "the little princess"]  # the label itself is not repeated
    text = render_retrieved_context({"graph_context": ctx})
    assert "- Lise Meinen (also: Lise, the little princess)" in text
    plain = handler(**OLD)
    plain.graph = _AliasGraph()
    assert "aliases" not in plain._assemble_graph_context(PPR, SEEDS)["seed_entities"][0]


def test_clean_descriptions_drop_repeated_sentences_and_end_at_a_sentence() -> None:
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        _clean_description,
    )

    merged = (
        "A general who has appointed Prince Andrew as his aide-de-camp. "
        "The general appointed Prince Andrew aide-de-camp. "
        "The Commander in Chief of the Russian army."
    )
    assert _clean_description(merged, 200) == (
        "A general who has appointed Prince Andrew as his aide-de-camp. "
        "The Commander in Chief of the Russian army."
    )
    # a word the kept sentence lacks is a fact it does not state: kept (2026-10-09)
    reworded = merged.replace("The general appointed", "A high-ranking general appointed")
    assert "high-ranking" in _clean_description(reworded, 200)
    # the second sentence does not fit: the description ends after the first, not mid-word
    assert (
        _clean_description(merged, 80)
        == "A general who has appointed Prince Andrew as his aide-de-camp."
    )
    # only a first sentence longer than the limit is cut, at a word
    assert (
        _clean_description("An extremely long opening sentence about everything.", 20)
        == "An extremely long…"
    )


def test_relationship_evidence_points_at_a_retrieved_sentence_naming_both_ends() -> None:
    from chaoscypher_core.services.workflows.tools.engine.context_render import (
        EVIDENCE_NOTE,
        render_retrieved_context,
    )
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        _annotate_evidence,
    )

    chunk = (
        "<untrusted_document>\n[CHUNK C0 | book.txt]\n[S1] Anna Pávlovna received her guests.\n"
        "[S2] He married Lise Meinen lately; Prince Andrew is an aide-de-camp of Kutúzov's.\n"
        "</untrusted_document>"
    )
    result = {
        "chunks": [{"content": chunk}],
        "graph_context": {
            "relationships": [
                {"source": "Prince Andrew", "label": "spouse_of", "target": "Lise Meinen"},
                {"source": "Borís", "label": "member_of", "target": "Semënov Guards"},
            ],
            "paths": [
                {
                    "steps": [
                        {"source": "Prince Andrew", "label": "serves", "target": "General Kutúzov"}
                    ]
                }
            ],
        },
    }
    _annotate_evidence(result, LITERARY)
    rels = result["graph_context"]["relationships"]
    assert rels[0]["evidence"] == "C0:S2" and "evidence" not in rels[1]
    # "General" is a title, not a name: Kutúzov alone is matched
    assert result["graph_context"]["paths"][0]["steps"][0]["evidence"] == "C0:S2"
    text = render_retrieved_context(result, explain=True, graph_last=True)
    assert "- Prince Andrew spouse_of Lise Meinen [C0:S2]" in text
    assert "- Borís member_of Semënov Guards\n" in text + "\n"
    assert EVIDENCE_NOTE in text


def test_question_entities_first_puts_what_the_question_names_ahead_of_a_seed_it_does_not() -> None:
    """Moscow became a seed and tops PageRank; the question names Lise and Kutúzov, not Moscow."""
    extra = [
        edge("Z", "located_in", "L"),
        edge("Z", "located_in", "K"),
        edge("A", "located_in", "Z"),
    ]
    ppr = {**PPR, "Z": 0.9}
    seeds = [*SEEDS, ("Z", 0.95)]
    base = {**OLD, "triple_selection": "connected", "seed_paths": True}
    question = "How is Lise connected to Kutúzov?"
    plain = _handler_on(extra, **base)._assemble_graph_context(ppr, seeds, question)
    first = _handler_on(extra, **{**base, "question_entities_first": True})._assemble_graph_context(
        ppr, seeds, question
    )

    named = {"Lise Meinen", "General Kutúzov"}
    assert _triples(plain)[0] == (
        "Moscow",
        "located_in",
        "Lise Meinen",
    )  # PageRank leads with Moscow
    touches = [bool(named & {t[0], t[2]}) for t in _triples(first)]
    assert touches == sorted(touches, reverse=True)  # every line naming Lise or Kutúzov first
    assert [e["label"] for e in first["seed_entities"]][:2] == ["Lise Meinen", "General Kutúzov"]

    def ends(path: dict[str, Any]) -> set[str]:
        return {x for step in path["steps"] for x in (step["source"], step["target"])}

    # without the setting Moscow anchors paths of its own; with it every path joins Lise and
    # Kutúzov (a chain may still pass through Moscow on the way)
    assert any(not named <= ends(p) for p in plain.get("paths", []))
    assert first.get("paths") and all(named <= ends(p) for p in first["paths"])
    # a question naming nobody leaves the order as it was
    unnamed = _handler_on(
        extra, **{**base, "question_entities_first": True}
    )._assemble_graph_context(ppr, seeds, "Who was there?")
    assert _triples(unnamed) == _triples(plain)


def test_the_v10_shapes_are_off_by_default() -> None:
    """clean_descriptions left this set on 2026-10-09 (decision 18): on by default since."""
    g = EngineSettings().graphrag
    assert not (g.entity_aliases or g.relationship_evidence or g.question_entities_first)
