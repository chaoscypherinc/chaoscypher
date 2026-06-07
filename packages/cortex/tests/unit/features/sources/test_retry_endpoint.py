# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for POST /api/v1/sources/{id}/retry endpoint (Cluster D1).

Verifies HTTP contract and state-transition logic. Queue dispatch and
event emission are mocked — no real worker or queue connection required.
"""

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
    settings.pagination.extraction_tasks_page_size = 25
    settings.batching.template_name_cache_size = 100
    settings.priorities.background = 50
    settings.data_dir = "/tmp/cc-data"
    return settings


def _make_service(
    *,
    adapter: MagicMock | None = None,
    engine_service: MagicMock | None = None,
    database_name: str = "default",
) -> SourceService:
    """Return a SourceService with mock collaborators."""
    if adapter is None:
        adapter = MagicMock()
    # Ensure system-pause guard passes unless the test explicitly overrides.
    adapter.get_system_state.return_value = {"processing_paused": False}
    return SourceService(
        engine_service=engine_service or MagicMock(),
        database_name=database_name,
        settings=_settings(),
        storage_adapter=adapter,
    )


def _source_dict(
    source_id: str = "src-1",
    status: str = "error",
    error_stage: str | None = "commit",
    error_message: str | None = "Something went wrong",
    recovery_attempts: int = 2,
    **extra: Any,
) -> dict[str, Any]:
    """Return a minimal source dict resembling engine service output."""
    return {
        "id": source_id,
        "database_name": "default",
        "filename": "doc.pdf",
        "filepath": "/data/uploads/doc.pdf",
        "file_type": "pdf",
        "status": status,
        "error_stage": error_stage,
        "error_message": error_message,
        "recovery_attempts": recovery_attempts,
        "extraction_depth": "full",
        "is_paused": False,
        "extraction_results": {
            "entities": [{"name": "Alice"}],
            "relationships": [],
            "suggested_templates": [],
            "suggested_edge_templates": [],
            "inverse_relationships": {},
        },
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        **extra,
    }


# ---------------------------------------------------------------------------
# State-transition routing tests (unit — no HTTP layer)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRetrySourceService:
    """Unit tests for SourceService.retry_source state-transition logic."""

    @pytest.mark.asyncio
    async def test_commit_error_resets_to_extracted(self) -> None:
        """error_stage=commit routes to status=extracted."""
        adapter = MagicMock()
        engine = MagicMock()
        engine.get_source.return_value = _source_dict(status="error", error_stage="commit")
        # After reset, get_source returns the updated state
        engine.get_source.side_effect = [
            _source_dict(status="error", error_stage="commit"),
            _source_dict(
                status="extracted",
                error_stage=None,
                error_message=None,
                recovery_attempts=0,
            ),
        ]
        service = _make_service(adapter=adapter, engine_service=engine)

        with (
            patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
            patch("chaoscypher_cortex.features.sources.service.event_bus") as mock_bus,
        ):
            mock_queue.queue_import_indexing = AsyncMock(
                return_value={"task_id": "t1", "status": "queued"}
            )
            mock_queue.queue_import_analysis = AsyncMock(return_value="t2")
            mock_queue.queue_import_commit = AsyncMock(return_value="t3")
            result = await service.retry_source("src-1")

        adapter.reset_for_retry.assert_called_once_with(
            source_id="src-1",
            database_name="default",
            new_status="extracted",
            clear_commit_payload=False,
        )
        mock_queue.queue_import_commit.assert_called_once()
        mock_bus.emit.assert_called_once_with(
            "source_retry_requested",
            action="Manual retry: src-1 → extracted",
            source="user",
            details={
                "source_id": "src-1",
                "prior_error_stage": "commit",
                "new_status": "extracted",
            },
            database_name="default",
        )
        assert result.status == "extracted"

    @pytest.mark.asyncio
    async def test_extraction_error_resets_to_indexed(self) -> None:
        """error_stage=extraction routes to status=indexed."""
        adapter = MagicMock()
        engine = MagicMock()
        engine.get_source.side_effect = [
            _source_dict(status="error", error_stage="extraction"),
            _source_dict(
                status="indexed",
                error_stage=None,
                error_message=None,
                recovery_attempts=0,
            ),
        ]
        service = _make_service(adapter=adapter, engine_service=engine)

        with (
            patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
            patch("chaoscypher_cortex.features.sources.service.event_bus"),
        ):
            mock_queue.queue_import_analysis = AsyncMock(return_value="t2")
            result = await service.retry_source("src-1")

        adapter.reset_for_retry.assert_called_once_with(
            source_id="src-1",
            database_name="default",
            new_status="indexed",
            clear_commit_payload=True,
        )
        mock_queue.queue_import_analysis.assert_called_once()
        assert result.status == "indexed"

    @pytest.mark.asyncio
    async def test_indexing_error_resets_to_pending(self) -> None:
        """error_stage=indexing routes to status=pending."""
        adapter = MagicMock()
        engine = MagicMock()
        engine.get_source.side_effect = [
            _source_dict(status="error", error_stage="indexing"),
            _source_dict(
                status="pending",
                error_stage=None,
                error_message=None,
                recovery_attempts=0,
            ),
        ]
        service = _make_service(adapter=adapter, engine_service=engine)

        with (
            patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
            patch("chaoscypher_cortex.features.sources.service.event_bus"),
        ):
            mock_queue.queue_import_indexing = AsyncMock(
                return_value={"task_id": "t1", "status": "queued"}
            )
            result = await service.retry_source("src-1")

        adapter.reset_for_retry.assert_called_once_with(
            source_id="src-1",
            database_name="default",
            new_status="pending",
            clear_commit_payload=True,
        )
        mock_queue.queue_import_indexing.assert_called_once()
        assert result.status == "pending"

    @pytest.mark.asyncio
    async def test_recovery_exhausted_resets_to_pending(self) -> None:
        """error_stage=recovery_exhausted falls through to status=pending."""
        adapter = MagicMock()
        engine = MagicMock()
        engine.get_source.side_effect = [
            _source_dict(status="error", error_stage="recovery_exhausted"),
            _source_dict(
                status="pending",
                error_stage=None,
                error_message=None,
                recovery_attempts=0,
            ),
        ]
        service = _make_service(adapter=adapter, engine_service=engine)

        with (
            patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
            patch("chaoscypher_cortex.features.sources.service.event_bus"),
        ):
            mock_queue.queue_import_indexing = AsyncMock(
                return_value={"task_id": "t1", "status": "queued"}
            )
            result = await service.retry_source("src-1")

        adapter.reset_for_retry.assert_called_once_with(
            source_id="src-1",
            database_name="default",
            new_status="pending",
            clear_commit_payload=True,
        )
        assert result.status == "pending"

    @pytest.mark.asyncio
    async def test_none_error_stage_resets_to_pending(self) -> None:
        """error_stage=None (unknown) falls through to status=pending."""
        adapter = MagicMock()
        engine = MagicMock()
        engine.get_source.side_effect = [
            _source_dict(status="error", error_stage=None),
            _source_dict(
                status="pending",
                error_stage=None,
                error_message=None,
                recovery_attempts=0,
            ),
        ]
        service = _make_service(adapter=adapter, engine_service=engine)

        with (
            patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
            patch("chaoscypher_cortex.features.sources.service.event_bus"),
        ):
            mock_queue.queue_import_indexing = AsyncMock(
                return_value={"task_id": "t1", "status": "queued"}
            )
            await service.retry_source("src-1")

        adapter.reset_for_retry.assert_called_once_with(
            source_id="src-1",
            database_name="default",
            new_status="pending",
            clear_commit_payload=True,
        )

    @pytest.mark.asyncio
    async def test_non_error_source_raises_409(self) -> None:
        """retry_source raises Core ConflictError for non-error sources.

        Cortex services no longer raise ``HTTPException``; they raise the
        Core exception types and the API layer maps them to HTTP responses.
        """
        from chaoscypher_core.exceptions import ConflictError

        engine = MagicMock()
        engine.get_source.return_value = _source_dict(
            status="committed", error_stage=None, error_message=None
        )
        service = _make_service(engine_service=engine)

        with pytest.raises(ConflictError) as exc_info:
            await service.retry_source("src-1")

        assert "error state" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_missing_source_raises_404(self) -> None:
        """retry_source raises Core NotFoundError when source not found.

        Cortex services no longer raise ``HTTPException``; they raise the
        Core exception types and the API layer maps them to HTTP responses.
        """
        from chaoscypher_core.exceptions import NotFoundError

        engine = MagicMock()
        engine.get_source.return_value = None
        service = _make_service(engine_service=engine)

        with pytest.raises(NotFoundError):
            await service.retry_source("nonexistent")

    @pytest.mark.asyncio
    async def test_retry_from_commit_error_preserves_extraction_results(self) -> None:
        """Commit retry must preserve the prefetched commit payload — not silently empty it.

        Regression test for a bug where _dispatch_retry_task fetched source
        via the light-column path (no commit_payload) and then dispatched
        a commit with an empty entities list.
        """
        adapter = MagicMock()
        engine = MagicMock()
        engine.get_source.side_effect = [
            _source_dict(status="error", error_stage="commit"),
            _source_dict(
                status="extracted",
                error_stage=None,
                error_message=None,
                recovery_attempts=0,
            ),
        ]
        # Simulate adapter returning real commit payload from the
        # dedicated commit_payload column (migration 0042 retired the
        # extraction_results JSON column; commit_payload is the
        # canonical store for commit input).
        adapter.get_source_commit_payload.return_value = {
            "entities": [{"name": "Alice"}, {"name": "Bob"}],
            "relationships": [{"source": "Alice", "target": "Bob", "type": "KNOWS"}],
            "suggested_templates": [],
            "suggested_edge_templates": [],
            "inverse_relationships": {},
        }

        service = _make_service(adapter=adapter, engine_service=engine)

        captured_commit_data: dict = {}

        async def _capture_commit(**kwargs: Any) -> str:
            captured_commit_data.update(kwargs.get("commit_data", {}))
            return "task-commit-id"

        with (
            patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
            patch("chaoscypher_cortex.features.sources.service.event_bus"),
        ):
            mock_queue.queue_import_commit = AsyncMock(side_effect=_capture_commit)
            await service.retry_source("src-1")

        # Verify the adapter commit-payload reader was called (not the
        # light source path).
        adapter.get_source_commit_payload.assert_called_once_with("src-1", "default")

        # Verify the commit task received the real entities, not an empty list
        assert len(captured_commit_data.get("entities", [])) == 2
        assert captured_commit_data["entities"][0]["name"] == "Alice"
        assert len(captured_commit_data.get("relationships", [])) == 1

    @pytest.mark.asyncio
    async def test_retry_commit_missing_extraction_results_falls_back_to_empty_lists(
        self,
    ) -> None:
        """Commit retry with no stored commit payload falls back to empty lists.

        Edge case: the source had error_stage=commit but commit_payload was
        never persisted (or was cleared).  The retry should not raise — it falls
        back to empty lists and the Cluster B+E zero-entity commit path handles it.
        """
        adapter = MagicMock()
        engine = MagicMock()
        engine.get_source.side_effect = [
            _source_dict(status="error", error_stage="commit"),
            _source_dict(
                status="extracted",
                error_stage=None,
                error_message=None,
                recovery_attempts=0,
            ),
        ]
        # Adapter returns None commit_payload (never stored).
        adapter.get_source_commit_payload.return_value = None

        service = _make_service(adapter=adapter, engine_service=engine)

        captured_commit_data: dict = {}

        async def _capture_commit(**kwargs: Any) -> str:
            captured_commit_data.update(kwargs.get("commit_data", {}))
            return "task-commit-id"

        with (
            patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
            patch("chaoscypher_cortex.features.sources.service.event_bus"),
        ):
            mock_queue.queue_import_commit = AsyncMock(side_effect=_capture_commit)
            await service.retry_source("src-1")

        # Should still dispatch a commit (with empty lists — zero-entity path)
        mock_queue.queue_import_commit.assert_called_once()
        assert captured_commit_data.get("entities", []) == []
        assert captured_commit_data.get("relationships", []) == []

    @pytest.mark.asyncio
    async def test_event_emitted_with_correct_payload(self) -> None:
        """retry_source emits source_retry_requested with correct fields."""
        adapter = MagicMock()
        engine = MagicMock()
        engine.get_source.side_effect = [
            _source_dict(status="error", error_stage="extraction"),
            _source_dict(
                status="indexed",
                error_stage=None,
                error_message=None,
                recovery_attempts=0,
            ),
        ]
        service = _make_service(adapter=adapter, engine_service=engine)

        with (
            patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
            patch("chaoscypher_cortex.features.sources.service.event_bus") as mock_bus,
        ):
            mock_queue.queue_import_analysis = AsyncMock(return_value="t2")
            await service.retry_source("src-1")

        mock_bus.emit.assert_called_once_with(
            "source_retry_requested",
            action="Manual retry: src-1 → indexed",
            source="user",
            details={
                "source_id": "src-1",
                "prior_error_stage": "extraction",
                "new_status": "indexed",
            },
            database_name="default",
        )
