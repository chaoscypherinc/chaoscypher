# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""memory_render: the provenance notation, the cards, groups, chains and notes, and the budget cut."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from typing import Any

from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_blocks import (
    aggregate_blocks,
    walk_blocks,
)
from chaoscypher_core.services.workflows.tools.engine.memory_render import (
    EMPTY_RECALL,
    PASSAGE_CHARS,
    PASSAGE_LIMIT,
    Provenance,
    cut_on_word,
    cut_to_budget,
    fact_line,
    render_aggregate,
    render_lookup,
    render_notes,
    render_passages,
    render_path,
    render_recall,
    tag,
)


def _hex(n: int) -> str:
    """A 32-hex id, the shape of a stored UUID, so the no-UUID check bites."""
    return f"{n:032x}"


P, A, B, L, PA, Z = (_hex(i) for i in range(1, 7))
E_LIVES, E_KNOWS, E_BORN, E_FRIEND, E_OLD, E_ANNA, E_BORIS = (_hex(i) for i in range(11, 18))

ALICE = Provenance(
    "memory", "alice", None, date(2026, 10, 20), None, "Pierre moved to Lyon.", chunk_id="m1"
)
MARCH = Provenance(
    "memory", "alice", None, date(2026, 3, 2), None, "Pierre met Anna at a ball.", chunk_id="m2"
)
BOOK = Provenance("doc", None, "War and Peace", None, None, None)
BOB = Provenance("memory", "bob", None, date(2026, 10, 21), None, None, chunk_id="m3")
CLOSED = Provenance(
    "memory", "alice", None, date(2026, 3, 1), date(2026, 10, 20), "Pierre lived in Paris."
)
ANNA = Provenance("memory", "alice", None, date(2026, 10, 20), None, "Anna lives in Paris.")


class FakeProv:
    """A dict-backed provenance lookup: edges and chunks by id."""

    def __init__(self, edges: dict[str, Provenance], chunks: dict[str, Provenance]) -> None:
        self.edges = edges
        self.chunks = chunks

    def for_edge(self, edge_id: str) -> Provenance:
        return self.edges[edge_id]

    def for_chunk(self, chunk_id: str) -> Provenance:
        return self.chunks[chunk_id]


PROV = FakeProv(
    {
        E_LIVES: ALICE,
        E_KNOWS: MARCH,
        E_BORN: BOOK,
        E_FRIEND: BOB,
        E_OLD: CLOSED,
        E_ANNA: ANNA,
        E_BORIS: replace(BOB, sentence=None),
    },
    {
        "m1": ALICE,
        "m2": MARCH,
        "m4": replace(ALICE, sentence=None, chunk_id="m4"),
        "d1": replace(BOOK, chunk_id="d1"),
    },
)


def _rel(eid: str, src: str, sid: str, label: str, tgt: str, tid: str) -> dict[str, str]:
    return {
        "edge_id": eid,
        "source": src,
        "source_id": sid,
        "label": label,
        "target": tgt,
        "target_id": tid,
    }


def _lookup_result() -> dict[str, Any]:
    return {
        "success": True,
        "graph_context": {
            "seed_entities": [
                {"id": P, "label": "Pierre Bezukhov", "type": "Person", "aliases": ["Pierre"]}
            ],
            "related_entities": [{"id": L, "label": "Lyon"}],
            "relationships": [
                _rel(E_KNOWS, "Pierre Bezukhov", P, "knows", "Anna", A),
                _rel(E_FRIEND, "Anna", A, "friend_of", "Pierre Bezukhov", P),
                _rel(E_BORN, "Pierre Bezukhov", P, "born_in", "Moscow", Z),
                _rel(E_LIVES, "Pierre Bezukhov", P, "lives_in", "Lyon", L),
                _rel(E_OLD, "Pierre Bezukhov", P, "lives_in", "Paris", PA),
                _rel(E_ANNA, "Anna", A, "lives_in", "Paris", PA),
            ],
        },
        "retrieval_stats": {"shape": "lookup", "shape_source": "rules"},
        "chunks": [],
    }


def _node(nid: str, label: str) -> SimpleNamespace:
    return SimpleNamespace(id=nid, label=label)


def _edge(eid: str, src: str, label: str, tgt: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=eid, source_node_id=src, target_node_id=tgt, label=label, properties={}
    )


NODES = {
    nid: _node(nid, label)
    for nid, label in (
        (P, "Pierre Bezukhov"),
        (A, "Anna"),
        (B, "Boris"),
        (L, "Lyon"),
        (PA, "Paris"),
        (Z, "Zoe"),
    )
}
LABELS = {nid: n.label for nid, n in NODES.items()}


def _aggregate_result() -> dict[str, Any]:
    edges = [
        _edge(E_KNOWS, P, "knows", A),
        _edge(E_BORIS, P, "knows", B),
        _edge(E_LIVES, P, "lives_in", L),
        _edge(E_OLD, P, "lives_in", PA),
    ]
    blocks = aggregate_blocks(
        entity_ids=[P],
        edges_by_node={P: edges},
        nodes_map=NODES,
        label_map=LABELS,
        families={},
        citations={
            E_KNOWS: [
                {
                    "edge_id": E_KNOWS,
                    "chunk_id": "m2",
                    "sentences": [
                        {"text": "Pierre met Anna at a ball.", "chunk_id": "m2", "local_index": 0}
                    ],
                }
            ]
        },
        per_label=12,
        include_unevidenced=False,
    )
    return {
        "graph_context": {"seed_entities": [], "relationships": [], "aggregates": blocks},
        "retrieval_stats": {"shape": "aggregate", "shape_source": "rules"},
        "chunks": [],
    }


def _path_result() -> dict[str, Any]:
    walks, gaps = walk_blocks(
        pairs=[(P, PA), (P, Z)],
        edges=[_edge(E_KNOWS, P, "knows", A), _edge(E_ANNA, A, "lives_in", PA)],
        nodes_map=NODES,
        citations={},
        max_hops=3,
        max_walks=2,
        include_unevidenced=False,
    )
    return {
        "graph_context": {
            "seed_entities": [],
            "relationships": [],
            "walks": walks,
            "walk_gaps": gaps,
        },
        "retrieval_stats": {"shape": "path", "shape_source": "rules"},
        "chunks": [],
    }


# ---------------------------------------------------------------------------
# cut_on_word
# ---------------------------------------------------------------------------


def test_cut_on_word_keeps_a_short_text_and_cuts_a_long_one_on_a_word() -> None:
    assert cut_on_word("Pierre moved to Lyon.", 21) == "Pierre moved to Lyon."
    assert cut_on_word("Pierre moved to Lyon in the spring.", 20) == "Pierre moved to..."
    cut = cut_on_word("Pierre moved to Lyon in the spring.", 23)
    assert cut == "Pierre moved to Lyon..." and len(cut) <= 23


def test_cut_on_word_hard_cuts_one_word_longer_than_the_limit() -> None:
    assert cut_on_word("Supercalifragilistic is long", 10) == "Superca..."


# ---------------------------------------------------------------------------
# the notation
# ---------------------------------------------------------------------------


def test_the_notation_of_each_kind() -> None:
    p = Provenance(
        "memory", "user", None, date(2026, 10, 20), None, "Pierre moved to Lyon in the spring."
    )
    assert (
        fact_line("Pierre", "lives_in", "Lyon", p)
        == 'Pierre lives_in Lyon [user 2026-10-20] "Pierre moved to Lyon in the spring."'
    )
    closed = replace(p, valid_to=date(2026, 10, 21))
    assert tag(closed) == "[user 2026-10-20..2026-10-21, replaced]"
    title = "War and Peace, Book One, the long title of it"
    assert len(title) > 40
    assert (
        tag(Provenance("doc", None, title, None, None, None))
        == "[doc War and Peace, Book One, the long...]"
    )
    assert tag(Provenance("pack", None, "literary", None, None, None)) == "[pack literary]"
    assert tag(Provenance("memory", "user", None, None, None, None)) == "[user]"


def test_a_long_sentence_is_cut_at_160_on_a_word_and_folded_to_one_line() -> None:
    sentence = "Pierre walked\nto the river " + "and back again " * 20
    line = fact_line("Pierre", "visits", "the river", replace(ALICE, sentence=sentence))
    quoted = line.split(" [alice 2026-10-20] ", 1)[1]
    assert quoted.startswith('"Pierre walked to the river and back')
    assert quoted.endswith('..."') and len(quoted) - 2 <= 160
    assert "\n" not in line


# ---------------------------------------------------------------------------
# the four shapes
# ---------------------------------------------------------------------------


def test_a_lookup_card() -> None:
    assert "\n".join(render_lookup(_lookup_result(), PROV)) == "\n".join(
        [
            "Pierre Bezukhov (Person)",
            "  also: Pierre",
            '  Pierre Bezukhov lives_in Lyon [alice 2026-10-20] "Pierre moved to Lyon."',
            '  Pierre Bezukhov knows Anna [alice 2026-03-02] "Pierre met Anna at a ball."',
            "  Pierre Bezukhov born_in Moscow [doc War and Peace]",
            "  Anna friend_of Pierre Bezukhov [bob 2026-10-21]",
        ]
    )


def test_a_lookup_as_of_a_date_shows_the_facts_open_then() -> None:
    lines = render_lookup(_lookup_result(), PROV, as_of=date(2026, 5, 1))
    assert (
        '  Pierre Bezukhov lives_in Paris [alice 2026-03-01..2026-10-20, replaced] "Pierre lived in Paris."'
        in lines
    )
    assert not any("Lyon" in line for line in lines)  # not yet true on that date
    assert not any("friend_of" in line for line in lines)


def test_an_aggregate_block() -> None:
    assert render_aggregate(_aggregate_result(), PROV) == [
        "Pierre Bezukhov: knows (2)",
        '  Pierre Bezukhov knows Anna [alice 2026-03-02] "Pierre met Anna at a ball."',
        "  Pierre Bezukhov knows Boris [bob 2026-10-21]",
        "Pierre Bezukhov: lives_in (1)",
        '  Pierre Bezukhov lives_in Lyon [alice 2026-10-20] "Pierre moved to Lyon."',
    ]


def test_a_path_chain_and_a_gap() -> None:
    assert render_path(_path_result(), PROV) == [
        "Pierre Bezukhov to Paris:",
        '  Pierre Bezukhov knows Anna [alice 2026-03-02] "Pierre met Anna at a ball."',
        '  Anna lives_in Paris [alice 2026-10-20] "Anna lives in Paris."',
        "No path found between Pierre Bezukhov and Zoe.",
    ]


def test_a_chain_through_a_closed_fact_is_no_path() -> None:
    result = _path_result()
    prov = FakeProv({**PROV.edges, E_ANNA: CLOSED}, PROV.chunks)
    assert render_path(result, prov) == [
        "No path found between Pierre Bezukhov and Paris.",
        "No path found between Pierre Bezukhov and Zoe.",
    ]


def test_notes_list_only_uncovered_memory_chunks() -> None:
    long_text = "Anna keeps bees " * 20
    result = {
        "chunks": [
            {"chunk_id": "m1", "original_content": "Pierre moved to Lyon."},
            {"chunk_id": "m4", "original_content": "The weather was grey.\nIt rained."},
            {"chunk_id": "m2", "original_content": long_text},
            {"chunk_id": "d1", "original_content": "A chapter of the book."},
            {"chunk_id": "m4", "original_content": "The weather was grey.\nIt rained."},
        ]
    }
    lines = render_notes(result, {"m1"}, PROV)
    assert lines[:2] == ["Notes:", '  [alice 2026-10-20] "The weather was grey. It rained."']
    assert lines[2].startswith('  [alice 2026-03-02] "Anna keeps bees') and lines[2].endswith(
        '..."'
    )
    assert len(lines) == 3  # the document chunk is no note; the repeat is listed once
    assert render_notes(result, {"m1", "m2", "m4"}, PROV) == []


MANUAL = Provenance("doc", None, "Router manual", None, None, None)
PACK = Provenance("pack", None, "Field guide", None, None, None)
PASSAGE_PROV = FakeProv(
    PROV.edges,
    {
        **PROV.chunks,
        "r1": replace(MANUAL, chunk_id="r1"),
        "r2": replace(MANUAL, chunk_id="r2"),
        "g1": replace(PACK, chunk_id="g1"),
        "r3": replace(MANUAL, chunk_id="r3"),
    },
)
RESET = (
    "To reset the router, hold the button on the back panel for ten seconds.\n"
    "1. Wait for the light to blink amber.\n2. Release the button."
)


def _passage_chunks() -> list[dict[str, str]]:
    return [
        {"chunk_id": "m1", "original_content": "Pierre moved to Lyon."},
        {"chunk_id": "r1", "original_content": RESET},
        {"chunk_id": "r2", "original_content": "Firmware updates install overnight. " * 20},
        {"chunk_id": "r1", "original_content": RESET},
        {"chunk_id": "g1", "original_content": "Oak leaves have rounded lobes."},
        {"chunk_id": "r3", "original_content": "The fourth passage is past the limit."},
    ]


def test_passages_list_document_and_pack_chunks_in_order_tagged_and_cut() -> None:
    lines = render_passages({"chunks": _passage_chunks()}, PASSAGE_PROV)
    assert PASSAGE_LIMIT == 3 and PASSAGE_CHARS == 320
    assert lines[0] == "Passages:"
    assert lines[1] == (
        '  [doc Router manual] "To reset the router, hold the button on the back panel for ten '
        'seconds. 1. Wait for the light to blink amber. 2. Release the button."'
    )
    assert lines[2].startswith('  [doc Router manual] "Firmware updates') and lines[2].endswith(
        '..."'
    )
    assert len(lines[2]) <= len('  [doc Router manual] ""') + PASSAGE_CHARS
    assert lines[3] == '  [pack Field guide] "Oak leaves have rounded lobes."'
    # the memory chunk is a note, never a passage; the repeat is listed once; the fourth
    # document chunk is past the limit
    assert len(lines) == 1 + PASSAGE_LIMIT


def test_a_passage_a_fact_already_quotes_is_still_listed() -> None:
    """A fact line quotes one sentence; a manual's procedure is the whole passage."""
    result = _lookup_result()
    result["chunks"] = [{"chunk_id": "r1", "original_content": RESET}]
    text = render_recall(result, PASSAGE_PROV, budget=1600, more=False, ids=False)
    assert "Passages:" in text and "2. Release the button." in text


def test_recall_lists_the_notes_then_the_passages() -> None:
    result = _lookup_result()
    result["chunks"] = [
        {"chunk_id": "m4", "original_content": "The weather was grey."},
        {"chunk_id": "r1", "original_content": RESET},
    ]
    text = render_recall(result, PASSAGE_PROV, budget=1600, more=False, ids=False)
    notes, passages = text.index("Notes:"), text.index("Passages:")
    assert text.index("Pierre Bezukhov (Person)") < notes < passages


def test_a_broad_question_on_a_manual_gets_its_passages_after_the_digest() -> None:
    result = _lookup_result()
    result["retrieval_stats"]["shape"] = "global"
    result["chunks"] = [{"chunk_id": "r1", "original_content": RESET}]
    digest = "Since 2026-10-13: nothing new."
    text = render_recall(
        result, PASSAGE_PROV, budget=1600, more=False, ids=False, global_text=digest
    )
    assert text.startswith(digest)
    assert text.endswith('2. Release the button."')


def test_no_passages_is_no_header() -> None:
    assert render_passages({"chunks": [{"chunk_id": "m1", "original_content": "x"}]}, PROV) == []


# ---------------------------------------------------------------------------
# render_recall and the budget
# ---------------------------------------------------------------------------


def test_the_budget_cuts_on_a_line_and_says_how_many_more() -> None:
    text = cut_to_budget([f"line {i}" for i in range(100)], budget=60)
    assert len(text) <= 60 and text.endswith("more, ask with more=true)")
    kept = text.splitlines()[:-1]
    assert kept == [f"line {i}" for i in range(len(kept))]
    assert text.splitlines()[-1] == f"({100 - len(kept)} more, ask with more=true)"
    assert cut_to_budget(["a", "b"], budget=60) == "a\nb"


def test_recall_renders_the_shape_then_the_uncovered_notes() -> None:
    result = _lookup_result()
    result["chunks"] = [
        {"chunk_id": "m1", "original_content": "Pierre moved to Lyon."},
        {"chunk_id": "m4", "original_content": "The weather was grey."},
    ]
    text = render_recall(result, PROV, budget=1600, more=False, ids=False)
    assert text.splitlines()[0] == "Pierre Bezukhov (Person)"
    assert text.endswith('Notes:\n  [alice 2026-10-20] "The weather was grey."')
    assert '"Pierre moved to Lyon."' in text and text.count("Pierre moved to Lyon.") == 1


def test_recall_falls_back_to_cards_when_the_shape_found_no_block() -> None:
    result = _lookup_result()
    result["retrieval_stats"]["shape"] = "aggregate"
    assert render_recall(result, PROV, budget=1600, more=False, ids=False).startswith(
        "Pierre Bezukhov (Person)\n"
    )


def test_recall_hides_closed_facts_and_as_of_shows_them() -> None:
    now = render_recall(_lookup_result(), PROV, budget=1600, more=False, ids=False)
    assert "Paris" not in now
    then = render_recall(
        _lookup_result(), PROV, budget=1600, more=False, ids=False, as_of=date(2026, 5, 1)
    )
    assert "Pierre Bezukhov lives_in Paris [alice 2026-03-01..2026-10-20, replaced]" in then


def test_recall_on_nothing_is_one_line() -> None:
    empty = {"graph_context": {}, "retrieval_stats": {"shape": "lookup"}, "chunks": []}
    for result in ({}, empty, {"success": False, "error": "boom"}):
        assert render_recall(result, PROV, budget=1600, more=False, ids=False) == EMPTY_RECALL
    assert EMPTY_RECALL == "Nothing remembered about that yet."


def test_more_doubles_the_budget() -> None:
    result = _lookup_result()
    short = render_recall(result, PROV, budget=120, more=False, ids=False)
    longer = render_recall(result, PROV, budget=120, more=True, ids=False)
    assert len(short) <= 120 < len(longer) <= 240
    assert short.endswith("more, ask with more=true)")


def test_the_global_shape_renders_the_digest_text() -> None:
    result = _lookup_result()
    result["retrieval_stats"]["shape"] = "global"
    digest = "Since 2026-10-13: 2 memories, 3 facts added."
    assert render_recall(
        result, PROV, budget=1600, more=False, ids=False, global_text=digest
    ).startswith(digest)


def _four_shapes() -> list[tuple[dict[str, Any], str | None]]:
    global_result = _lookup_result()
    global_result["retrieval_stats"]["shape"] = "global"
    return [
        (_lookup_result(), None),
        (_aggregate_result(), None),
        (_path_result(), None),
        (global_result, "Since 2026-10-13: 2 memories, 3 facts added."),
    ]


def test_no_default_result_has_a_uuid_or_a_brace() -> None:
    for result, global_text in _four_shapes():
        text = render_recall(
            result, PROV, budget=1600, more=False, ids=False, global_text=global_text
        )
        assert text and text != EMPTY_RECALL
        assert not re.search(r"[0-9a-f]{32}", text), text
        assert "{" not in text and "}" not in text, text
        assert text.isascii(), text


def test_ids_true_appends_hash_ids() -> None:
    text = render_recall(_lookup_result(), PROV, budget=1600, more=False, ids=True)
    lines = text.splitlines()
    assert lines[0] == f"Pierre Bezukhov (Person) #{P}"
    assert lines[2] == (
        f'  Pierre Bezukhov lives_in Lyon [alice 2026-10-20] "Pierre moved to Lyon." #{E_LIVES}'
    )
    path = render_recall(_path_result(), PROV, budget=1600, more=False, ids=True)
    assert f'[alice 2026-10-20] "Anna lives in Paris." #{E_ANNA}' in path
