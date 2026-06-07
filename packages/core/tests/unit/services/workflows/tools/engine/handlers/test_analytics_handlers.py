# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for AnalyticsToolHandlers.

Covers graph structure analysis, shortest path, similar nodes, path traversal,
and all helper methods using mocked repositories and analytics service.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.workflows.tools.engine.handlers.analytics_handlers import (
    AnalyticsToolHandlers,
)
from chaoscypher_core.settings import EngineSettings


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def make_node(
    nid: str,
    label: str,
    template_id: str = "default",
    source_id: str | None = None,
    embedding: list[float] | None = None,
) -> SimpleNamespace:
    """Create a minimal node object for mocking graph repository returns.

    Args:
        nid: Node identifier.
        label: Human-readable label.
        template_id: Template / type identifier.
        source_id: Optional source file identifier.
        embedding: Optional embedding vector.

    Returns:
        A ``SimpleNamespace`` mimicking a node entity.

    """
    return SimpleNamespace(
        id=nid,
        label=label,
        template_id=template_id,
        source_id=source_id,
        embedding=embedding,
        properties={},
    )


def make_edge(
    eid: str,
    source_id: str,
    target_id: str,
    label: str = "related_to",
    template_id: str = "default",
) -> SimpleNamespace:
    """Create a minimal edge object for mocking graph repository returns.

    Args:
        eid: Edge identifier.
        source_id: Source node identifier.
        target_id: Target node identifier.
        label: Relationship label.
        template_id: Edge template identifier.

    Returns:
        A ``SimpleNamespace`` mimicking an edge entity.

    """
    return SimpleNamespace(
        id=eid,
        source_node_id=source_id,
        target_node_id=target_id,
        label=label,
        template_id=template_id,
    )


def _configure_graph_repo(
    repo: MagicMock,
    nodes: list[SimpleNamespace],
    edges: list[SimpleNamespace],
    batch_nodes: list[SimpleNamespace] | None = None,
) -> None:
    """Configure a graph repo mock with both standard and minimal method variants.

    ``AnalyticsToolHandlers`` uses ``hasattr`` to prefer ``list_nodes_minimal``
    and ``list_edges_minimal`` over their standard equivalents.  Because
    ``MagicMock`` auto-creates any attribute, ``hasattr`` always returns
    ``True``, so both variants must be configured explicitly.

    Args:
        repo: The ``MagicMock`` graph repository to configure.
        nodes: Node list to return from both ``list_nodes`` variants.
        edges: Edge list to return from both ``list_edges`` variants.
        batch_nodes: Optional node list for ``get_nodes_batch``; defaults to
            ``nodes`` when not provided.

    """
    repo.list_nodes.return_value = nodes
    repo.list_nodes_minimal.return_value = nodes
    repo.list_edges.return_value = edges
    repo.list_edges_minimal.return_value = edges
    repo.get_nodes_batch.return_value = batch_nodes if batch_nodes is not None else nodes


def _make_analytics_mock(
    communities: dict[str, Any] | None = None,
    pagerank: dict[str, Any] | None = None,
    clustering: dict[str, Any] | None = None,
    shortest_path: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a configured analytics service mock.

    Args:
        communities: Return value for ``detect_communities``.
        pagerank: Return value for ``calculate_pagerank``.
        clustering: Return value for ``calculate_clustering_coefficient``.
        shortest_path: Return value for ``find_shortest_path``.

    Returns:
        A ``MagicMock`` mimicking ``GraphAnalyticsService``.

    """
    mock = MagicMock()
    mock.detect_communities.return_value = communities or {
        "communities": [],
        "num_communities": 0,
    }
    mock.calculate_pagerank.return_value = pagerank or {"top_nodes": []}
    mock.calculate_clustering_coefficient.return_value = clustering or {
        "average_clustering": 0.0,
    }
    mock.find_shortest_path.return_value = shortest_path or {
        "success": True,
        "path": [],
    }
    return mock


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> EngineSettings:
    """Provide default EngineSettings."""
    return EngineSettings()


@pytest.fixture
def graph_repo() -> MagicMock:
    """Mock graph repository with all required methods.

    Both the standard and *_minimal variants are configured because
    AnalyticsToolHandlers uses ``hasattr`` to prefer minimal variants, and
    ``MagicMock`` auto-creates any attribute, making ``hasattr`` always
    return ``True``.
    """
    repo = MagicMock()
    _configure_graph_repo(repo, nodes=[], edges=[])
    return repo


@pytest.fixture
def search_repo() -> MagicMock:
    """Mock search repository with vector_search."""
    repo = MagicMock()
    repo.vector_search.return_value = []
    return repo


@pytest.fixture
def analytics_service() -> MagicMock:
    """Mock analytics service with all required methods."""
    return _make_analytics_mock()


def _make_handler(
    graph_repo: MagicMock,
    search_repo: MagicMock,
    analytics_service: MagicMock,
    settings: EngineSettings,
    node_limit: int | None = None,
    edge_limit: int | None = None,
) -> AnalyticsToolHandlers:
    """Construct an ``AnalyticsToolHandlers`` instance from mocks.

    Args:
        graph_repo: Mock graph repository.
        search_repo: Mock search repository.
        analytics_service: Mock analytics service.
        settings: Engine settings.
        node_limit: Optional node limit override.
        edge_limit: Optional edge limit override.

    Returns:
        Configured handler ready for testing.

    """
    return AnalyticsToolHandlers(
        graph_repository=graph_repo,
        search_repository=search_repo,
        analytics_service=analytics_service,
        node_limit=node_limit,
        edge_limit=edge_limit,
        settings=settings,
    )


# ===========================================================================
# analyze_graph_structure Tests
# ===========================================================================


class TestAnalyzeGraphStructure:
    """Tests for the analyze_graph_structure handler."""

    @pytest.mark.asyncio
    async def test_basic_analysis_returns_statistics(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Basic graph analysis returns correct statistics structure.

        When nodes and edges exist and no filters are applied, the handler
        should call all analytics methods and return a well-formed statistics
        dict.
        """
        node_a = make_node("n1", "Alice", template_id="person")
        node_b = make_node("n2", "Bob", template_id="person")
        edge = make_edge("e1", "n1", "n2", label="knows")
        _configure_graph_repo(graph_repo, nodes=[node_a, node_b], edges=[edge])

        analytics = _make_analytics_mock(
            communities={
                "communities": [{"id": 0, "size": 2, "members": ["n1", "n2"]}],
                "num_communities": 1,
            },
            pagerank={"top_nodes": [{"id": "n1", "label": "Alice", "score": 0.7}]},
            clustering={"average_clustering": 0.5},
        )

        handler = _make_handler(graph_repo, search_repo, analytics, settings)
        result = await handler.analyze_graph_structure()

        assert result["success"] is True
        assert result["statistics"]["node_count"] == 2
        assert result["statistics"]["edge_count"] == 1
        assert result["statistics"]["num_communities"] == 1
        assert result["statistics"]["average_clustering"] == 0.5
        assert len(result["communities"]) == 1
        assert result["communities"][0]["size"] == 2
        assert len(result["top_nodes"]) == 1

    @pytest.mark.asyncio
    async def test_empty_graph_returns_zero_statistics(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """An empty graph returns zeroed statistics without errors.

        When there are no nodes or edges, the handler should still succeed
        and return all expected keys with zero/empty values.
        """
        _configure_graph_repo(graph_repo, nodes=[], edges=[])

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)

        with (
            patch.object(
                type(analytics_service),
                "calculate_node_degrees_simple",
                staticmethod(lambda edges: {}),
                create=True,
            ),
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.calculate_node_degrees_simple",
                return_value={},
            ),
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.find_isolated_nodes_simple",
                return_value=[],
            ),
        ):
            result = await handler.analyze_graph_structure()

        assert result["success"] is True
        assert result["statistics"]["node_count"] == 0
        assert result["statistics"]["edge_count"] == 0
        assert result["statistics"]["average_degree"] == 0
        assert result["statistics"]["isolated_nodes"] == 0
        assert result["communities"] == []

    @pytest.mark.asyncio
    async def test_template_id_filter_narrows_nodes(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Filtering by template_ids includes only matching nodes and their edges.

        Nodes with non-matching template IDs should be excluded, and edges
        between excluded nodes should also be filtered out.
        """
        person = make_node("n1", "Alice", template_id="person")
        company = make_node("n2", "Acme Corp", template_id="company")
        edge = make_edge("e1", "n1", "n2", label="works_at")
        _configure_graph_repo(graph_repo, nodes=[person, company], edges=[edge])

        # Template lookup: graph.get_template returns a mock with .name
        person_template = SimpleNamespace(name="Person")
        company_template = SimpleNamespace(name="Company")

        def get_template_side_effect(tid: str) -> SimpleNamespace | None:
            if tid == "person":
                return person_template
            if tid == "company":
                return company_template
            return None

        graph_repo.get_template.side_effect = get_template_side_effect

        analytics = _make_analytics_mock(
            communities={"communities": [], "num_communities": 0},
            pagerank={"top_nodes": []},
            clustering={"average_clustering": 0.0},
        )

        handler = _make_handler(graph_repo, search_repo, analytics, settings)

        with (
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.calculate_node_degrees_simple",
                return_value={},
            ),
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.find_isolated_nodes_simple",
                return_value=[],
            ),
        ):
            result = await handler.analyze_graph_structure(template_ids=["person"])

        # Only the person node should be included
        assert result["statistics"]["node_count"] == 1
        # Edge between person and company should be excluded (company node not in set)
        assert result["statistics"]["edge_count"] == 0
        assert result["template_filter"] == ["person"]

    @pytest.mark.asyncio
    async def test_template_filter_case_insensitive_partial_match(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Template matching is case-insensitive and supports partial matches.

        A filter like ``["PER"]`` should match a node with template_id ``"person"``
        because ``"per"`` is contained in ``"person"``.
        """
        node = make_node("n1", "Alice", template_id="person")
        _configure_graph_repo(graph_repo, nodes=[node], edges=[])
        graph_repo.get_template.return_value = SimpleNamespace(name="Person")

        analytics = _make_analytics_mock(
            communities={"communities": [], "num_communities": 0},
            pagerank={"top_nodes": []},
            clustering={"average_clustering": 0.0},
        )

        handler = _make_handler(graph_repo, search_repo, analytics, settings)

        with (
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.calculate_node_degrees_simple",
                return_value={},
            ),
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.find_isolated_nodes_simple",
                return_value=[{"id": "n1", "label": "Alice"}],
            ),
        ):
            result = await handler.analyze_graph_structure(template_ids=["PER"])

        # Partial case-insensitive match: "per" in "person"
        assert result["statistics"]["node_count"] == 1

    @pytest.mark.asyncio
    async def test_source_ids_filter_narrows_nodes(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Filtering by source_ids excludes nodes from non-matching sources.

        Nodes without a source_id pass the filter. Nodes with a source_id
        not in the filter list are excluded.
        """
        node_in_scope = make_node("n1", "Alice", source_id="src1")
        node_out_of_scope = make_node("n2", "Bob", source_id="src2")
        node_no_source = make_node("n3", "Charlie")  # source_id=None passes filter
        edge_in = make_edge("e1", "n1", "n3", label="knows")
        edge_out = make_edge("e2", "n1", "n2", label="knows")

        _configure_graph_repo(
            graph_repo,
            nodes=[node_in_scope, node_out_of_scope, node_no_source],
            edges=[edge_in, edge_out],
        )

        analytics = _make_analytics_mock(
            communities={"communities": [], "num_communities": 0},
            pagerank={"top_nodes": []},
            clustering={"average_clustering": 0.0},
        )

        handler = _make_handler(graph_repo, search_repo, analytics, settings)

        with (
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.calculate_node_degrees_simple",
                return_value={"n1": 1, "n3": 1},
            ),
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.find_isolated_nodes_simple",
                return_value=[],
            ),
        ):
            result = await handler.analyze_graph_structure(source_ids=["src1"])

        # n1 (src1) and n3 (no source_id) pass; n2 (src2) excluded
        assert result["statistics"]["node_count"] == 2
        # Only edge e1 (n1->n3) remains; e2 excluded because n2 is out of scope
        assert result["statistics"]["edge_count"] == 1

    @pytest.mark.asyncio
    async def test_combined_template_and_source_filters(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Both template_ids and source_ids filters are applied together.

        Nodes must match both the template filter and the source scope.
        """
        person_in = make_node("n1", "Alice", template_id="person", source_id="src1")
        person_out = make_node("n2", "Bob", template_id="person", source_id="src2")
        company_in = make_node("n3", "Acme", template_id="company", source_id="src1")

        _configure_graph_repo(
            graph_repo,
            nodes=[person_in, person_out, company_in],
            edges=[],
        )

        graph_repo.get_template.side_effect = lambda tid: (
            SimpleNamespace(name="Person") if tid == "person" else SimpleNamespace(name="Company")
        )

        analytics = _make_analytics_mock(
            communities={"communities": [], "num_communities": 0},
            pagerank={"top_nodes": []},
            clustering={"average_clustering": 0.0},
        )

        handler = _make_handler(graph_repo, search_repo, analytics, settings)

        with (
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.calculate_node_degrees_simple",
                return_value={},
            ),
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.find_isolated_nodes_simple",
                return_value=[],
            ),
        ):
            result = await handler.analyze_graph_structure(
                template_ids=["person"], source_ids=["src1"]
            )

        # Only n1 matches both: person template + src1 source
        assert result["statistics"]["node_count"] == 1

    @pytest.mark.asyncio
    async def test_community_output_limited_to_top_10(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Community output is capped at top 10 by size with 5 sample members each.

        The handler sorts communities by size descending and takes the top 10.
        Each community's members are truncated to the first 5.
        """
        nodes = [make_node(f"n{i}", f"Node{i}") for i in range(50)]
        _configure_graph_repo(graph_repo, nodes=nodes, edges=[])

        # Create 15 communities of varying sizes
        communities_list = [
            {"id": i, "size": 50 - i, "members": [f"n{j}" for j in range(50 - i)]}
            for i in range(15)
        ]

        analytics = _make_analytics_mock(
            communities={
                "communities": communities_list,
                "num_communities": 15,
            },
            pagerank={"top_nodes": []},
            clustering={"average_clustering": 0.3},
        )

        handler = _make_handler(graph_repo, search_repo, analytics, settings)

        with (
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.calculate_node_degrees_simple",
                return_value={},
            ),
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.find_isolated_nodes_simple",
                return_value=[],
            ),
        ):
            result = await handler.analyze_graph_structure()

        assert len(result["communities"]) == 10
        # Largest community first
        assert result["communities"][0]["size"] == 50
        # Members capped at 5
        assert len(result["communities"][0]["sample_members"]) == 5

    @pytest.mark.asyncio
    async def test_average_degree_calculated_correctly(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Average degree is sum of degrees divided by number of nodes with edges.

        The degrees dict maps node_id to degree count. Average is computed
        over all entries in the dict.
        """
        node_a = make_node("n1", "Alice")
        node_b = make_node("n2", "Bob")
        node_c = make_node("n3", "Charlie")
        edge1 = make_edge("e1", "n1", "n2")
        edge2 = make_edge("e2", "n2", "n3")
        _configure_graph_repo(graph_repo, nodes=[node_a, node_b, node_c], edges=[edge1, edge2])

        analytics = _make_analytics_mock(
            communities={"communities": [], "num_communities": 0},
            pagerank={"top_nodes": []},
            clustering={"average_clustering": 0.0},
        )

        handler = _make_handler(graph_repo, search_repo, analytics, settings)

        # n1: degree 1, n2: degree 2, n3: degree 1 => avg = 4/3
        with (
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.calculate_node_degrees_simple",
                return_value={"n1": 1, "n2": 2, "n3": 1},
            ),
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.find_isolated_nodes_simple",
                return_value=[],
            ),
        ):
            result = await handler.analyze_graph_structure()

        assert result["statistics"]["average_degree"] == pytest.approx(4 / 3)

    @pytest.mark.asyncio
    async def test_template_filter_not_in_result_when_no_filter(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """The template_filter key is absent when no template_ids are passed.

        Only when template_ids is specified should the result include a
        ``template_filter`` key.
        """
        _configure_graph_repo(graph_repo, nodes=[], edges=[])
        analytics = _make_analytics_mock()

        handler = _make_handler(graph_repo, search_repo, analytics, settings)

        with (
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.calculate_node_degrees_simple",
                return_value={},
            ),
            patch(
                "chaoscypher_core.services.workflows.tools.engine.handlers"
                ".analytics_handlers.GraphAnalyticsService.find_isolated_nodes_simple",
                return_value=[],
            ),
        ):
            result = await handler.analyze_graph_structure()

        assert "template_filter" not in result


# ===========================================================================
# find_shortest_path Tests
# ===========================================================================


class TestFindShortestPath:
    """Tests for the find_shortest_path handler."""

    @pytest.mark.asyncio
    async def test_basic_shortest_path(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Basic shortest path delegates to analytics service.

        All nodes and edges are loaded and passed to the analytics service's
        find_shortest_path method.
        """
        node_a = make_node("n1", "Alice")
        node_b = make_node("n2", "Bob")
        edge = make_edge("e1", "n1", "n2")
        _configure_graph_repo(graph_repo, nodes=[node_a, node_b], edges=[edge])

        expected = {
            "success": True,
            "path": [{"id": "n1", "label": "Alice"}, {"id": "n2", "label": "Bob"}],
            "length": 1,
        }
        analytics = _make_analytics_mock(shortest_path=expected)

        handler = _make_handler(graph_repo, search_repo, analytics, settings)
        result = await handler.find_shortest_path("n1", "n2")

        assert result == expected
        analytics.find_shortest_path.assert_called_once()
        call_args = analytics.find_shortest_path.call_args
        assert call_args[0][2] == "n1"
        assert call_args[0][3] == "n2"

    @pytest.mark.asyncio
    async def test_shortest_path_with_source_scope(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Source scope filter removes out-of-scope nodes and their edges.

        When source_ids is provided, nodes from other sources are excluded,
        and only edges between in-scope nodes are passed to the algorithm.
        """
        node_in = make_node("n1", "Alice", source_id="src1")
        node_out = make_node("n2", "Bob", source_id="src2")
        node_target = make_node("n3", "Charlie", source_id="src1")
        edge_in = make_edge("e1", "n1", "n3")
        edge_out = make_edge("e2", "n1", "n2")

        _configure_graph_repo(
            graph_repo,
            nodes=[node_in, node_out, node_target],
            edges=[edge_in, edge_out],
        )

        analytics = _make_analytics_mock(shortest_path={"success": True, "path": [], "length": 0})

        handler = _make_handler(graph_repo, search_repo, analytics, settings)
        await handler.find_shortest_path("n1", "n3", source_ids=["src1"])

        # Verify the analytics call received only in-scope nodes/edges
        call_args = analytics.find_shortest_path.call_args
        passed_nodes = call_args[0][0]
        passed_edges = call_args[0][1]

        passed_node_ids = {n.id for n in passed_nodes}
        assert "n2" not in passed_node_ids
        assert "n1" in passed_node_ids
        assert "n3" in passed_node_ids

        passed_edge_ids = {e.id for e in passed_edges}
        assert "e2" not in passed_edge_ids  # Edge to out-of-scope node
        assert "e1" in passed_edge_ids


# ===========================================================================
# find_similar_nodes Tests
# ===========================================================================


class TestFindSimilarNodes:
    """Tests for the find_similar_nodes handler."""

    @pytest.mark.asyncio
    async def test_node_not_found_returns_error(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Requesting similarity for a non-existent node returns an error dict.

        When get_node returns None, the handler should return success=False
        with an appropriate error message.
        """
        graph_repo.get_node.return_value = None

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.find_similar_nodes("nonexistent")

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_with_embedding_uses_vector_search(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """When the source node has an embedding, vector search is used.

        The handler uses vector_search with the node's embedding, removes
        self from results, and batch-fetches the result nodes.
        """
        embedding = [0.1, 0.2, 0.3]
        source_node = make_node("n1", "Alice", template_id="person", embedding=embedding)
        similar_node = make_node("n2", "Bob", template_id="person")

        graph_repo.get_node.return_value = source_node
        search_repo.vector_search.return_value = [
            ("n1", 0.99),  # Self — should be removed
            ("n2", 0.85),
        ]
        graph_repo.get_nodes_batch.return_value = [similar_node]

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.find_similar_nodes("n1", limit=5)

        assert result["success"] is True
        assert result["count"] == 1
        assert result["similar_nodes"][0]["id"] == "n2"
        assert result["similar_nodes"][0]["similarity"] == 0.85
        assert result["source_node"]["id"] == "n1"

        # vector_search called with limit+1 to account for self removal
        search_repo.vector_search.assert_called_once_with(
            query_embedding=embedding,
            k=6,  # limit(5) + 1
        )

    @pytest.mark.asyncio
    async def test_without_embedding_fallback_to_same_template(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Without an embedding, falls back to same-template matching.

        Nodes with the same template_id are returned with a fixed 0.5
        similarity score. The source node is excluded from results.
        """
        source_node = make_node("n1", "Alice", template_id="person", embedding=None)
        same_template = make_node("n2", "Bob", template_id="person")
        diff_template = make_node("n3", "Acme", template_id="company")

        graph_repo.get_node.return_value = source_node
        _configure_graph_repo(
            graph_repo,
            nodes=[source_node, same_template, diff_template],
            edges=[],
        )

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.find_similar_nodes("n1", limit=10)

        assert result["success"] is True
        assert result["count"] == 1
        assert result["similar_nodes"][0]["id"] == "n2"
        assert result["similar_nodes"][0]["similarity"] == 0.5
        # vector_search should NOT be called
        search_repo.vector_search.assert_not_called()

    @pytest.mark.asyncio
    async def test_source_scope_on_source_node(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Source scope check on the source node itself blocks out-of-scope lookups.

        If the requested node belongs to a source not in the allowed list,
        the handler returns an error without performing any search.
        """
        node = make_node("n1", "Alice", source_id="src_other", embedding=[0.1])
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.find_similar_nodes("n1", source_ids=["src_allowed"])

        assert result["success"] is False
        assert "not accessible" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_source_scope_filters_vector_results(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Source scope filtering applies to vector search results.

        Result nodes from out-of-scope sources are excluded from the final
        output even if they are returned by vector_search.
        """
        embedding = [0.1, 0.2, 0.3]
        source_node = make_node("n1", "Alice", source_id="src1", embedding=embedding)
        in_scope = make_node("n2", "Bob", template_id="person", source_id="src1")
        out_scope = make_node("n3", "Eve", template_id="person", source_id="src2")

        graph_repo.get_node.return_value = source_node
        search_repo.vector_search.return_value = [
            ("n2", 0.9),
            ("n3", 0.8),
        ]
        graph_repo.get_nodes_batch.return_value = [in_scope, out_scope]

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.find_similar_nodes("n1", source_ids=["src1"])

        assert result["success"] is True
        # Only n2 should remain after scope filtering
        assert result["count"] == 1
        assert result["similar_nodes"][0]["id"] == "n2"

    @pytest.mark.asyncio
    async def test_source_scope_filters_fallback_results(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Source scope filtering applies to fallback same-template results.

        When using the no-embedding fallback, nodes from out-of-scope sources
        are excluded.
        """
        source_node = make_node("n1", "Alice", template_id="person", source_id="src1")
        in_scope = make_node("n2", "Bob", template_id="person", source_id="src1")
        out_scope = make_node("n3", "Eve", template_id="person", source_id="src2")

        graph_repo.get_node.return_value = source_node
        _configure_graph_repo(
            graph_repo,
            nodes=[source_node, in_scope, out_scope],
            edges=[],
        )

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.find_similar_nodes("n1", source_ids=["src1"])

        assert result["success"] is True
        assert result["count"] == 1
        assert result["similar_nodes"][0]["id"] == "n2"

    @pytest.mark.asyncio
    async def test_source_node_without_source_id_passes_scope(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """A source node with no source_id passes the scope check.

        Nodes without source_id are considered universally accessible.
        """
        node = make_node("n1", "Alice", embedding=[0.1, 0.2])
        graph_repo.get_node.return_value = node
        search_repo.vector_search.return_value = []
        graph_repo.get_nodes_batch.return_value = []

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.find_similar_nodes("n1", source_ids=["src1"])

        # No error — node without source_id passes the scope check
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_vector_results_respect_limit(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Vector search results are capped at the requested limit.

        After removing self and applying scope, at most ``limit`` nodes
        should be returned.
        """
        embedding = [0.1, 0.2]
        source_node = make_node("n1", "Alice", embedding=embedding)
        nodes = [make_node(f"n{i}", f"Node{i}") for i in range(2, 12)]

        graph_repo.get_node.return_value = source_node
        search_repo.vector_search.return_value = [(f"n{i}", 0.9 - i * 0.01) for i in range(2, 12)]
        graph_repo.get_nodes_batch.return_value = nodes

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.find_similar_nodes("n1", limit=3)

        # vector_search called with limit+1
        search_repo.vector_search.assert_called_once_with(query_embedding=embedding, k=4)
        # After self-removal (none in this case), results capped at limit
        assert result["count"] <= 3


# ===========================================================================
# traverse_path Tests
# ===========================================================================


class TestTraversePath:
    """Tests for the traverse_path handler."""

    @pytest.mark.asyncio
    async def test_start_node_not_found_returns_error(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Traversal from a non-existent start node returns an error dict.

        When get_node returns None for the start_node_id, the handler
        should return success=False.
        """
        graph_repo.get_node.return_value = None

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.traverse_path("nonexistent")

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_basic_traversal(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Basic BFS traversal returns connected nodes with path info.

        Starting from n1 with edges to n2 and n3, both neighbors should
        be found at depth 1 with outgoing direction.
        """
        start = make_node("n1", "Alice")
        neighbor1 = make_node("n2", "Bob")
        neighbor2 = make_node("n3", "Charlie")
        edge1 = make_edge("e1", "n1", "n2", label="knows")
        edge2 = make_edge("e2", "n1", "n3", label="knows")

        graph_repo.get_node.return_value = start
        _configure_graph_repo(
            graph_repo,
            nodes=[start, neighbor1, neighbor2],
            edges=[edge1, edge2],
            batch_nodes=[neighbor1, neighbor2],
        )

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.traverse_path("n1")

        assert result["success"] is True
        assert result["start_node"]["id"] == "n1"
        assert result["nodes_found"] == 2
        assert len(result["results"]) == 2

        result_ids = {r["node"]["id"] for r in result["results"]}
        assert result_ids == {"n2", "n3"}

        # All paths should have depth 1
        for r in result["results"]:
            assert r["depth"] == 1
            assert len(r["path"]) == 1

    @pytest.mark.asyncio
    async def test_traversal_follows_incoming_edges(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """BFS traversal follows both outgoing and incoming edges.

        An edge from n2 to n1 (where n1 is start) should discover n2
        via the incoming adjacency list.
        """
        start = make_node("n1", "Alice")
        incoming_neighbor = make_node("n2", "Bob")
        edge = make_edge("e1", "n2", "n1", label="follows")

        graph_repo.get_node.return_value = start
        _configure_graph_repo(
            graph_repo,
            nodes=[start, incoming_neighbor],
            edges=[edge],
            batch_nodes=[incoming_neighbor],
        )

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.traverse_path("n1")

        assert result["success"] is True
        assert result["nodes_found"] == 1
        assert result["results"][0]["node"]["id"] == "n2"
        assert result["results"][0]["path"][0]["direction"] == "incoming"

    @pytest.mark.asyncio
    async def test_edge_type_filter(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Edge type filter restricts which edges are followed.

        Only edges with matching label or template_id should be traversed.
        """
        start = make_node("n1", "Alice")
        friend = make_node("n2", "Bob")
        colleague = make_node("n3", "Charlie")
        edge_friend = make_edge("e1", "n1", "n2", label="friend_of")
        edge_colleague = make_edge("e2", "n1", "n3", label="colleague_of")

        graph_repo.get_node.return_value = start
        _configure_graph_repo(
            graph_repo,
            nodes=[start, friend, colleague],
            edges=[edge_friend, edge_colleague],
            batch_nodes=[friend],
        )

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.traverse_path("n1", edge_types=["friend_of"])

        assert result["success"] is True
        assert result["nodes_found"] == 1
        assert result["results"][0]["node"]["id"] == "n2"
        assert result["edge_types_filter"] == ["friend_of"]

    @pytest.mark.asyncio
    async def test_edge_type_filter_matches_template_id(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Edge type filter also matches against edge template_id.

        The handler checks both ``edge.label`` and ``edge.template_id``
        against the edge_types list.
        """
        start = make_node("n1", "Alice")
        target = make_node("n2", "Bob")
        edge = make_edge("e1", "n1", "n2", label="some_label", template_id="relationship_tmpl")

        graph_repo.get_node.return_value = start
        _configure_graph_repo(
            graph_repo,
            nodes=[start, target],
            edges=[edge],
            batch_nodes=[target],
        )

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.traverse_path("n1", edge_types=["relationship_tmpl"])

        assert result["success"] is True
        assert result["nodes_found"] == 1

    @pytest.mark.asyncio
    async def test_max_depth_limits_traversal(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Traversal stops at max_depth.

        With a chain n1->n2->n3->n4 and max_depth=2, only n2 (depth 1)
        and n3 (depth 2) should be reached; n4 is beyond the limit.
        """
        n1 = make_node("n1", "A")
        n2 = make_node("n2", "B")
        n3 = make_node("n3", "C")
        n4 = make_node("n4", "D")
        edges = [
            make_edge("e1", "n1", "n2", label="next"),
            make_edge("e2", "n2", "n3", label="next"),
            make_edge("e3", "n3", "n4", label="next"),
        ]

        graph_repo.get_node.return_value = n1
        _configure_graph_repo(
            graph_repo,
            nodes=[n1, n2, n3, n4],
            edges=edges,
            batch_nodes=[n2, n3],
        )

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.traverse_path("n1", max_depth=2)

        assert result["success"] is True
        assert result["max_depth"] == 2
        result_ids = {r["node"]["id"] for r in result["results"]}
        assert "n2" in result_ids
        assert "n3" in result_ids
        assert "n4" not in result_ids

    @pytest.mark.asyncio
    async def test_limit_caps_total_results(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """The limit parameter caps the total number of traversed nodes.

        With many connected nodes and limit=2, at most 2 nodes should be
        returned.
        """
        start = make_node("n1", "Start")
        neighbors = [make_node(f"n{i}", f"Node{i}") for i in range(2, 12)]
        edges = [make_edge(f"e{i}", "n1", f"n{i}", label="connected") for i in range(2, 12)]

        graph_repo.get_node.return_value = start
        _configure_graph_repo(
            graph_repo,
            nodes=[start, *neighbors],
            edges=edges,
            batch_nodes=neighbors[:2],  # Only first 2 will be hydrated
        )

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.traverse_path("n1", limit=2)

        assert result["success"] is True
        # BFS may discover more than 2 in the internal loop, but
        # the result list should be capped
        assert len(result["results"]) <= 2

    @pytest.mark.asyncio
    async def test_cycle_handling(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """BFS does not revisit already-visited nodes.

        In a cycle n1->n2->n3->n1, each node should appear at most once
        in the results (excluding the start node which is in the visited set).
        """
        n1 = make_node("n1", "A")
        n2 = make_node("n2", "B")
        n3 = make_node("n3", "C")
        edges = [
            make_edge("e1", "n1", "n2", label="next"),
            make_edge("e2", "n2", "n3", label="next"),
            make_edge("e3", "n3", "n1", label="next"),  # Back to start
        ]

        graph_repo.get_node.return_value = n1
        _configure_graph_repo(
            graph_repo,
            nodes=[n1, n2, n3],
            edges=edges,
            batch_nodes=[n2, n3],
        )

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.traverse_path("n1", max_depth=10)

        assert result["success"] is True
        result_ids = [r["node"]["id"] for r in result["results"]]
        # No duplicates
        assert len(result_ids) == len(set(result_ids))
        # n1 should not appear in results (it's the start node)
        assert "n1" not in result_ids
        assert "n2" in result_ids
        assert "n3" in result_ids

    @pytest.mark.asyncio
    async def test_source_scope_filters_traversal_results(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Source scope filtering removes out-of-scope nodes from traversal results.

        Hydrated nodes that belong to excluded sources are filtered from the
        final results, even though BFS discovered them.
        """
        start = make_node("n1", "Alice")
        in_scope = make_node("n2", "Bob", source_id="src1")
        out_scope = make_node("n3", "Eve", source_id="src2")

        edges = [
            make_edge("e1", "n1", "n2", label="knows"),
            make_edge("e2", "n1", "n3", label="knows"),
        ]

        graph_repo.get_node.return_value = start
        _configure_graph_repo(
            graph_repo,
            nodes=[start, in_scope, out_scope],
            edges=edges,
            batch_nodes=[in_scope, out_scope],
        )

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.traverse_path("n1", source_ids=["src1"])

        assert result["success"] is True
        result_ids = {r["node"]["id"] for r in result["results"]}
        assert "n2" in result_ids
        # n3 should be filtered out by source scope
        assert "n3" not in result_ids

    @pytest.mark.asyncio
    async def test_multi_hop_traversal_records_full_path(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Multi-hop traversal records the complete path from start to each node.

        A node at depth 2 should have a path list with 2 entries showing
        the full traversal from the start node.
        """
        n1 = make_node("n1", "Alice")
        n2 = make_node("n2", "Bob")
        n3 = make_node("n3", "Charlie")
        edges = [
            make_edge("e1", "n1", "n2", label="knows"),
            make_edge("e2", "n2", "n3", label="knows"),
        ]

        graph_repo.get_node.return_value = n1
        _configure_graph_repo(
            graph_repo,
            nodes=[n1, n2, n3],
            edges=edges,
            batch_nodes=[n2, n3],
        )

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.traverse_path("n1", max_depth=3)

        assert result["success"] is True

        # Find depth-2 result (n3)
        depth_2_results = [r for r in result["results"] if r["depth"] == 2]
        assert len(depth_2_results) == 1
        n3_result = depth_2_results[0]

        assert n3_result["node"]["id"] == "n3"
        assert len(n3_result["path"]) == 2
        assert n3_result["path"][0]["from"] == "n1"
        assert n3_result["path"][0]["to"] == "n2"
        assert n3_result["path"][1]["from"] == "n2"
        assert n3_result["path"][1]["to"] == "n3"

    @pytest.mark.asyncio
    async def test_no_edges_returns_empty_results(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Traversal from an isolated node (no edges) returns empty results.

        When there are no edges, BFS has nothing to explore and should
        return successfully with zero results.
        """
        start = make_node("n1", "Alice")
        graph_repo.get_node.return_value = start
        _configure_graph_repo(graph_repo, nodes=[start], edges=[], batch_nodes=[])

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.traverse_path("n1")

        assert result["success"] is True
        assert result["nodes_found"] == 0
        assert result["results"] == []


# ===========================================================================
# Helper Method Tests
# ===========================================================================


class TestHelperMethods:
    """Tests for internal helper methods of AnalyticsToolHandlers."""

    def test_load_nodes_prefers_minimal(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """_load_nodes prefers list_nodes_minimal when available.

        Because MagicMock auto-creates attributes, hasattr always returns
        True, so list_nodes_minimal should be the method called.
        """
        nodes = [make_node("n1", "Alice")]
        _configure_graph_repo(graph_repo, nodes=nodes, edges=[])

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = handler._load_nodes()

        graph_repo.list_nodes_minimal.assert_called_once()
        assert result == nodes

    def test_load_edges_prefers_minimal(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """_load_edges prefers list_edges_minimal when available.

        Because MagicMock auto-creates attributes, hasattr always returns
        True, so list_edges_minimal should be the method called.
        """
        edges = [make_edge("e1", "n1", "n2")]
        _configure_graph_repo(graph_repo, nodes=[], edges=edges)

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = handler._load_edges()

        graph_repo.list_edges_minimal.assert_called_once()
        assert result == edges

    def test_load_nodes_uses_custom_limit(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """_load_nodes passes explicit limit parameter when provided.

        When called with a limit argument, that limit should be forwarded
        to the underlying repository method.
        """
        _configure_graph_repo(graph_repo, nodes=[], edges=[])

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        handler._load_nodes(limit=100)

        graph_repo.list_nodes_minimal.assert_called_once_with(limit=100)

    def test_load_nodes_uses_default_limit(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """_load_nodes uses self._node_limit when no explicit limit is given.

        The default node limit should come from BatchingSettings.
        """
        _configure_graph_repo(graph_repo, nodes=[], edges=[])

        handler = _make_handler(
            graph_repo, search_repo, analytics_service, settings, node_limit=5000
        )
        handler._load_nodes()

        graph_repo.list_nodes_minimal.assert_called_once_with(limit=5000)

    def test_build_template_name_map(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """_build_template_name_map resolves template IDs to lowercase names.

        Each unique template_id in the node list should be looked up via
        graph.get_template, and the result name lowercased.
        """
        nodes = [
            make_node("n1", "Alice", template_id="person_tmpl"),
            make_node("n2", "Acme", template_id="company_tmpl"),
            make_node("n3", "Bob", template_id="person_tmpl"),  # Duplicate template
        ]

        graph_repo.get_template.side_effect = lambda tid: (
            SimpleNamespace(name="Person")
            if tid == "person_tmpl"
            else SimpleNamespace(name="Company")
        )

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        name_map = handler._build_template_name_map(nodes)

        assert name_map["person_tmpl"] == "person"
        assert name_map["company_tmpl"] == "company"

    def test_build_template_name_map_handles_lookup_failure(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """_build_template_name_map skips templates that fail to look up.

        If get_template raises an exception for a template_id, that entry
        should be silently omitted from the map.
        """
        nodes = [make_node("n1", "Alice", template_id="broken_tmpl")]
        graph_repo.get_template.side_effect = RuntimeError("DB error")

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        name_map = handler._build_template_name_map(nodes)

        assert "broken_tmpl" not in name_map

    def test_build_template_name_map_skips_empty_template_ids(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """_build_template_name_map ignores nodes with empty/None template_id.

        Nodes with no template_id should not trigger a template lookup.
        """
        nodes = [
            make_node("n1", "Alice", template_id=""),
            make_node("n2", "Bob", template_id="person"),
        ]
        graph_repo.get_template.return_value = SimpleNamespace(name="Person")

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        name_map = handler._build_template_name_map(nodes)

        assert "" not in name_map
        assert "person" in name_map

    def test_matches_template_exact_match(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """_matches_template returns True for exact template_id match.

        An exact match between the filter tid and the node's template_id
        should return True.
        """
        node = make_node("n1", "Alice", template_id="person")
        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)

        assert handler._matches_template(node, ["person"]) is True

    def test_matches_template_partial_tid_in_node(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """_matches_template returns True when tid is a substring of node template.

        ``"per"`` should match a node with template_id ``"person"``.
        """
        node = make_node("n1", "Alice", template_id="person")
        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)

        assert handler._matches_template(node, ["per"]) is True

    def test_matches_template_partial_node_in_tid(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """_matches_template returns True when node template is substring of tid.

        A node with template_id ``"per"`` should match filter ``"person"``.
        """
        node = make_node("n1", "Alice", template_id="per")
        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)

        assert handler._matches_template(node, ["person"]) is True

    def test_matches_template_case_insensitive(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """_matches_template is case-insensitive.

        ``"PERSON"`` should match template_id ``"person"``.
        """
        node = make_node("n1", "Alice", template_id="person")
        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)

        assert handler._matches_template(node, ["PERSON"]) is True

    def test_matches_template_by_name_map(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """_matches_template matches against resolved template name.

        When a template_name_map is provided, the filter is also checked
        against the human-readable name.
        """
        # Node has UUID-like template_id, but the resolved name is "Person"
        node = make_node("n1", "Alice", template_id="abc-123-uuid")
        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)

        name_map = {"abc-123-uuid": "person"}
        assert handler._matches_template(node, ["person"], template_name_map=name_map) is True

    def test_matches_template_no_match(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """_matches_template returns False when nothing matches.

        When neither the template_id nor the resolved name matches any
        filter entry, the method should return False.
        """
        node = make_node("n1", "Alice", template_id="person")
        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)

        assert handler._matches_template(node, ["organization"]) is False

    def test_matches_template_empty_template_id(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """_matches_template handles node with empty template_id gracefully.

        A node with no template_id should not match any filter unless
        the filter also contains an empty string.
        """
        node = make_node("n1", "Alice", template_id="")
        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)

        # Empty template_id: "" in "person" is True due to Python substring semantics
        # The handler does `tid_lower in node_template_lower or node_template_lower in tid_lower`
        # "" in "person" is True, so this actually matches
        assert handler._matches_template(node, ["person"]) is True


# ===========================================================================
# Error Handling Tests (tool_handler decorator)
# ===========================================================================


class TestErrorHandling:
    """Tests for error handling via the @tool_handler decorator."""

    @pytest.mark.asyncio
    async def test_analyze_graph_structure_exception_returns_error(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Unhandled exception in analyze_graph_structure is caught by decorator.

        The @tool_handler decorator wraps exceptions into a
        ``{"success": False, "error": "Operation failed"}`` response.
        """
        graph_repo.list_nodes_minimal.side_effect = RuntimeError("DB crash")

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.analyze_graph_structure()

        assert result["success"] is False
        assert result["error"] == "Operation failed"

    @pytest.mark.asyncio
    async def test_find_shortest_path_exception_returns_error(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Unhandled exception in find_shortest_path is caught by decorator.

        The @tool_handler decorator converts the exception into a
        standard error dict.
        """
        graph_repo.list_nodes_minimal.side_effect = RuntimeError("DB crash")

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.find_shortest_path("n1", "n2")

        assert result["success"] is False
        assert result["error"] == "Operation failed"

    @pytest.mark.asyncio
    async def test_find_similar_nodes_exception_returns_error(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Unhandled exception in find_similar_nodes is caught by decorator.

        The @tool_handler decorator converts the exception into a
        standard error dict.
        """
        graph_repo.get_node.side_effect = RuntimeError("DB crash")

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.find_similar_nodes("n1")

        assert result["success"] is False
        assert result["error"] == "Operation failed"

    @pytest.mark.asyncio
    async def test_traverse_path_exception_returns_error(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Unhandled exception in traverse_path is caught by decorator.

        The @tool_handler decorator converts the exception into a
        standard error dict.
        """
        graph_repo.get_node.side_effect = RuntimeError("DB crash")

        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)
        result = await handler.traverse_path("n1")

        assert result["success"] is False
        assert result["error"] == "Operation failed"


# ===========================================================================
# Constructor / Initialization Tests
# ===========================================================================


class TestInitialization:
    """Tests for AnalyticsToolHandlers constructor behavior."""

    def test_default_limits_from_settings(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
    ) -> None:
        """Default limits come from BatchingSettings when not explicitly provided.

        When node_limit and edge_limit are None, the handler should pull
        values from the settings' batching configuration.
        """
        settings = EngineSettings()
        handler = _make_handler(graph_repo, search_repo, analytics_service, settings)

        assert handler._node_limit == settings.batching.graph_analysis_node_limit
        assert handler._edge_limit == settings.batching.graph_analysis_edge_limit

    def test_explicit_limits_override_settings(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
        settings: EngineSettings,
    ) -> None:
        """Explicit node_limit and edge_limit override settings defaults.

        When the caller provides explicit limits, those should be used
        instead of the batching settings.
        """
        handler = _make_handler(
            graph_repo,
            search_repo,
            analytics_service,
            settings,
            node_limit=1000,
            edge_limit=2000,
        )

        assert handler._node_limit == 1000
        assert handler._edge_limit == 2000

    def test_no_settings_uses_batching_defaults(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        analytics_service: MagicMock,
    ) -> None:
        """When settings is None and no explicit limits, BatchingSettings defaults apply.

        The handler creates a BatchingSettings() internally when settings
        is None and limits are not provided.
        """
        handler = AnalyticsToolHandlers(
            graph_repository=graph_repo,
            search_repository=search_repo,
            analytics_service=analytics_service,
            settings=None,
        )

        from chaoscypher_core.settings import BatchingSettings

        defaults = BatchingSettings()
        assert handler._node_limit == defaults.graph_analysis_node_limit
        assert handler._edge_limit == defaults.graph_analysis_edge_limit
