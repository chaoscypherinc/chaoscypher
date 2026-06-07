# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tools API Endpoints.

GET    /api/v1/tools/system - List system tools
GET    /api/v1/tools/system/{id} - Get system tool
GET    /api/v1/tools - List user tools
POST   /api/v1/tools - Create user tool
GET    /api/v1/tools/{id} - Get user tool
PATCH  /api/v1/tools/{id} - Update user tool
DELETE /api/v1/tools/{id} - Delete user tool
"""

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.factories import get_tool_service as _make_tool_service
from chaoscypher_cortex.features.tools.models import (
    PaginatedUserToolsResponse,
    SystemToolResponse,
    SystemToolSummaryResponse,
    ToolStatsResponse,
    UserToolCreate,
    UserToolResponse,
    UserToolUpdate,
)
from chaoscypher_cortex.shared.api.dependencies import (
    PageParams,
    paginate_list,
)
from chaoscypher_cortex.shared.api.errors import raise_if_not_found
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
    ErrorDetail,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


if TYPE_CHECKING:
    from chaoscypher_core.ports.types import SystemToolDict, UserToolDict
    from chaoscypher_core.services.workflows.tools.management import ToolService


# Create router
router = APIRouter()


# Dependency to get tool service
def get_tool_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ToolService:
    """Get ToolService instance using shared factory."""
    return _make_tool_service(settings.current_database)


# ============================================================================
# System Tools Endpoints
# ============================================================================


@router.get(
    "/system",
    response_model=list[SystemToolSummaryResponse],
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def list_system_tools(
    _: CurrentUsername,
    tool_service: Annotated[ToolService, Depends(get_tool_service)],
    category: str | None = Query(None, description="Filter by category"),
    is_active: bool | None = Query(None, description="Filter by active flag"),
) -> list[SystemToolDict]:
    """List all system tools.

    System tools are built-in tools available to all users.
    Returns summary data (excludes input/output schemas).
    Use GET /system/{tool_id} for full details.
    """
    return tool_service.list_system_tools(category=category, is_active=is_active)


@router.get(
    "/system/{tool_id}",
    response_model=SystemToolResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_system_tool(
    _: CurrentUsername,
    tool_id: str,
    tool_service: Annotated[ToolService, Depends(get_tool_service)],
) -> SystemToolDict:
    """Get system tool by ID.

    Returns details about a specific system tool.
    """
    result = tool_service.get_system_tool(tool_id)
    return raise_if_not_found(result, "Tool not found")


# ============================================================================
# User Tools Endpoints
# ============================================================================


@router.get(
    "",
    response_model=PaginatedUserToolsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def list_user_tools(
    tool_service: Annotated[ToolService, Depends(get_tool_service)],
    pagination: PageParams,
    _: CurrentUsername,
    system_tool_id: str | None = Query(None, description="Filter by system tool ID"),
    is_active: bool | None = Query(None, description="Filter by active flag"),
) -> PaginatedUserToolsResponse:
    """List user-configured tools with pagination.

    - Single-user mode: the local operator owns everything.
    """
    page, page_size = pagination
    all_tools = tool_service.list_user_tools(
        system_tool_id=system_tool_id,
        is_active=is_active,
    )
    result = paginate_list(all_tools, page, page_size)
    return PaginatedUserToolsResponse(
        data=[UserToolResponse(**t) for t in result["data"]],
        pagination=result["pagination"],
    )


@router.post(
    "",
    response_model=UserToolResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def create_user_tool(
    tool_create: UserToolCreate,
    tool_service: Annotated[ToolService, Depends(get_tool_service)],
    _: CurrentUsername,
) -> UserToolDict:
    """Create a new user tool.

    User tools are configured instances of system tools.

    - Single-user mode: the local operator owns everything.
    """
    tool_id = tool_service.create_user_tool(tool_create.model_dump())
    result = tool_service.get_user_tool(tool_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorDetail(
                code="OPERATION_FAILED", message="Failed to create tool"
            ).model_dump(),
        )
    return result


@router.get(
    "/{tool_id}",
    response_model=UserToolResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_user_tool(
    tool_id: str,
    tool_service: Annotated[ToolService, Depends(get_tool_service)],
    _: CurrentUsername,
) -> UserToolDict:
    """Get user tool by ID.

    - Single-user mode: the local operator owns everything.
    """
    result = tool_service.get_user_tool(tool_id)
    return raise_if_not_found(result, "Tool not found")


@router.patch(
    "/{tool_id}",
    response_model=UserToolResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def update_user_tool(
    tool_id: str,
    tool_update: UserToolUpdate,
    tool_service: Annotated[ToolService, Depends(get_tool_service)],
    _: CurrentUsername,
) -> UserToolDict:
    """Update an existing user tool.

    - Single-user mode: the local operator owns everything.
    """
    success = tool_service.update_user_tool(tool_id, tool_update.model_dump(exclude_unset=True))
    raise_if_not_found(success, "Tool not found")
    result = tool_service.get_user_tool(tool_id)
    return raise_if_not_found(result, "Tool not found")


@router.delete(
    "/{tool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def delete_user_tool(
    tool_id: str,
    tool_service: Annotated[ToolService, Depends(get_tool_service)],
    _: CurrentUsername,
) -> None:
    """Delete a user tool.

    - Single-user mode: the local operator owns everything.
    """
    success = tool_service.delete_user_tool(tool_id)
    raise_if_not_found(success, "Tool not found")


# ============================================================================
# Statistics Endpoints
# ============================================================================


@router.get(
    "/stats/{tool_type}/{tool_id}",
    response_model=ToolStatsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_tool_stats(
    _: CurrentUsername,
    tool_type: str,
    tool_id: str,
    tool_service: Annotated[ToolService, Depends(get_tool_service)],
) -> dict[str, Any]:
    """Get stats for a specific tool.

    Args:
        tool_type: 'system' or 'user'
        tool_id: Tool ID
        tool_service: Tool service dependency

    """
    stats = tool_service.get_tool_stats(tool_type, tool_id)
    return raise_if_not_found(stats, f"Statistics not found for {tool_type} tool {tool_id}")
