# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Logs Feature.

Container log viewing and service status monitoring.

Exports:
    LogService: Reads log files and queries service status.
    LogResponse: Log lines response model.
    ServiceStatus: Individual service status model.
    ServiceStatusResponse: All services status response.
    router: FastAPI router for log endpoints.
"""

from chaoscypher_cortex.features.logs.api import router
from chaoscypher_cortex.features.logs.models import (
    LogResponse,
    ServiceStatus,
    ServiceStatusResponse,
)
from chaoscypher_cortex.features.logs.service import LogService


__all__ = [
    "LogResponse",
    "LogService",
    "ServiceStatus",
    "ServiceStatusResponse",
    "router",
]
