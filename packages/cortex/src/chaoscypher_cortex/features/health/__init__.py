# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Health Feature.

Consolidated system health monitoring.

This is a documented **aggregator feature** under ADR-0002. The DI
factory in ``api.py`` reaches into ``features/search`` to construct a
``SearchHealthProbe`` so the ``/health`` endpoint can run a real
search-service probe. The cross-slice reach stays inside the factory
function — ``HealthService`` itself only sees the probe object. Any
new cross-slice reach must be added to ADR-0002's aggregator
allow-list.

Exports:
    HealthService: Aggregates health checks from all subsystems.
    HealthCheckResponse: Pydantic response model.
    HealthCheckItem: Individual health check result.
    router: FastAPI router for health endpoints.
"""

from chaoscypher_cortex.features.health.api import router
from chaoscypher_cortex.features.health.models import (
    HealthCheckItem,
    HealthCheckResponse,
)
from chaoscypher_cortex.features.health.service import HealthService


__all__ = [
    "HealthCheckItem",
    "HealthCheckResponse",
    "HealthService",
    "router",
]
