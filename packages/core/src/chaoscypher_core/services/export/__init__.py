# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export Module - Graph export to the CCX 3.0 format.

Provides export functionality for knowledge graphs to the CCX
(Chaos Cypher eXchange) 3.0 format via the ``ccx-format`` reference library.

Components:
- ccx_identity: stable CCX IRI minting (pure).
- ccx_mapping: pure domain-dict → CCX JSON-LD / RDF mapping.
- CcxExporter: main export orchestration (calls ccx-format PackageBuilder).
- Statistics calculators: module-level functions for the statistics graph.

Works in both backend and CLI modes.
"""

from chaoscypher_core.services.export import ccx_identity, ccx_mapping
from chaoscypher_core.services.export.engine.stats import (
    calculate_knowledge_stats,
    calculate_lens_stats,
    calculate_source_stats,
    calculate_template_stats,
    calculate_workflow_stats,
)
from chaoscypher_core.services.export.management.service import CcxExporter


__all__ = [
    "CcxExporter",
    "calculate_knowledge_stats",
    "calculate_lens_stats",
    "calculate_source_stats",
    "calculate_template_stats",
    "calculate_workflow_stats",
    "ccx_identity",
    "ccx_mapping",
]
