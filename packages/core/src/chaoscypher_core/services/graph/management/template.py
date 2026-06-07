# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template Service for chaoscypher-engine.

Business logic for template operations - thin wrapper around GraphRepository.
"""

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.services.graph.engine.validator import TemplateValidator


if TYPE_CHECKING:
    from chaoscypher_core.models import TemplateCreate, TemplateUpdate
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol

logger = structlog.get_logger(__name__)


class TemplateService:
    """Service for template business logic.

    Thin wrapper around GraphRepository that provides validation
    and standardized error handling for template operations.
    """

    def __init__(self, graph_repository: GraphRepositoryProtocol):
        """Initialize template service.

        Args:
            graph_repository: GraphRepository implementation

        """
        self.graph_repository = graph_repository

    def list_templates(
        self,
        template_type: str | None = None,
        page: int = 1,
        page_size: int = 50,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        """List templates with pagination.

        Args:
            template_type: Filter by type (node/edge, optional)
            page: Page number (1-indexed)
            page_size: Items per page
            source_id: Filter by source ID (optional)

        Returns:
            Dict with keys:
                - data: List of template dicts
                - pagination: Pagination metadata

        """
        # SQL-level pagination — avoids loading all templates into memory
        skip = (page - 1) * page_size
        # list_templates (below) hides templates from disabled sources by
        # default, so the count must match or pagination over-reports.
        total = self.graph_repository.count_templates(
            template_type=template_type,
            source_id=source_id,
            include_disabled_sources=False,
        )
        paginated = self.graph_repository.list_templates(
            template_type=template_type, source_id=source_id, skip=skip, limit=page_size
        )

        total_pages = (total + page_size - 1) // page_size if total > 0 else 1

        # Get usage counts for paginated templates only
        template_ids = [t.id for t in paginated]
        usage_counts = self.graph_repository.get_template_usage_counts(template_ids)

        return {
            "data": [
                {
                    "id": t.id,
                    "name": t.name,
                    "description": t.description,
                    "template_type": t.template_type,
                    "properties": [p.model_dump(mode="json") for p in t.properties],
                    "is_system": t.is_system,
                    "icon": t.icon,
                    "color": t.color,
                    "source_id": t.source_id,
                    "node_count": usage_counts.get(t.id, {}).get("nodes", 0),
                    "edge_count": usage_counts.get(t.id, {}).get("edges", 0),
                    "created_at": t.created_at,
                    "updated_at": t.updated_at,
                }
                for t in paginated
            ],
            "pagination": {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
            },
        }

    def get_template(self, template_id: str) -> dict[str, Any]:
        """Get template by ID.

        Args:
            template_id: Template ID

        Returns:
            Template dictionary

        Raises:
            NotFoundError: If template not found

        """
        template = self.graph_repository.get_template(template_id)
        if not template:
            msg = "Template"
            raise NotFoundError(msg, template_id)

        return {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "template_type": template.template_type,
            "properties": [p.model_dump(mode="json") for p in template.properties],
            "icon": template.icon,
            "color": template.color,
            "is_system": template.is_system,
            "created_at": template.created_at,
            "updated_at": template.updated_at,
        }

    def create_template(self, template_create: TemplateCreate) -> dict[str, Any]:
        """Create new template.

        Args:
            template_create: Template creation data

        Returns:
            Created template dictionary

        Raises:
            ValidationError: If name uses system prefix

        """
        # Validate name doesn't use system prefix
        TemplateValidator.validate_not_system_prefix(template_create.name)

        template = self.graph_repository.create_template(template_create)
        return {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "template_type": template.template_type,
            "properties": [p.model_dump(mode="json") for p in template.properties],
            "icon": template.icon,
            "color": template.color,
            "is_system": template.is_system,
            "created_at": template.created_at,
            "updated_at": template.updated_at,
        }

    def update_template(self, template_id: str, template_update: TemplateUpdate) -> dict[str, Any]:
        """Update template.

        Args:
            template_id: Template ID
            template_update: Template update data

        Returns:
            Updated template dictionary

        Raises:
            NotFoundError: If template not found
            ValidationError: If name uses system prefix

        """
        # Validate name if being updated
        if template_update.name:
            TemplateValidator.validate_not_system_prefix(template_update.name)

        template = self.graph_repository.update_template(template_id, template_update)
        if not template:
            msg = "Template"
            raise NotFoundError(msg, template_id)

        return {
            "id": template.id,
            "name": template.name,
            "description": template.description,
            "template_type": template.template_type,
            "properties": [p.model_dump(mode="json") for p in template.properties],
            "icon": template.icon,
            "color": template.color,
            "is_system": template.is_system,
            "created_at": template.created_at,
            "updated_at": template.updated_at,
        }

    def delete_template(self, template_id: str, force: bool = False) -> None:
        """Delete template.

        Args:
            template_id: Template ID
            force: Force delete even if in use

        Raises:
            NotFoundError: If template not found

        """
        success = self.graph_repository.delete_template(template_id, force=force)
        if not success:
            msg = "Template"
            raise NotFoundError(msg, template_id)
