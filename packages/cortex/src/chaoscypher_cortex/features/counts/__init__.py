# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Counts Feature.

Aggregate resource counts and statistics.

This feature provides dashboard statistics including counts of nodes, edges,
templates, sources, workflows, and other resources. Aggregates data from multiple
repositories to provide a unified view of knowledge base size and composition.
Used by frontend dashboard for overview metrics and health monitoring.
Uses engine CountsService directly without wrapper layer for simplified architecture.

Components:
- CountsService: Aggregates counts from multiple repositories (uses engine directly)
- CountsResponse: Dashboard statistics response DTO
- router: FastAPI endpoints for /api/v1/counts

Architecture:
Simplified VSA - uses engine CountsService directly without wrapper layer.
Factory function in api.py provides dependency injection for all required repositories.

Example:
    from chaoscypher_core.services.graph.engine.stats import CountsService

    # Get dashboard statistics
    service = CountsService(graph_repo, sources_repo, "default")
    counts = service.get_counts(system_template_ids=["system_workflow"])

"""

from chaoscypher_core.services.graph.engine.stats import CountsService
from chaoscypher_cortex.features.counts.api import router
from chaoscypher_cortex.features.counts.models import CountsResponse


__all__ = [
    "CountsResponse",
    "CountsService",
    "router",
]
