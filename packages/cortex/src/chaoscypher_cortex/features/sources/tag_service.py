# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tag management service.

Handles all tag CRUD and source-tag assignment operations.
Extracted from SourceService for Single Responsibility compliance.
"""

from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.services.graph.management.source import (
        SourceService as EngineSourceService,
    )


logger = structlog.get_logger(__name__)


class TagService:
    """Service for tag CRUD and source-tag assignment operations.

    Delegates all business logic to the engine SourceService.
    """

    def __init__(
        self,
        engine_service: EngineSourceService,
        database_name: str,
    ):
        """Initialize tag service.

        Args:
            engine_service: Engine SourceService instance
            database_name: Database name for tag operations

        """
        self.engine_service = engine_service
        self.database_name = database_name

    # ================================
    # Tag CRUD Operations
    # ================================

    def get_tag(self, tag_id: str) -> dict[str, Any] | None:
        """Get a tag by ID."""
        return self.engine_service.get_tag(tag_id)

    def list_tags(self) -> list[dict[str, Any]]:
        """List all tags."""
        return self.engine_service.list_tags()

    def create_tag(
        self,
        name: str,
        color: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a new tag."""
        return self.engine_service.create_tag(
            name=name,
            color=color,
            description=description,
            database_name=self.database_name,
        )

    def update_tag(
        self,
        tag_id: str,
        name: str | None = None,
        color: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any] | None:
        """Update a tag."""
        return self.engine_service.update_tag(
            tag_id=tag_id, name=name, color=color, description=description
        )

    def delete_tag(self, tag_id: str) -> bool:
        """Delete a tag."""
        return self.engine_service.delete_tag(tag_id)

    # ================================
    # Tag Assignment Operations
    # ================================

    def assign_tag(self, source_id: str, tag_id: str) -> bool:
        """Assign a tag to a source."""
        self.engine_service.assign_tag(source_id, tag_id)
        return True

    def unassign_tag(self, source_id: str, tag_id: str) -> bool:
        """Unassign a tag from a source."""
        return self.engine_service.unassign_tag(source_id, tag_id)

    def get_source_tags(self, source_id: str) -> list[dict[str, Any]]:
        """Get all tags assigned to a source."""
        return self.engine_service.get_source_tags(source_id)
