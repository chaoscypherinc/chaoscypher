# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Backup API endpoints.

POST   /api/v1/backup              - Create a backup of the current database
GET    /api/v1/backup              - List available backups
POST   /api/v1/backup/{filename}/restore  - Restore from a backup
GET    /api/v1/backup/{filename}/download - Download a backup file
DELETE /api/v1/backup/{filename}   - Delete a specific backup
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, status
from fastapi.responses import FileResponse

from chaoscypher_core.app_config import Settings, get_current_database_name
from chaoscypher_core.app_config import get_settings as get_settings_dep
from chaoscypher_core.services.backup import BackupService
from chaoscypher_cortex.features.backup.models import (
    BackupListResponse,
    BackupResponse,
    RestoreResponse,
)
from chaoscypher_cortex.features.backup.service import BackupFeatureService
from chaoscypher_cortex.shared.api.errors import resource_not_found_error, validation_error
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


router = APIRouter()

# Backup filenames follow the pattern: app_YYYYMMDD_HHMMSS.db
BackupFilename = Annotated[str, Path(pattern=r"^app_\d{8}_\d{6}\.db$")]


def get_backup_service(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> BackupFeatureService:
    """Create BackupFeatureService with DI."""
    core_service = BackupService(
        data_dir=str(settings.data_dir),
        backup_subdir=settings.backup.backup_dir,
    )
    return BackupFeatureService(core_service)


@router.post(
    "",
    response_model=BackupResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def create_backup(
    _: CurrentUsername,
    database_name: Annotated[str, Depends(get_current_database_name)],
    service: Annotated[BackupFeatureService, Depends(get_backup_service)],
) -> dict[str, Any]:
    """Create a backup of the current database.

    Returns 201 Created since a new backup file is created.
    """
    return service.create_backup(database_name)


@router.get(
    "",
    response_model=BackupListResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def list_backups(
    _: CurrentUsername,
    database_name: Annotated[str, Depends(get_current_database_name)],
    service: Annotated[BackupFeatureService, Depends(get_backup_service)],
) -> dict[str, Any]:
    """List available backups for the current database."""
    backups = service.list_backups(database_name)
    return {"backups": backups}


@router.post(
    "/{filename}/restore",
    response_model=RestoreResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def restore_backup(
    _: CurrentUsername,
    filename: BackupFilename,
    database_name: Annotated[str, Depends(get_current_database_name)],
    service: Annotated[BackupFeatureService, Depends(get_backup_service)],
) -> dict[str, str]:
    """Restore the current database from a backup."""
    try:
        return service.restore_backup(database_name, filename)
    except FileNotFoundError:
        raise resource_not_found_error("backup", filename) from None
    except ValueError as e:
        raise validation_error("backup_restore", internal_error=e) from e


@router.get(
    "/{filename}/download",
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def download_backup(
    _: CurrentUsername,
    filename: BackupFilename,
    database_name: Annotated[str, Depends(get_current_database_name)],
    service: Annotated[BackupFeatureService, Depends(get_backup_service)],
) -> FileResponse:
    """Download a backup file."""
    try:
        path = service.get_backup_path(database_name, filename)
        return FileResponse(
            path=str(path),
            filename=filename,
            media_type="application/x-sqlite3",
        )
    except FileNotFoundError:
        raise resource_not_found_error("backup", filename) from None


@router.delete(
    "/{filename}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def delete_backup(
    _: CurrentUsername,
    filename: BackupFilename,
    database_name: Annotated[str, Depends(get_current_database_name)],
    service: Annotated[BackupFeatureService, Depends(get_backup_service)],
) -> None:
    """Delete a specific backup."""
    try:
        service.delete_backup(database_name, filename)
    except FileNotFoundError:
        raise resource_not_found_error("backup", filename) from None
