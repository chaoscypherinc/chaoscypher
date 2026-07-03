# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Import Models - Data models for CCX package import operations.

Defines import options and statistics for the CCX 3.0 import service.

Example:
    from chaoscypher_core.services.package.importer.models import (
        ImportOptions,
        ImportStats,
    )

    options = ImportOptions(database_name="default")
    stats = ImportStats()
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
        checksum_verified: Whether package integrity was verified. For CCX
            3.0 this is set from ``ccx-format``'s fail-closed
            ``validate().ok`` rather than per-file SHA checks.
        conformance_classes: The CCX conformance classes the package
            declared (e.g. ``("core", "sources")``), recorded from the
            validation report.
        package_version: The imported package's ``package_version`` (from
            the CCX manifest), for conflict-policy provenance.
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
    conformance_classes: list[str] = field(default_factory=list)
    package_version: str | None = None
    embeddings_need_regeneration: bool = False
    embedding_mismatch_reason: str | None = None
    # Count of node + chunk vectors restored from the package's embedding sidecar
    # (model matched the import side, so the index op skips re-embedding).
    embeddings_restored: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Local ids of the sources created/updated this import. The worker import
    # handler enqueues one OP_INDEX_IMPORTED_SOURCE per id to re-embed chunks
    # and push node/chunk vectors (imported sources arrive unsearchable).
    imported_source_ids: list[str] = field(default_factory=list)
    # Local ids of the KNOWLEDGE nodes created/updated this import (lens
    # app-graph nodes excluded). Source-bearing imports index these via their
    # source; knowledge-only imports (lexicon, CLI) have no source, so the index
    # step works off this explicit id list. See OP_INDEX_IMPORTED_NODES.
    imported_node_ids: list[str] = field(default_factory=list)

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
            "conformance_classes": self.conformance_classes,
            "package_version": self.package_version,
            "embeddings_need_regeneration": self.embeddings_need_regeneration,
            "embedding_mismatch_reason": self.embedding_mismatch_reason,
            "embeddings_restored": self.embeddings_restored,
            "warnings": self.warnings,
            "errors": self.errors,
            "imported_source_ids": self.imported_source_ids,
            "imported_node_ids": self.imported_node_ids,
            "is_success": self.is_success,
            "total_items": self.total_items,
        }


__all__ = [
    "ImportOptions",
    "ImportStats",
]
