# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tools Models.

Pydantic DTOs for system and user tools API requests/responses.

SQLModel table definitions are in chaoscypher.adapters.sqlite.models
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from chaoscypher_cortex.shared.api.models import PaginationMetadata


# SQLModel tables imported from chaoscypher


# ============================================================================
# Request/Response Models (Pydantic)
# ============================================================================


class SystemToolSummaryResponse(BaseModel):
    """System tool summary for list endpoints (excludes large JSON schemas)."""

    id: str
    category: str
    icon: str | None = None
    name: str
    description: str
    version: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SystemToolResponse(BaseModel):
    """System tool response DTO."""

    id: str
    category: str
    icon: str | None = None
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    version: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserToolCreate(BaseModel):
    """Create user tool DTO."""

    name: str
    description: str | None = None
    system_tool_id: str
    configuration: dict[str, Any]
    tags: list[str] | None = None
    is_active: bool = True


class UserToolUpdate(BaseModel):
    """Update user tool DTO."""

    name: str | None = None
    description: str | None = None
    configuration: dict[str, Any] | None = None
    tags: list[str] | None = None
    is_active: bool | None = None


class UserToolResponse(BaseModel):
    """User tool response DTO."""

    id: str
    name: str
    description: str | None = None
    system_tool_id: str
    configuration: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] | None = None
    is_active: bool = True
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    system_tool: SystemToolResponse | None = None  # Include system tool info

    model_config = {"from_attributes": True}


class PaginatedUserToolsResponse(BaseModel):
    """Paginated response for listing user tools."""

    data: list[UserToolResponse]
    pagination: PaginationMetadata


class ToolStatsResponse(BaseModel):
    """Tool stats response DTO."""

    tool_type: str
    tool_id: str
    total_calls: int
    successful_calls: int
    failed_calls: int
    avg_execution_ms: int
    last_called_at: datetime | None
    updated_at: datetime

    model_config = {"from_attributes": True}
