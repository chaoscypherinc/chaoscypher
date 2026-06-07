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

    def test_prefers_name_based_over_index_based(self) -> None:
        """When both from/to and source/target exist, from/to takes priority."""
        name_map = {"Alice": "n1", "Bob": "n2"}
        index_map = {0: "n3", 1: "n4"}
        result = RelationshipCommitHandler._resolve_node_ids(
            {"from": "Alice", "to": "Bob", "source": 0, "target": 1},
            entity_name_to_node_id=name_map,
            entity_index_to_node_id=index_map,
        )
        assert result == ("n1", "n2")
