# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Vector Search Plugin - Semantic/Hybrid Search.

Performs semantic search using vector embeddings with hybrid fallback.
Filters results by template and similarity threshold.

Extracted from executors/vector_operations.py and converted to plugin architecture.
"""

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import OperationError


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.plugins import ToolExecutionContext

logger = structlog.get_logger(__name__)


class SearchPlugin:
    """Vector Search tool plugin.

    Execute AI-powered semantic/hybrid search using embeddings. Combines
    vector similarity with keyword fallback for robust results.
    """

    @property
    def tool_id(self) -> str:
        """Stub implementation - not yet implemented."""
        return "ai.vector_search"

    @property
    def category(self) -> str:
        """Stub implementation - not yet implemented."""
        return "ai"

    @property
    def icon(self) -> str:
        """MUI icon name for UI display."""
        return "TravelExplore"

    @property
    def name(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Vector Search"

    @property
    def description(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Semantic search using vector embeddings with hybrid fallback"

    @property
    def input_schema(self) -> dict[str, Any]:
        """Stub implementation - not yet implemented."""
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query text"},
                "template_id": {"type": "string", "description": "Filter by template ID"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum results to return",
                    "default": 10,
                },
                "threshold": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "Minimum similarity score",
                    "default": 0.7,
                },
            },
            "required": ["query"],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        """Output schema for Vector Search tool."""
        return {
            "type": "object",
            "properties": {
                "nodes": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Matching nodes sorted by similarity",
                },
                "similarities": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Similarity scores for each node (0-1)",
                },
            },
            "required": ["nodes", "similarities"],
        }

    async def execute(
        self, inputs: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any]:
        """Execute semantic/hybrid search.

        Args:
            inputs: Tool inputs (query, template_id, limit, threshold)
            context: Execution context with graph manager

        Returns:
            Dictionary with matching nodes and similarity scores

        Raises:
            OperationError: If search manager not available

        """
        query = inputs["query"]
        template_id = inputs.get("template_id")
        limit = inputs.get("limit", 10)
        threshold = inputs.get("threshold", 0.7)

        graph_manager = context.graph_manager

        # Stub plugin: search_manager and llm_service live on the bootstrap
        # bag, not on GraphRepositoryProtocol. The real wiring will inject a
        # search service alongside graph_manager — until then we go through
        # the dynamic attrs and silence mypy locally.
        search_manager = getattr(graph_manager, "search_manager", None)
        if not search_manager:
            raise OperationError(
                "Search manager not available",
                operation="ai.vector_search",
            )

        llm_service = getattr(graph_manager, "llm_service", None)
        # Use hybrid search (semantic with keyword fallback)
        search_results = await search_manager.hybrid_search(
            query,
            k=limit * 2,  # Get more results for filtering
            llm_service=llm_service,
            settings=llm_service.settings if llm_service is not None else None,
        )

        # Fetch nodes and apply filters
        node_ids = [node_id for node_id, _ in search_results]
        nodes_list = graph_manager.get_nodes_batch(node_ids)
        nodes_dict = {n.id: n for n in nodes_list}

        results = []
        for node_id, score in search_results:
            node = nodes_dict.get(node_id)
            if not node:
                continue

            # Filter by template if specified
            if template_id and node.template_id != template_id:
                continue

            # Filter by threshold
            if score < threshold:
                continue

            results.append({"node": node.model_dump(mode="json"), "similarity": score})

            # Stop once we have enough results
            if len(results) >= limit:
                break

        return {
            "nodes": [r["node"] for r in results],
            "similarities": [r["similarity"] for r in results],
        }


__all__ = ["SearchPlugin"]
