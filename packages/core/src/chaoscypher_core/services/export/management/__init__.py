# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export Management - CCX 3.0 package generation.

Example:
    from chaoscypher_core.services.export.management import CcxExporter

    exporter = CcxExporter(
        graph_repository=graph_repo,
        sources_repository=adapter,
        settings=engine_settings,
    )
    data = exporter.export()
"""

from chaoscypher_core.services.export.management.service import CcxExporter


__all__ = ["CcxExporter"]
