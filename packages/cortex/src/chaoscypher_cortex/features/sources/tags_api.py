# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""API endpoints for Source Tags management."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.features.sources.models import (
    TagCreate,
    TagResponse,
    TagUpdate,
)
from chaoscypher_cortex.features.sources.tag_service import TagService
from chaoscypher_cortex.shared.api.errors import raise_if_not_found, validation_error
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


router = APIRouter()


# ================================
# Dependency Injection Factory
# ================================


def get_tag_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TagService:
    """Create TagService with dependencies.

    Builds its own adapter from ``settings.current_database``; no request
    session is needed (the prior ``get_current_session`` dependency opened a
    DB session per request that this factory never used).
    """
    from chaoscypher_core.app_config.engine_factory import (
        build_engine_settings,
    )
    from chaoscypher_core.database import get_sqlite_adapter
    from chaoscypher_core.services.graph.management import (
        SourceService as EngineSourceService,
    )

    adapter = get_sqlite_adapter(database_name=settings.current_database)
    engine_service = EngineSourceService(
        repository=adapter,
        database_name=settings.current_database,
        settings=build_engine_settings(settings),
    )
    return TagService(engine_service, database_name=settings.current_database)


# ================================
# Tag CRUD Endpoints
# ================================


@router.get(
    "",
    response_model=list[TagResponse],
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def list_tags(
    _: CurrentUsername,
    service: Annotated[TagService, Depends(get_tag_service)],
) -> list[dict[str, Any]]:
    """List all tags."""
    return service.list_tags()


@router.get(
    "/{tag_id}",
    response_model=TagResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_tag(
    _: CurrentUsername,
    tag_id: str,
    service: Annotated[TagService, Depends(get_tag_service)],
) -> dict[str, Any]:
    """Get a tag by ID."""
    tag = service.get_tag(tag_id)
    return raise_if_not_found(tag, "Tag not found")


@router.post(
    "",
    response_model=TagResponse,
    status_code=201,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def create_tag(
    _: CurrentUsername,
    tag_data: TagCreate,
    service: Annotated[TagService, Depends(get_tag_service)],
) -> dict[str, Any]:
    """Create a new tag."""
    try:
        return service.create_tag(
            name=tag_data.name,
            color=tag_data.color,
            description=tag_data.description,
        )
    except ValueError as e:
        raise validation_error("tag_operation", internal_error=e) from e


@router.patch(
    "/{tag_id}",
    response_model=TagResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def update_tag(
    _: CurrentUsername,
    tag_id: str,
    tag_data: TagUpdate,
    service: Annotated[TagService, Depends(get_tag_service)],
) -> dict[str, Any]:
    """Update a tag."""
    try:
        tag = service.update_tag(
            tag_id=tag_id,
            name=tag_data.name,
            color=tag_data.color,
            description=tag_data.description,
        )
        return raise_if_not_found(tag, "Tag not found")
    except ValueError as e:
        raise validation_error("tag_operation", internal_error=e) from e


@router.delete(
    "/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def delete_tag(
    _: CurrentUsername,
    tag_id: str,
    service: Annotated[TagService, Depends(get_tag_service)],
) -> None:
    """Delete a tag."""
    success = service.delete_tag(tag_id)
    raise_if_not_found(success, "Tag not found")
