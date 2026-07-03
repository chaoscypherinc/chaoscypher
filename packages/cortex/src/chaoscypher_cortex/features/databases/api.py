# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Databases API Endpoints.

GET    /api/v1/databases - List all databases
GET    /api/v1/databases/current - Get current database
PATCH  /api/v1/databases/current - Switch database
GET    /api/v1/databases/{name} - Get database info
POST   /api/v1/databases - Create database
DELETE /api/v1/databases/{name} - Delete database.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.database.repository import DatabaseRepository
from chaoscypher_core.services.events import event_bus
from chaoscypher_cortex.features.databases.models import (
    CurrentDatabaseResponse,
    DatabaseCreateRequest,
    DatabaseListResponse,
    DatabaseResponse,
    DatabaseSwitchRequest,
    DatabaseSwitchResponse,
)
from chaoscypher_cortex.features.databases.service import DatabasesService
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername
from chaoscypher_cortex.shared.worker_notify import publish_settings_change


router = APIRouter()


def get_database_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> DatabaseRepository:
    """Get DatabaseRepository instance."""
    return DatabaseRepository(data_root=settings.paths.data_dir)


def get_databases_service(
    database_repository: Annotated[DatabaseRepository, Depends(get_database_repository)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DatabasesService:
    """Get DatabasesService instance (VSA factory pattern)."""
    return DatabasesService(database_repository, settings)


@router.get("", response_model=DatabaseListResponse)
async def list_databases(
    _: CurrentUsername,
    databases_service: Annotated[DatabasesService, Depends(get_databases_service)],
) -> DatabaseListResponse:
    """List all available databases.

    **Returns:**
    - List of databases with metadata (name, size, last modified)
    """
    return databases_service.list_databases()


@router.get("/current", response_model=CurrentDatabaseResponse)
async def get_current_database(
    _: CurrentUsername,
    databases_service: Annotated[DatabasesService, Depends(get_databases_service)],
) -> CurrentDatabaseResponse:
    """Get the currently active database.

    **Returns:**
    - Current database name
    - Database metadata
    """
    return databases_service.get_current_database()


@router.patch("/current", response_model=DatabaseSwitchResponse)
async def switch_database(
    _: CurrentUsername,
    request: DatabaseSwitchRequest,
    databases_service: Annotated[DatabasesService, Depends(get_databases_service)],
) -> DatabaseSwitchResponse:
    """Switch to a different database (partial update of current setting).

    **VSA Simplification:**
    - No need to reinitialize managers (unlike old architecture)
    - Each request creates fresh service instances
    - Services automatically use current_database from settings

    **Auto-initialization:**
    - If app.db doesn't exist for target database, it's created automatically
    - Ensures seamless switching to new databases

    **Request Body:**
    - `name`: Database name to switch to

    **Returns:**
    - Success status
    - Message (includes instruction to refresh page)

    **Note:**
    - Frontend should refresh after switching to load new database context
    """
    result = databases_service.switch_database(request.name)
    # Notify the worker so it re-points its DB-file-bound repositories to the new
    # active database. Without this the worker keeps writing imports / indexing /
    # extraction into the PREVIOUS database's app.db file (stamped with the new
    # name but stranded in the wrong file). Best-effort; the switch has persisted.
    await publish_settings_change("v1:database_switched")
    return result


@router.get("/{name}", response_model=DatabaseResponse)
async def get_database(
    _: CurrentUsername,
    name: str,
    databases_service: Annotated[DatabasesService, Depends(get_databases_service)],
) -> DatabaseResponse:
    """Get information about a specific database.

    **Returns:**
    - Database metadata

    **Errors:**
    - 404: Database not found
    """
    return databases_service.get_database(name)


@router.post("", response_model=DatabaseResponse, status_code=status.HTTP_201_CREATED)
async def create_database(
    _: CurrentUsername,
    request: DatabaseCreateRequest,
    databases_service: Annotated[DatabasesService, Depends(get_databases_service)],
) -> DatabaseResponse:
    """Create a new database.

    **Request Body:**
    - `name`: Database name (alphanumeric, underscores, hyphens)

    **Creates:**
    - Database directory structure
    - graphs/ directory with .ttl files (knowledge, templates, lenses)
    - app.db with default system data
    - search/ directory for search indices

    **Returns:**
    - Created database metadata

    **Errors:**
    - 400: Invalid name or database already exists
    """
    result = databases_service.create_database(request.name)
    event_bus.emit(
        "database_created",
        action=f"Database created: {request.name}",
        source="user",
        details={"database_name": request.name},
    )
    return result


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_database(
    _: CurrentUsername,
    name: str,
    databases_service: Annotated[DatabasesService, Depends(get_databases_service)],
) -> Response:
    """Delete a database.

    **WARNING:** This operation cannot be undone!

    **Safety Checks:**
    - Cannot delete currently active database
    - Cannot delete 'default' database

    **Deletes:**
    - Entire database directory
    - All knowledge graph data
    - All app data (workflows, triggers, etc.)
    - All search indexes

    **Errors:**
    - 400: Trying to delete current database or invalid operation
    - 404: Database not found
    """
    databases_service.delete_database(name)
    event_bus.emit(
        "database_deleted",
        action=f"Database deleted: {name}",
        source="user",
        details={"database_name": name},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
