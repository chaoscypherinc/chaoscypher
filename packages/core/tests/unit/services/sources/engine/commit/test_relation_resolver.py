# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for RelationshipCommitHandler._resolve_node_ids."""

from chaoscypher_core.services.sources.engine.commit.relation import (
    RelationshipCommitHandler,
)


class TestResolveNodeIds:
    """Tests for RelationshipCommitHandler._resolve_node_ids static method."""

    def test_resolves_name_based_from_to(self) -> None:
        name_map = {"Alice": "n1", "Bob": "n2"}
        result = RelationshipCommitHandler._resolve_node_ids(
            {"from": "Alice", "to": "Bob", "type": "knows"},
            entity_name_to_node_id=name_map,
            entity_index_to_node_id={},
        )
        assert result == ("n1", "n2")

    def test_resolves_index_based_source_target(self) -> None:
        index_map = {0: "n1", 1: "n2"}
        result = RelationshipCommitHandler._resolve_node_ids(
            {"source": 0, "target": 1, "type": "relates"},
            entity_name_to_node_id={},
            entity_index_to_node_id=index_map,
        )
        assert result == ("n1", "n2")

    def test_index_wins_when_a_reloaded_edge_carries_both(self) -> None:
        """Reload paths project name and index; the name map keeps the last same-name node.

        Regression (section-audit 2026-10-12): two "Moscow" entities the type
        gate kept apart (a place at 0, an organization at 2) both resolved to
        the last node written under the name; the index names one record.
        """
        result = RelationshipCommitHandler._resolve_node_ids(
            {"from": "Moscow", "to": "Pierre", "source": 0, "target": 1, "type": "home_of"},
            entity_name_to_node_id={"Moscow": "n-org", "Pierre": "n-pierre"},
            entity_index_to_node_id={0: "n-place", 1: "n-pierre", 2: "n-org"},
        )
        assert result == ("n-place", "n-pierre")

    def test_a_null_name_does_not_drop_an_edge_whose_index_resolves(self) -> None:
        result = RelationshipCommitHandler._resolve_node_ids(
            {"from": None, "to": "Pierre", "source": 0, "target": 1},
            entity_name_to_node_id={"Pierre": "n-pierre"},
            entity_index_to_node_id={0: "n-place", 1: "n-pierre"},
        )
        assert result == ("n-place", "n-pierre")

    def test_returns_none_for_missing_name(self) -> None:
        name_map = {"Alice": "n1"}
        result = RelationshipCommitHandler._resolve_node_ids(
            {"from": "Alice", "to": "Unknown"},
            entity_name_to_node_id=name_map,
            entity_index_to_node_id={},
        )
        assert result is None

    def test_returns_none_for_missing_index(self) -> None:
        index_map = {0: "n1"}
        result = RelationshipCommitHandler._resolve_node_ids(
            {"source": 0, "target": 99},
            entity_name_to_node_id={},
            entity_index_to_node_id=index_map,
        )
        assert result is None

    def test_returns_none_for_missing_both_formats(self) -> None:
        result = RelationshipCommitHandler._resolve_node_ids(
            {"type": "relates"},
            entity_name_to_node_id={},
            entity_index_to_node_id={},
        )
        assert result is None

    def test_returns_none_for_null_indices(self) -> None:
        result = RelationshipCommitHandler._resolve_node_ids(
            {"source": None, "target": None},
            entity_name_to_node_id={},
            entity_index_to_node_id={},
        )
        assert result is None

    def test_prefers_index_based_over_name_based(self) -> None:
        """When both from/to and source/target exist, source/target takes priority.

        Flipped 2026-10-12 (section-audit): the index names one record where
        a name keeps only the last node written under it; the only producer
        of both is the reload projection, whose names decorate the
        relationship-list endpoint.
        """
        name_map = {"Alice": "n1", "Bob": "n2"}
        index_map = {0: "n3", 1: "n4"}
        result = RelationshipCommitHandler._resolve_node_ids(
            {"from": "Alice", "to": "Bob", "source": 0, "target": 1},
            entity_name_to_node_id=name_map,
            entity_index_to_node_id=index_map,
        )
        assert result == ("n3", "n4")
