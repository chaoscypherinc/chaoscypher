# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Dashboard feature — aggregates summary data from sibling slices.

See ADR-0002 for the rationale on dashboard being the documented VSA
exception.
"""

from chaoscypher_cortex.features.dashboard.api import get_dashboard_service, router
from chaoscypher_cortex.features.dashboard.models import DashboardResponse
from chaoscypher_cortex.features.dashboard.service import DashboardService


__all__ = [
    "DashboardResponse",
    "DashboardService",
    "get_dashboard_service",
    "router",
]
