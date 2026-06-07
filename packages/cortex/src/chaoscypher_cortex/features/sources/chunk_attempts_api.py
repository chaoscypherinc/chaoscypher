# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP endpoints for chunk_extraction_attempts history.

- GET /{source_id}/chunks/{chunk_index}/attempts — list summary rows
- GET /{source_id}/chunks/{chunk_index}/attempts/{attempt_id} — full body
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends

from chaoscypher_cortex.features.sources.chunk_attempts_service import (
    ChunkAttemptsService,
)
from chaoscypher_cortex.features.sources.models import (
    ChunkAttemptDetail,
    ChunkAttemptsListResponse,
    ChunkAttemptSummary,
)
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import (
    CurrentUsername,  # noqa: TC001
)


logger = structlog.get_logger(__name__)
router = APIRouter()


def get_chunk_attempts_service() -> ChunkAttemptsService:
    """Build a ChunkAttemptsService (CC001)."""
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.database.adapter_factory import get_sqlite_adapter

    settings = get_settings()
    database_name = settings.current_database
    adapter = get_sqlite_adapter(database_name=database_name)
    return ChunkAttemptsService(adapter=adapter, database_name=database_name)


@router.get(
    "/{source_id}/chunks/{chunk_index}/attempts",
    response_model=ChunkAttemptsListResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
    summary="List prior extraction attempts for a chunk",
)
async def list_chunk_attempts_endpoint(
    source_id: str,
    chunk_index: int,
    _: CurrentUsername,
    service: Annotated[ChunkAttemptsService, Depends(get_chunk_attempts_service)],
) -> ChunkAttemptsListResponse:
    """List prior attempts for a chunk."""
    rows = await service.list_attempts(source_id=source_id, chunk_index=chunk_index)
    return ChunkAttemptsListResponse(data=[ChunkAttemptSummary(**r) for r in rows])


@router.get(
    "/{source_id}/chunks/{chunk_index}/attempts/{attempt_id}",
    response_model=ChunkAttemptDetail,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
    summary="Get one prior extraction attempt with full body",
)
async def get_chunk_attempt_endpoint(
    source_id: str,
    chunk_index: int,
    attempt_id: str,
    _: CurrentUsername,
    service: Annotated[ChunkAttemptsService, Depends(get_chunk_attempts_service)],
) -> ChunkAttemptDetail:
    """Fetch one prior attempt's full body."""
    row = await service.get_attempt(
        source_id=source_id,
        chunk_index=chunk_index,
        attempt_id=attempt_id,
    )
    return ChunkAttemptDetail(**row)


__all__ = [
    "get_chunk_attempt_endpoint",
    "get_chunk_attempts_service",
    "list_chunk_attempts_endpoint",
    "router",
]
