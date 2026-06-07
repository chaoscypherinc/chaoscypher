# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool Storage Protocol Mixin for SqliteAdapter."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import load_only
from sqlmodel import delete, select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import SystemTool, ToolStatistics, UserTool
from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.ports.storage_tools import ToolStorageProtocol


class ToolsMixin(SqliteMixinBase, ToolStorageProtocol):
    """Mixin implementing ToolStorageProtocol for SQLite storage.

    Implements CRUD operations for:
    - System tools
    - User tools
    - Tool statistics
    """

    def list_system_tools(
        self, category: str | None = None, is_active: bool | None = None
    ) -> list[dict[str, Any]]:
        """List all system tools with optional filters."""
        self._ensure_connected()
        stmt = select(SystemTool).options(
            load_only(
                SystemTool.id,
                SystemTool.category,
                SystemTool.name,
                SystemTool.description,
                SystemTool.version,
                SystemTool.is_active,
                SystemTool.created_at,
                SystemTool.updated_at,
            )
        )

        if category is not None:
            stmt = stmt.where(SystemTool.category == category)
        if is_active is not None:
            stmt = stmt.where(SystemTool.is_active == is_active)

        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    def get_system_tool(self, tool_id: str) -> dict[str, Any] | None:
        """Get system tool by ID."""
        self._ensure_connected()
        tool = self.session.get(SystemTool, tool_id)
        return self._entity_to_dict(tool) if tool else None

    def create_system_tool(self, tool_data: dict[str, Any]) -> dict[str, Any]:
        """Create or update system tool."""
        self._ensure_connected()
        tool = SystemTool(**tool_data)
        self.session.add(tool)
        self._maybe_commit()
        self.session.refresh(tool)
        return self._entity_to_dict(tool)

    def update_system_tool(self, tool_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update system tool."""
        self._ensure_connected()
        tool = self.session.get(SystemTool, tool_id)
        if not tool:
            msg = "SystemTool"
            raise NotFoundError(msg, tool_id)

        for key, value in updates.items():
            setattr(tool, key, value)

        tool.updated_at = datetime.now(UTC)
        self.session.add(tool)
        self._maybe_commit()
        self.session.refresh(tool)
        return self._entity_to_dict(tool)

    def list_user_tools(
        self,
        database_name: str,
        user_id: int | None = None,
        system_tool_id: str | None = None,
        is_active: bool | None = None,
    ) -> list[dict[str, Any]]:
        """List user tools for database with optional filters."""
        self._ensure_connected()
        stmt = (
            select(UserTool)
            .options(
                load_only(
                    UserTool.id,
                    UserTool.database_name,
                    UserTool.user_id,
                    UserTool.name,
                    UserTool.description,
                    UserTool.system_tool_id,
                    UserTool.tags,
                    UserTool.is_active,
                    UserTool.created_by,
                    UserTool.created_at,
                    UserTool.updated_at,
                )
            )
            .where(UserTool.database_name == database_name)
        )

        if user_id is not None:
            stmt = stmt.where(UserTool.user_id == user_id)
        if system_tool_id is not None:
            stmt = stmt.where(UserTool.system_tool_id == system_tool_id)
        if is_active is not None:
            stmt = stmt.where(UserTool.is_active == is_active)

        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    def get_user_tool(self, tool_id: str, database_name: str) -> dict[str, Any] | None:
        """Get user tool by ID and database."""
        self._ensure_connected()
        tool = self.session.get(UserTool, tool_id)
        if tool and tool.database_name == database_name:
            return self._entity_to_dict(tool)
        return None

    def create_user_tool(self, tool_data: dict[str, Any]) -> dict[str, Any]:
        """Create user tool."""
        self._ensure_connected()
        tool = UserTool(**tool_data)
        self.session.add(tool)
        self._maybe_commit()
        self.session.refresh(tool)
        return self._entity_to_dict(tool)

    def update_user_tool(self, tool_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update user tool."""
        self._ensure_connected()
        tool = self.session.get(UserTool, tool_id)
        if not tool:
            msg = "UserTool"
            raise NotFoundError(msg, tool_id)

        for key, value in updates.items():
            setattr(tool, key, value)

        tool.updated_at = datetime.now(UTC)
        self.session.add(tool)
        self._maybe_commit()
        self.session.refresh(tool)
        return self._entity_to_dict(tool)

    def delete_user_tool(self, tool_id: str) -> bool:
        """Delete user tool."""
        self._ensure_connected()
        tool = self.session.get(UserTool, tool_id)
        if not tool:
            return False

        self.session.delete(tool)
        self._maybe_commit()
        return True

    def get_tool_statistics(self, tool_type: str, tool_id: str) -> dict[str, Any] | None:
        """Get statistics for a tool."""
        self._ensure_connected()
        stmt = select(ToolStatistics).where(
            ToolStatistics.tool_type == tool_type, ToolStatistics.tool_id == tool_id
        )
        result = self.session.exec(stmt)
        stats = result.first()
        return self._entity_to_dict(stats) if stats else None

    def create_tool_statistics(self, stats_data: dict[str, Any]) -> dict[str, Any]:
        """Create tool statistics."""
        self._ensure_connected()
        stats = ToolStatistics(**stats_data)
        self.session.add(stats)
        self._maybe_commit()
        self.session.refresh(stats)
        return self._entity_to_dict(stats)

    def update_tool_statistics(
        self, tool_type: str, tool_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Update tool statistics."""
        self._ensure_connected()
        stmt = select(ToolStatistics).where(
            ToolStatistics.tool_type == tool_type, ToolStatistics.tool_id == tool_id
        )
        result = self.session.exec(stmt)
        stats = result.first()

        if not stats:
            msg = "ToolStatistics"
            raise NotFoundError(msg, f"{tool_type}:{tool_id}")

        for key, value in updates.items():
            setattr(stats, key, value)

        stats.updated_at = datetime.now(UTC)
        self.session.add(stats)
        self._maybe_commit()
        self.session.refresh(stats)
        return self._entity_to_dict(stats)

    def list_tool_statistics(self) -> list[dict[str, Any]]:
        """List all tool statistics."""
        self._ensure_connected()
        stmt = select(ToolStatistics).options(
            load_only(
                ToolStatistics.tool_type,
                ToolStatistics.tool_id,
                ToolStatistics.total_calls,
                ToolStatistics.successful_calls,
                ToolStatistics.failed_calls,
                ToolStatistics.avg_execution_ms,
                ToolStatistics.last_called_at,
                ToolStatistics.updated_at,
            )
        )
        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 9).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def count_system_tools(self) -> int:
        """Count every SystemTool row."""
        self._ensure_connected()
        stmt = select(func.count()).select_from(SystemTool)
        return int(self.session.exec(stmt).one())

    def clear_all_system_tools(self) -> int:
        """Delete every SystemTool row."""
        self._ensure_connected()
        result = self.session.exec(delete(SystemTool))
        self._maybe_commit()
        return int(result.rowcount or 0)

    def clear_all_tool_statistics(self) -> int:
        """Delete every ToolStatistics row."""
        self._ensure_connected()
        result = self.session.exec(delete(ToolStatistics))
        self._maybe_commit()
        return int(result.rowcount or 0)

    def count_user_tools(self, *, database_name: str) -> int:
        """Count UserTool rows in one database."""
        self._ensure_connected()
        stmt = (
            select(func.count())
            .select_from(UserTool)
            .where(UserTool.database_name == database_name)
        )
        return int(self.session.exec(stmt).one())

    def delete_all_user_tools(self, *, database_name: str) -> int:
        """Delete every UserTool row in one database."""
        self._ensure_connected()
        stmt = delete(UserTool).where(UserTool.database_name == database_name)
        result = self.session.exec(stmt)
        self._maybe_commit()
        return int(result.rowcount or 0)
