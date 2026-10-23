# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""An entity line's description (decision 18, 2026-10-09): sentence-bounded by default, and
the question's sentences first within the cap when ``question_aware_descriptions`` is on.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    _clean_description,
    _DescriptionDrops,
    _node_description,
    _significant_terms,
)
from tests.fakes.graphrag_world import (
    LITERARY,
    NODES,
    OLD,
    PPR,
    SEEDS,
    Graph,
    handler,
    node,
)


def test_question_aware_order_puts_the_questions_sentences_first_within_the_cap() -> None:
    node = SimpleNamespace(
        properties={
            "definition": "A rich count of Moscow. His children are Véra, Nicholas, Natásha and Pétya. He loves his wife."
        }
    )
    terms = _significant_terms("Which of the Rostóv children are named?", LITERARY)
    assert (
        _node_description(node, 60, clean=True, query_terms=terms, conventions=LITERARY)
        == "His children are Véra, Nicholas, Natásha and Pétya."
    )
    assert _node_description(node, 60, clean=True) == "A rich count of Moscow."


def test_a_sentence_stating_another_fact_is_no_repeat_and_a_true_repeat_is_counted() -> None:
    """Content words, not overlap: "the father of Nicholas" is a fact "the father of Pierre" is not.

    Until 2026-10-09 a 60% overlap of the words, function words included,
    dropped the second (125 distinct sentences on 46 nodes of the v5d
    literary graph).
    """
    dropped = _DescriptionDrops()
    text = "He is the father of Pierre. He is the father of Nicholas."
    assert _clean_description(text, 400, dropped=dropped) == text
    assert dropped.sentences == 0
    repeated = "He is the father of Pierre. He was the father of Pierre. Pierre's father."
    assert _clean_description(repeated, 400, dropped=dropped) == "He is the father of Pierre."
    assert dropped.sentences == 2
    # through the entity's description, the same count
    stored = SimpleNamespace(properties={"definition": repeated})
    _node_description(stored, 400, clean=True, dropped=dropped)
    assert dropped.sentences == 4


def test_the_default_is_four_hundred_characters_sentence_bounded() -> None:
    h = handler()
    assert (h.levers.entity_description_chars, h.levers.clean_descriptions) == (400, True)


STORED = (
    "His son Nicholas is a hussar. A rich count of Moscow. He loves his wife. "
    "His children are Véra and Pétya. He is generous to a fault."
)


def test_the_reorder_is_stable_both_halves_keep_their_stored_order() -> None:
    terms = _significant_terms("Who are the count's children?", LITERARY)
    assert _clean_description(STORED, 400, query_terms=terms, conventions=LITERARY) == (
        "A rich count of Moscow. His children are Véra and Pétya. "
        "His son Nicholas is a hussar. He loves his wife. He is generous to a fault."
    )


def test_without_question_terms_the_description_is_todays() -> None:
    """No terms (None or empty), or ``clean`` off: byte for byte the description before the lever."""
    assert _clean_description(STORED, 400) == STORED
    assert _clean_description(STORED, 400, query_terms=set(), conventions=LITERARY) == STORED
    assert _clean_description(STORED, 400, query_terms=None, conventions=LITERARY) == STORED
    assert _clean_description(STORED, 60) == "His son Nicholas is a hussar. A rich count of Moscow."
    node = SimpleNamespace(properties={"definition": STORED})
    terms = _significant_terms("Who is the count's wife?", LITERARY)
    # clean off is a raw cut of the stored text: sentence order is not defined there
    assert (
        _node_description(node, 25, query_terms=terms, conventions=LITERARY)
        == _node_description(node, 25)
        == "His son Nicholas is a hu…"
    )


class _LiseGraph(Graph):
    """The context-shape graph, Lise described in three sentences."""

    def get_nodes_batch(self, ids: list[str], **_: Any) -> list[Any]:
        nodes = {
            **NODES,
            "L": node(
                "L",
                "Lise Meinen",
                definition="The little princess of Petersburg. She is Prince Andrew's wife. "
                "She is expecting a child.",
            ),
        }
        return [nodes[i] for i in ids if i in nodes]


def _lise_description(question: str, **graphrag: Any) -> str:
    h = handler(**{**OLD, "entity_descriptions": True, **graphrag})
    h.graph = _LiseGraph()  # type: ignore[assignment]
    ctx = h._assemble_graph_context(PPR, SEEDS, question)
    (lise,) = [e for e in ctx["seed_entities"] if e["label"] == "Lise Meinen"]
    return str(lise["description"])


def test_the_lever_puts_the_questions_sentence_first_through_the_pipeline() -> None:
    """On, the sentence naming the wife leads; the lever reads the question with the gate off too."""
    question = "Whose wife was Lise?"
    first = (
        "She is Prince Andrew's wife. The little princess of Petersburg. She is expecting a child."
    )
    assert _lise_description(question, question_aware_descriptions=True) == first
    assert (
        _lise_description(question, question_aware_descriptions=True, question_gate=True) == first
    )
    # off: the stored order, whatever the question (on is the default since 2026-10-19)
    assert _lise_description(question, question_aware_descriptions=False) == (
        "The little princess of Petersburg. She is Prince Andrew's wife. She is expecting a child."
    )


class _RepeatingLiseGraph(Graph):
    """The context-shape graph, Lise's description stating her marriage twice."""

    def get_nodes_batch(self, ids: list[str], **_: Any) -> list[Any]:
        repeated = "She is Prince Andrew's wife. She is the wife of Prince Andrew."
        nodes = {**NODES, "L": node("L", "Lise Meinen", definition=repeated)}
        return [nodes[i] for i in ids if i in nodes]


def test_the_assembly_counts_the_dropped_sentences_on_the_stats() -> None:
    h = handler(**{**OLD, "entity_descriptions": True})
    h.graph = _RepeatingLiseGraph()  # type: ignore[assignment]
    stats: dict[str, Any] = {}
    ctx = h._assemble_graph_context(PPR, SEEDS, "Who is Lise?", stats=stats)
    (lise,) = [e for e in ctx["seed_entities"] if e["label"] == "Lise Meinen"]
    assert lise["description"] == "She is Prince Andrew's wife."
    assert stats["descriptions_dropped"] == 1
