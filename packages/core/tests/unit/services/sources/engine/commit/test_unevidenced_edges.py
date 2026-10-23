# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""An unevidenced relationship commits like any edge, flagged; a fact's copies fold into one edge.

Keep, flag, never delete (2026-10-07): the evidence filter keeps a
relationship no cited sentence states, flagged ``properties.unevidenced``
with ``properties.evidence_reason``. The commit stores it as an edge
carrying both (and its inverse with them). The stable-edge upsert stores
one edge per fact and keeps the first copy's properties, so the copies of a
fact fold first (``relation.fold_fact_copies`` through
``merge_edge_properties``): the edge is unevidenced only when every copy is.
"""

from __future__ import annotations

from typing import Any

from chaoscypher_core.models import EdgeCreate
from chaoscypher_core.services.sources.engine.commit.relation import (
    _build_edge_properties,
    fold_fact_copies,
)
from chaoscypher_core.services.sources.engine.deduplication.service import merge_edge_properties


REASON = 'neither "Prince Vasíli" nor "Anna Pávlovna" is named in S15'


def _flagged(justification: str, sent_ref: str = "S15") -> dict[str, Any]:
    return {
        "source": 0,
        "target": 1,
        "type": "confides_in",
        "confidence": 0.9,
        "sent_ref": sent_ref,
        "justification": justification,
        "chunk_index": 3,
        "properties": {"unevidenced": True, "evidence_reason": REASON},
    }


def test_the_edge_carries_the_flag_the_reason_and_the_justification() -> None:
    props = _build_edge_properties(_flagged("Prince Vasíli tells Anna Pávlovna his troubles."))
    assert props == {
        "unevidenced": True,
        "evidence_reason": REASON,
        "confidence": 0.9,
        "justification": "Prince Vasíli tells Anna Pávlovna his troubles.",
        "sent_ref": "S15",
        "chunk_index": 3,
    }


def _edge(props: dict[str, Any], source: str = "vasili", target: str = "anna") -> EdgeCreate:
    return EdgeCreate(
        template_id="t-confides",
        source_node_id=source,
        target_node_id=target,
        label="confides_in",
        properties=props,
    )


def test_an_evidenced_copy_of_the_fact_clears_the_flag_on_its_one_edge() -> None:
    first = _edge(_build_edge_properties(_flagged("He confides in her.")))
    second = _edge(
        {"confidence": 0.8, "justification": "Prince Vasíli confided in Anna Pávlovna.",
         "sent_ref": "S2", "chunk_index": 7}
    )  # fmt: skip
    other_fact = _edge(_build_edge_properties(_flagged("x")), target="pierre")
    edges = fold_fact_copies([first, second, other_fact])
    assert edges == [first, second, other_fact]
    assert first.properties == {
        "confidence": 0.9,
        "justification": "Prince Vasíli confided in Anna Pávlovna.",
        "sent_ref": "S2",
        "chunk_index": 7,
    }
    assert other_fact.properties["unevidenced"] is True  # another fact: its own copy only


def test_copies_that_agree_leave_the_first_edge_as_it_was() -> None:
    a = _edge({"confidence": 0.9, "justification": "A.", "sent_ref": "S1"})
    b = _edge({"confidence": 0.5, "justification": "B.", "sent_ref": "S2"})
    before = dict(a.properties)
    fold_fact_copies([a, b])
    assert a.properties == before  # first write wins, as before 2026-10-07
    u1 = _edge({"justification": "A.", "unevidenced": True, "evidence_reason": "r1"})
    u2 = _edge({"justification": "B.", "unevidenced": True, "evidence_reason": "r2"})
    fold_fact_copies([u1, u2])
    assert u1.properties == {"justification": "A.", "unevidenced": True, "evidence_reason": "r1"}


def test_the_merge_policy_keeps_a_text_value_under_the_flag_key() -> None:
    """An extracted ``unevidenced`` of the text's ("no") is no flag: never cleared or read."""
    merged = merge_edge_properties(
        {"unevidenced": "no", "justification": "A."}, {"justification": "B."}
    )
    assert merged["unevidenced"] == "no"
    assert merged["justifications"] == ["A.", "B."]


def test_every_edge_flag_merges_by_the_nodes_unanimous_rule() -> None:
    """One flag-merge rule for nodes and edges (``merge_unanimous_flags``).

    Every edge flag (``capped``, ``unevidenced``, ``unmatched_label``,
    ``type_mismatch``) stays only when both copies carry it, each on its own; a
    flag's reason goes with it, the written label stays as data.
    """
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
        PIPELINE_EDGE_FLAG_KEYS,
    )
    from chaoscypher_core.services.sources.engine.deduplication.service import (
        merge_node_properties,
    )

    both = {"capped": True, "unevidenced": True, "evidence_reason": REASON}
    assert set(PIPELINE_EDGE_FLAG_KEYS) == {
        "capped",
        "unevidenced",
        "unmatched_label",
        "type_mismatch",
    }
    kept = merge_edge_properties({"justification": "A.", **both}, {"justification": "A.", **both})
    assert kept == {"justification": "A.", **both}
    uncapped = merge_edge_properties(
        {"justification": "A.", **both},
        {"justification": "A.", "unevidenced": True, "evidence_reason": "r2"},
    )
    assert "capped" not in uncapped
    assert uncapped["unevidenced"] is True and uncapped["evidence_reason"] == REASON
    evidenced = merge_edge_properties(
        {"justification": "A.", **both}, {"justification": "B.", "capped": True}
    )
    assert evidenced["capped"] is True
    assert "unevidenced" not in evidenced and "evidence_reason" not in evidenced
    assert merge_edge_properties({"capped": "yes"}, {"capped": True})["capped"] == "yes"
    # the type check's flags (2026-10-08): the reason goes with its flag, the written label stays
    misfit = {
        "unmatched_label": True,
        "written_label": "owes_money_to",
        "type_mismatch": True,
        "type_mismatch_reason": "related_to takes a Person",
    }
    assert merge_edge_properties(dict(misfit), dict(misfit)) == misfit
    matched = merge_edge_properties(dict(misfit), {"justification": "B."})
    assert matched == {"written_label": "owes_money_to"}
    assert merge_edge_properties({"type_mismatch": "no"}, {"type_mismatch": True}) == {
        "type_mismatch": "no"
    }
    # the nodes' flags by the same rule
    node = merge_node_properties({"orphan": True, "low_plausibility": True}, {"orphan": True})
    assert node.get("orphan") is True and "low_plausibility" not in node
    named = merge_node_properties(
        {"unevidenced": True, "excluded_name": True, "excluded_by": "Chapter headings"},
        {"unevidenced": True},
    )
    assert named["unevidenced"] is True and "excluded_name" not in named
