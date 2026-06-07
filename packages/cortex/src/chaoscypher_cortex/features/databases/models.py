# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Databases Models.

Pydantic DTOs for database operations.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from chaoscypher_core import policy


class DatabaseResponse(BaseModel):
    """Database information response."""

    name: str
    path: str
    exists: bool
    size: int
    last_modified: datetime | None


class DatabaseListResponse(BaseModel):
    """List of databases response."""

    databases: list[DatabaseResponse]


class CurrentDatabaseResponse(BaseModel):
    """Current database response."""

    current: str
    info: DatabaseResponse


class DatabaseCreateRequest(BaseModel):
    """Create database request."""

    name: str = Field(max_length=policy.DATABASE_NAME_MAX_LENGTH)


class DatabaseSwitchRequest(BaseModel):
    """Switch database request."""

    name: str = Field(max_length=policy.DATABASE_NAME_MAX_LENGTH)


class DatabaseSwitchResponse(BaseModel):
    """Switch database response."""

    success: bool
    message: str
    database: str
