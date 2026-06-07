# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export Models.

Data structures for CCX (Chaos Cypher eXchange) format.

Structure:
- schemas.py: External schemas for export/import (ExportManifest, ImportResult, statistics)

Example:
    from chaoscypher_core.services.export.models.schemas import ExportManifest, ImportResult

    manifest = ExportManifest(
        ccx_version="2.0",
        package_type=["knowledge", "templates"],
        name="my-package",
        package_version="v1.0.0",
        generator="chaoscypher@0.1.0",
        database_name="my_db",
        stats=GraphStats(total_nodes=0, total_edges=0, total_sources=0),
    )

"""

from chaoscypher_core.services.export.models.schemas import (
    # Statistics Models
    ChunkingConfig,
    # Export/Import Models
    ContentFile,
    DateRange,
    EmbeddingStats,
    ExportManifest,
    ImportResult,
    KnowledgeStats,
    LensStats,
    SourceStats,
    TemplateStats,
    WorkflowStats,
)


__all__ = [
    # Statistics
    "ChunkingConfig",
    # Export/Import
    "ContentFile",
    "DateRange",
    "EmbeddingStats",
    "ExportManifest",
    "ImportResult",
    "KnowledgeStats",
    "LensStats",
    "SourceStats",
    "TemplateStats",
    "WorkflowStats",
]
