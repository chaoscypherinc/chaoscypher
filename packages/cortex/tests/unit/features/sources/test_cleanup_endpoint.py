# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for POST /api/v1/sources/cleanup/orphan_tasks (Cluster F).

Verifies the service-layer contract for cleanup_orphan_tasks.
No HTTP layer required — the endpoint is a thin wrapper that delegates
to the service method, mirroring the pattern used by test_retry_endpoint.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.sources.service import SourceService


# ---------------------------------------------------------------------------
# Helpers (mirror test_retry_endpoint.py)
# ---------------------------------------------------------------------------


def _settings(retention_days: int = 7) -> MagicMock:
    """Return minimal settings stub with source_recovery configured."""
    settings = MagicMock()
    settings.pagination.default_page_size = 50
    settings.pagination.max_page_size = 1000
    settings.pagination.extraction_tasks_page_size = 25
    settings.batching.template_name_cache_size = 100
    settings.priorities.background = 50
    settings.data_dir = "/tmp/cc-data"
    settings.source_recovery.orphan_task_retention_days = retention_days
    return settings


def _make_service(
    *,
    adapter: MagicMock | None = None,
    engine_service: MagicMock | None = None,
    database_name: str = "default",
    retention_days: int = 7,
) -> SourceService:
    """Return a SourceService with mock collaborators."""
    return SourceService(
        engine_service=engine_service or MagicMock(),
        database_name=database_name,
        settings=_settings(retention_days=retention_days),
        storage_adapter=adapter or MagicMock(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanupOrphanTasksService:
    """Unit tests for SourceService.cleanup_orphan_tasks."""

    def test_returns_zero_when_no_orphans(self) -> None:
        """cleanup_orphan_tasks returns deleted_count=0 when nothing to clean."""
        adapter = MagicMock()
        adapter.cleanup_orphaned_chunk_tasks.return_value = 0
        service = _make_service(adapter=adapter)

        result = service.cleanup_orphan_tasks()

        assert result["deleted_count"] == 0
        assert result["retention_days"] == 7

    def test_returns_deleted_count_when_orphans_cleaned(self) -> None:
        """cleanup_orphan_tasks returns the count reported by the adapter."""
        adapter = MagicMock()
        adapter.cleanup_orphaned_chunk_tasks.return_value = 3
        service = _make_service(adapter=adapter)

        result = service.cleanup_orphan_tasks()

        assert result["deleted_count"] == 3
        assert result["retention_days"] == 7

    def test_passes_correct_older_than_seconds_to_adapter(self) -> None:
        """7 retention days → older_than_seconds=604800 passed to adapter."""
        adapter = MagicMock()
        adapter.cleanup_orphaned_chunk_tasks.return_value = 0
        service = _make_service(adapter=adapter, retention_days=7)

        service.cleanup_orphan_tasks()

        adapter.cleanup_orphaned_chunk_tasks.assert_called_once_with(
            older_than_seconds=7 * 86400,
        )

    def test_retention_days_reflects_settings(self) -> None:
        """retention_days in response matches the configured setting value."""
        adapter = MagicMock()
        adapter.cleanup_orphaned_chunk_tasks.return_value = 0
        service = _make_service(adapter=adapter, retention_days=14)

        result = service.cleanup_orphan_tasks()

        assert result["retention_days"] == 14
        adapter.cleanup_orphaned_chunk_tasks.assert_called_once_with(
            older_than_seconds=14 * 86400,
        )

    def test_result_has_required_keys(self) -> None:
        """Result dict contains exactly deleted_count and retention_days."""
        adapter = MagicMock()
        adapter.cleanup_orphaned_chunk_tasks.return_value = 5
        service = _make_service(adapter=adapter)

        result = service.cleanup_orphan_tasks()

        assert set(result.keys()) == {"deleted_count", "retention_days"}
