# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export Feature.

Knowledge graph export and import in CCX format.

This feature provides portable knowledge graph export/import capabilities using
the CCX (Chaos Cypher eXchange) format. Exports complete graph structures
including nodes, edges, templates, and metadata for backup, sharing, or migration.
Supports selective export by template type and merge strategies for import.
Enables knowledge base portability across Chaos Cypher instances.

Components:
- ExportService: CCX export/import orchestration and format conversion
- ExportRepository: Graph data extraction and CCX format serialization

Architecture:
VSA pattern with engine repository handling CCX format operations and backend
service orchestrating export/import workflows. Repository extracts graph data
and serializes to CCX JSON. Service handles file I/O, validation, and merge
logic. Factory function provides dependency injection.

Example:
    from chaoscypher_cortex.features.export import ExportService

    # Export and import knowledge graph
    service = ExportService(export_repo, graph_repo)
    export_path = service.export_graph(templates=["Person", "Organization"])
    service.import_graph(export_path, merge_strategy="update")

"""

from chaoscypher_core.services.export.management import ExportRepository
from chaoscypher_cortex.features.export.service import ExportService


__all__ = [
    "ExportRepository",
    "ExportService",
]
