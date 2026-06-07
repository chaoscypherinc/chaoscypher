# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export Management - Package Generation and Export Operations.

Handles export package creation, README generation, and statistics calculation.

Example:
    from chaoscypher_core.services.export.management import ExportRepository

    repo = ExportRepository(graph_repository=graph_repo, settings=engine_settings)
    zip_buffer = repo.export_graph()

"""

from chaoscypher_core.services.export.management.service import ExportRepository


__all__ = ["ExportRepository"]
