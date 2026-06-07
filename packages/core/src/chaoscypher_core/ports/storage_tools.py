# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ToolStorageProtocol — storage contract for system and user tools.

Split from the legacy ``ports/storage.py`` god file on 2026-04-23.
Implemented by ``chaoscypher_core.adapters.sqlite.mixins.tools.ToolsMixin``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from chaoscypher_core.ports.types import SystemToolDict, UserToolDict


@runtime_checkable
class ToolStorageProtocol(Protocol):
    """Storage protocol for tool operations.

    Handles CRUD for:
    - System tools (built-in tools)
    - User tools (user-configured tool instances)
    - Tool statistics
    """

    # System Tools
    def list_system_tools(
        self, category: str | None = None, is_active: bool | None = None
    ) -> list[SystemToolDict]:
        """List all system tools with optional filters."""
        ...

    def get_system_tool(self, tool_id: str) -> SystemToolDict | None:
        """Get system tool by ID."""
        ...

    def create_system_tool(self, tool: dict[str, Any]) -> SystemToolDict:
        """Create or update system tool."""
        ...

    def update_system_tool(self, tool_id: str, updates: dict[str, Any]) -> SystemToolDict:
        """Update system tool."""
        ...

    # User Tools
    def list_user_tools(
        self,
        database_name: str,
        user_id: int | None = None,
        system_tool_id: str | None = None,
        is_active: bool | None = None,
    ) -> list[UserToolDict]:
        """List user tools for database with optional filters."""
        ...

    def get_user_tool(self, tool_id: str, database_name: str) -> UserToolDict | None:
        """Get user tool by ID and database."""
        ...

    def create_user_tool(self, tool: dict[str, Any]) -> UserToolDict:
        """Create user tool."""
        ...

    def update_user_tool(self, tool_id: str, updates: dict[str, Any]) -> UserToolDict:
        """Update user tool."""
        ...

    def delete_user_tool(self, tool_id: str) -> bool:
        """Delete user tool."""
        ...

    # Tool Statistics
    def get_tool_statistics(self, tool_type: str, tool_id: str) -> dict[str, Any] | None:
        """Get statistics for a tool."""
        ...

    def create_tool_statistics(self, stats: dict[str, Any]) -> dict[str, Any]:
        """Create tool statistics."""
        ...

    def update_tool_statistics(
        self, tool_type: str, tool_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Update tool statistics."""
        ...

    def list_tool_statistics(self) -> list[dict[str, Any]]:
        """List all tool statistics."""
        ...

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 9).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def count_system_tools(self) -> int:
        """Count every SystemTool row. Returns non-negative int."""
        ...

    def clear_all_system_tools(self) -> int:
        """Delete every SystemTool row. Returns count."""
        ...

    def clear_all_tool_statistics(self) -> int:
        """Delete every ToolStatistics row. Returns count."""
        ...

    def count_user_tools(self, *, database_name: str) -> int:
        """Count UserTool rows in one database."""
        ...

    def delete_all_user_tools(self, *, database_name: str) -> int:
        """Delete every UserTool row in one database. Returns count."""
        ...
