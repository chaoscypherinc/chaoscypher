# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Analytics Tool Handlers.

Handles graph analytics operations including structure analysis, pathfinding,
and similarity search.

Extracted from tool_executor.py for SRP compliance.
"""

from collections import defaultdict
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.graph.engine.analytics import GraphAnalyticsService
from chaoscypher_core.services.workflows.tools.engine.handlers.decorators import tool_handler
from chaoscypher_core.settings import BatchingSettings


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.search import SearchRepositoryProtocol
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


class AnalyticsToolHandlers:
    """Handles all graph analytics tool operations."""

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        search_repository: SearchRepositoryProtocol,
        analytics_service: GraphAnalyticsService,
        node_limit: int | None = None,
        edge_limit: int | None = None,
        settings: EngineSettings | None = None,
    ):
        """Initialize the instance.

        Args:
            graph_repository: Repository for graph operations.
            search_repository: Repository for search operations.
            analytics_service: Service for graph analytics operations.
            node_limit: Max nodes to load for analytics.
            edge_limit: Max edges to load for analytics.
            settings: Optional engine settings with batching configuration.

        """
        if node_limit is None:
            _batching = settings.batching if settings else BatchingSettings()
            node_limit = _batching.graph_analysis_node_limit
        if edge_limit is None:
            _batching = settings.batching if settings else BatchingSettings()
            edge_limit = _batching.graph_analysis_edge_limit
        self.graph = graph_repository
        self.search = search_repository
        self.analytics = analytics_service
        self._node_limit = node_limit
        self._edge_limit = edge_limit

    def _load_nodes(self, limit: int | None = None) -> list[Any]:
        """Load nodes using minimal method if available.

        Args:
            limit: Max nodes to load. Defaults to self._node_limit.

        Returns:
            List of node objects.

        """
        n = limit if limit is not None else self._node_limit
        if hasattr(self.graph, "list_nodes_minimal"):
            return list(self.graph.list_nodes_minimal(limit=n))
        return list(self.graph.list_nodes(limit=n))

    def _load_edges(self, limit: int | None = None) -> list[Any]:
        """Load edges using minimal method if available.

        Args:
            limit: Max edges to load. Defaults to self._edge_limit.

        Returns:
            List of edge objects.

        """
        n = limit if limit is not None else self._edge_limit
        if hasattr(self.graph, "list_edges_minimal"):
            return list(self.graph.list_edges_minimal(limit=n))
        return list(self.graph.list_edges(limit=n))

    @tool_handler("graph_analysis_failed")
    async def analyze_graph_structure(
        self,
        template_ids: list[str] | None = None,
        source_ids: list[str] | None = None,
    ) -> dict:
        """Analyze overall graph structure, optionally filtered by template types.

        Args:
            template_ids: Optional list of template IDs to filter analysis.
                         When provided, only nodes matching these templates
                         and edges between them are included in the analysis.
            source_ids: Optional source scope filter.

        Returns:
            Dict with graph statistics, communities, and top nodes by PageRank.

        """
        # Use optimized minimal methods if available (much faster for large graphs)
        # These load only essential fields, excluding large embedding/properties data
        # Limit of 1.5M fits in 4GB: ~730MB nodes + ~2.7GB edges = ~3.4GB total
        nodes = self._load_nodes()
        total_loaded = len(nodes)

        # Filter nodes by template_ids if provided
        if template_ids:
            # Build template name lookup so human-readable names match UUID-based IDs
            template_name_map = self._build_template_name_map(nodes)
            nodes = [n for n in nodes if self._matches_template(n, template_ids, template_name_map)]
            logger.info(
                "graph_analysis_template_filter",
                total_loaded=total_loaded,
                after_template_filter=len(nodes),
                template_ids=template_ids,
            )

        # Filter nodes by source scope
        if source_ids:
            pre_filter = len(nodes)
            nodes = [
                n for n in nodes if not getattr(n, "source_id", None) or n.source_id in source_ids
            ]
            logger.info(
                "graph_analysis_source_filter",
                before_source_filter=pre_filter,
                after_source_filter=len(nodes),
                source_ids=source_ids,
                sample_node_source_ids=[
                    getattr(n, "source_id", None) for n in self._load_nodes()[:5]
                ]
                if pre_filter > 0 and len(nodes) == 0
                else [],
            )

        edges = self._load_edges()

        # Filter edges to only those between filtered nodes
        if template_ids or source_ids:
            node_ids = {n.id for n in nodes}
            edges = [
                e for e in edges if e.source_node_id in node_ids and e.target_node_id in node_ids
            ]

        # Basic statistics
        node_count = len(nodes)
        edge_count = len(edges)

        # Community detection
        communities = self.analytics.detect_communities(nodes, edges)

        # PageRank
        pagerank = self.analytics.calculate_pagerank(nodes, edges)

        # Clustering coefficient
        clustering = self.analytics.calculate_clustering_coefficient(nodes, edges)

        # Degree distribution
        degrees = GraphAnalyticsService.calculate_node_degrees_simple(edges)

        # Isolated nodes
        isolated = GraphAnalyticsService.find_isolated_nodes_simple(nodes, edges)

        # Limit community output to prevent massive responses for large graphs
        # Only include summary stats and sample members from top communities
        sorted_communities = sorted(
            communities["communities"],
            key=lambda c: c["size"],
            reverse=True,
        )
        community_summaries = [
            {
                "id": comm["id"],
                "size": comm["size"],
                "sample_members": comm["members"][:5],  # Only first 5 members
            }
            for comm in sorted_communities[:10]  # Top 10 communities by size
        ]

        result = {
            "success": True,
            "statistics": {
                "node_count": node_count,
                "edge_count": edge_count,
                "average_degree": sum(degrees.values()) / len(degrees) if degrees else 0,
                "isolated_nodes": len(isolated),
                "num_communities": communities["num_communities"],
                "average_clustering": clustering["average_clustering"],
            },
            "communities": community_summaries,
            "top_nodes": pagerank["top_nodes"][:10],
            "isolated_nodes": isolated[:20],
        }

        # Add filter info if templates were specified
        if template_ids:
            result["template_filter"] = template_ids

        return result

    def _build_template_name_map(self, nodes: list[Any]) -> dict[str, str]:
        """Build a mapping of template_id to template name for name-based matching.

        Args:
            nodes: List of node objects to extract unique template IDs from.

        Returns:
            Dict mapping template_id to lowercase template name.

        """
        template_name_map: dict[str, str] = {}
        unique_template_ids = {getattr(n, "template_id", "") for n in nodes}
        for tid in unique_template_ids:
            if not tid:
                continue
            try:
                template = self.graph.get_template(tid)
                if template:
                    name = template.name
                    if name:
                        template_name_map[tid] = name.lower()
            except Exception:
                logger.debug("template_lookup_failed", template_id=tid)
        return template_name_map

    def _matches_template(
        self,
        node: Any,
        template_ids: list[str],
        template_name_map: dict[str, str] | None = None,
    ) -> bool:
        """Check if node matches any of the template IDs or template names.

        Performs case-insensitive partial matching against both template ID
        and resolved template name to handle UUID-based template IDs.

        Args:
            node: Node object with template_id attribute.
            template_ids: List of template IDs or names to match against.
            template_name_map: Optional mapping of template_id to template name.

        Returns:
            True if node matches any of the template IDs.

        """
        node_template = getattr(node, "template_id", "") or ""
        node_template_lower = node_template.lower()
        node_template_name = template_name_map.get(node_template, "") if template_name_map else ""

        for tid in template_ids:
            tid_lower = tid.lower()
            # Match against template ID
            if tid_lower in node_template_lower or node_template_lower in tid_lower:
                return True
            # Match against resolved template name
            if node_template_name and (
                tid_lower in node_template_name or node_template_name in tid_lower
            ):
                return True
        return False

    @tool_handler("find_shortest_path_failed")
    async def find_shortest_path(
        self,
        source_id: str,
        target_id: str,
        source_ids: list[str] | None = None,
    ) -> dict:
        """Find shortest path between two nodes.

        Args:
            source_id: Starting node ID.
            target_id: Target node ID.
            source_ids: Optional source scope filter.

        Returns:
            Dict with shortest path results.

        """
        nodes = self._load_nodes()

        if source_ids:
            nodes = [
                n for n in nodes if not getattr(n, "source_id", None) or n.source_id in source_ids
            ]

        edges = self._load_edges()

        if source_ids:
            node_ids = {n.id for n in nodes}
            edges = [
                e for e in edges if e.source_node_id in node_ids and e.target_node_id in node_ids
            ]

        return self.analytics.find_shortest_path(nodes, edges, source_id, target_id)

    @tool_handler("find_similar_nodes_failed")
    async def find_similar_nodes(
        self,
        node_id: str,
        limit: int = 10,
        source_ids: list[str] | None = None,
    ) -> dict:
        """Find nodes similar to the given node.

        Args:
            node_id: ID of the node to find similar nodes for.
            limit: Maximum number of similar nodes to return.
            source_ids: Optional source scope filter.

        Returns:
            Dict with similar nodes and similarity scores.

        """
        # Get the source node
        node = self.graph.get_node(node_id)
        if not node:
            return {"success": False, "error": f"Node not found: {node_id}"}

        # Check scope on the source node itself
        if source_ids:
            node_source = getattr(node, "source_id", None)
            if node_source and node_source not in source_ids:
                return {
                    "success": False,
                    "error": f"Node '{node.label}' is not accessible in the current source scope",
                }

        # Use vector search if node has embedding
        if node.embedding:
            results_list = self.search.vector_search(
                query_embedding=node.embedding,
                k=limit + 1,  # +1 to exclude self
            )

            # Remove the source node from results
            results = [(nid, score) for nid, score in results_list if nid != node_id][:limit]

            # Fetch nodes
            node_ids = [nid for nid, _ in results]
            nodes = self.graph.get_nodes_batch(node_ids)

            # Filter by source scope
            if source_ids:
                nodes = [
                    n
                    for n in nodes
                    if not getattr(n, "source_id", None) or n.source_id in source_ids
                ]
            nodes_dict = {n.id: n for n in nodes}

            similar = [
                {
                    "id": nid,
                    "label": nodes_dict[nid].label,
                    "similarity": score,
                    "template_id": nodes_dict[nid].template_id,
                }
                for nid, score in results
                if nid in nodes_dict
            ]
        else:
            # Fallback: Find nodes with same template (uses minimal - only needs id, label, template_id)
            all_nodes = self._load_nodes()

            # Filter by source scope
            if source_ids:
                all_nodes = [
                    n
                    for n in all_nodes
                    if not getattr(n, "source_id", None) or n.source_id in source_ids
                ]

            similar = [
                {
                    "id": n.id,
                    "label": n.label,
                    "similarity": 0.5,  # Arbitrary similarity for same template
                    "template_id": n.template_id,
                }
                for n in all_nodes
                if n.template_id == node.template_id and n.id != node_id
            ][:limit]

        return {
            "success": True,
            "count": len(similar),
            "similar_nodes": similar,
            "source_node": {"id": node.id, "label": node.label},
        }

    @tool_handler("traverse_path_failed")
    async def traverse_path(  # noqa: C901
        self,
        start_node_id: str,
        edge_types: list[str] | None = None,
        max_depth: int = 2,
        limit: int = 50,
        source_ids: list[str] | None = None,
    ) -> dict:
        """Traverse the graph from a starting node following specified edge types.

        Use this for multi-hop queries like "children of X", "spouse's siblings", etc.

        Args:
            start_node_id: The ID of the node to start traversal from
            edge_types: List of edge labels/templates to follow (e.g., ["spouse_of", "sibling_of"])
                       If None, follows all edge types
            max_depth: Maximum depth of traversal (default 2)
            limit: Maximum total nodes to return (default 50)
            source_ids: Optional source scope filter

        Returns:
            Dict with traversal results including paths

        """
        # Get the starting node
        start_node = self.graph.get_node(start_node_id)
        if not start_node:
            return {"success": False, "error": f"Start node not found: {start_node_id}"}

        # BFS traversal
        visited: set[str] = {start_node_id}
        results: list[dict] = []
        current_level: list[tuple[str, list[dict[str, str]], int]] = [
            (start_node_id, [], 0)
        ]  # (node_id, path, depth)

        # Get all edges once for efficiency (minimal - only needs source/target/label)
        all_edges = self._load_edges()

        # Build adjacency lists for both directions
        outgoing: dict[str, list] = defaultdict(list)
        incoming: dict[str, list] = defaultdict(list)
        for edge in all_edges:
            # Filter by edge type if specified
            if edge_types and edge.label not in edge_types and edge.template_id not in edge_types:
                continue

            outgoing[edge.source_node_id].append(edge)
            incoming[edge.target_node_id].append(edge)

        while current_level and len(results) < limit:
            next_level = []

            for current_id, path, depth in current_level:
                if depth >= max_depth:
                    continue

                # Check outgoing edges
                for edge in outgoing.get(current_id, []):
                    target_id = edge.target_node_id
                    if target_id not in visited:
                        visited.add(target_id)
                        new_path = [
                            *path,
                            {
                                "from": current_id,
                                "edge": edge.label,
                                "to": target_id,
                                "direction": "outgoing",
                            },
                        ]
                        results.append(
                            {
                                "node_id": target_id,
                                "path": new_path,
                                "depth": depth + 1,
                            }
                        )
                        if len(results) < limit:
                            next_level.append((target_id, new_path, depth + 1))

                # Check incoming edges
                for edge in incoming.get(current_id, []):
                    source_id = edge.source_node_id
                    if source_id not in visited:
                        visited.add(source_id)
                        new_path = [
                            *path,
                            {
                                "from": current_id,
                                "edge": edge.label,
                                "to": source_id,
                                "direction": "incoming",
                            },
                        ]
                        results.append(
                            {
                                "node_id": source_id,
                                "path": new_path,
                                "depth": depth + 1,
                            }
                        )
                        if len(results) < limit:
                            next_level.append((source_id, new_path, depth + 1))

            current_level = next_level

        # Hydrate nodes
        result_node_ids = [r["node_id"] for r in results]
        nodes = self.graph.get_nodes_batch(result_node_ids)
        # Filter by source scope
        if source_ids:
            nodes = [
                n for n in nodes if not getattr(n, "source_id", None) or n.source_id in source_ids
            ]
        nodes_dict = {n.id: n for n in nodes}

        # Build final results
        traversal_results = []
        for r in results:
            node = nodes_dict.get(r["node_id"])
            if node:
                traversal_results.append(
                    {
                        "node": {
                            "id": node.id,
                            "label": node.label,
                            "template_id": node.template_id,
                        },
                        "path": r["path"],
                        "depth": r["depth"],
                    }
                )

        return {
            "success": True,
            "start_node": {"id": start_node.id, "label": start_node.label},
            "edge_types_filter": edge_types,
            "max_depth": max_depth,
            "nodes_found": len(traversal_results),
            "results": traversal_results,
        }
