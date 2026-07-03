# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Import Service - CCX 3.0 package import functionality.

Provides the ``CcxImporter`` service and its option/stats models for
importing CCX 3.0 packages into Chaos Cypher knowledge graphs. The importer
consumes ``ccx-format`` (``ccx.open_package``) and persists every entity by
its stable CCX IRI (upsert-by-IRI), so re-importing the same bytes is
idempotent.

Submodules:
    - service: The ``CcxImporter`` orchestrator.
    - models: ``ImportOptions`` / ``ImportStats``.
    - ccx_import_mapping: Pure CCX-dict → domain-DTO mapping.

Example:
    from chaoscypher_core.services.package.importer import (
        CcxImporter,
        ImportOptions,
        ImportStats,
    )

    importer = CcxImporter(
        graph_repository=graph_repo,
        sources_repository=sources_repo,
    )
    stats = await importer.import_from_bytes(
        data,
        options=ImportOptions(database_name="default"),
    )
    if stats.errors:
        for error in stats.errors:
            print(f"Error: {error}")
    else:
        print(f"Imported {stats.total_items} items")
"""

from chaoscypher_core.services.package.importer.models import (
    ImportOptions,
    ImportStats,
)
from chaoscypher_core.services.package.importer.service import CcxImporter


__all__ = [
    "CcxImporter",
    "ImportOptions",
    "ImportStats",
]
