# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool Service - Business logic layer for tool management.

Uses ToolStorageProtocol for backend-independent data access.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.ports.storage_tools import ToolStorageProtocol
    from chaoscypher_core.ports.types import SystemToolDict, UserToolDict

logger = structlog.get_logger(__name__)


class ToolService:
    """Service for managing system and user tools.

    Example:
        >>> from chaoscypher_core.services.workflows.tools.management.api import get_tool_service
        >>> from chaoscypher_core.adapters.sqlite import get_db_session
        >>> from chaoscypher_core.settings import EngineSettings
        >>>
        >>> # Get service instance via factory
        >>> settings = EngineSettings()
        >>> with get_db_session("my_database") as session:
        ...     settings = get_settings()
        ...     service = get_tool_service(session, settings)
        ...
        ...     # List system tools
        ...     system_tools = service.list_system_tools(category="ai", is_active=True)
        ...     print(len(system_tools))
        ...     5
        ...
        ...     # Create a user tool configuration
        ...     user_tool_id = service.create_user_tool({
        ...         "name": "My Custom Extractor",
        ...         "system_tool_id": "ai.extract_entities",
        ...         "configuration": {
        ...             "extraction_depth": "full",
        ...             "generate_embeddings": True
        ...         },
        ...         "tags": ["research", "extraction"]
        ...     })
        ...     print(user_tool_id)
        ...     "ut_abc123"
        ...
        ...     # Get tool statistics
        ...     stats = service.get_tool_stats("user", user_tool_id)
        ...     print(stats["total_calls"])
        ...     0

    """

    def __init__(self, storage: ToolStorageProtocol, database_name: str):
        """Initialize tool service.

        Args:
            storage: ToolStorageProtocol instance
            database_name: Database name for filtering

        """
        self.storage = storage
        self.database_name = database_name

    # ========================================================================
    # System Tools
    # ========================================================================

    def list_system_tools(
        self,
        category: str | None = None,
        is_active: bool | None = None,
    ) -> list[SystemToolDict]:
        """List system tools with optional filters.

        Args:
            category: Filter by category
            is_active: Filter by active flag

        Returns:
            List of system tool dictionaries

        """
        return self.storage.list_system_tools(category=category, is_active=is_active)

    def get_system_tool(self, tool_id: str) -> SystemToolDict | None:
        """Get system tool by ID.

        Args:
            tool_id: System tool ID

        Returns:
            System tool dictionary or None

        """
        return self.storage.get_system_tool(tool_id)

    # ========================================================================
    # User Tools
    # ========================================================================

    def list_user_tools(
        self,
        system_tool_id: str | None = None,
        is_active: bool | None = None,
    ) -> list[UserToolDict]:
        """List user tools with optional filters.

        Args:
            system_tool_id: Filter by system tool ID
            is_active: Filter by active flag

        Returns:
            List of user tool dictionaries

        """
        tools = self.storage.list_user_tools(
            database_name=self.database_name,
            user_id=None,
            system_tool_id=system_tool_id,
            is_active=is_active,
        )
        # Ensure arrays are never None (API contract)
        for tool in tools:
            if tool.get("tags") is None:
                tool["tags"] = []
        return tools

    def get_user_tool(self, tool_id: str) -> UserToolDict | None:
        """Get user tool by ID.

        Args:
            tool_id: User tool ID

        Returns:
            User tool dictionary or None

        """
        tool = self.storage.get_user_tool(tool_id, self.database_name)
        if tool and tool.get("tags") is None:
            tool["tags"] = []
        return tool

    def create_user_tool(self, tool_data: dict[str, Any]) -> str:
        """Create a new user tool.

        Args:
            tool_data: User tool data dictionary

        Returns:
            Created tool ID

        """
        tool_id = generate_id()

        tool_dict = {
            "id": tool_id,
            "database_name": self.database_name,
            "name": tool_data["name"],
            "description": tool_data.get("description"),
            "system_tool_id": tool_data["system_tool_id"],
            "configuration": tool_data["configuration"],
            "tags": tool_data.get("tags", []),
            "is_active": tool_data.get("is_active", True),
            "created_by": tool_data.get("created_by"),
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }

        created = self.storage.create_user_tool(tool_dict)

        logger.info(
            "user_tool_created",
            tool_id=created["id"],
            tool_name=created["name"],
            system_tool_id=created["system_tool_id"],
            database_name=created["database_name"],
        )
        return created["id"]

    def update_user_tool(self, tool_id: str, updates: dict[str, Any]) -> bool:
        """Update user tool.

        Args:
            tool_id: User tool ID
            updates: Dictionary of fields to update

        Returns:
            True if updated, False if not found

        """
        tool = self.storage.get_user_tool(tool_id, self.database_name)
        if not tool:
            return False

        # Prepare updates with timestamp
        update_dict = {
            k: v
            for k, v in updates.items()
            if k in ["name", "description", "configuration", "tags", "is_active"]
        }
        update_dict["updated_at"] = datetime.now(UTC)

        self.storage.update_user_tool(tool_id, update_dict)

        logger.info(
            "user_tool_updated",
            tool_id=tool_id,
            tool_name=tool.get("name"),
            updated_fields=list(updates.keys()),
        )
        return True

    def delete_user_tool(self, tool_id: str) -> bool:
        """Delete user tool.

        Args:
            tool_id: User tool ID

        Returns:
            True if deleted, False if not found

        """
        tool = self.storage.get_user_tool(tool_id, self.database_name)
        if not tool:
            return False

        self.storage.delete_user_tool(tool_id)

        logger.info("user_tool_deleted", tool_id=tool_id, tool_name=tool.get("name"))
        return True

    # ========================================================================
    # Tool Statistics
    # ========================================================================

    def get_tool_stats(self, tool_type: str, tool_id: str) -> dict[str, Any] | None:
        """Get tool execution stats.

        Args:
            tool_type: Type of tool ('system' or 'user')
            tool_id: Tool ID

        Returns:
            Stats dictionary or None

        """
        return self.storage.get_tool_statistics(tool_type, tool_id)
