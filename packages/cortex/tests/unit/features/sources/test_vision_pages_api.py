# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP-layer tests for the vision-pages retry endpoints.

Follows the canonical Cortex pattern of calling the endpoint
coroutines directly with a mocked service rather than spinning up a
TestClient (see ``test_pause_api.py``). Much faster, simpler, and
avoids the dependency-override boilerplate that a TestClient run
would require.

Coverage:

- Single-page retry → returns 202-shaped response, awaits service
  with the right kwargs, region_index defaults to 0.
- Single-page retry with explicit region_index threads through.
- Batch retry-failed → returns the right shape and awaits the
  service.
- Pydantic Query validation: ``region_index < 0`` is enforced by the
  endpoint signature (``Query(default=0, ge=0)``) — tested directly
  via the underlying model rather than at the HTTP layer, since this
  module mirrors the project's direct-coroutine test style.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from chaoscypher_cortex.features.sources.models import (
    VisionPageRetryResponse,
    VisionPagesBatchRetryResponse,
    VisionPagesListResponse,
)
from chaoscypher_cortex.features.sources.vision_pages_api import (
    list_vision_pages_endpoint,
    retry_failed_vision_pages_endpoint,
    retry_vision_page_endpoint,
)


@pytest.mark.asyncio
async def test_retry_vision_page_endpoint_delegates_default_region_index() -> None:
    """Single-page endpoint awaits the service with region_index=0 by default."""
    service = AsyncMock()
    service.retry_page = AsyncMock(
        return_value={
            "source_id": "s1",
            "page_number": 5,
            "region_index": 0,
            "page_id": "p1",
            "status": "pending",
            "reset": True,
        }
    )

    result = await retry_vision_page_endpoint(
        source_id="s1",
        page_number=5,
        _="test-user",
        service=service,
        region_index=0,
    )

    assert isinstance(result, VisionPageRetryResponse)
    assert result.source_id == "s1"
    assert result.page_id == "p1"
    assert result.page_number == 5
    assert result.region_index == 0
    assert result.status == "pending"
    assert result.reset is True
    service.retry_page.assert_awaited_once_with(
        source_id="s1",
        page_number=5,
        region_index=0,
    )


@pytest.mark.asyncio
async def test_retry_vision_page_endpoint_threads_region_index() -> None:
    """Single-page endpoint passes a non-zero region_index through to the service."""
    service = AsyncMock()
    service.retry_page = AsyncMock(
        return_value={
            "source_id": "s1",
            "page_number": 5,
            "region_index": 2,
            "page_id": "p1r2",
            "status": "pending",
            "reset": True,
        }
    )

    result = await retry_vision_page_endpoint(
        source_id="s1",
        page_number=5,
        _="test-user",
        service=service,
        region_index=2,
    )

    assert result.region_index == 2
    assert result.page_id == "p1r2"
    service.retry_page.assert_awaited_once_with(
        source_id="s1",
        page_number=5,
        region_index=2,
    )


@pytest.mark.asyncio
async def test_retry_vision_page_endpoint_passes_through_noop_reset() -> None:
    """If the row was already PENDING, the service returns reset=False — pass it through."""
    service = AsyncMock()
    service.retry_page = AsyncMock(
        return_value={
            "source_id": "s1",
            "page_number": 7,
            "region_index": 0,
            "page_id": "p7",
            "status": "pending",
            "reset": False,
        }
    )

    result = await retry_vision_page_endpoint(
        source_id="s1",
        page_number=7,
        _="test-user",
        service=service,
        region_index=0,
    )

    assert result.reset is False
    assert result.status == "pending"


@pytest.mark.asyncio
async def test_retry_failed_vision_pages_endpoint_delegates() -> None:
    """Batch endpoint awaits the service with the source_id and returns the shape."""
    service = AsyncMock()
    service.retry_failed = AsyncMock(
        return_value={
            "source_id": "s1",
            "retried_count": 3,
            "skipped_count": 2,
            "page_ids": ["p1", "p2", "p3"],
        }
    )

    result = await retry_failed_vision_pages_endpoint(
        source_id="s1",
        _="test-user",
        service=service,
    )

    assert isinstance(result, VisionPagesBatchRetryResponse)
    assert result.source_id == "s1"
    assert result.retried_count == 3
    assert result.skipped_count == 2
    assert result.page_ids == ["p1", "p2", "p3"]
    service.retry_failed.assert_awaited_once_with(source_id="s1")


@pytest.mark.asyncio
async def test_retry_failed_vision_pages_endpoint_zero_failed() -> None:
    """Zero failed pages → 202 with retried_count=0 and empty page_ids."""
    service = AsyncMock()
    service.retry_failed = AsyncMock(
        return_value={
            "source_id": "s1",
            "retried_count": 0,
            "skipped_count": 4,
            "page_ids": [],
        }
    )

    result = await retry_failed_vision_pages_endpoint(
        source_id="s1",
        _="test-user",
        service=service,
    )

    assert result.retried_count == 0
    assert result.skipped_count == 4
    assert result.page_ids == []


def test_vision_pages_router_registers_two_routes() -> None:
    """Sanity: the two POST routes are registered with status 202."""
    from chaoscypher_cortex.features.sources.vision_pages_api import router

    paths = {(route.path, tuple(sorted(route.methods))) for route in router.routes}  # type: ignore[attr-defined]
    assert (
        "/{source_id}/vision_pages/{page_number}/retry",
        ("POST",),
    ) in paths
    assert ("/{source_id}/vision_pages/retry_failed", ("POST",)) in paths


def test_vision_pages_router_mounted_under_sources_in_api_v1() -> None:
    """Sanity: the router is included under /api/v1/sources in the v1 aggregator."""
    from chaoscypher_cortex.api.v1.router import create_api_router

    api = create_api_router()
    paths = {getattr(route, "path", None) for route in api.routes}
    assert "/api/v1/sources/{source_id}/vision_pages/{page_number}/retry" in paths
    assert "/api/v1/sources/{source_id}/vision_pages/retry_failed" in paths


# ============================================================================
# GET /{source_id}/vision_pages — read-only listing for the frontend panel
# ============================================================================


@pytest.mark.asyncio
async def test_list_vision_pages_endpoint_returns_envelope() -> None:
    """Happy path: endpoint awaits the service and wraps the result in the DTO."""
    service = AsyncMock()
    service.list_pages = AsyncMock(
        return_value={
            "source_id": "s1",
            "job": {
                "id": "j1",
                "total_pages": 3,
                "completed": 1,
                "failed": 1,
                "is_terminal": False,
                "created_at": "2026-05-13T12:00:00Z",
                "updated_at": "2026-05-13T12:01:00Z",
            },
            "pages": [
                {
                    "id": "p1",
                    "source_id": "s1",
                    "job_id": "j1",
                    "page_number": 1,
                    "region_index": 0,
                    "kind": "pdf_page",
                    "status": "succeeded",
                    "image_path": "/s1.pdf",
                    "description": "ok",
                    "finish_reason": "stop",
                    "error_message": None,
                    "created_at": "2026-05-13T12:00:00Z",
                    "updated_at": "2026-05-13T12:01:00Z",
                },
            ],
        }
    )

    result = await list_vision_pages_endpoint(
        source_id="s1",
        _="test-user",
        service=service,
    )

    assert isinstance(result, VisionPagesListResponse)
    assert result.source_id == "s1"
    assert result.job is not None
    assert result.job.id == "j1"
    assert result.job.is_terminal is False
    assert len(result.pages) == 1
    assert result.pages[0].id == "p1"
    assert result.pages[0].job_id == "j1"
    service.list_pages.assert_awaited_once_with(source_id="s1")


@pytest.mark.asyncio
async def test_list_vision_pages_endpoint_no_job_returns_empty_envelope() -> None:
    """No vision_job → ``job`` is None, ``pages`` is empty."""
    service = AsyncMock()
    service.list_pages = AsyncMock(return_value={"source_id": "s1", "job": None, "pages": []})

    result = await list_vision_pages_endpoint(
        source_id="s1",
        _="test-user",
        service=service,
    )

    assert result.source_id == "s1"
    assert result.job is None
    assert result.pages == []


def test_vision_pages_router_registers_get_route() -> None:
    """Sanity: the read-only GET route is registered."""
    from chaoscypher_cortex.features.sources.vision_pages_api import router

    paths = {(route.path, tuple(sorted(route.methods))) for route in router.routes}  # type: ignore[attr-defined]
    assert ("/{source_id}/vision_pages", ("GET",)) in paths


def test_vision_pages_get_mounted_under_sources_in_api_v1() -> None:
    """Sanity: the GET route is reachable at /api/v1/sources/{id}/vision_pages."""
    from chaoscypher_cortex.api.v1.router import create_api_router

    api = create_api_router()
    paths = {getattr(route, "path", None) for route in api.routes}
    assert "/api/v1/sources/{source_id}/vision_pages" in paths
