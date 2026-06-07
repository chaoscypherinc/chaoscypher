# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for EdgeToolHandlers.

Covers edge creation, listing (with optional node and source scope filters),
and get_node_edges (direction filtering, edge_type filtering, self-loop
deduplication, source scope, missing related nodes, and the @tool_handler
error wrapper).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.workflows.tools.engine.handlers.edge_handlers import (
    EdgeToolHandlers,
)


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def make_node(
    nid: str,
    label: str,
    template_id: str = "default",
    source_id: str | None = None,
) -> SimpleNamespace:
    """Create a minimal node object for mocking graph repository returns.

    Args:
        nid: Node identifier.
        label: Human-readable label.
        template_id: Template / type identifier.
        source_id: Optional source identifier for scope filtering.

    Returns:
        A ``SimpleNamespace`` mimicking a node entity.

    """
    return SimpleNamespace(
        id=nid,
        label=label,
        template_id=template_id,
        source_id=source_id,
        created_at=None,
        updated_at=None,
        properties={},
    )


def make_edge(
    eid: str,
    source_id: str,
    target_id: str,
    label: str = "related_to",
    template_id: str = "default",
    properties: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """Create a minimal edge object for mocking graph repository returns.

    Args:
        eid: Edge identifier.
        source_id: Source node identifier.
        target_id: Target node identifier.
        label: Relationship label.
        template_id: Edge template identifier.
        properties: Edge properties dict.

    Returns:
        A ``SimpleNamespace`` mimicking an edge entity.

    """
    return SimpleNamespace(
        id=eid,
        source_node_id=source_id,
        target_node_id=target_id,
        label=label,
        template_id=template_id,
        properties=properties or {},
    )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def graph_repo() -> MagicMock:
    """Mock graph repository with sensible defaults for edge handler tests."""
    repo = MagicMock()
    repo.list_edges.return_value = []
    repo.get_nodes_batch.return_value = []
    repo.create_edge.return_value = SimpleNamespace(
        id="new-edge-id",
        source_node_id="src",
        target_node_id="tgt",
        label="related_to",
        properties={},
    )
    return repo


@pytest.fixture
def handler(graph_repo: MagicMock) -> EdgeToolHandlers:
    """Construct an ``EdgeToolHandlers`` wired to the mock graph repository.

    Args:
        graph_repo: Mock graph repository fixture.

    Returns:
        Handler instance ready for testing.

    """
    return EdgeToolHandlers(graph_repository=graph_repo)


# ===========================================================================
# create_edge Tests
# ===========================================================================


class TestCreateEdge:
    """Tests for EdgeToolHandlers.create_edge."""

    @pytest.mark.asyncio
    async def test_basic_creation(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """A simple create_edge call returns success with edge details."""
        created = SimpleNamespace(
            id="e1",
            source_node_id="n1",
            target_node_id="n2",
            label="knows",
            properties={"weight": 1},
        )
        graph_repo.create_edge.return_value = created

        result = await handler.create_edge(
            source_node_id="n1",
            target_node_id="n2",
            template_id="person_link",
            label="knows",
            properties={"weight": 1},
        )

        assert result["success"] is True
        assert result["edge_id"] == "e1"
        assert result["edge"]["source_node_id"] == "n1"
        assert result["edge"]["target_node_id"] == "n2"
        assert result["edge"]["label"] == "knows"
        assert result["edge"]["properties"] == {"weight": 1}
        assert "Created edge: knows" in result["message"]

        # Verify create_edge was called with an EdgeCreate model
        graph_repo.create_edge.assert_called_once()
        call_arg = graph_repo.create_edge.call_args[0][0]
        assert call_arg.source_node_id == "n1"
        assert call_arg.target_node_id == "n2"
        assert call_arg.template_id == "person_link"
        assert call_arg.label == "knows"
        assert call_arg.properties == {"weight": 1}

    @pytest.mark.asyncio
    async def test_default_template_and_label(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Omitting template_id and label uses GraphSettings defaults."""
        created = SimpleNamespace(
            id="e2",
            source_node_id="n1",
            target_node_id="n2",
            label="related_to",
            properties={},
        )
        graph_repo.create_edge.return_value = created

        result = await handler.create_edge(
            source_node_id="n1",
            target_node_id="n2",
        )

        assert result["success"] is True
        # Verify defaults were passed through
        call_arg = graph_repo.create_edge.call_args[0][0]
        assert call_arg.template_id == "system_template_link"
        assert call_arg.label == "related_to"
        assert call_arg.properties == {}

    @pytest.mark.asyncio
    async def test_custom_properties(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Custom properties dict is forwarded to EdgeCreate and included in result."""
        props = {"confidence": 0.95, "source": "manual"}
        created = SimpleNamespace(
            id="e3",
            source_node_id="a",
            target_node_id="b",
            label="linked",
            properties=props,
        )
        graph_repo.create_edge.return_value = created

        result = await handler.create_edge(
            source_node_id="a",
            target_node_id="b",
            template_id="t1",
            label="linked",
            properties=props,
        )

        assert result["success"] is True
        assert result["edge"]["properties"] == props

    @pytest.mark.asyncio
    async def test_exception_returns_failure(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """When graph.create_edge raises, the @tool_handler decorator catches it."""
        graph_repo.create_edge.side_effect = RuntimeError("DB write failed")

        result = await handler.create_edge(
            source_node_id="n1",
            target_node_id="n2",
        )

        assert result["success"] is False
        assert result["error"] == "Operation failed"


# ===========================================================================
# list_edges Tests
# ===========================================================================


class TestListEdges:
    """Tests for EdgeToolHandlers.list_edges."""

    @pytest.mark.asyncio
    async def test_unfiltered_list(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Listing without filters returns all edges from the repository."""
        edges = [
            make_edge("e1", "n1", "n2", label="knows"),
            make_edge("e2", "n2", "n3", label="works_with"),
        ]
        graph_repo.list_edges.return_value = edges

        result = await handler.list_edges()

        assert result["success"] is True
        assert result["count"] == 2
        assert len(result["edges"]) == 2
        assert result["edges"][0]["id"] == "e1"
        assert result["edges"][1]["label"] == "works_with"

    @pytest.mark.asyncio
    async def test_node_id_filter(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Filtering by node_id returns only edges touching that node."""
        edges = [
            make_edge("e1", "n1", "n2"),
            make_edge("e2", "n2", "n3"),
            make_edge("e3", "n4", "n5"),
        ]
        graph_repo.list_edges.return_value = edges

        result = await handler.list_edges(node_id="n2")

        assert result["success"] is True
        assert result["count"] == 2
        edge_ids = {e["id"] for e in result["edges"]}
        assert edge_ids == {"e1", "e2"}

    @pytest.mark.asyncio
    async def test_source_ids_scope_filter(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Source scope filtering keeps only edges between in-scope nodes.

        Edges where BOTH endpoints are in-scope (node has matching source_id
        or no source_id at all) are retained.
        """
        edges = [
            make_edge("e1", "n1", "n2"),  # Both in scope
            make_edge("e2", "n2", "n3"),  # n3 out of scope
        ]
        graph_repo.list_edges.return_value = edges

        node_n1 = make_node("n1", "Alice", source_id="src_a")
        node_n2 = make_node("n2", "Bob", source_id="src_a")
        node_n3 = make_node("n3", "Charlie", source_id="src_b")
        graph_repo.get_nodes_batch.return_value = [node_n1, node_n2, node_n3]

        result = await handler.list_edges(source_ids=["src_a"])

        assert result["success"] is True
        assert result["count"] == 1
        assert result["edges"][0]["id"] == "e1"

    @pytest.mark.asyncio
    async def test_source_ids_nodes_without_source_id_pass_filter(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Nodes without a source_id attribute pass the scope filter.

        The handler uses ``getattr(n, 'source_id', None)`` so nodes lacking
        source_id are treated as always in scope.
        """
        edges = [make_edge("e1", "n1", "n2")]
        graph_repo.list_edges.return_value = edges

        # n1 has no source_id (None), n2 has matching source_id
        node_n1 = make_node("n1", "Alice", source_id=None)
        node_n2 = make_node("n2", "Bob", source_id="src_a")
        graph_repo.get_nodes_batch.return_value = [node_n1, node_n2]

        result = await handler.list_edges(source_ids=["src_a"])

        assert result["success"] is True
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_empty_results(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """An empty graph returns count 0 and an empty edges list."""
        graph_repo.list_edges.return_value = []

        result = await handler.list_edges()

        assert result["success"] is True
        assert result["count"] == 0
        assert result["edges"] == []

    @pytest.mark.asyncio
    async def test_combined_node_id_and_source_ids_filter(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Both node_id and source_ids filters are applied together."""
        edges = [
            make_edge("e1", "n1", "n2"),  # touches n1, both in scope
            make_edge("e2", "n1", "n3"),  # touches n1, but n3 out of scope
            make_edge("e3", "n4", "n5"),  # doesn't touch n1
        ]
        graph_repo.list_edges.return_value = edges

        node_n1 = make_node("n1", "Alice", source_id="src_a")
        node_n2 = make_node("n2", "Bob", source_id="src_a")
        node_n3 = make_node("n3", "Charlie", source_id="src_b")
        graph_repo.get_nodes_batch.return_value = [node_n1, node_n2, node_n3]

        result = await handler.list_edges(node_id="n1", source_ids=["src_a"])

        assert result["success"] is True
        assert result["count"] == 1
        assert result["edges"][0]["id"] == "e1"

    @pytest.mark.asyncio
    async def test_edge_serialization_fields(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Each edge dict in the result includes all expected fields."""
        edge = make_edge("e1", "n1", "n2", label="knows", template_id="link_tpl")
        edge.properties = {"weight": 5}
        graph_repo.list_edges.return_value = [edge]

        result = await handler.list_edges()

        edge_dict = result["edges"][0]
        assert edge_dict == {
            "id": "e1",
            "source_node_id": "n1",
            "target_node_id": "n2",
            "label": "knows",
            "template_id": "link_tpl",
            "properties": {"weight": 5},
        }

    @pytest.mark.asyncio
    async def test_exception_returns_failure(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """When graph.list_edges raises, the @tool_handler catches it."""
        graph_repo.list_edges.side_effect = RuntimeError("DB read failed")

        result = await handler.list_edges()

        assert result["success"] is False
        assert result["error"] == "Operation failed"


# ===========================================================================
# get_node_edges Tests
# ===========================================================================


class TestGetNodeEdges:
    """Tests for EdgeToolHandlers.get_node_edges."""

    @pytest.mark.asyncio
    async def test_outgoing_only(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Direction 'outgoing' queries only edges where node is source."""
        outgoing = [make_edge("e1", "n1", "n2", label="knows")]
        graph_repo.list_edges.return_value = outgoing

        related_node = make_node("n2", "Bob")
        graph_repo.get_nodes_batch.return_value = [related_node]

        result = await handler.get_node_edges(node_id="n1", direction="outgoing")

        assert result["success"] is True
        assert result["direction"] == "outgoing"
        assert result["count"] == 1
        assert result["edges"][0]["direction"] == "outgoing"
        assert result["edges"][0]["related_node"]["id"] == "n2"

        # Only one call to list_edges (source_node_id=n1)
        graph_repo.list_edges.assert_called_once_with(
            source_node_id="n1",
            limit=50,
        )

    @pytest.mark.asyncio
    async def test_incoming_only(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Direction 'incoming' queries only edges where node is target."""
        incoming = [make_edge("e2", "n3", "n1", label="follows")]
        graph_repo.list_edges.return_value = incoming

        related_node = make_node("n3", "Charlie")
        graph_repo.get_nodes_batch.return_value = [related_node]

        result = await handler.get_node_edges(node_id="n1", direction="incoming")

        assert result["success"] is True
        assert result["direction"] == "incoming"
        assert result["count"] == 1
        assert result["edges"][0]["direction"] == "incoming"
        assert result["edges"][0]["related_node"]["id"] == "n3"

        graph_repo.list_edges.assert_called_once_with(
            target_node_id="n1",
            limit=50,
        )

    @pytest.mark.asyncio
    async def test_both_directions(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Direction 'both' queries outgoing and incoming edges."""
        outgoing = [make_edge("e1", "n1", "n2", label="knows")]
        incoming = [make_edge("e2", "n3", "n1", label="follows")]

        # list_edges is called twice: once for outgoing, once for incoming
        graph_repo.list_edges.side_effect = [outgoing, incoming]

        node_n2 = make_node("n2", "Bob")
        node_n3 = make_node("n3", "Charlie")
        graph_repo.get_nodes_batch.return_value = [node_n2, node_n3]

        result = await handler.get_node_edges(node_id="n1", direction="both")

        assert result["success"] is True
        assert result["direction"] == "both"
        assert result["count"] == 2

        directions = {e["direction"] for e in result["edges"]}
        assert directions == {"outgoing", "incoming"}

    @pytest.mark.asyncio
    async def test_self_loop_deduplication(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """A self-loop edge appears in both outgoing and incoming but is deduplicated.

        When querying 'both' directions for a node with a self-loop, the same
        edge appears in both the outgoing and incoming result sets. The handler
        must deduplicate by edge ID so it appears only once.
        """
        self_loop = make_edge("e_loop", "n1", "n1", label="self_ref")

        # Same edge returned for both outgoing and incoming queries
        graph_repo.list_edges.side_effect = [[self_loop], [self_loop]]

        # For a self-loop, the "related node" is n1 itself, but the handler
        # looks up the target_node_id (n1) since is_outgoing is True for the
        # first (deduplicated) occurrence.
        node_n1 = make_node("n1", "Alice")
        graph_repo.get_nodes_batch.return_value = [node_n1]

        result = await handler.get_node_edges(node_id="n1", direction="both")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["edges"][0]["edge_id"] == "e_loop"

    @pytest.mark.asyncio
    async def test_edge_type_filter_by_label(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """The edge_type parameter filters edges matching the label."""
        edges = [
            make_edge("e1", "n1", "n2", label="knows"),
            make_edge("e2", "n1", "n3", label="works_with"),
        ]
        graph_repo.list_edges.return_value = edges

        node_n2 = make_node("n2", "Bob")
        node_n3 = make_node("n3", "Charlie")
        graph_repo.get_nodes_batch.return_value = [node_n2, node_n3]

        result = await handler.get_node_edges(
            node_id="n1",
            direction="outgoing",
            edge_type="knows",
        )

        assert result["success"] is True
        assert result["count"] == 1
        assert result["edges"][0]["label"] == "knows"
        assert result["edge_type_filter"] == "knows"

    @pytest.mark.asyncio
    async def test_edge_type_filter_by_template_id(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """The edge_type parameter also matches on template_id."""
        edges = [
            make_edge("e1", "n1", "n2", label="knows", template_id="person_link"),
            make_edge("e2", "n1", "n3", label="works_with", template_id="org_link"),
        ]
        graph_repo.list_edges.return_value = edges

        node_n2 = make_node("n2", "Bob")
        graph_repo.get_nodes_batch.return_value = [node_n2]

        result = await handler.get_node_edges(
            node_id="n1",
            direction="outgoing",
            edge_type="person_link",
        )

        assert result["success"] is True
        assert result["count"] == 1
        assert result["edges"][0]["template_id"] == "person_link"

    @pytest.mark.asyncio
    async def test_limit_applied(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """The limit parameter caps the number of edges returned."""
        edges = [make_edge(f"e{i}", "n1", f"n{i + 10}", label="knows") for i in range(10)]
        graph_repo.list_edges.return_value = edges

        # Create matching related nodes
        related_nodes = [make_node(f"n{i + 10}", f"Node{i}") for i in range(10)]
        graph_repo.get_nodes_batch.return_value = related_nodes

        result = await handler.get_node_edges(
            node_id="n1",
            direction="outgoing",
            limit=3,
        )

        assert result["success"] is True
        assert result["count"] == 3

        # Verify limit was forwarded to the repository call
        graph_repo.list_edges.assert_called_once_with(
            source_node_id="n1",
            limit=3,
        )

    @pytest.mark.asyncio
    async def test_source_scope_filters_related_nodes(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Source scope filtering excludes edges to out-of-scope related nodes.

        When source_ids is specified, edges pointing to nodes whose source_id
        is not in the allowed list are excluded from results.
        """
        edges = [
            make_edge("e1", "n1", "n2", label="knows"),
            make_edge("e2", "n1", "n3", label="works_with"),
        ]
        graph_repo.list_edges.return_value = edges

        # n2 is in scope, n3 is out of scope
        node_n2 = make_node("n2", "Bob", source_id="src_a")
        node_n3 = make_node("n3", "Charlie", source_id="src_b")
        # After source filtering, only n2 remains
        graph_repo.get_nodes_batch.return_value = [node_n2, node_n3]

        result = await handler.get_node_edges(
            node_id="n1",
            direction="outgoing",
            source_ids=["src_a"],
        )

        assert result["success"] is True
        assert result["count"] == 1
        assert result["edges"][0]["related_node"]["id"] == "n2"

    @pytest.mark.asyncio
    async def test_source_scope_nodes_without_source_id_pass(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Related nodes without source_id pass the source scope filter."""
        edges = [make_edge("e1", "n1", "n2", label="knows")]
        graph_repo.list_edges.return_value = edges

        # n2 has no source_id — should pass scope filter
        node_n2 = make_node("n2", "Bob", source_id=None)
        graph_repo.get_nodes_batch.return_value = [node_n2]

        result = await handler.get_node_edges(
            node_id="n1",
            direction="outgoing",
            source_ids=["src_a"],
        )

        assert result["success"] is True
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_missing_related_node_shows_not_found(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """When a related node is not found in batch, its label is '[not found]'.

        If ``get_nodes_batch`` does not return a node for an edge's related
        endpoint, the handler should still include the edge but with a
        placeholder node containing only id and '[not found]' label.
        """
        edges = [make_edge("e1", "n1", "n_missing", label="knows")]
        graph_repo.list_edges.return_value = edges

        # get_nodes_batch returns empty — node is missing
        graph_repo.get_nodes_batch.return_value = []

        result = await handler.get_node_edges(node_id="n1", direction="outgoing")

        assert result["success"] is True
        assert result["count"] == 1
        related = result["edges"][0]["related_node"]
        assert related["id"] == "n_missing"
        assert related["label"] == "[not found]"
        # Missing node placeholder should NOT have template_id or properties
        assert "template_id" not in related
        assert "properties" not in related

    @pytest.mark.asyncio
    async def test_edge_result_includes_all_fields(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Each edge result includes all expected fields.

        Verifies edge_id, label, template_id, direction, related_node,
        and properties are present.
        """
        edge = make_edge(
            "e1",
            "n1",
            "n2",
            label="knows",
            template_id="link_tpl",
            properties={"since": 2020},
        )
        graph_repo.list_edges.return_value = [edge]

        related = make_node("n2", "Bob", template_id="person")
        related.properties = {"age": 30}
        graph_repo.get_nodes_batch.return_value = [related]

        result = await handler.get_node_edges(node_id="n1", direction="outgoing")

        edge_result = result["edges"][0]
        assert edge_result["edge_id"] == "e1"
        assert edge_result["label"] == "knows"
        assert edge_result["template_id"] == "link_tpl"
        assert edge_result["direction"] == "outgoing"
        assert edge_result["properties"] == {"since": 2020}
        assert edge_result["related_node"] == {
            "id": "n2",
            "label": "Bob",
            "template_id": "person",
            "properties": {"age": 30},
        }

    @pytest.mark.asyncio
    async def test_top_level_result_fields(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """The top-level result dict includes all metadata fields."""
        graph_repo.list_edges.return_value = []

        result = await handler.get_node_edges(
            node_id="n1",
            direction="incoming",
            edge_type="knows",
        )

        assert result["success"] is True
        assert result["node_id"] == "n1"
        assert result["direction"] == "incoming"
        assert result["edge_type_filter"] == "knows"
        assert result["count"] == 0
        assert result["edges"] == []

    @pytest.mark.asyncio
    async def test_no_edge_type_filter_returns_none(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """When edge_type is not specified, edge_type_filter in result is None."""
        graph_repo.list_edges.return_value = []

        result = await handler.get_node_edges(node_id="n1", direction="outgoing")

        assert result["edge_type_filter"] is None

    @pytest.mark.asyncio
    async def test_incoming_edge_direction_label(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Incoming edges have direction 'incoming' and related node is the source."""
        edge = make_edge("e1", "n_other", "n1", label="follows")
        graph_repo.list_edges.return_value = [edge]

        related = make_node("n_other", "Other")
        graph_repo.get_nodes_batch.return_value = [related]

        result = await handler.get_node_edges(node_id="n1", direction="incoming")

        assert result["edges"][0]["direction"] == "incoming"
        assert result["edges"][0]["related_node"]["id"] == "n_other"

    @pytest.mark.asyncio
    async def test_exception_returns_failure(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """When graph.list_edges raises, the @tool_handler catches it."""
        graph_repo.list_edges.side_effect = RuntimeError("Connection lost")

        result = await handler.get_node_edges(node_id="n1")

        assert result["success"] is False
        assert result["error"] == "Operation failed"

    @pytest.mark.asyncio
    async def test_empty_edges_for_node(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """A node with no edges returns an empty list."""
        graph_repo.list_edges.return_value = []

        result = await handler.get_node_edges(node_id="n1", direction="both")

        assert result["success"] is True
        assert result["count"] == 0
        assert result["edges"] == []

    @pytest.mark.asyncio
    async def test_both_directions_with_edge_type_and_limit(
        self,
        handler: EdgeToolHandlers,
        graph_repo: MagicMock,
    ) -> None:
        """Combining direction='both', edge_type, and limit works correctly."""
        outgoing = [
            make_edge("e1", "n1", "n2", label="knows"),
            make_edge("e2", "n1", "n3", label="works_with"),
            make_edge("e3", "n1", "n4", label="knows"),
        ]
        incoming = [
            make_edge("e4", "n5", "n1", label="knows"),
            make_edge("e5", "n6", "n1", label="follows"),
        ]
        graph_repo.list_edges.side_effect = [outgoing, incoming]

        nodes = [
            make_node("n2", "Bob"),
            make_node("n4", "Dave"),
            make_node("n5", "Eve"),
        ]
        graph_repo.get_nodes_batch.return_value = nodes

        result = await handler.get_node_edges(
            node_id="n1",
            direction="both",
            edge_type="knows",
            limit=2,
        )

        assert result["success"] is True
        # Only "knows" edges, limited to 2
        assert result["count"] == 2
        for e in result["edges"]:
            assert e["label"] == "knows"
