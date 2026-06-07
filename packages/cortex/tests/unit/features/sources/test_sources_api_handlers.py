# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Handler-level tests for the Sources API routes.

FastAPI DI is bypassed — each async route function is called directly with
a MagicMock service and ``_="test-user"``. Verifies delegation, response
model construction, and the 404 / 400 branches. Mirrors the handler-level
pattern in tests/unit/features/nodes/test_node_handlers.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from chaoscypher_cortex.features.sources.api import (
    cleanup_orphan_tasks,
    delete_source,
    get_processing_stats,
    get_source,
    get_source_tags,
    import_url,
    list_source_recovery_events,
    list_sources,
    reextract_source,
    retry_source,
    unassign_tag_from_source,
    update_source,
)
from chaoscypher_cortex.features.sources.models import (
    PaginatedSourcesResponse,
    SourceResponse,
    UrlImportRequest,
)


_NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings() -> MagicMock:
    """Return a minimal settings mock."""
    settings = MagicMock()
    settings.current_database = "default"
    settings.priorities.background = 50
    return settings


def _source_response(source_id: str = "src-1") -> SourceResponse:
    """Return a minimal valid SourceResponse instance."""
    return SourceResponse(
        id=source_id,
        database_name="default",
        filename="doc.pdf",
        status="committed",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _summary_dict(source_id: str = "src-1") -> dict[str, Any]:
    """Return a dict valid for SourceSummaryResponse construction."""
    return {
        "id": source_id,
        "database_name": "default",
        "filename": "doc.pdf",
        "status": "committed",
        "created_at": _NOW,
        "updated_at": _NOW,
    }


# ---------------------------------------------------------------------------
# list_sources
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListSourcesHandler:
    """Tests for the list_sources route handler."""

    @pytest.mark.asyncio
    async def test_returns_paginated_response(self) -> None:
        """Handler delegates to list_sources_enriched and builds pagination."""
        service = MagicMock()
        service.list_sources_enriched.return_value = {
            "sources": [_summary_dict("s1"), _summary_dict("s2")],
            "total": 2,
        }

        result = await list_sources(
            _="test-user",
            service=service,
            settings=_settings(),
            pagination=(1, 50),
            source_type=None,
            status=None,
            enabled=None,
            search=None,
            tag_id=None,
        )

        service.list_sources_enriched.assert_called_once_with(
            page=1,
            page_size=50,
            source_type=None,
            status=None,
            enabled=None,
            search=None,
            tag_id=None,
        )
        assert isinstance(result, PaginatedSourcesResponse)
        assert len(result.data) == 2
        assert result.pagination.total == 2
        assert result.pagination.total_pages == 1
        assert result.pagination.has_next is False

    @pytest.mark.asyncio
    async def test_forwards_filters_and_computes_pages(self) -> None:
        """Handler forwards all filters and computes total_pages for page 2."""
        service = MagicMock()
        service.list_sources_enriched.return_value = {
            "sources": [_summary_dict("s3")],
            "total": 25,
        }

        result = await list_sources(
            _="test-user",
            service=service,
            settings=_settings(),
            pagination=(2, 10),
            source_type="pdf",
            status="indexed",
            enabled="enabled",
            search="neural",
            tag_id="tag-1",
        )

        service.list_sources_enriched.assert_called_once_with(
            page=2,
            page_size=10,
            source_type="pdf",
            status="indexed",
            enabled="enabled",
            search="neural",
            tag_id="tag-1",
        )
        assert result.pagination.total_pages == 3
        assert result.pagination.has_prev is True
        assert result.pagination.has_next is True


# ---------------------------------------------------------------------------
# get_source
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSourceHandler:
    """Tests for the get_source route handler."""

    @pytest.mark.asyncio
    async def test_returns_enriched_source(self) -> None:
        """Handler returns the source after enrichment when it exists."""
        service = MagicMock()
        service.get_source.return_value = {"id": "src-1", "status": "committed"}

        with (
            patch("chaoscypher_cortex.features.sources.api.add_duration_fields"),
            patch(
                "chaoscypher_cortex.features.sources.api.build_domain_icon_map",
                return_value={},
            ),
            patch("chaoscypher_cortex.features.sources.api.enrich_domain_icons"),
            patch(
                "chaoscypher_cortex.features.sources.api.build_domain_fingerprint_map",
                return_value={},
            ),
            patch("chaoscypher_cortex.features.sources.api.enrich_domain_changed"),
        ):
            result = await get_source(
                _="test-user",
                source_id="src-1",
                service=service,
                settings=_settings(),
            )

        service.get_source.assert_called_once_with("src-1")
        assert result["id"] == "src-1"

    @pytest.mark.asyncio
    async def test_raises_404_when_missing(self) -> None:
        """Handler raises HTTPException 404 when the source is missing."""
        service = MagicMock()
        service.get_source.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_source(
                _="test-user",
                source_id="missing",
                service=service,
                settings=_settings(),
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# update_source
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateSourceHandler:
    """Tests for the update_source route handler."""

    @pytest.mark.asyncio
    async def test_updates_and_returns_source(self) -> None:
        """Handler forwards SourceUpdate fields and returns the updated source."""
        from chaoscypher_cortex.features.sources.models import SourceUpdate

        service = MagicMock()
        service.update_source.return_value = {"id": "src-1", "title": "New"}

        result = await update_source(
            _="test-user",
            source_id="src-1",
            source_data=SourceUpdate(title="New", enabled=False),
            service=service,
        )

        service.update_source.assert_called_once_with(
            source_id="src-1",
            title="New",
            processing_status=None,
            enabled=False,
            user_metadata=None,
        )
        assert result["title"] == "New"

    @pytest.mark.asyncio
    async def test_raises_404_when_missing(self) -> None:
        """Handler raises 404 when update_source returns None."""
        from chaoscypher_cortex.features.sources.models import SourceUpdate

        service = MagicMock()
        service.update_source.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await update_source(
                _="test-user",
                source_id="missing",
                source_data=SourceUpdate(title="X"),
                service=service,
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# delete_source
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteSourceHandler:
    """Tests for the delete_source route handler."""

    @pytest.mark.asyncio
    async def test_deletes_when_present(self) -> None:
        """Handler offloads delete to a thread and returns None on success."""
        service = MagicMock()
        service.delete_source.return_value = True

        result = await delete_source(
            _="test-user",
            source_id="src-1",
            service=service,
        )

        service.delete_source.assert_called_once_with("src-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_raises_404_when_missing(self) -> None:
        """Handler raises 404 when delete_source reports a no-op."""
        service = MagicMock()
        service.delete_source.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            await delete_source(
                _="test-user",
                source_id="missing",
                service=service,
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# retry_source / reextract_source
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRetryReextractHandlers:
    """Tests for the retry_source / reextract_source thin shims."""

    @pytest.mark.asyncio
    async def test_retry_delegates_to_service(self) -> None:
        """retry_source awaits service.retry_source and returns the response."""
        service = MagicMock()
        service.retry_source = AsyncMock(return_value=_source_response("src-1"))

        result = await retry_source(_="test-user", source_id="src-1", service=service)

        service.retry_source.assert_awaited_once_with("src-1")
        assert result.id == "src-1"

    @pytest.mark.asyncio
    async def test_reextract_delegates_to_service(self) -> None:
        """reextract_source awaits service.reextract_source and returns the response."""
        service = MagicMock()
        service.reextract_source = AsyncMock(return_value=_source_response("src-2"))

        result = await reextract_source(_="test-user", source_id="src-2", service=service)

        service.reextract_source.assert_awaited_once_with("src-2")
        assert result.id == "src-2"


# ---------------------------------------------------------------------------
# list_source_recovery_events
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecoveryEventsHandler:
    """Tests for the list_source_recovery_events route handler."""

    @pytest.mark.asyncio
    async def test_returns_events(self) -> None:
        """Handler returns recovery events when the source exists."""
        service = MagicMock()
        service.get_source.return_value = {"id": "src-1"}
        service.list_recovery_events.return_value = [
            {
                "id": "ev-1",
                "source_id": "src-1",
                "database_name": "default",
                "attempt_at": _NOW,
                "from_status": "extracting",
                "action_taken": "extract_chunk",
                "reason": "stalled",
                "enqueued_count": 1,
            }
        ]

        result = await list_source_recovery_events(
            _="test-user",
            source_id="src-1",
            service=service,
            limit=50,
        )

        service.list_recovery_events.assert_called_once_with("src-1", limit=50)
        assert len(result.events) == 1
        assert result.events[0].id == "ev-1"

    @pytest.mark.asyncio
    async def test_raises_404_when_source_missing(self) -> None:
        """Handler raises 404 when the source does not exist."""
        service = MagicMock()
        service.get_source.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await list_source_recovery_events(
                _="test-user",
                source_id="missing",
                service=service,
                limit=50,
            )

        assert exc_info.value.status_code == 404
        service.list_recovery_events.assert_not_called()


# ---------------------------------------------------------------------------
# cleanup_orphan_tasks / get_processing_stats / get_source_tags
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMiscHandlers:
    """Tests for cleanup, processing-stats, and tag-list handlers."""

    @pytest.mark.asyncio
    async def test_cleanup_orphan_tasks_returns_response(self) -> None:
        """Handler wraps the service result in OrphanTaskCleanupResponse."""
        service = MagicMock()
        service.cleanup_orphan_tasks.return_value = {
            "deleted_count": 4,
            "retention_days": 7,
        }

        result = await cleanup_orphan_tasks(_="test-user", service=service)

        assert result.deleted_count == 4
        assert result.retention_days == 7

    @pytest.mark.asyncio
    async def test_get_processing_stats_returns_response(self) -> None:
        """Handler wraps get_stats() in ProcessingStatsResponse."""
        processing_service = MagicMock()
        processing_service.get_stats.return_value = {
            "total_files": 3,
            "by_status": {"committed": 3},
            "total_size_bytes": 1024,
        }

        result = await get_processing_stats(
            _="test-user",
            source_processing_service=processing_service,
        )

        assert result.total_files == 3
        assert result.by_status == {"committed": 3}

    @pytest.mark.asyncio
    async def test_get_source_tags_delegates(self) -> None:
        """Handler returns whatever the tag service reports."""
        tag_service = MagicMock()
        tag_service.get_source_tags.return_value = [{"id": "t1", "name": "Research"}]

        result = await get_source_tags(
            _="test-user",
            source_id="src-1",
            service=tag_service,
        )

        tag_service.get_source_tags.assert_called_once_with("src-1")
        assert result == [{"id": "t1", "name": "Research"}]

    @pytest.mark.asyncio
    async def test_unassign_tag_raises_404_when_missing(self) -> None:
        """Handler raises 404 when the tag assignment is absent."""
        tag_service = MagicMock()
        tag_service.unassign_tag.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            await unassign_tag_from_source(
                _="test-user",
                source_id="src-1",
                tag_id="tag-x",
                service=tag_service,
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# import_url
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImportUrlHandler:
    """Tests for the import_url route handler."""

    @pytest.mark.asyncio
    async def test_queues_fetch_and_returns_202_payload(self) -> None:
        """Valid URL enqueues a fetch task and returns the queued response."""
        settings = _settings()
        request = UrlImportRequest(url="https://example.com/page")

        with (
            patch(
                "chaoscypher_core.services.llm.require_extraction_ready",
                new=AsyncMock(),
            ),
            patch(
                "chaoscypher_cortex.features.sources.api.validate_url_safety",
                return_value=True,
            ),
            patch(
                "chaoscypher_cortex.features.sources.api.queue_utils.queue_fetch_url",
                new=AsyncMock(return_value="task-url-1"),
            ) as mock_fetch,
        ):
            result = await import_url(_="test-user", request=request, settings=settings)

        mock_fetch.assert_awaited_once()
        assert result.task_id == "task-url-1"
        assert result.url == "https://example.com/page"
        assert result.status == "queued"

    @pytest.mark.asyncio
    async def test_rejects_unsafe_url_with_400(self) -> None:
        """An unsafe URL raises HTTPException 400 before enqueueing."""
        settings = _settings()
        request = UrlImportRequest(url="https://169.254.169.254/latest/meta-data")

        with (
            patch(
                "chaoscypher_core.services.llm.require_extraction_ready",
                new=AsyncMock(),
            ),
            patch(
                "chaoscypher_cortex.features.sources.api.validate_url_safety",
                return_value=False,
            ),
            patch(
                "chaoscypher_cortex.features.sources.api.queue_utils.queue_fetch_url",
                new=AsyncMock(),
            ) as mock_fetch,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await import_url(_="test-user", request=request, settings=settings)

        assert exc_info.value.status_code == 400
        mock_fetch.assert_not_called()
