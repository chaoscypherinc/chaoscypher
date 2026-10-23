# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The commit orients relationships by node id so one fact commits as one edge."""

from __future__ import annotations

from chaoscypher_core.services.sources.engine.commit.relation import RelationshipCommitHandler


_INDEX_TO_NODE = {0: "node_b", 1: "node_a", 2: "node_c"}


def _orient(rels: list[dict], **kwargs: object) -> list[dict]:
    return RelationshipCommitHandler.orient_relationships_for_commit(
        rels,
        symmetric_relationships=kwargs.get("symmetric", ["interacts_with"]),  # type: ignore[arg-type]
        inverse_relationships=kwargs.get("inverse", {"parent_of": "child_of"}),  # type: ignore[arg-type]
        entity_name_to_node_id={"Anna": "node_a", "Boris": "node_b"},
        entity_index_to_node_id=_INDEX_TO_NODE,
    )


def test_symmetric_relationships_from_any_path_share_one_node_order() -> None:
    """Stored rows in either direction become one (source, target) by node id."""
    out = _orient(
        [
            {"source": 0, "target": 1, "type": "interacts_with"},
            {"source": 1, "target": 0, "type": "interacts_with"},
        ]
    )
    assert {(r["source"], r["target"]) for r in out} == {(1, 0)}  # node_a before node_b


def test_inverse_pair_commits_on_one_label() -> None:
    """parent_of(A,B) and child_of(B,A) become the same child_of(B,A)."""
    out = _orient(
        [
            {"source": 1, "target": 0, "type": "parent_of"},
            {"source": 0, "target": 1, "type": "child_of"},
        ]
    )
    assert {(r["source"], r["target"], r["type"]) for r in out} == {(0, 1, "child_of")}


def test_name_keyed_relationships_are_oriented_by_their_nodes() -> None:
    """``from``/``to`` names resolve to nodes for the symmetric order."""
    (out,) = _orient([{"from": "Boris", "to": "Anna", "type": "interacts_with"}])
    assert (out["from"], out["to"]) == ("Anna", "Boris")


def test_without_domain_sets_relationships_pass_through() -> None:
    """No symmetric labels and no inverse map: the list is returned as given."""
    rels = [{"source": 1, "target": 0, "type": "interacts_with"}]
    assert _orient(rels, symmetric=None, inverse=None) == rels
