# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Feature.

RDF-based knowledge graph operations and grounding API.

This feature provides foundational knowledge graph capabilities. Core graph
operations (nodes, edges, queries) are exposed through dedicated features.
Default templates are defined in ``chaoscypher_core.templates.default_templates``.

Components:
- GroundingService: Read-only graph query service for MCP integration
- grounding_router: Grounding API router

Architecture:
Core graph operations live in engine.repos.graph.GraphRepository and are
accessed through nodes/edges features. Default template definitions have been
moved to shared/ since they are used across multiple features and packages.

Example:
    from chaoscypher_core.templates.default_templates import get_all_default_templates

    # Initialize new database with default templates
    templates = get_all_default_templates()
    for template in templates:
        graph_repo.create_template(template)

"""

from chaoscypher_cortex.features.graph.grounding_api import router as grounding_router
from chaoscypher_cortex.features.graph.grounding_service import GroundingService


__all__ = [
    "GroundingService",
    "grounding_router",
]
