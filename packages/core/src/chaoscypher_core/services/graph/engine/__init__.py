# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Engine - Analytics, Validation, and Graph Algorithms.

Engine-layer services for graph analytics, validation, and algorithmic operations.

Components:
- GraphAnalyticsService: Graph analytics and metrics
- CountsService: Resource counting operations
- TemplateValidator: Template and property validation
- PropertyValidator: Property value validation
- Graph algorithms: Traversal, statistics, algorithms

Example:
    from chaoscypher_core.services.graph.engine import GraphAnalyticsService, CountsService
    from chaoscypher_core.services.graph.engine.validator import TemplateValidator

    analytics = GraphAnalyticsService(graph_repo, database_name)
    stats = analytics.get_graph_statistics()

"""

from chaoscypher_core.exceptions import PropertyValidationError
from chaoscypher_core.services.graph.engine.analytics import GraphAnalyticsService
from chaoscypher_core.services.graph.engine.stats import CountsService
from chaoscypher_core.services.graph.engine.validator import (
    PropertyValidator,
    TemplateValidator,
)


__all__ = [
    "CountsService",
    "GraphAnalyticsService",
    "PropertyValidationError",
    "PropertyValidator",
    "TemplateValidator",
]
