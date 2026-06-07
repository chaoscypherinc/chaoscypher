# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for OP_VISION_PAGE handler (VisionOperationsService._handle_vision_page)."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.services.llm.spend import _reset_tracker_for_tests, _utc_today
from chaoscypher_core.services.vision.service import VisionResult
from chaoscypher_core.utils.id import generate_id
from chaoscypher_core.vision.states import VisionPageKind, VisionPageStatus


@pytest.fixture(autouse=True)
def _fresh_spend_tracker() -> Generator[None]:
    """Each test gets a clean process-wide spend tracker."""
    _reset_tracker_for_tests()
    yield
    _reset_tracker_for_tests()


def _vision_settings(tmp_path: Path) -> MagicMock:
    """Build settings for the vision handler with spend caps disabled (default)."""
    settings = MagicMock()
    settings.llm.chat_provider = "ollama"
    settings.llm.ollama_vision_max_output_tokens = 8192
    settings.llm.vision_image_dpi = 150
    settings.llm.max_tokens_per_source = None
    settings.llm.max_tokens_per_day = None
    settings.paths.data_dir = str(tmp_path)
    return settings


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Real SQLite adapter on a tmp DB."""
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    try:
        yield a
    finally:
        a.disconnect()


def _create_source_with_pending_page(
    adapter: SqliteAdapter, image_path: Path
) -> tuple[str, str, str]:
    """Create a source + vision_job + 1 pending page row.

    Returns (source_id, job_id, page_id).
    """
    source_id = generate_id("src")
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "test",
            "filename": "test.pdf",
            "filepath": str(image_path),
            "status": "vision_pending",
        }
    )
    job_id = adapter.create_vision_job_with_pages(
        source_id=source_id,
        pages=[
            {
                "page_number": 1,
                "kind": VisionPageKind.STANDALONE_IMAGE,
                "image_path": str(image_path),
            }
        ],
    )
    page_id = adapter.list_vision_page_descriptions(source_id)[0]["id"]
    return source_id, job_id, page_id


def _make_service(adapter: SqliteAdapter, settings, vision_service=None):
    """Construct a VisionOperationsService with test dependencies injected."""
    from chaoscypher_core.operations.importing.vision_operations_service import (
        VisionOperationsService,
    )

    return VisionOperationsService(
        adapter=adapter,
        settings=settings,
        database_name="test",
        vision_service=vision_service,
    )


@pytest.mark.asyncio
async def test_vision_page_handler_succeeded_writes_row_and_bumps_counter(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")  # Fake but non-empty.
    source_id, job_id, page_id = _create_source_with_pending_page(adapter, img)

    fake_vision = MagicMock()
    fake_vision.describe_image = AsyncMock(
        return_value=VisionResult(description="A cat sitting on a mat.", finish_reason="stop")
    )
    settings = _vision_settings(tmp_path)

    service = _make_service(adapter, settings, vision_service=fake_vision)

    # Single-page job is terminal after this handler — mock the queue so the
    # _enqueue_finalize call doesn't require a live Valkey connection.
    with patch(
        "chaoscypher_core.operations.importing.vision_operations_service._enqueue_finalize",
        new_callable=AsyncMock,
    ):
        result = await service._handle_vision_page(
            data={"page_id": page_id, "job_id": job_id, "source_id": source_id},
            metadata={},
            task_id="test-task",
        )

    assert result["status"] == "success"
    row = adapter.list_vision_page_descriptions(source_id)[0]
    assert row["status"] == VisionPageStatus.SUCCEEDED
    assert row["description"] == "A cat sitting on a mat."
    assert row["finish_reason"] == "stop"

    job = adapter.get_vision_job(job_id)
    assert job is not None
    assert job["completed"] == 1
    # Single-page job → terminal after this one increment.
    assert result["is_terminal"] is True


@pytest.mark.asyncio
async def test_vision_page_handler_truncated_bumps_quality_counter(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG-fake")
    source_id, job_id, page_id = _create_source_with_pending_page(adapter, img)

    fake_vision = MagicMock()
    fake_vision.describe_image = AsyncMock(
        return_value=VisionResult(description="partial transcription...", finish_reason="length")
    )
    settings = _vision_settings(tmp_path)

    service = _make_service(adapter, settings, vision_service=fake_vision)

    # Single-page job is terminal after this handler — mock the queue.
    with patch(
        "chaoscypher_core.operations.importing.vision_operations_service._enqueue_finalize",
        new_callable=AsyncMock,
    ):
        result = await service._handle_vision_page(
            data={"page_id": page_id, "job_id": job_id, "source_id": source_id},
            metadata={},
            task_id="test-task",
        )

    assert result["status"] == "success"
    row = adapter.list_vision_page_descriptions(source_id)[0]
    assert row["status"] == VisionPageStatus.TRUNCATED
    assert row["description"] == "partial transcription..."
    assert row["finish_reason"] == "length"

    # VISION_PAGES_TRUNCATED counter must have been bumped.
    src = adapter.get_source(source_id, "test")
    assert src["vision_pages_truncated"] == 1


@pytest.mark.asyncio
async def test_vision_page_handler_failed_bumps_failed_counter(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG-fake")
    source_id, job_id, page_id = _create_source_with_pending_page(adapter, img)

    fake_vision = MagicMock()
    fake_vision.describe_image = AsyncMock(
        return_value=VisionResult(description=None, finish_reason=None)
    )
    settings = _vision_settings(tmp_path)

    service = _make_service(adapter, settings, vision_service=fake_vision)

    # Single-page job is terminal after this handler — mock the queue.
    with patch(
        "chaoscypher_core.operations.importing.vision_operations_service._enqueue_finalize",
        new_callable=AsyncMock,
    ):
        result = await service._handle_vision_page(
            data={"page_id": page_id, "job_id": job_id, "source_id": source_id},
            metadata={},
            task_id="test-task",
        )

    assert result["status"] == "success"
    row = adapter.list_vision_page_descriptions(source_id)[0]
    assert row["status"] == VisionPageStatus.FAILED
    job = adapter.get_vision_job(job_id)
    assert job is not None
    assert job["failed"] == 1


@pytest.mark.asyncio
async def test_vision_page_handler_stale_dispatch_bails_gracefully(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """If the row's status moved from PENDING (e.g. manual retry reset),
    the handler must bail without bumping the counter.
    """
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG-fake")
    source_id, job_id, page_id = _create_source_with_pending_page(adapter, img)

    # Pre-mark the row succeeded (simulates a concurrent retry resetting + re-running).
    adapter.update_vision_page_description(
        page_id=page_id,
        new_status=VisionPageStatus.SUCCEEDED,
        description="winner",
        finish_reason="stop",
        error_message=None,
    )
    adapter.increment_vision_job_completed_and_check(
        job_id=job_id, outcome=VisionPageStatus.SUCCEEDED
    )

    fake_vision = MagicMock()
    fake_vision.describe_image = AsyncMock(
        return_value=MagicMock(description="stale", finish_reason="stop")
    )
    settings = MagicMock()
    settings.llm.chat_provider = "ollama"
    settings.llm.ollama_vision_max_output_tokens = 8192

    service = _make_service(adapter, settings, vision_service=fake_vision)

    result = await service._handle_vision_page(
        data={"page_id": page_id, "job_id": job_id, "source_id": source_id},
        metadata={},
        task_id="test-task",
    )
    assert result["status"] == "skipped_stale"

    row = adapter.list_vision_page_descriptions(source_id)[0]
    assert row["description"] == "winner"  # Not overwritten by stale dispatch.
    job = adapter.get_vision_job(job_id)
    assert job is not None
    assert job["completed"] == 1  # Counter not double-bumped.


@pytest.mark.asyncio
async def test_vision_page_handler_no_model_bumps_counter(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """When no vision model is configured, page is FAILED and job counter bumps.

    Regression test for the no_model stall bug: prior to the fix, the handler
    wrote FAILED for the page but never called
    increment_vision_job_completed_and_check, leaving jobs with no vision
    model configured in vision_pending indefinitely.
    """
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG-fake")
    source_id, job_id, page_id = _create_source_with_pending_page(adapter, img)

    settings = MagicMock()
    settings.llm.chat_provider = "ollama"
    settings.paths.data_dir = str(tmp_path)
    # No vision model configured for this provider.
    settings.llm.ollama_vision_model = None

    # No vision_service override — forces the no_model path.
    service = _make_service(adapter, settings, vision_service=None)

    with patch(
        "chaoscypher_core.operations.importing.vision_operations_service._enqueue_finalize",
        new_callable=AsyncMock,
    ) as mock_finalize:
        result = await service._handle_vision_page(
            data={"page_id": page_id, "job_id": job_id, "source_id": source_id},
            metadata={},
            task_id="test-task",
        )

    assert result["status"] == "no_model"

    # Page row must be FAILED.
    row = adapter.list_vision_page_descriptions(source_id)[0]
    assert row["status"] == VisionPageStatus.FAILED
    assert row["error_message"] == "no vision_model configured for provider"

    # Job counter must have been bumped (single-page job → terminal).
    job = adapter.get_vision_job(job_id)
    assert job is not None
    assert job["failed"] == 1

    # Single-page job is terminal → finalize must have been enqueued.
    mock_finalize.assert_awaited_once()


@pytest.mark.asyncio
async def test_vision_page_handler_records_spend(adapter: SqliteAdapter, tmp_path: Path) -> None:
    """A successful vision page records its token usage against the daily cap."""
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG-fake")
    source_id, job_id, page_id = _create_source_with_pending_page(adapter, img)

    fake_vision = MagicMock()
    fake_vision.describe_image = AsyncMock(
        return_value=VisionResult(
            description="A diagram.",
            finish_reason="stop",
            input_tokens=70,
            output_tokens=30,
        )
    )
    settings = _vision_settings(tmp_path)
    service = _make_service(adapter, settings, vision_service=fake_vision)

    with patch(
        "chaoscypher_core.operations.importing.vision_operations_service._enqueue_finalize",
        new_callable=AsyncMock,
    ):
        await service._handle_vision_page(
            data={"page_id": page_id, "job_id": job_id, "source_id": source_id},
            metadata={},
            task_id="test-task",
        )

    assert adapter.get_daily_token_spend(database_name="test", spend_date=_utc_today()) == 100


@pytest.mark.asyncio
async def test_vision_page_handler_spend_cap_marks_failed_without_calling_llm(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """When the daily cap is already exceeded, the page is marked FAILED and the
    billable vision LLM call is never made (permanent failure, no retry).
    """
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG-fake")
    source_id, job_id, page_id = _create_source_with_pending_page(adapter, img)

    # Pre-load the persisted daily spend above the cap.
    adapter.add_daily_token_spend(database_name="test", spend_date=_utc_today(), tokens=10_000)

    fake_vision = MagicMock()
    fake_vision.describe_image = AsyncMock(
        return_value=VisionResult(description="x", finish_reason="stop")
    )
    settings = _vision_settings(tmp_path)
    settings.llm.max_tokens_per_day = 5_000

    service = _make_service(adapter, settings, vision_service=fake_vision)

    with patch(
        "chaoscypher_core.operations.importing.vision_operations_service._enqueue_finalize",
        new_callable=AsyncMock,
    ):
        result = await service._handle_vision_page(
            data={"page_id": page_id, "job_id": job_id, "source_id": source_id},
            metadata={},
            task_id="test-task",
        )

    assert result["status"] == "spend_cap_exceeded"
    fake_vision.describe_image.assert_not_called()
    row = adapter.list_vision_page_descriptions(source_id)[0]
    assert row["status"] == VisionPageStatus.FAILED


@pytest.mark.asyncio
async def test_vision_page_handler_persists_page_image_to_disk(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """After a successful _handle_vision_page, the rendered PNG must land
    at the canonical ``vision_images_dir(...)/page_{N}.png`` path so the
    Cortex ``GET /sources/{id}/images`` endpoint can serve it.

    Regression for the 2026-05-13 ephemeral-render rewrite (PR #69), which
    silently broke the UI contract: ``VisionPagesGrid`` and ``ChunkCitation``
    both expect ``page_{N}.png`` files at this path.
    """
    from chaoscypher_core.operations.importing.indexing_handler import vision_images_dir

    img = tmp_path / "p.png"
    expected_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    img.write_bytes(expected_bytes)
    source_id, job_id, page_id = _create_source_with_pending_page(adapter, img)

    fake_vision = MagicMock()
    fake_vision.describe_image = AsyncMock(
        return_value=VisionResult(description="A cat sitting on a mat.", finish_reason="stop")
    )
    settings = _vision_settings(tmp_path)

    service = _make_service(adapter, settings, vision_service=fake_vision)

    with patch(
        "chaoscypher_core.operations.importing.vision_operations_service._enqueue_finalize",
        new_callable=AsyncMock,
    ):
        result = await service._handle_vision_page(
            data={"page_id": page_id, "job_id": job_id, "source_id": source_id},
            metadata={},
            task_id="test-task",
        )

    assert result["status"] == "success"

    expected_path = (
        vision_images_dir(
            data_dir=str(tmp_path),
            database_name="test",
            source_id=source_id,
        )
        / "page_1.png"
    )
    assert expected_path.exists(), f"expected {expected_path} to exist"
    assert expected_path.read_bytes() == expected_bytes


@pytest.mark.asyncio
async def test_vision_page_handler_render_failed_bumps_counter(
    adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """When rendering fails, page is FAILED and job counter bumps.

    Regression coverage: the render_failed path already bumped the counter
    in the original implementation, but this test explicitly verifies both
    the counter bump and the finalize enqueue for a single-page (terminal) job.
    """
    # Non-existent image to trigger a render failure (read_bytes raises).
    img_missing = tmp_path / "missing.png"

    source_id = generate_id("src")
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "test",
            "filename": "test.pdf",
            "filepath": str(img_missing),
            "status": "vision_pending",
        }
    )
    job_id = adapter.create_vision_job_with_pages(
        source_id=source_id,
        pages=[
            {
                "page_number": 1,
                "kind": VisionPageKind.STANDALONE_IMAGE,
                "image_path": str(img_missing),
            }
        ],
    )
    page_id = adapter.list_vision_page_descriptions(source_id)[0]["id"]

    settings = MagicMock()
    settings.llm.chat_provider = "ollama"
    settings.llm.ollama_vision_max_output_tokens = 8192

    service = _make_service(adapter, settings)

    with patch(
        "chaoscypher_core.operations.importing.vision_operations_service._enqueue_finalize",
        new_callable=AsyncMock,
    ) as mock_finalize:
        result = await service._handle_vision_page(
            data={"page_id": page_id, "job_id": job_id, "source_id": source_id},
            metadata={},
            task_id="test-task",
        )

    assert result["status"] == "render_failed"

    # Page row must be FAILED.
    row = adapter.list_vision_page_descriptions(source_id)[0]
    assert row["status"] == VisionPageStatus.FAILED

    # Job counter must have been bumped (single-page job → terminal).
    job = adapter.get_vision_job(job_id)
    assert job is not None
    assert job["failed"] == 1

    # Single-page job is terminal → finalize must have been enqueued.
    mock_finalize.assert_awaited_once()
