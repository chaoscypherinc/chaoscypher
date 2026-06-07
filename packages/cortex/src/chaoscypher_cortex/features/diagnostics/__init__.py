# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Diagnostics Feature.

Diagnostic ZIP bundle export for bug reports.

This is a documented **aggregator feature** under ADR-0002. It reaches
across slices into ``features/logs.service.LogService`` at the DI
composition root so the ZIP can include recent log tails without
duplicating log-tailing logic here. Any new cross-slice reach must be
added to ADR-0002's aggregator allow-list.

Exports:
    DiagnosticsService: Creates diagnostic bundles.
    DiagnosticExportResponse: Export response model.
    router: FastAPI router for diagnostics endpoints.
"""

from chaoscypher_cortex.features.diagnostics.api import router
from chaoscypher_cortex.features.diagnostics.models import DiagnosticExportResponse
from chaoscypher_cortex.features.diagnostics.service import DiagnosticsService


__all__ = [
    "DiagnosticExportResponse",
    "DiagnosticsService",
    "router",
]
