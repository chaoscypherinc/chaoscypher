# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cache API endpoints (mounted at ``/api/v1/cache``).

GET    /                 the completion and embedding caches: settings, size, history
PATCH  /                 change their settings (persisted to settings.yaml)
POST   /{name}/clear     delete one cache's entries (its history is kept)
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends

from chaoscypher_core.app_config import (
    ConfigManager,
    Settings,
    get_config_manager,
)
from chaoscypher_core.app_config import (
    get_settings as get_settings_dep,
)
from chaoscypher_cortex.features.cache.models import (
    CacheClearResponse,
    CacheOverviewResponse,
    CacheUpdateRequest,
)
from chaoscypher_cortex.features.cache.service import CacheService
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


router = APIRouter()


def get_cache_service(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    config_manager: Annotated[ConfigManager, Depends(get_config_manager)],
) -> CacheService:
    """Build the CacheService for the current request's settings."""
    return CacheService(settings, config_manager)


@router.get(
    "",
    response_model=CacheOverviewResponse,
    responses={**COMMON_ERROR_RESPONSES, **AUTH_ERROR_RESPONSES},
)
async def get_cache_overview(
    _: CurrentUsername,
    service: Annotated[CacheService, Depends(get_cache_service)],
) -> CacheOverviewResponse:
    """The completion and embedding caches: enabled flags, size against the cap, hits by call site."""
    return await asyncio.to_thread(service.overview)


@router.patch(
    "",
    response_model=CacheOverviewResponse,
    responses={**COMMON_ERROR_RESPONSES, **AUTH_ERROR_RESPONSES},
)
async def update_cache_settings(
    _: CurrentUsername,
    patch: CacheUpdateRequest,
    service: Annotated[CacheService, Depends(get_cache_service)],
) -> CacheOverviewResponse:
    """Change the caches' enabled flags and caps; providers built afterwards use them.

    The change is written to the ``cache`` section of ``settings.yaml``.
    Turning a cache off stops reads and writes for new providers and keeps
    its store and stats. Returns the overview after the change.
    """
    return await service.update(patch)


@router.post(
    "/{name}/clear",
    response_model=CacheClearResponse,
    responses={**COMMON_ERROR_RESPONSES, **AUTH_ERROR_RESPONSES, **NOT_FOUND_RESPONSE},
)
async def clear_cache(
    name: str,
    _: CurrentUsername,
    service: Annotated[CacheService, Depends(get_cache_service)],
) -> CacheClearResponse:
    """Delete every entry of cache ``name`` (``completions`` or ``embeddings``); 404 otherwise."""
    return await asyncio.to_thread(service.clear, name)
