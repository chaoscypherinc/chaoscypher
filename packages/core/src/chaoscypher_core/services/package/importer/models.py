# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Import Models - Data models for CCX package import operations.

Defines import options, statistics, and ID mapping for the import service.

Example:
    from chaoscypher_core.services.package.importer.models import (
        ImportOptions,
        ImportStats,
        IdMapper,
    )

    options = ImportOptions(verify_checksums=True)
    stats = ImportStats()
    mapper = IdMapper()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ImportOptions:
    """Options for import operation.

    Attributes:
        verify_checksums: Whether to verify SHA-256/512 checksums.
        skip_existing_templates: If True, reuse a local template when a CCX
            template shares the same name. Defaults to False — CCX imports
            are self-contained and always mint fresh templates.
        import_templates: Whether to import templates.
        import_knowledge: Whether to import knowledge graph (nodes + edges).
        import_workflows: Whether to import workflows.
        import_sources: Whether to import sources (chunks, citations, tags).
        database_name: Target database name for import.
    """

    verify_checksums: bool = True
    skip_existing_templates: bool = False
    import_templates: bool = True
    import_knowledge: bool = True
    import_workflows: bool = True
    import_sources: bool = True
    database_name: str = "default"


@dataclass
class ImportStats:
    """Statistics from an import operation.

    Attributes:
        templates_imported: Number of templates imported.
        templates_skipped: Number of templates skipped (already exist).
        nodes_imported: Number of nodes imported.
        edges_imported: Number of edges imported.
        workflows_imported: Number of workflow nodes imported.
        workflow_edges_imported: Number of workflow edges imported.
        triggers_imported: Number of triggers imported.
        sources_imported: Number of sources imported.
        chunks_imported: Number of document chunks imported.
        citations_imported: Number of citations imported.
        checksum_verified: Whether checksums were verified.
        embeddings_need_regeneration: Whether imported items need re-embedding.
        embedding_mismatch_reason: Reason embeddings need regeneration.
        warnings: List of warning messages.
        errors: List of error messages.
    """

    templates_imported: int = 0
    templates_skipped: int = 0
    nodes_imported: int = 0
    edges_imported: int = 0
    workflows_imported: int = 0
    workflow_edges_imported: int = 0
    triggers_imported: int = 0
    sources_imported: int = 0
    chunks_imported: int = 0
    citations_imported: int = 0
    checksum_verified: bool = False
    embeddings_need_regeneration: bool = False
    embedding_mismatch_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        """Check if import completed without errors."""
        return len(self.errors) == 0

    @property
    def total_items(self) -> int:
        """Get total number of items imported."""
        return (
            self.templates_imported
            + self.nodes_imported
            + self.edges_imported
            + self.workflows_imported
            + self.workflow_edges_imported
            + self.triggers_imported
            + self.sources_imported
            + self.chunks_imported
            + self.citations_imported
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "templates_imported": self.templates_imported,
            "templates_skipped": self.templates_skipped,
            "nodes_imported": self.nodes_imported,
            "edges_imported": self.edges_imported,
            "workflows_imported": self.workflows_imported,
            "workflow_edges_imported": self.workflow_edges_imported,
            "triggers_imported": self.triggers_imported,
            "sources_imported": self.sources_imported,
            "chunks_imported": self.chunks_imported,
            "citations_imported": self.citations_imported,
            "checksum_verified": self.checksum_verified,
            "embeddings_need_regeneration": self.embeddings_need_regeneration,
            "embedding_mismatch_reason": self.embedding_mismatch_reason,
            "warnings": self.warnings,
            "errors": self.errors,
            "is_success": self.is_success,
            "total_items": self.total_items,
        }


@dataclass
class IdMapper:
    """Tracks original→new ID mappings during import.

    When importing, we generate new IDs for all entities. This mapper
    tracks the original ID from the export to the new generated ID,
    allowing edges to be remapped to reference the correct nodes.

    Attributes:
        template_map: Maps template name → new template ID.
        node_map: Maps original node ID → new node ID.
        source_map: Maps original source ID → new source ID.
        chunk_map: Maps original chunk ID → new chunk ID.
    """

    template_map: dict[str, str] = field(default_factory=dict)
    node_map: dict[str, str] = field(default_factory=dict)
    source_map: dict[str, str] = field(default_factory=dict)
    chunk_map: dict[str, str] = field(default_factory=dict)

    def map_template(self, name: str, new_id: str) -> None:
        """Register a template mapping."""
        self.template_map[name] = new_id

    def map_node(self, original_id: str, new_id: str) -> None:
        """Register a node mapping."""
        self.node_map[original_id] = new_id

    def map_source(self, original_id: str, new_id: str) -> None:
        """Register a source mapping."""
        self.source_map[original_id] = new_id

    def map_chunk(self, original_id: str, new_id: str) -> None:
        """Register a chunk mapping."""
        self.chunk_map[original_id] = new_id

    def get_template_id(self, name: str) -> str | None:
        """Get the new ID for a template by name."""
        return self.template_map.get(name)

    def get_node_id(self, original_id: str) -> str | None:
        """Get the new ID for a node."""
        return self.node_map.get(original_id)

    def get_source_id(self, original_id: str) -> str | None:
        """Get the new ID for a source."""
        return self.source_map.get(original_id)

    def get_chunk_id(self, original_id: str) -> str | None:
        """Get the new ID for a chunk."""
        return self.chunk_map.get(original_id)

    def remap_edge(self, edge_data: dict[str, Any]) -> dict[str, Any]:
        """Remap edge source and target node IDs.

        Args:
            edge_data: Edge data dictionary with source_node_id and target_node_id.

        Returns:
            Edge data with remapped IDs.

        Raises:
            KeyError: If source or target node ID not found in mapping.
        """
        source_id = edge_data.get("source_node_id")
        target_id = edge_data.get("target_node_id")

        if source_id and source_id not in self.node_map:
            msg = f"Source node not found in mapping: {source_id}"
            raise KeyError(msg)

        if target_id and target_id not in self.node_map:
            msg = f"Target node not found in mapping: {target_id}"
            raise KeyError(msg)

        return {
            **edge_data,
            "source_node_id": self.node_map.get(source_id) if source_id else None,
            "target_node_id": self.node_map.get(target_id) if target_id else None,
        }

    def remap_citation(self, citation_data: dict[str, Any]) -> dict[str, Any]:
        """Remap citation chunk_id reference.

        Args:
            citation_data: Citation data dictionary with chunk_id.

        Returns:
            Citation data with remapped chunk_id.
        """
        chunk_id = citation_data.get("chunk_id")

        return {
            **citation_data,
            "chunk_id": self.chunk_map.get(chunk_id) if chunk_id else None,
        }


__all__ = [
    "IdMapper",
    "ImportOptions",
    "ImportStats",
]
