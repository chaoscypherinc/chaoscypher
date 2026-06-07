# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Counts Service for chaoscypher-engine.

Business logic for resource counting operations.
Provides framework-agnostic counting of knowledge graph resources.
"""

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.storage_sources import SourceStorageProtocol as SourcesProtocol


class CountsService:
    """Service for resource counts.

    Provides efficient counting of knowledge graph resources including:
    - Knowledge nodes (user-created entities)
    - Links (relationships/edges)
    - Templates (entity schemas)
    - Workflows (system workflow nodes)
    - Lenses (system lens nodes)
    - Sources (document sources)

    Uses optimized repository count methods to avoid loading full datasets.
    """

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        sources_repository: SourcesProtocol,
        database_name: str,
    ):
        """Initialize counts service.

        Args:
            graph_repository: GraphRepository instance for RDF operations
            sources_repository: SourcesProtocol implementation for source counting
            database_name: Name of the database to count sources for

        """
        self.graph_repository = graph_repository
        self.sources_repository = sources_repository
        self.database_name = database_name

    def get_counts(self, system_template_ids: list[str]) -> dict[str, Any]:
        """Get counts of all resources efficiently.

        **Performance Optimization:**
        - Uses count_*() methods instead of loading all data
        - Filters system templates and nodes
        - O(1) memory usage (no data loading)

        **Counts:**
        - knowledge_nodes: Non-system nodes (excludes workflows, lenses)
        - links: All edges
        - templates: User templates (excludes system templates)
        - workflows: Nodes with template_id='system_workflow'
        - lenses: Nodes with template_id='system_lens'
        - sources: Document sources (PDFs, text, CSV, etc.)

        Args:
            system_template_ids: List of system template IDs to exclude from knowledge nodes
                                 (e.g., ['system_workflow', 'system_workflow_step', 'system_lens'])

        Returns:
            Dictionary with resource counts:
                - knowledge_nodes: int
                - links: int
                - templates: int
                - workflows: int
                - lenses: int
                - sources: int

        Example:
            >>> service = CountsService(graph_repo, sources_repo)
            >>> counts = service.get_counts(['system_workflow', 'system_lens'])
            >>> counts
            {
                'knowledge_nodes': 1234,
                'links': 567,
                'templates': 12,
                'workflows': 5,
                'lenses': 3,
                'sources': 45
            }

        """
        # Use optimized filtered count methods (O(1) memory, no loading)
        knowledge_nodes_count = self.graph_repository.count_nodes_by_template(
            system_template_ids, exclude=True
        )
        workflows_count = self.graph_repository.count_nodes_by_template(["system_workflow"])
        lenses_count = self.graph_repository.count_nodes_by_template(["system_lens"])
        user_templates_count = self.graph_repository.count_templates_by_system(is_system=False)
        edge_count = self.graph_repository.count_edges()
        sources_count = self.sources_repository.count_sources(database_name=self.database_name)

        return {
            "knowledge_nodes": knowledge_nodes_count,
            "links": edge_count,
            "templates": user_templates_count,
            "workflows": workflows_count,
            "lenses": lenses_count,
            "sources": sources_count,
        }
