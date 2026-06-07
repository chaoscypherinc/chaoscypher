# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP endpoints for vision_page retry + listing — sources subresource.

Three endpoints, all mounted under ``/api/v1/sources``:

- ``POST /{source_id}/vision_pages/{page_number}/retry`` — reset one
  vision_page_descriptions row to PENDING and re-enqueue
  ``OP_VISION_PAGE``.  Optional ``?region_index=N`` (defaults to 0).
- ``POST /{source_id}/vision_pages/retry_failed`` — reset every FAILED
  vision_page_descriptions row for the source to PENDING and re-enqueue
  one ``OP_VISION_PAGE`` per reset.
- ``GET /{source_id}/vision_pages`` — read-only listing the frontend
  per-page panel consumes. Returns the vision_job summary + every
  page row regardless of source state.

Path-segment convention: snake_case (``vision_pages``, ``retry_failed``)
to satisfy CC006 (no hyphens in API paths). Matches sibling endpoints
like ``/llm_metrics`` and ``/recovery_events`` on the parent
``sources_router``.

The retry endpoints operate pre-finalize only — the service raises
``ConflictError`` (→ HTTP 409) if the source has advanced past
``vision_pending``. The GET listing has no such gate (it is
read-only). Out-of-scope (v1): post-finalize retry, TRUNCATED-page
retry, region-split retry.

Following the Cortex VSA convention (mirrors
``features.pause.api``):

- ``router`` and ``get_vision_pages_service()`` live in this module.
- ``api/v1/router.py`` mounts the router under the ``/sources`` prefix.
- Tests call the endpoint coroutines directly with a mocked service
  (the canonical Cortex test style — see ``test_pause_api.py``).
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, status

from chaoscypher_cortex.features.sources.models import (
    VisionPageRetryResponse,
    VisionPagesBatchRetryResponse,
    VisionPagesListResponse,
)
from chaoscypher_cortex.features.sources.vision_pages_repository import (
    VisionPagesRepository,
)
from chaoscypher_cortex.features.sources.vision_pages_service import VisionPagesService
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


def get_vision_pages_service() -> VisionPagesService:
    """Build a VisionPagesService for the current request (CC001).

    Wires the SqliteAdapter (which implements
    ``VisionStorageProtocol``) and the shared queue client into a fresh
    VSA repository + service. Mirrors ``get_pause_service`` — uses the
    shared adapter singleton, constructs the queue client lazily, and
    has no other dependencies. Called per-request via FastAPI's
    ``Depends``; tests override via ``app.dependency_overrides`` or by
    passing a mock to the endpoint directly.
    """
    from typing import TYPE_CHECKING, cast

    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.database.adapter_factory import get_sqlite_adapter
    from chaoscypher_core.queue import queue_client

    if TYPE_CHECKING:
        from chaoscypher_core.ports.storage_vision import (
            VisionStorageProtocol,
        )

    settings = get_settings()
    database_name = settings.current_database
    adapter = get_sqlite_adapter(database_name=database_name)
    # SqliteAdapter implements VisionStorageProtocol via VisionPagesMixin —
    # the cast aligns the (broader) mixin signature with the (TypedDict)
    # protocol that VisionPagesRepository requires.
    repository = VisionPagesRepository(
        storage=cast("VisionStorageProtocol", adapter),
        database_name=database_name,
    )
    return VisionPagesService(
        repository=repository,
        source_storage=adapter,
        queue_client=queue_client,
        database_name=database_name,
    )


@router.post(
    "/{source_id}/vision_pages/{page_number}/retry",
    response_model=VisionPageRetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
    },
    summary="Retry one vision page",
    description=(
        "Reset one ``vision_page_descriptions`` row to ``pending`` and "
        "re-enqueue ``OP_VISION_PAGE``. The source must still be in "
        "state ``vision_pending`` (pre-finalize retry only). The "
        "optional ``region_index`` query parameter defaults to ``0``."
    ),
)
async def retry_vision_page_endpoint(
    source_id: str,
    page_number: int,
    _: CurrentUsername,
    service: Annotated[VisionPagesService, Depends(get_vision_pages_service)],
    region_index: int = Query(default=0, ge=0),
) -> VisionPageRetryResponse:
    """Single-page retry endpoint.

    **Errors:**
    - 404: Source, vision job, or page not found.
    - 409: Source is not in ``vision_pending`` state.
    """
    result = await service.retry_page(
        source_id=source_id,
        page_number=page_number,
        region_index=region_index,
    )
    return VisionPageRetryResponse(**result)


@router.post(
    "/{source_id}/vision_pages/retry_failed",
    response_model=VisionPagesBatchRetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
    },
    summary="Retry all failed vision pages for a source",
    description=(
        "Reset every ``failed`` ``vision_page_descriptions`` row for "
        "the source to ``pending`` and re-enqueue ``OP_VISION_PAGE`` "
        "for each. ``truncated`` pages are preserved (v1 keeps partial "
        "content). Source must still be in state ``vision_pending``."
    ),
)
async def retry_failed_vision_pages_endpoint(
    source_id: str,
    _: CurrentUsername,
    service: Annotated[VisionPagesService, Depends(get_vision_pages_service)],
) -> VisionPagesBatchRetryResponse:
    """Batch retry-failed endpoint.

    **Errors:**
    - 404: Source or vision job not found.
    - 409: Source is not in ``vision_pending`` state.
    """
    result = await service.retry_failed(source_id=source_id)
    return VisionPagesBatchRetryResponse(**result)


@router.get(
    "/{source_id}/vision_pages",
    response_model=VisionPagesListResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
    summary="List vision page descriptions for a source",
    description=(
        "Return the vision_job summary and every vision_page_descriptions "
        "row for the source, ordered by ``(page_number, region_index)``. "
        "``job`` is ``null`` if the source has no vision_job (text-only "
        "source or pre-loader-phase). Read-only — works regardless of "
        "source state, so the per-page panel can show post-finalize "
        "history."
    ),
)
async def list_vision_pages_endpoint(
    source_id: str,
    _: CurrentUsername,
    service: Annotated[VisionPagesService, Depends(get_vision_pages_service)],
) -> VisionPagesListResponse:
    """List vision-page rows + job summary for the source.

    **Errors:**
    - 404: Source not found.
    """
    result = await service.list_pages(source_id=source_id)
    return VisionPagesListResponse(**result)


__all__ = [
    "get_vision_pages_service",
    "list_vision_pages_endpoint",
    "retry_failed_vision_pages_endpoint",
    "retry_vision_page_endpoint",
    "router",
]
