# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Import Service - CCX package import functionality.

Provides the ImportService class and related models for importing
CCX packages into ChaosCypher knowledge graphs. Supports importing
templates, nodes, edges, sources, chunks, and citations.

Submodules:
    - service: Main ImportService orchestrator
    - models: ImportOptions, ImportStats, IdMapper
    - loaders: Content-type specific loaders

Example:
    from chaoscypher_core.services.package.importer import (
        ImportService,
        ImportOptions,
        ImportStats,
    )

    # Create service with dependencies
    service = ImportService(
        graph_repository=graph_repo,
        sources_repository=sources_repo,
    )

    # Import from bytes (e.g., downloaded from Lexicon)
    stats = await service.import_from_bytes(
        archive_data,
        options=ImportOptions(
            verify_checksums=True,
            import_sources=True,
        ),
    )

    # Check results
    if stats.is_success:
        print(f"Imported {stats.total_items} items")
    else:
        for error in stats.errors:
            print(f"Error: {error}")
"""

# Models
from chaoscypher_core.services.package.importer.models import (
    IdMapper,
    ImportOptions,
    ImportStats,
)

# Service
from chaoscypher_core.services.package.importer.service import ImportService


__all__ = [
    "IdMapper",
    "ImportOptions",
    "ImportService",
    "ImportStats",
]
