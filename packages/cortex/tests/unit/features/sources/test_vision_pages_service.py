# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for VisionPagesService — retry orchestration logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.exceptions import (
    ConflictError,
    NotFoundError,
)
from chaoscypher_core.vision.states import VisionPageKind


def _make_page(
    page_id: str = "p1",
    page_number: int = 1,
    region_index: int = 0,
    status: str = "failed",
) -> dict:
    return {
        "id": page_id,
        "source_id": "s1",
        "job_id": "j1",
        "page_number": page_number,
        "region_index": region_index,
        "kind": VisionPageKind.PDF_PAGE.value,
        "status": status,
        "image_path": "/s1.pdf",
        "description": None,
        "finish_reason": None,
        "error_message": "some error" if status == "failed" else None,
        "created_at": "2026-05-13T12:00:00Z",
        "updated_at": "2026-05-13T12:01:00Z",
    }


@pytest.fixture
def fake_repo() -> MagicMock:
    repo = MagicMock()
    repo.get_job_by_source.return_value = {
        "id": "j1",
        "total_pages": 2,
        "completed": 0,
        "failed": 1,
        "is_terminal": False,
        "created_at": "2026-05-13T12:00:00Z",
        "updated_at": "2026-05-13T12:01:00Z",
    }
    repo.list_pages.return_value = [
        _make_page(page_id="p1", page_number=1, status="failed"),
        _make_page(page_id="p2", page_number=2, status="pending"),
    ]
    repo.reset_for_retry.return_value = True
    return repo


@pytest.fixture
def fake_queue() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def fake_source_storage() -> MagicMock:
    """Returns the parent source row used for the vision_pending gate."""
    storage = MagicMock()
    storage.get_source.return_value = {
        "id": "s1",
        "status": "vision_pending",
        "database_name": "test",
    }
    return storage


@pytest.mark.asyncio
async def test_retry_single_page_success(fake_repo, fake_queue, fake_source_storage):
    """Happy path: FAILED row, source still vision_pending → reset + enqueue."""
    from chaoscypher_cortex.features.sources.vision_pages_service import (
        VisionPagesService,
    )

    service = VisionPagesService(
        repository=fake_repo,
        source_storage=fake_source_storage,
        queue_client=fake_queue,
        database_name="test",
    )

    result = await service.retry_page(
        source_id="s1",
        page_number=1,
        region_index=0,
    )

    assert result["reset"] is True
    assert result["page_id"] == "p1"
    fake_repo.reset_for_retry.assert_called_once_with("p1")
    fake_queue.enqueue.assert_awaited_once()
    enqueue_kwargs = fake_queue.enqueue.await_args.kwargs
    assert enqueue_kwargs["operation"] == "vision_page"
    assert enqueue_kwargs["queue"] == "llm"
    assert enqueue_kwargs["data"] == {
        "page_id": "p1",
        "job_id": "j1",
        "source_id": "s1",
    }


@pytest.mark.asyncio
async def test_retry_single_page_source_not_found(fake_repo, fake_queue, fake_source_storage):
    fake_source_storage.get_source.return_value = None
    from chaoscypher_cortex.features.sources.vision_pages_service import (
        VisionPagesService,
    )

    service = VisionPagesService(
        repository=fake_repo,
        source_storage=fake_source_storage,
        queue_client=fake_queue,
        database_name="test",
    )
    with pytest.raises(NotFoundError, match="source"):
        await service.retry_page(source_id="missing", page_number=1, region_index=0)


@pytest.mark.asyncio
async def test_retry_single_page_post_finalize_refused(fake_repo, fake_queue, fake_source_storage):
    """Source state has advanced past vision_pending — retry rejected.

    Out-of-scope per the v1 retry policy: post-finalize retry deferred.
    """
    fake_source_storage.get_source.return_value = {"id": "s1", "status": "indexed"}
    from chaoscypher_cortex.features.sources.vision_pages_service import (
        VisionPagesService,
    )

    service = VisionPagesService(
        repository=fake_repo,
        source_storage=fake_source_storage,
        queue_client=fake_queue,
        database_name="test",
    )
    with pytest.raises(ConflictError, match="vision_pending"):
        await service.retry_page(source_id="s1", page_number=1, region_index=0)
    fake_repo.reset_for_retry.assert_not_called()
    fake_queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_single_page_no_vision_job(fake_repo, fake_queue, fake_source_storage):
    fake_repo.get_job_by_source.return_value = None
    from chaoscypher_cortex.features.sources.vision_pages_service import (
        VisionPagesService,
    )

    service = VisionPagesService(
        repository=fake_repo,
        source_storage=fake_source_storage,
        queue_client=fake_queue,
        database_name="test",
    )
    with pytest.raises(NotFoundError, match="vision_job"):
        await service.retry_page(source_id="s1", page_number=1, region_index=0)


@pytest.mark.asyncio
async def test_retry_single_page_not_found(fake_repo, fake_queue, fake_source_storage):
    fake_repo.list_pages.return_value = [_make_page(page_number=2)]  # only page 2
    from chaoscypher_cortex.features.sources.vision_pages_service import (
        VisionPagesService,
    )

    service = VisionPagesService(
        repository=fake_repo,
        source_storage=fake_source_storage,
        queue_client=fake_queue,
        database_name="test",
    )
    with pytest.raises(NotFoundError, match="page"):
        await service.retry_page(source_id="s1", page_number=99, region_index=0)


@pytest.mark.asyncio
async def test_retry_single_page_already_pending_no_op(fake_repo, fake_queue, fake_source_storage):
    """Page is already PENDING — reset returns False, no enqueue."""
    fake_repo.list_pages.return_value = [_make_page(page_id="p1", page_number=1, status="pending")]
    fake_repo.reset_for_retry.return_value = False
    from chaoscypher_cortex.features.sources.vision_pages_service import (
        VisionPagesService,
    )

    service = VisionPagesService(
        repository=fake_repo,
        source_storage=fake_source_storage,
        queue_client=fake_queue,
        database_name="test",
    )
    result = await service.retry_page(source_id="s1", page_number=1, region_index=0)

    assert result["reset"] is False
    assert result["status"] == "pending"
    # No enqueue when already PENDING — the row is already pending; a queue
    # task may already be in flight via the recovery scanner.
    fake_queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_failed_batch_success(fake_repo, fake_queue, fake_source_storage):
    """Three pages: one FAILED + one TRUNCATED + one PENDING → only FAILED retried."""
    fake_repo.list_pages.return_value = [
        _make_page(page_id="p1", page_number=1, status="failed"),
        _make_page(page_id="p2", page_number=2, status="truncated"),
        _make_page(page_id="p3", page_number=3, status="pending"),
    ]
    fake_repo.reset_for_retry.return_value = True
    from chaoscypher_cortex.features.sources.vision_pages_service import (
        VisionPagesService,
    )

    service = VisionPagesService(
        repository=fake_repo,
        source_storage=fake_source_storage,
        queue_client=fake_queue,
        database_name="test",
    )
    result = await service.retry_failed(source_id="s1")

    assert result["retried_count"] == 1
    assert result["page_ids"] == ["p1"]
    # Skipped: 1 TRUNCATED + 1 PENDING = 2.
    assert result["skipped_count"] == 2
    assert fake_queue.enqueue.await_count == 1


@pytest.mark.asyncio
async def test_retry_failed_batch_no_pages(fake_repo, fake_queue, fake_source_storage):
    """No FAILED pages → no resets, no enqueues, zero counts."""
    fake_repo.list_pages.return_value = [
        _make_page(page_id="p1", page_number=1, status="succeeded"),
    ]
    from chaoscypher_cortex.features.sources.vision_pages_service import (
        VisionPagesService,
    )

    service = VisionPagesService(
        repository=fake_repo,
        source_storage=fake_source_storage,
        queue_client=fake_queue,
        database_name="test",
    )
    result = await service.retry_failed(source_id="s1")

    assert result["retried_count"] == 0
    assert result["skipped_count"] == 1  # The SUCCEEDED page.
    fake_repo.reset_for_retry.assert_not_called()
    fake_queue.enqueue.assert_not_awaited()


# ============================================================================
# list_pages — read-only listing for the frontend panel
# ============================================================================


def _make_storage_job(
    *,
    job_id: str = "j1",
    total_pages: int = 2,
    completed: int = 1,
    failed: int = 0,
) -> dict:
    """Mimic the storage VisionJob TypedDict (no ``is_terminal``)."""
    return {
        "id": job_id,
        "source_id": "s1",
        "total_pages": total_pages,
        "completed": completed,
        "failed": failed,
        "created_at": "2026-05-13T12:00:00Z",
        "updated_at": "2026-05-13T12:01:00Z",
    }


def _make_storage_page(
    *,
    page_id: str = "p1",
    page_number: int = 1,
    region_index: int = 0,
    status: str = "succeeded",
) -> dict:
    """Mimic the storage VisionPageDescription TypedDict.

    Note: storage uses ``vision_job_id`` (service must rename to
    ``job_id`` for the DTO) and includes ``attempts`` (which the
    service must drop).
    """
    return {
        "id": page_id,
        "source_id": "s1",
        "vision_job_id": "j1",
        "page_number": page_number,
        "region_index": region_index,
        "kind": VisionPageKind.PDF_PAGE.value,
        "status": status,
        "description": "ok" if status == "succeeded" else None,
        "image_path": "/s1.pdf",
        "finish_reason": "stop" if status == "succeeded" else None,
        "error_message": None,
        "attempts": 1,
        "created_at": "2026-05-13T12:00:00Z",
        "updated_at": "2026-05-13T12:01:00Z",
    }


@pytest.mark.asyncio
async def test_list_pages_success(fake_repo, fake_queue, fake_source_storage):
    """Happy path: returns job summary + pages, mapping storage → DTO shape."""
    fake_repo.get_job_by_source.return_value = _make_storage_job(
        total_pages=3, completed=1, failed=1
    )
    fake_repo.list_pages.return_value = [
        _make_storage_page(page_id="p1", page_number=1, status="succeeded"),
        _make_storage_page(page_id="p2", page_number=2, status="failed"),
    ]
    from chaoscypher_cortex.features.sources.vision_pages_service import (
        VisionPagesService,
    )

    service = VisionPagesService(
        repository=fake_repo,
        source_storage=fake_source_storage,
        queue_client=fake_queue,
        database_name="test",
    )
    result = await service.list_pages(source_id="s1")

    assert result["source_id"] == "s1"
    assert result["job"] is not None
    assert result["job"]["id"] == "j1"
    assert result["job"]["total_pages"] == 3
    assert result["job"]["completed"] == 1
    assert result["job"]["failed"] == 1
    # is_terminal is (1 + 1) >= 3 → False.
    assert result["job"]["is_terminal"] is False
    # Storage's ``source_id`` is intentionally NOT in the job dict.
    assert "source_id" not in result["job"]

    assert len(result["pages"]) == 2
    page = result["pages"][0]
    # Field-mapping: storage ``vision_job_id`` → DTO ``job_id``.
    assert page["job_id"] == "j1"
    assert "vision_job_id" not in page
    # Storage-only ``attempts`` is stripped.
    assert "attempts" not in page


@pytest.mark.asyncio
async def test_list_pages_is_terminal_true(fake_repo, fake_queue, fake_source_storage):
    """``is_terminal`` is computed: completed + failed >= total_pages."""
    fake_repo.get_job_by_source.return_value = _make_storage_job(
        total_pages=2, completed=2, failed=0
    )
    fake_repo.list_pages.return_value = []
    from chaoscypher_cortex.features.sources.vision_pages_service import (
        VisionPagesService,
    )

    service = VisionPagesService(
        repository=fake_repo,
        source_storage=fake_source_storage,
        queue_client=fake_queue,
        database_name="test",
    )
    result = await service.list_pages(source_id="s1")

    assert result["job"]["is_terminal"] is True


@pytest.mark.asyncio
async def test_list_pages_source_not_found(fake_repo, fake_queue, fake_source_storage):
    """Missing source → NotFoundError."""
    fake_source_storage.get_source.return_value = None
    from chaoscypher_cortex.features.sources.vision_pages_service import (
        VisionPagesService,
    )

    service = VisionPagesService(
        repository=fake_repo,
        source_storage=fake_source_storage,
        queue_client=fake_queue,
        database_name="test",
    )
    with pytest.raises(NotFoundError, match="source"):
        await service.list_pages(source_id="missing")


@pytest.mark.asyncio
async def test_list_pages_no_vision_job(fake_repo, fake_queue, fake_source_storage):
    """No vision_job exists → job=None, pages=[] (text-only source)."""
    fake_repo.get_job_by_source.return_value = None
    fake_repo.list_pages.return_value = []
    from chaoscypher_cortex.features.sources.vision_pages_service import (
        VisionPagesService,
    )

    service = VisionPagesService(
        repository=fake_repo,
        source_storage=fake_source_storage,
        queue_client=fake_queue,
        database_name="test",
    )
    result = await service.list_pages(source_id="s1")

    assert result["source_id"] == "s1"
    assert result["job"] is None
    assert result["pages"] == []


@pytest.mark.asyncio
async def test_list_pages_works_post_finalize(fake_repo, fake_queue, fake_source_storage):
    """Source state is irrelevant — read-only has no vision_pending gate."""
    fake_source_storage.get_source.return_value = {
        "id": "s1",
        "status": "indexed",
        "database_name": "test",
    }
    fake_repo.get_job_by_source.return_value = _make_storage_job(
        total_pages=1, completed=1, failed=0
    )
    fake_repo.list_pages.return_value = [
        _make_storage_page(page_id="p1", page_number=1, status="succeeded"),
    ]
    from chaoscypher_cortex.features.sources.vision_pages_service import (
        VisionPagesService,
    )

    service = VisionPagesService(
        repository=fake_repo,
        source_storage=fake_source_storage,
        queue_client=fake_queue,
        database_name="test",
    )
    # Must NOT raise ConflictError — read-only never refuses on state.
    result = await service.list_pages(source_id="s1")
    assert result["source_id"] == "s1"
    assert result["job"]["is_terminal"] is True
    assert len(result["pages"]) == 1
