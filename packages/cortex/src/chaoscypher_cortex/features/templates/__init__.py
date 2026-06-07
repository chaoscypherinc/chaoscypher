# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Templates Feature.

Entity template management with CRUD operations.

This feature provides template definitions for structured entity types in the
knowledge graph. Templates define entity schemas including property types,
required fields, validation rules, and relationship patterns. Used by import
extraction pipeline for entity recognition and by UI for consistent data entry.
Uses engine TemplateService directly without wrapper layer for simplified architecture.

Components:
- TemplateService: Business logic for template operations (uses engine directly)
- TemplateResponse: Pydantic response DTOs for API serialization
- router: FastAPI endpoints for /api/v1/templates

Architecture:
Simplified VSA - uses engine TemplateService directly without wrapper layer.
Factory function in api.py provides dependency injection with GraphRepository.

Example:
    from chaoscypher_core.services.graph.management.template import TemplateService
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository

    # Define and use entity templates
    graph_repository = GraphRepository(session=session, database_name="default")
    service = TemplateService(graph_repository=graph_repository)
    template = service.create_template(template_create)

"""

from chaoscypher_core.services.graph.management.template import TemplateService
from chaoscypher_cortex.features.templates.api import router
from chaoscypher_cortex.features.templates.models import TemplateResponse


__all__ = [
    "TemplateResponse",
    "TemplateService",
    "router",
]
