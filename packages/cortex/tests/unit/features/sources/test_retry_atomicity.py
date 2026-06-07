# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: manual retry from error_stage=commit must fetch results before reset."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.sources.service import SourceService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings() -> MagicMock:
    """Return minimal settings stub."""
    settings = MagicMock()
    settings.pagination.default_page_size = 50
    settings.pagination.max_page_size = 1000
    settings.priorities.background = 50
    return settings


def _make_service(
    *,
    adapter: MagicMock | None = None,
    engine_service: MagicMock | None = None,
) -> SourceService:
    """Return a SourceService with mock collaborators."""
    if adapter is None:
        adapter = MagicMock()
    # Ensure system-pause guard passes unless the test explicitly overrides.
    adapter.get_system_state.return_value = {"processing_paused": False}
    return SourceService(
        engine_service=engine_service or MagicMock(),
        database_name="default",
        settings=_settings(),
        storage_adapter=adapter,
    )


def _error_source(error_stage: str = "commit", **extra: Any) -> dict[str, Any]:
    """Return a minimal error-state source dict."""
    return {
        "id": "src_1",
        "database_name": "default",
        "filename": "x.txt",
        "filepath": "/tmp/x.txt",
        "file_type": "txt",
        "status": "error",
        "error_stage": error_stage,
        "error_message": "Something went wrong",
        "recovery_attempts": 1,
        "extraction_depth": "full",
        "forced_domain": None,
        "extraction_domain": None,
        "extraction_domain_auto": True,
        "is_paused": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        **extra,
    }


# ---------------------------------------------------------------------------
# Core regression: fetch before reset (audit fix H8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_commit_fetch_failure_leaves_row_in_error() -> None:
    """If get_source_commit_payload raises, reset_for_retry must NOT be called.

    Pre-fix order: reset → fetch → dispatch. If fetch fails, source is reset
    to EXTRACTED but no commit job is queued — source stuck until recovery sweep.

    Post-fix order: fetch → reset → dispatch. If fetch fails, source stays in
    ERROR and the operator can retry again cleanly.
    """
    adapter = MagicMock()
    adapter.get_source_commit_payload.side_effect = RuntimeError("db locked")

    engine = MagicMock()
    engine.get_source.return_value = _error_source(error_stage="commit")

    service = _make_service(adapter=adapter, engine_service=engine)

    with pytest.raises(RuntimeError, match="db locked"):
        await service.retry_source("src_1")

    # reset_for_retry MUST NOT have been called — the row is still ERROR.
    adapter.reset_for_retry.assert_not_called()


@pytest.mark.asyncio
async def test_retry_commit_success_calls_reset_then_dispatch() -> None:
    """Happy-path: get_source_commit_payload succeeds → reset called → commit dispatched."""
    adapter = MagicMock()
    adapter.get_source_commit_payload.return_value = {
        "entities": [{"name": "Alice"}],
        "relationships": [],
        "suggested_templates": [],
        "suggested_edge_templates": [],
        "inverse_relationships": {},
    }

    engine = MagicMock()
    engine.get_source.side_effect = [
        _error_source(error_stage="commit"),
        # Second call after reset returns updated source
        {**_error_source(error_stage="commit"), "status": "extracted", "error_stage": None},
    ]

    service = _make_service(adapter=adapter, engine_service=engine)

    captured: dict[str, Any] = {}

    async def _capture(**kwargs: Any) -> str:
        captured.update(kwargs.get("commit_data", {}))
        return "task-id"

    with (
        patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
        patch("chaoscypher_cortex.features.sources.service.event_bus"),
    ):
        mock_queue.queue_import_commit = AsyncMock(side_effect=_capture)
        await service.retry_source("src_1")

    # reset_for_retry was called
    adapter.reset_for_retry.assert_called_once_with(
        source_id="src_1",
        database_name="default",
        new_status="extracted",
        clear_commit_payload=False,
    )

    # get_source_commit_payload was called BEFORE reset (confirmed by call order)
    # and commit dispatch received the real entities
    assert captured.get("entities") == [{"name": "Alice"}]
    mock_queue.queue_import_commit.assert_called_once()


@pytest.mark.asyncio
async def test_retry_commit_none_extraction_results_still_dispatches() -> None:
    """Adapter returning None commit_payload triggers empty-fallback commit (not a raise)."""
    adapter = MagicMock()
    adapter.get_source_commit_payload.return_value = None

    engine = MagicMock()
    engine.get_source.side_effect = [
        _error_source(error_stage="commit"),
        {**_error_source(error_stage="commit"), "status": "extracted", "error_stage": None},
    ]

    service = _make_service(adapter=adapter, engine_service=engine)

    captured: dict[str, Any] = {}

    async def _capture(**kwargs: Any) -> str:
        captured.update(kwargs.get("commit_data", {}))
        return "task-id"

    with (
        patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
        patch("chaoscypher_cortex.features.sources.service.event_bus"),
    ):
        mock_queue.queue_import_commit = AsyncMock(side_effect=_capture)
        await service.retry_source("src_1")

    # Should still dispatch with empty lists (zero-entity path)
    mock_queue.queue_import_commit.assert_called_once()
    assert captured.get("entities", []) == []
    assert captured.get("relationships", []) == []


@pytest.mark.asyncio
async def test_retry_pending_does_not_call_get_extraction_results() -> None:
    """Non-commit retries (PENDING path) must NOT call get_source_commit_payload."""
    adapter = MagicMock()

    engine = MagicMock()
    engine.get_source.side_effect = [
        _error_source(error_stage="indexing"),
        {**_error_source(error_stage="indexing"), "status": "pending", "error_stage": None},
    ]

    service = _make_service(adapter=adapter, engine_service=engine)

    with (
        patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
        patch("chaoscypher_cortex.features.sources.service.event_bus"),
    ):
        mock_queue.queue_import_indexing = AsyncMock(
            return_value={"task_id": "t1", "status": "queued"}
        )
        await service.retry_source("src_1")

    adapter.get_source_commit_payload.assert_not_called()


@pytest.mark.asyncio
async def test_retry_indexed_does_not_call_get_extraction_results() -> None:
    """Extraction-stage retries (INDEXED path) must NOT call get_source_commit_payload."""
    adapter = MagicMock()

    engine = MagicMock()
    engine.get_source.side_effect = [
        _error_source(error_stage="extraction"),
        {**_error_source(error_stage="extraction"), "status": "indexed", "error_stage": None},
    ]

    service = _make_service(adapter=adapter, engine_service=engine)

    with (
        patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
        patch("chaoscypher_cortex.features.sources.service.event_bus"),
    ):
        mock_queue.queue_import_analysis = AsyncMock(return_value="t2")
        await service.retry_source("src_1")

    adapter.get_source_commit_payload.assert_not_called()
