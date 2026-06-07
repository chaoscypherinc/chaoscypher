# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP endpoint: POST /{source_id}/chunks/{chunk_index}/rerun.

Mounts under the parent ``sources`` router. Returns 202 Accepted with
the new chunk task / queue task IDs + the snapshotted attempt_number.

Mirrors the vision_pages retry pattern (factory + service + Pydantic
response). Raises Core exceptions (never HTTPException — CC031);
mapped to 409 / 404 / 500 by the global handler.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, status

from chaoscypher_cortex.features.sources.chunk_rerun_service import ChunkRerunService
from chaoscypher_cortex.features.sources.models import ChunkRerunResponse
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
)
from chaoscypher_cortex.shared.auth.dependencies import (
    CurrentUsername,  # noqa: TC001 - FastAPI runtime dep
)


logger = structlog.get_logger(__name__)
router = APIRouter()


def get_chunk_rerun_service() -> ChunkRerunService:
    """Build a ChunkRerunService for the current request (CC001)."""
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
    from chaoscypher_core.queue import queue_client

    settings = get_settings()
    database_name = settings.current_database
    adapter = get_sqlite_adapter(database_name=database_name)
    return ChunkRerunService(
        adapter=adapter,
        queue_client=queue_client,
        database_name=database_name,
    )


@router.post(
    "/{source_id}/chunks/{chunk_index}/rerun",
    response_model=ChunkRerunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
    },
    summary="Rerun one chunk on a source",
    description=(
        "Reset one ``chunk_extraction_tasks`` row to ``pending``, snapshot "
        "the prior result into ``chunk_extraction_attempts``, walk the "
        "source.status back to ``extracting``, and re-enqueue "
        "``OP_EXTRACT_CHUNK``. The existing chunk / finalize / commit "
        "handlers take over from there. First-write-wins upsert preserves "
        "prior committed entities."
    ),
)
async def rerun_chunk_endpoint(
    source_id: str,
    chunk_index: int,
    _: CurrentUsername,
    service: Annotated[ChunkRerunService, Depends(get_chunk_rerun_service)],
) -> ChunkRerunResponse:
    """Per-chunk rerun endpoint.

    **Errors:**
    - 404: Source or chunk_task not found.
    - 409: Source is ``committing``; chunk is already pending/queued/running;
      or atomic reset lost a race.
    """
    result = await service.rerun_chunk(source_id=source_id, chunk_index=chunk_index)
    return ChunkRerunResponse(**result)


__all__ = [
    "get_chunk_rerun_service",
    "rerun_chunk_endpoint",
    "router",
]
