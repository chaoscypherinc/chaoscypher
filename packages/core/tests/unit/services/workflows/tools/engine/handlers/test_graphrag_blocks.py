# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""graphrag_blocks: aggregate blocks with one sentence per row, walks between entities, markers, chunk ids."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    CAPPED_KEY,
    INVERSE_OF_KEY,
    UNEVIDENCED_KEY,
)
from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_blocks import (
    EVIDENCE_QUOTE_CHARS,
    aggregate_blocks,
    annotate_markers,
    block_chunk_ids,
    block_edge_ids,
    first_sentence,
    quote,
    shortest_walks,
    walk_blocks,
    walk_chains,
    walk_pairs_left_out,
)


def _edge(eid: str, src: str, label: str, tgt: str, **props: Any) -> SimpleNamespace:
    return SimpleNamespace(
        id=eid, source_node_id=src, target_node_id=tgt, label=label, properties=dict(props)
    )


NODES = {
    "R": SimpleNamespace(id="R", label="Count Rostóv"),
    "N": SimpleNamespace(id="N", label="Natásha"),
    "K": SimpleNamespace(id="K", label="Nicholas"),
    "V": SimpleNamespace(id="V", label="Véra"),
    "C": SimpleNamespace(id="C", label="the countess"),
}
LABELS = {k: v.label for k, v in NODES.items()}
EDGES = [
    _edge("e1", "N", "child_of", "R"),
    _edge("e1i", "R", "parent_of", "N", inverse_of="child_of"),
    _edge("e2", "K", "child_of", "R"),
    _edge("e3", "V", "child_of", "R"),
    _edge("e4", "R", "spouse_of", "C"),
    _edge("e5", "R", "interacts_with", "N", **{UNEVIDENCED_KEY: True}),
]
BY_NODE = {"R": EDGES}
CITES: dict[str, list[dict[str, Any]]] = {
    "e1": [
        {
            "edge_id": "e1",
            "chunk_id": "c2",
            "sentences": [
                {"text": "She was the youngest girl.", "chunk_id": "c2", "local_index": 3}
            ],
        }
    ],
    "e2": [{"edge_id": "e2", "chunk_id": "c5", "sentences": []}],
}


def _blocks(**kw: Any) -> list[dict[str, Any]]:
    base: dict[str, Any] = {
        "entity_ids": ["R"],
        "edges_by_node": BY_NODE,
        "nodes_map": NODES,
        "label_map": LABELS,
        "families": {},
        "citations": CITES,
        "per_label": 12,
        "include_unevidenced": False,
    }
    return aggregate_blocks(**{**base, **kw})


def test_rows_group_by_label_with_the_first_sentence_and_the_inverse_twin_dropped() -> None:
    [block] = _blocks()
    assert block["entity"] == "Count Rostóv"
    assert block["entity_id"] == "R"
    groups = {g["label"]: g for g in block["groups"]}
    assert set(groups) == {"child_of", "spouse_of"}
    child = groups["child_of"]
    assert child["count"] == 3 and [r["target"] for r in child["rows"]] == ["Count Rostóv"] * 3
    assert child["rows"][0]["source"] == "Natásha"
    assert child["rows"][0]["sentence"] == "She was the youngest girl."
    assert child["rows"][0]["chunk_id"] == "c2" and child["rows"][0]["local_index"] == 3
    assert block["skipped"] == {
        "inverse": 1,
        "unevidenced": 1,
        "other_labels": 0,
        "duplicate": 0,
        "past_per_label": 0,
        "listing_capped": 0,
    }
    assert child["count_capped"] is False


def test_a_row_cited_without_a_sentence_keeps_its_chunk_and_no_sentence() -> None:
    [block] = _blocks()
    child = next(g for g in block["groups"] if g["label"] == "child_of")
    nicholas = child["rows"][1]
    assert (nicholas["source"], nicholas["sentence"], nicholas["chunk_id"]) == (
        "Nicholas",
        None,
        "c5",
    )
    assert nicholas["local_index"] is None
    assert child["rows"][2]["chunk_id"] is None  # Véra: no citation at all


def test_families_keep_only_the_questions_relations_and_count_the_rest() -> None:
    [block] = _blocks(families={"child": frozenset({"child_of", "parent_of"})})
    assert [g["label"] for g in block["groups"]] == ["child_of"]
    assert block["skipped"]["other_labels"] == 1  # spouse_of


def test_per_label_cuts_the_rows_but_not_the_count() -> None:
    [block] = _blocks(per_label=2)
    child = next(g for g in block["groups"] if g["label"] == "child_of")
    assert len(child["rows"]) == 2 and child["count"] == 3
    assert block["skipped"]["past_per_label"] == 1
    assert [r["source"] for r in child["rows"]] == ["Natásha", "Nicholas"]  # sentence first


def test_unevidenced_rows_enter_with_their_flag_when_the_lever_is_on() -> None:
    [block] = _blocks(include_unevidenced=True)
    inter = next(g for g in block["groups"] if g["label"] == "interacts_with")
    assert inter["rows"][0]["flags"] == [UNEVIDENCED_KEY]
    assert block["skipped"]["unevidenced"] == 0


def test_an_inverse_with_no_forward_twin_is_kept_as_the_forward_fact() -> None:
    lone = [_edge("e9i", "R", "parent_of", "V", inverse_of="child_of")]
    [block] = _blocks(edges_by_node={"R": lone})
    [group] = block["groups"]
    assert group["label"] == "child_of"
    [row] = group["rows"]
    assert (row["source"], row["target"], row["sentence"]) == ("Véra", "Count Rostóv", None)
    assert block["skipped"]["inverse"] == 0


def test_an_entity_with_no_rows_has_no_block_and_order_follows_the_ids() -> None:
    by_node = {"R": EDGES, "K": [_edge("e2", "K", "child_of", "R")], "C": []}
    blocks = _blocks(entity_ids=["K", "C", "R"], edges_by_node=by_node)
    assert [b["entity_id"] for b in blocks] == ["K", "R"]


def test_an_entity_whose_rows_were_all_dropped_has_no_block() -> None:
    hidden = [_edge("e5", "R", "interacts_with", "N", **{UNEVIDENCED_KEY: True})]
    assert _blocks(edges_by_node={"R": hidden}) == []


def test_quote_cuts_at_a_word_boundary() -> None:
    long = "word " * 60
    q = quote(long)
    assert len(q) <= EVIDENCE_QUOTE_CHARS and q.endswith("…") and not q[:-1].endswith(" ")
    assert quote("  She   was\nthe youngest.  ") == "She was the youngest."
    unbroken = "x" * (EVIDENCE_QUOTE_CHARS + 10)
    assert len(quote(unbroken)) == EVIDENCE_QUOTE_CHARS and quote(unbroken).endswith("…")


def test_first_sentence_is_the_first_row_with_one() -> None:
    rows: list[dict[str, Any]] = [
        {"edge_id": "e", "chunk_id": "c1", "sentences": []},
        {"edge_id": "e", "chunk_id": "c2", "sentences": [{"text": " ", "local_index": 0}]},
        {"edge_id": "e", "chunk_id": "c3", "sentences": [{"text": "It is.", "local_index": 4}]},
    ]
    assert first_sentence(rows) == ("It is.", "c3", 4)
    assert first_sentence(rows[:2]) is None
    assert first_sentence([]) is None


def test_block_ids_are_unique_in_row_order() -> None:
    blocks = _blocks()
    assert block_edge_ids(blocks) == ["e1", "e2", "e3", "e4"]
    assert block_chunk_ids(blocks) == ["c2", "c5"]


def _chunked(content: str) -> dict[str, Any]:
    return {"chunk_id": "c2", "chunk_alias": "C1", "content": content}


def test_markers_resolve_through_the_final_chunk_aliases() -> None:
    result: dict[str, Any] = {
        "graph_context": {"aggregates": _blocks()},
        "chunks": [
            _chunked(
                "[CHUNK C1 | b.txt]\n[S1] a.\n[S2] b.\n[S3] c.\n[S4] She was the youngest girl."
            )
        ],
    }
    annotate_markers(result)
    rows = result["graph_context"]["aggregates"][0]["groups"][0]["rows"]
    assert rows[0]["marker"] == "C1:S4"
    assert "marker" not in rows[1]  # c5 is not among the chunks
    assert block_chunk_ids(result["graph_context"]["aggregates"]) == ["c2", "c5"]


def test_a_marker_whose_index_misses_falls_back_to_the_sentence_that_matches() -> None:
    result: dict[str, Any] = {
        "graph_context": {"aggregates": _blocks()},
        "chunks": [_chunked("[CHUNK C1 | b.txt]\n[S1] a.\n[S2] She  was the youngest girl!")],
    }
    annotate_markers(result)
    row = result["graph_context"]["aggregates"][0]["groups"][0]["rows"][0]
    assert row["marker"] == "C1:S2"


def test_no_sentence_of_the_chunk_matching_leaves_the_marker_unset() -> None:
    result: dict[str, Any] = {
        "graph_context": {"aggregates": _blocks()},
        "chunks": [_chunked("[CHUNK C1 | b.txt]\n[S1] a.\n[S2] b.\n[S3] c.\n[S4] Another one.")],
    }
    annotate_markers(result)
    row = result["graph_context"]["aggregates"][0]["groups"][0]["rows"][0]
    assert "marker" not in row
    annotate_markers({"graph_context": {}, "chunks": []})  # nothing to annotate: no error


def test_a_fact_committed_twice_is_one_row_the_copy_with_a_sentence_kept() -> None:
    copy = _edge("e1b", "N", "child_of", "R")  # the same fact, from another source
    [block] = _blocks(edges_by_node={"R": [copy, *EDGES]})
    child = next(g for g in block["groups"] if g["label"] == "child_of")
    assert child["count"] == 3
    assert [r["source"] for r in child["rows"]] == ["Natásha", "Nicholas", "Véra"]
    assert child["rows"][0]["edge_id"] == "e1"  # the cited copy wins over the uncited one
    assert block["skipped"]["duplicate"] == 1


def test_labels_group_canonically() -> None:
    spelled = [_edge("e6", "K", "Child Of", "R"), _edge("e2", "N", "child_of", "R")]
    [block] = _blocks(edges_by_node={"R": spelled}, families={"parent": frozenset({"child_of"})})
    [group] = block["groups"]
    assert (group["label"], group["count"]) == ("child_of", 2)


def test_a_capped_listing_marks_every_group_and_is_counted() -> None:
    [block] = _blocks(capped_ids={"R"})
    assert all(g["count_capped"] for g in block["groups"])
    assert block["skipped"]["listing_capped"] == 1


# --------------------------------------------------------------------------- walks

CHAIN_NODES = {
    k: SimpleNamespace(id=k, label=n)
    for k, n in {
        "D": "Dólokhov",
        "A": "Anatole",
        "P": "Pierre",
        "V": "Prince Vasíli",
        "X": "nobody",
    }.items()
}
CHAIN = [
    _edge("w1", "D", "lives_with", "A"),
    _edge("w1i", "A", "lives_with", "D", **{INVERSE_OF_KEY: "lives_with"}),
    _edge("w2", "A", "friend_of", "P"),
    _edge("w3", "V", "parent_of", "A"),
    _edge("w4", "V", "knows", "P"),
    _edge("w5", "D", "interacts_with", "P", **{UNEVIDENCED_KEY: True}),
]
CHAIN_CITES: dict[str, list[dict[str, Any]]] = {
    "w1": [
        {
            "edge_id": "w1",
            "chunk_id": "c7",
            "sentences": [
                {"text": "Dólokhov lived with Anatole.", "chunk_id": "c7", "local_index": 2}
            ],
        }
    ]
}


def _walks(**kw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    base: dict[str, Any] = {
        "pairs": [("D", "P")],
        "edges": CHAIN,
        "nodes_map": CHAIN_NODES,
        "citations": CHAIN_CITES,
        "max_hops": 3,
        "max_walks": 5,
        "include_unevidenced": False,
    }
    return walk_blocks(**{**base, **kw})


def _triple_hops(walk: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(h["source"], h["label"], h["target"]) for h in walk["hops"]]


def test_shortest_walks_returns_the_shortest_chains_first_in_edge_order() -> None:
    adjacency: dict[str, list[Any]] = {}
    for e in CHAIN[:5]:
        adjacency.setdefault(e.source_node_id, []).append(e)
        adjacency.setdefault(e.target_node_id, []).append(e)
    walks = shortest_walks("D", "P", adjacency, max_hops=3, max_walks=5)
    assert [[e.id for e in w] for w in walks] == [["w1", "w2"], ["w1", "w3", "w4"]]
    assert shortest_walks("D", "X", adjacency, max_hops=3, max_walks=5) == []
    assert shortest_walks("D", "P", adjacency, max_hops=1, max_walks=5) == []
    # the shortest first: with room for one walk, the two-hop chain alone
    assert [
        [e.id for e in w] for w in shortest_walks("D", "P", adjacency, max_hops=3, max_walks=1)
    ] == [["w1", "w2"]]


def test_walk_blocks_fold_the_inverse_skip_unevidenced_by_default_and_quote_hops() -> None:
    walks, gaps = _walks(pairs=[("D", "P"), ("D", "X")])
    assert [w["from"] + "→" + w["to"] for w in walks] == ["Dólokhov→Pierre", "Dólokhov→Pierre"]
    first = walks[0]["hops"]
    assert _triple_hops(walks[0]) == [
        ("Dólokhov", "lives_with", "Anatole"),
        ("Anatole", "friend_of", "Pierre"),
    ]
    assert first[0]["sentence"] == "Dólokhov lived with Anatole." and first[0]["chunk_id"] == "c7"
    assert first[0]["local_index"] == 2 and first[0]["edge_id"] == "w1"
    assert first[1]["sentence"] is None and first[1]["flags"] == []
    # the walk folded an inverse and skipped an unevidenced edge: the gap says so
    assert gaps == [{"from": "Dólokhov", "to": "nobody", "max_hops": 3, "left_out": True}]
    direct, _ = _walks(citations={}, include_unevidenced=True)
    assert [h["label"] for h in direct[0]["hops"]] == ["interacts_with"]
    assert direct[0]["hops"][0]["flags"] == [UNEVIDENCED_KEY]


def test_a_hop_states_the_stored_fact_even_when_the_walk_runs_against_it() -> None:
    """Dólokhov → Anatole → Prince Vasíli → Pierre: the second hop is Vasíli parent_of Anatole.

    The sentence states the fact in its stored direction, so the hop does too;
    the walk's order is the order of the hops.
    """
    walks, _ = _walks()
    assert _triple_hops(walks[1]) == [
        ("Dólokhov", "lives_with", "Anatole"),
        ("Prince Vasíli", "parent_of", "Anatole"),
        ("Prince Vasíli", "knows", "Pierre"),
    ]


def test_what_the_walk_leaves_out_is_counted() -> None:
    chains, skipped = walk_chains(
        pairs=[("D", "P")], edges=CHAIN, max_hops=3, max_walks=5, include_unevidenced=False
    )
    assert [[e.id for e in c] for c in chains[("D", "P")]] == [["w1", "w2"], ["w1", "w3", "w4"]]
    assert skipped == {
        "outside_graph": 0,
        "inverse": 1,
        "unevidenced": 1,
        "parallel": 0,
        "degree_capped": 0,
        "listing_capped": 0,
        "pairs": 0,
        "past_max_walks": 0,
    }


def test_an_inverse_without_its_forward_twin_is_walked_as_the_forward_fact() -> None:
    _, skipped = walk_chains(
        pairs=[("D", "P")], edges=CHAIN[1:3], max_hops=3, max_walks=5, include_unevidenced=False
    )
    assert skipped["inverse"] == 0
    walks, gaps = _walks(edges=CHAIN[1:3])
    assert gaps == []
    [walk] = walks
    assert _triple_hops(walk) == [
        ("Dólokhov", "lives_with", "Anatole"),
        ("Anatole", "friend_of", "Pierre"),
    ]
    assert walk["hops"][0]["sentence"] is None  # an inverse has no citation of its own


def test_precomputed_chains_are_rendered_as_given() -> None:
    chains = {("D", "P"): [[CHAIN[0], CHAIN[2]]], ("D", "X"): []}
    walks, gaps = _walks(pairs=[("D", "P"), ("D", "X")], edges=[], chains=chains)
    assert [_triple_hops(w) for w in walks] == [
        [("Dólokhov", "lives_with", "Anatole"), ("Anatole", "friend_of", "Pierre")]
    ]
    # chains without the counts of what their walk left out: the gap cannot claim it read all
    assert gaps == [{"from": "Dólokhov", "to": "nobody", "max_hops": 3, "left_out": True}]
    nothing = dict.fromkeys(("outside_graph", "inverse", "unevidenced", "listing_capped"), 0)
    _, gaps = _walks(pairs=[("D", "X")], edges=[], chains={("D", "X"): []}, skipped=nothing)
    assert gaps == [{"from": "Dólokhov", "to": "nobody", "max_hops": 3, "left_out": False}]
    _, gaps = _walks(
        pairs=[("D", "X")], edges=[], chains={("D", "X"): []}, skipped={"listing_capped": 1}
    )
    assert gaps[0]["left_out"] is True
    # folds, parallels and the caps on pairs and walks never cut a route: the plain wording
    harmless = {"inverse": 2, "parallel": 1, "pairs": 3, "past_max_walks": 4}
    _, gaps = _walks(pairs=[("D", "X")], edges=[], chains={("D", "X"): []}, skipped=harmless)
    assert gaps[0]["left_out"] is False
    for key in ("outside_graph", "unevidenced", "degree_capped", "listing_capped"):
        _, gaps = _walks(pairs=[("D", "X")], edges=[], chains={("D", "X"): []}, skipped={key: 1})
        assert gaps[0]["left_out"] is True, key


def test_a_walk_lists_the_shortest_length_and_one_longer_never_beyond() -> None:
    """D-P directly, D-A-P one longer, D-V-X-P two longer: the last is never listed, room or not."""
    edges = [
        _edge("a", "D", "knows", "P"),
        _edge("b", "D", "knows", "A"),
        _edge("c", "A", "knows", "P"),
        _edge("d", "D", "knows", "V"),
        _edge("e", "V", "knows", "X"),
        _edge("f", "X", "knows", "P"),
    ]
    adjacency: dict[str, list[Any]] = {}
    for e in edges:
        adjacency.setdefault(e.source_node_id, []).append(e)
        adjacency.setdefault(e.target_node_id, []).append(e)
    walks = shortest_walks("D", "P", adjacency, max_hops=4, max_walks=10)
    assert [[e.id for e in w] for w in walks] == [["a"], ["b", "c"]]
    # without the direct edge the shortest is two hops, and three is one longer
    del adjacency["D"][0], adjacency["P"][0]
    walks = shortest_walks("D", "P", adjacency, max_hops=4, max_walks=10)
    assert [[e.id for e in w] for w in walks] == [["b", "c"], ["d", "e", "f"]]


def test_the_walk_cap_is_the_questions_each_pairs_shortest_first() -> None:
    """Two pairs, two chains each, room for two: each pair keeps its shortest; the rest are counted."""
    chains, skipped = walk_chains(
        pairs=[("D", "P"), ("A", "V")],
        edges=CHAIN,
        max_hops=3,
        max_walks=2,
        include_unevidenced=False,
    )
    assert {pair: [[e.id for e in c] for c in found] for pair, found in chains.items()} == {
        ("D", "P"): [["w1", "w2"]],
        ("A", "V"): [["w3"]],
    }
    assert skipped["past_max_walks"] == 2
    # room for one: the shortest of all; the other pair is connected, so absent, never a gap
    chains, skipped = walk_chains(
        pairs=[("D", "P"), ("A", "V")],
        edges=CHAIN,
        max_hops=3,
        max_walks=1,
        include_unevidenced=False,
    )
    assert {pair: [[e.id for e in c] for c in found] for pair, found in chains.items()} == {
        ("A", "V"): [["w3"]]
    }
    assert skipped["past_max_walks"] == 1  # each pair searched for one: D-A-P left out
    walks, gaps = _walks(
        pairs=[("D", "P"), ("A", "V")], chains=chains, skipped=skipped, max_walks=1
    )
    assert [(w["from"], w["to"]) for w in walks] == [("Anatole", "Prince Vasíli")]
    assert gaps == []


def test_the_pairs_past_the_pair_cap_are_counted() -> None:
    assert walk_pairs_left_out(["a", "b", "c"]) == 0  # three pairs: all joined
    assert walk_pairs_left_out(["a", "b", "c", "d"]) == 3  # six pairs, three joined
    assert walk_pairs_left_out(["a", "a", "b"]) == 0


def test_a_connector_past_a_hubs_first_edges_is_never_cut_by_the_degree_cap() -> None:
    """The cap applies after the reach pruning: the hub's edges that lead nowhere take no slot.

    Until 2026-10-09 the hub's first 500 edges by id were kept before the
    pruning, and n599, the connector past them, was lost (a gap that said no
    chain exists).
    """
    hub = [_edge(f"h{i:04d}", "H", "knows", f"n{i}") for i in range(600)]
    far = _edge("z", "n599", "knows", "T")  # past the first 500 edges by id
    near = _edge("y", "n3", "knows", "T")
    capped: dict[str, int] = {}
    adjacency: dict[str, list[Any]] = {}
    for e in [*hub, far, near]:
        adjacency.setdefault(e.source_node_id, []).append(e)
        adjacency.setdefault(e.target_node_id, []).append(e)
    walks = shortest_walks("H", "T", adjacency, max_hops=2, max_walks=5, degree_capped=capped)
    assert [[e.id for e in w] for w in walks] == [["h0003", "y"], ["h0599", "z"]]
    assert capped == {}  # two candidates: under the cap


def test_a_hub_with_more_reaching_edges_than_the_cap_is_expanded_through_the_first_and_counted() -> (
    None
):
    hub = [_edge(f"h{i:04d}", "H", "knows", f"n{i}") for i in range(600)]
    onward = [_edge(f"t{i:04d}", f"n{i}", "knows", "T") for i in range(600)]
    capped: dict[str, int] = {}
    adjacency: dict[str, list[Any]] = {}
    for e in [*hub, *onward]:
        adjacency.setdefault(e.source_node_id, []).append(e)
        adjacency.setdefault(e.target_node_id, []).append(e)
    walks = shortest_walks("H", "T", adjacency, max_hops=2, max_walks=2, degree_capped=capped)
    assert [[e.id for e in w] for w in walks] == [["h0000", "t0000"], ["h0001", "t0001"]]
    assert capped == {"H": 100}  # max(500, 50 * 2) edges followed, the other 100 counted


def test_walk_hops_get_their_markers_too() -> None:
    walks, _ = _walks()
    result: dict[str, Any] = {
        "graph_context": {"walks": walks},
        "chunks": [
            {
                "chunk_id": "c7",
                "content": "[CHUNK C3 | w.txt]\n[S1] a.\n[S2] b.\n[S3] Dólokhov lived with Anatole.",
            }
        ],
    }
    annotate_markers(result)
    assert result["graph_context"]["walks"][0]["hops"][0]["marker"] == "C3:S3"
    assert "marker" not in result["graph_context"]["walks"][0]["hops"][1]


def test_an_inverse_whose_twin_is_missing_is_the_forward_fact_in_both_blocks() -> None:
    """The one fold (``_folds_onto_twin``): the twin is the mirrored fact, not any edge between the two.

    Count Rostóv guardian_of Natásha mirrors "Natásha ward_of Count Rostóv",
    which no edge states (Natásha child_of Count Rostóv is another fact): the
    inverse is kept, written forward, and counted as folded nowhere.
    """
    ward = _edge("e9i", "R", "guardian_of", "N", **{INVERSE_OF_KEY: "ward_of"})
    [block] = _blocks(edges_by_node={"R": [EDGES[0], ward]})
    assert block["skipped"]["inverse"] == 0
    wards = next(g for g in block["groups"] if g["label"] == "ward_of")
    assert [(r["source"], r["target"]) for r in wards["rows"]] == [("Natásha", "Count Rostóv")]
    mentor = _edge("w9i", "A", "mentors", "D", **{INVERSE_OF_KEY: "pupil_of"})
    _, skipped = walk_chains(
        pairs=[("D", "P")],
        edges=[mentor, CHAIN[2]],
        max_hops=3,
        max_walks=5,
        include_unevidenced=False,
    )
    assert skipped["inverse"] == 0
    [walk], _ = _walks(edges=[mentor, CHAIN[2]])
    assert _triple_hops(walk)[0] == ("Dólokhov", "pupil_of", "Anatole")


def test_parallel_edges_are_one_step_through_the_unflagged_one_and_the_others_are_counted() -> None:
    flagged = _edge("a1", "D", "knows", "A", **{CAPPED_KEY: True})
    plain = _edge("a2", "D", "lives_with", "A")
    edges = [flagged, plain, CHAIN[2]]
    chains, skipped = walk_chains(
        pairs=[("D", "P")], edges=edges, max_hops=3, max_walks=5, include_unevidenced=False
    )
    assert [[e.id for e in c] for c in chains[("D", "P")]] == [["a2", "w2"]]
    assert skipped["parallel"] == 1  # a1, seen from both ends, counted once


def test_an_edge_to_a_node_outside_the_loaded_graph_is_never_walked_and_is_counted() -> None:
    chains, skipped = walk_chains(
        pairs=[("D", "P")],
        edges=CHAIN,
        max_hops=3,
        max_walks=5,
        include_unevidenced=False,
        node_ids={"D", "A", "P"},  # Prince Vasíli was not loaded
    )
    assert [[e.id for e in c] for c in chains[("D", "P")]] == [["w1", "w2"]]
    assert skipped["outside_graph"] == 2  # w3 and w4
