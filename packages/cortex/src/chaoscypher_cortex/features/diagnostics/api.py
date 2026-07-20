# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Diagnostics API.

Endpoint for downloading a diagnostic ZIP bundle for bug reports.
"""

import os
import shutil
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from chaoscypher_core.app_config import Settings, get_settings, mask_settings_dict
from chaoscypher_cortex.features.diagnostics.service import DiagnosticsService
from chaoscypher_cortex.features.logs.service import LogService
from chaoscypher_cortex.shared.api import safe_create
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


router = APIRouter()


def _get_queue_client() -> Any:
    """Get the raw async Valkey client from the queue singleton.

    Returns:
        Async Valkey client instance.
    """
    from chaoscypher_core.queue import queue_client

    return queue_client.client


async def get_diagnostics_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> DiagnosticsService:
    """Create DiagnosticsService with all available dependencies.

    Args:
        settings: Application settings.

    Returns:
        Configured DiagnosticsService instance.
    """
    log_dir = os.path.join(settings.paths.data_dir, settings.paths.logs_subdir)

    supervisor_password = (
        settings.logs.supervisor_password.get_secret_value()
        if settings.logs.supervisor_password
        else ""
    )
    return DiagnosticsService(
        data_dir=settings.paths.data_dir,
        log_dir=log_dir,
        database_name=settings.current_database,
        settings_dict=mask_settings_dict(settings.model_dump()),
        queue_client=safe_create(_get_queue_client),
        log_service=safe_create(
            LogService,
            log_dir=log_dir,
            supervisor_password=supervisor_password,
        ),
    )


@router.get(
    "/export",
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def export_diagnostics(
    _: CurrentUsername,
    service: Annotated[DiagnosticsService, Depends(get_diagnostics_service)],
) -> FileResponse:
    """Download a diagnostic ZIP bundle for bug reports.

    Gathers system info, sanitized settings, database stats, logs,
    queue status, and service status into a single downloadable ZIP.

    Args:
        _: Authenticated username (injected by FastAPI; gates the endpoint).
        service: DiagnosticsService instance.

    Returns:
        ZIP file download response.
    """
    bundle_path = await service.create_bundle()
    tmp_dir = bundle_path.parent

    return FileResponse(
        path=str(bundle_path),
        media_type="application/zip",
        filename="chaoscypher-diagnostics.zip",
        background=BackgroundTask(shutil.rmtree, str(tmp_dir), ignore_errors=True),
    )
