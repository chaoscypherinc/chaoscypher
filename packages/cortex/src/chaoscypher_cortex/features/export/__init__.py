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
- ExportService: CCX 3.0 export/import orchestration via Core operations.

Architecture:
VSA pattern: the feature service orchestrates export/import workflows by
queuing Core operations (which build CCX 3.0 packages via ccx-format).

Example:
    from chaoscypher_cortex.features.export import ExportService

    service = ExportService(export_operations)
    task_id = await service.queue_export_by_sources(source_ids=[...])

"""

from chaoscypher_cortex.features.export.service import ExportService


__all__ = [
    "ExportService",
]
