# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Logs API.

Endpoints for viewing container service logs and status.
"""

import asyncio
import os
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.features.logs.models import LogResponse, ServiceStatusResponse
from chaoscypher_cortex.features.logs.service import LogService
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
    SERVICE_UNAVAILABLE_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


router = APIRouter()


def get_log_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> LogService:
    """Create LogService with configured paths.

    Args:
        settings: Application settings.

    Returns:
        Configured LogService instance.
    """
    log_dir = os.path.join(settings.paths.data_dir, settings.paths.logs_subdir)
    supervisor_password = (
        settings.logs.supervisor_password.get_secret_value()
        if settings.logs.supervisor_password
        else ""
    )
    return LogService(
        log_dir=log_dir,
        supervisor_password=supervisor_password,
        known_services=settings.logs.known_services,
        max_log_lines=settings.logs.max_log_lines,
    )


@router.get(
    "",
    response_model=LogResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def get_all_logs(
    _: CurrentUsername,
    service: Annotated[LogService, Depends(get_log_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    lines: int | None = Query(
        default=None, ge=1, le=10000, description="Number of lines to return"
    ),
) -> LogResponse:
    """Get interleaved logs from all services, sorted by timestamp.

    Args:
        _: CurrentUsername — auth-gate only, value unused.
        service: LogService instance.
        settings: Application settings.
        lines: Number of tail lines to return (default from settings).

    Returns:
        LogResponse with merged log lines.
    """
    effective_lines = lines if lines is not None else settings.pagination.log_tail_lines
    return await asyncio.to_thread(service.get_all_logs, lines=effective_lines)


@router.get(
    "/status",
    response_model=ServiceStatusResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def get_service_status(
    _: CurrentUsername,
    service: Annotated[LogService, Depends(get_log_service)],
) -> ServiceStatusResponse:
    """Get status of all managed services (PID, uptime, state).

    Returns available=false if supervisord is not reachable.

    Args:
        _: CurrentUsername — auth-gate only, value unused.
        service: LogService instance.

    Returns:
        ServiceStatusResponse with all service statuses.
    """
    return await asyncio.to_thread(service.get_service_status)


@router.get(
    "/{service_name}",
    response_model=LogResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_service_logs(
    _: CurrentUsername,
    service_name: str,
    service: Annotated[LogService, Depends(get_log_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    lines: int | None = Query(
        default=None, ge=1, le=10000, description="Number of lines to return"
    ),
) -> LogResponse:
    """Get logs for a specific service.

    Args:
        _: CurrentUsername — auth-gate only, value unused.
        service_name: Service name (cortex, neuron, nginx, valkey).
        service: LogService instance.
        settings: Application settings.
        lines: Number of tail lines to return (default from settings).

    Returns:
        LogResponse with the requested service's log lines.
    """
    effective_lines = lines if lines is not None else settings.pagination.log_tail_lines
    return await asyncio.to_thread(service.get_logs, service_name, lines=effective_lines)
