# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for tools API handler logic.

Verifies that each handler calls the correct ToolService method with the
correct arguments and transforms the response correctly. FastAPI DI is
bypassed — the service mock is passed directly as a function argument.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from chaoscypher_cortex.features.tools.api import (
    create_user_tool,
    delete_user_tool,
    get_system_tool,
    get_tool_stats,
    get_user_tool,
    list_system_tools,
    list_user_tools,
    update_user_tool,
)
from chaoscypher_cortex.features.tools.models import (
    UserToolCreate,
    UserToolUpdate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _system_tool_dict(tool_id: str = "sys-1") -> dict:
    """Return a minimal SystemToolDict-compatible mapping."""
    return {
        "id": tool_id,
        "category": "search",
        "icon": None,
        "name": "Data Lookup",
        "description": "Look up structured data",
        "input_schema": {},
        "output_schema": {},
        "version": "1.0.0",
        "is_active": True,
        "created_at": _NOW,
        "updated_at": _NOW,
    }


def _user_tool_dict(tool_id: str = "usr-1") -> dict:
    """Return a minimal UserToolDict-compatible mapping."""
    return {
        "id": tool_id,
        "name": "My Search Tool",
        "description": "Custom search",
        "system_tool_id": "sys-1",
        "configuration": {"api_key": "secret"},
        "tags": None,
        "is_active": True,
        "created_by": None,
        "created_at": _NOW,
        "updated_at": _NOW,
        "system_tool": None,
    }


def _stats_dict(tool_type: str = "user", tool_id: str = "usr-1") -> dict:
    """Return a minimal stats dict."""
    return {
        "tool_type": tool_type,
        "tool_id": tool_id,
        "total_calls": 10,
        "successful_calls": 9,
        "failed_calls": 1,
        "avg_execution_ms": 250,
        "last_called_at": _NOW,
        "updated_at": _NOW,
    }


# ---------------------------------------------------------------------------
# TestListSystemTools
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListSystemTools:
    """Tests for the list_system_tools handler."""

    @pytest.mark.asyncio
    async def test_returns_all_system_tools(self) -> None:
        """Handler calls list_system_tools with no filters and returns the list."""
        mock_service = MagicMock()
        mock_service.list_system_tools.return_value = [
            _system_tool_dict("sys-1"),
            _system_tool_dict("sys-2"),
        ]

        result = await list_system_tools(
            _="test-user",
            tool_service=mock_service,
            category=None,
            is_active=None,
        )

        mock_service.list_system_tools.assert_called_once_with(category=None, is_active=None)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_passes_category_filter(self) -> None:
        """Handler forwards category filter to the service."""
        mock_service = MagicMock()
        mock_service.list_system_tools.return_value = [_system_tool_dict()]

        await list_system_tools(
            _="test-user",
            tool_service=mock_service,
            category="search",
            is_active=None,
        )

        mock_service.list_system_tools.assert_called_once_with(category="search", is_active=None)

    @pytest.mark.asyncio
    async def test_passes_is_active_filter(self) -> None:
        """Handler forwards is_active filter to the service."""
        mock_service = MagicMock()
        mock_service.list_system_tools.return_value = []

        await list_system_tools(
            _="test-user",
            tool_service=mock_service,
            category=None,
            is_active=True,
        )

        mock_service.list_system_tools.assert_called_once_with(category=None, is_active=True)

    @pytest.mark.asyncio
    async def test_returns_empty_list(self) -> None:
        """Handler returns empty list when service has no tools."""
        mock_service = MagicMock()
        mock_service.list_system_tools.return_value = []

        result = await list_system_tools(
            _="test-user",
            tool_service=mock_service,
            category=None,
            is_active=None,
        )

        assert result == []


# ---------------------------------------------------------------------------
# TestGetSystemTool
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSystemTool:
    """Tests for the get_system_tool handler."""

    @pytest.mark.asyncio
    async def test_returns_system_tool_dict(self) -> None:
        """Handler calls get_system_tool with the ID and returns the result."""
        mock_service = MagicMock()
        mock_service.get_system_tool.return_value = _system_tool_dict("sys-99")

        result = await get_system_tool(
            _="test-user",
            tool_id="sys-99",
            tool_service=mock_service,
        )

        mock_service.get_system_tool.assert_called_once_with("sys-99")
        assert result["id"] == "sys-99"

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self) -> None:
        """Handler raises HTTP 404 when service returns None."""
        mock_service = MagicMock()
        mock_service.get_system_tool.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_system_tool(
                _="test-user",
                tool_id="missing",
                tool_service=mock_service,
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestListUserTools
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListUserTools:
    """Tests for the list_user_tools handler."""

    @pytest.mark.asyncio
    async def test_returns_paginated_response(self) -> None:
        """Handler calls list_user_tools and wraps results in PaginatedUserToolsResponse."""
        mock_service = MagicMock()
        mock_service.list_user_tools.return_value = [
            _user_tool_dict("usr-1"),
            _user_tool_dict("usr-2"),
        ]

        result = await list_user_tools(
            tool_service=mock_service,
            pagination=(1, 50),
            _="test-user",
            system_tool_id=None,
            is_active=None,
        )

        mock_service.list_user_tools.assert_called_once_with(
            system_tool_id=None,
            is_active=None,
        )
        assert len(result.data) == 2
        assert result.data[0].id == "usr-1"
        assert result.data[1].id == "usr-2"
        assert result.pagination.total == 2

    @pytest.mark.asyncio
    async def test_passes_filters_to_service(self) -> None:
        """Handler forwards system_tool_id and is_active filters to the service."""
        mock_service = MagicMock()
        mock_service.list_user_tools.return_value = [_user_tool_dict()]

        await list_user_tools(
            tool_service=mock_service,
            pagination=(1, 50),
            _="test-user",
            system_tool_id="sys-1",
            is_active=True,
        )

        mock_service.list_user_tools.assert_called_once_with(
            system_tool_id="sys-1",
            is_active=True,
        )

    @pytest.mark.asyncio
    async def test_pagination_slices_results(self) -> None:
        """Handler slices the full list according to page/page_size params."""
        mock_service = MagicMock()
        items = [_user_tool_dict(f"usr-{i}") for i in range(5)]
        mock_service.list_user_tools.return_value = items

        result = await list_user_tools(
            tool_service=mock_service,
            pagination=(2, 2),
            _="test-user",
            system_tool_id=None,
            is_active=None,
        )

        assert len(result.data) == 2
        assert result.data[0].id == "usr-2"
        assert result.pagination.page == 2

    @pytest.mark.asyncio
    async def test_returns_empty_response(self) -> None:
        """Handler returns empty paginated response when no tools exist."""
        mock_service = MagicMock()
        mock_service.list_user_tools.return_value = []

        result = await list_user_tools(
            tool_service=mock_service,
            pagination=(1, 50),
            _="test-user",
            system_tool_id=None,
            is_active=None,
        )

        assert result.data == []
        assert result.pagination.total == 0


# ---------------------------------------------------------------------------
# TestCreateUserTool
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateUserTool:
    """Tests for the create_user_tool handler."""

    @pytest.mark.asyncio
    async def test_creates_and_returns_user_tool(self) -> None:
        """Handler calls create_user_tool then get_user_tool and returns the result."""
        mock_service = MagicMock()
        mock_service.create_user_tool.return_value = "usr-new"
        mock_service.get_user_tool.return_value = _user_tool_dict("usr-new")

        tool_create = UserToolCreate(
            name="My Tool",
            system_tool_id="sys-1",
            configuration={"api_key": "key123"},
        )

        result = await create_user_tool(
            tool_create=tool_create,
            tool_service=mock_service,
            _="test-user",
        )

        mock_service.create_user_tool.assert_called_once()
        call_kwargs = mock_service.create_user_tool.call_args[0][0]
        assert call_kwargs["name"] == "My Tool"
        assert call_kwargs["system_tool_id"] == "sys-1"

        mock_service.get_user_tool.assert_called_once_with("usr-new")
        assert result["id"] == "usr-new"

    @pytest.mark.asyncio
    async def test_raises_500_when_get_returns_none(self) -> None:
        """Handler raises HTTP 500 when get_user_tool returns None after creation."""
        mock_service = MagicMock()
        mock_service.create_user_tool.return_value = "usr-new"
        mock_service.get_user_tool.return_value = None

        tool_create = UserToolCreate(
            name="My Tool",
            system_tool_id="sys-1",
            configuration={},
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_user_tool(
                tool_create=tool_create,
                tool_service=mock_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 500
        # ``detail`` is the structured error envelope dict
        # (see shared/api/errors.py) — assert against its ``message`` key.
        assert "Failed to create tool" in exc_info.value.detail["message"]

    @pytest.mark.asyncio
    async def test_passes_full_model_dump_to_service(self) -> None:
        """create_user_tool passes model_dump() (all fields) to the service."""
        mock_service = MagicMock()
        mock_service.create_user_tool.return_value = "usr-1"
        mock_service.get_user_tool.return_value = _user_tool_dict()

        tool_create = UserToolCreate(
            name="Tool",
            description="A desc",
            system_tool_id="sys-1",
            configuration={"k": "v"},
            tags=["tag1"],
            is_active=False,
        )

        await create_user_tool(
            tool_create=tool_create,
            tool_service=mock_service,
            _="test-user",
        )

        payload = mock_service.create_user_tool.call_args[0][0]
        assert payload["description"] == "A desc"
        assert payload["tags"] == ["tag1"]
        assert payload["is_active"] is False


# ---------------------------------------------------------------------------
# TestGetUserTool
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetUserTool:
    """Tests for the get_user_tool handler."""

    @pytest.mark.asyncio
    async def test_returns_user_tool_dict(self) -> None:
        """Handler calls get_user_tool with the ID and returns the result."""
        mock_service = MagicMock()
        mock_service.get_user_tool.return_value = _user_tool_dict("usr-42")

        result = await get_user_tool(
            tool_id="usr-42",
            tool_service=mock_service,
            _="test-user",
        )

        mock_service.get_user_tool.assert_called_once_with("usr-42")
        assert result["id"] == "usr-42"

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self) -> None:
        """Handler raises HTTP 404 when service returns None."""
        mock_service = MagicMock()
        mock_service.get_user_tool.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_user_tool(
                tool_id="missing",
                tool_service=mock_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestUpdateUserTool
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateUserTool:
    """Tests for the update_user_tool handler."""

    @pytest.mark.asyncio
    async def test_updates_and_returns_tool(self) -> None:
        """Handler calls update_user_tool then get_user_tool and returns the fresh state."""
        mock_service = MagicMock()
        updated = _user_tool_dict("usr-5")
        updated["name"] = "Renamed Tool"
        mock_service.update_user_tool.return_value = True
        mock_service.get_user_tool.return_value = updated

        tool_update = UserToolUpdate(name="Renamed Tool")

        result = await update_user_tool(
            tool_id="usr-5",
            tool_update=tool_update,
            tool_service=mock_service,
            _="test-user",
        )

        mock_service.update_user_tool.assert_called_once_with("usr-5", {"name": "Renamed Tool"})
        mock_service.get_user_tool.assert_called_once_with("usr-5")
        assert result["name"] == "Renamed Tool"

    @pytest.mark.asyncio
    async def test_raises_404_when_update_returns_falsy(self) -> None:
        """Handler raises HTTP 404 when update_user_tool returns False (not found)."""
        mock_service = MagicMock()
        mock_service.update_user_tool.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            await update_user_tool(
                tool_id="missing",
                tool_update=UserToolUpdate(name="x"),
                tool_service=mock_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_404_when_get_after_update_returns_none(self) -> None:
        """Handler raises HTTP 404 when get_user_tool returns None after update."""
        mock_service = MagicMock()
        mock_service.update_user_tool.return_value = True
        mock_service.get_user_tool.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await update_user_tool(
                tool_id="usr-5",
                tool_update=UserToolUpdate(name="x"),
                tool_service=mock_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_excludes_unset_fields_from_update_payload(self) -> None:
        """update_user_tool calls model_dump(exclude_unset=True) so only set fields are sent."""
        mock_service = MagicMock()
        mock_service.update_user_tool.return_value = True
        mock_service.get_user_tool.return_value = _user_tool_dict()

        tool_update = UserToolUpdate(is_active=False)

        await update_user_tool(
            tool_id="usr-1",
            tool_update=tool_update,
            tool_service=mock_service,
            _="test-user",
        )

        payload = mock_service.update_user_tool.call_args[0][1]
        assert payload == {"is_active": False}
        assert "name" not in payload


# ---------------------------------------------------------------------------
# TestDeleteUserTool
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteUserTool:
    """Tests for the delete_user_tool handler."""

    @pytest.mark.asyncio
    async def test_calls_delete_and_returns_none(self) -> None:
        """Handler delegates to delete_user_tool and returns None (204)."""
        mock_service = MagicMock()
        mock_service.delete_user_tool.return_value = True

        result = await delete_user_tool(
            tool_id="usr-del",
            tool_service=mock_service,
            _="test-user",
        )

        mock_service.delete_user_tool.assert_called_once_with("usr-del")
        assert result is None

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self) -> None:
        """Handler raises HTTP 404 when delete_user_tool returns False (not found)."""
        mock_service = MagicMock()
        mock_service.delete_user_tool.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            await delete_user_tool(
                tool_id="missing",
                tool_service=mock_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestGetToolStats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetToolStats:
    """Tests for the get_tool_stats handler."""

    @pytest.mark.asyncio
    async def test_returns_stats_for_user_tool(self) -> None:
        """Handler calls get_tool_stats with tool_type and tool_id and returns the result."""
        mock_service = MagicMock()
        mock_service.get_tool_stats.return_value = _stats_dict("user", "usr-1")

        result = await get_tool_stats(
            _="test-user",
            tool_type="user",
            tool_id="usr-1",
            tool_service=mock_service,
        )

        mock_service.get_tool_stats.assert_called_once_with("user", "usr-1")
        assert result["tool_type"] == "user"
        assert result["tool_id"] == "usr-1"
        assert result["total_calls"] == 10

    @pytest.mark.asyncio
    async def test_returns_stats_for_system_tool(self) -> None:
        """Handler works for system tool type as well."""
        mock_service = MagicMock()
        mock_service.get_tool_stats.return_value = _stats_dict("system", "sys-1")

        result = await get_tool_stats(
            _="test-user",
            tool_type="system",
            tool_id="sys-1",
            tool_service=mock_service,
        )

        mock_service.get_tool_stats.assert_called_once_with("system", "sys-1")
        assert result["tool_type"] == "system"

    @pytest.mark.asyncio
    async def test_raises_404_when_stats_not_found(self) -> None:
        """Handler raises HTTP 404 when service returns None."""
        mock_service = MagicMock()
        mock_service.get_tool_stats.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_tool_stats(
                _="test-user",
                tool_type="user",
                tool_id="missing",
                tool_service=mock_service,
            )

        assert exc_info.value.status_code == 404
