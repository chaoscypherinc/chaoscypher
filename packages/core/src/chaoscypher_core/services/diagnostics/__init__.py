# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Diagnostic Collector Service.

Gathers system diagnostics for bug reports and troubleshooting.

Exports:
    DiagnosticCollector: Main collector class.
    DiagnosticReport: Complete diagnostic report model.
    SystemInfo: System version and platform info.
    DiagnosticDatabaseStats: Database statistics model.
"""

from chaoscypher_core.services.diagnostics.collector import DiagnosticCollector
from chaoscypher_core.services.diagnostics.models import (
    DiagnosticDatabaseStats,
    DiagnosticReport,
    SystemInfo,
)


__all__ = [
    "DiagnosticCollector",
    "DiagnosticDatabaseStats",
    "DiagnosticReport",
    "SystemInfo",
]
