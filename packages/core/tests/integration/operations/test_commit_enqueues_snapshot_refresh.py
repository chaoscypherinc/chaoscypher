# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests that a successful _run_commit enqueues OP_BUILD_GRAPH_SNAPSHOT.

These are lightweight unit-style tests (no real database) that call
``_run_commit`` with all heavy collaborators mocked out.  The goal is to pin
the post-commit enqueue behaviour without requiring a real Valkey connection.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing


class TestCommitEnqueuesSnapshotRefresh:
    """_run_commit success path enqueues graph-snapshot refresh."""

    def _make_service(self) -> object:
        """Return a minimally-wired ImportOperationsService with mock repos."""
        from chaoscypher_core.operations.importing.import_service import (
            ImportOperationsService,
        )

        adapter = MagicMock()
        # transaction() must be a sync context manager
        adapter.transaction = _sync_ctx_manager
        adapter.clear_source_commit_payload = MagicMock()

        service = ImportOperationsService(
            graph_repository=MagicMock(),
            config_manager=MagicMock(),
            source_manager=MagicMock(),
            trigger_service=None,  # skip trigger event publishing
            llm_service=MagicMock(),
            source_repository=adapter,
            chunking_service=MagicMock(),
            indexing_service=MagicMock(),
        )
        # Provide a pre-built search_repository so _run_commit skips the
        # SQLite engine creation path.
        service.search_repository = MagicMock()
        return service

    def _make_settings(self) -> MagicMock:
        settings = MagicMock()
        settings.current_database = "default"
        settings.priorities.background = 50
        return settings

    @pytest.mark.asyncio
    async def test_commit_success_enqueues_snapshot_refresh(self) -> None:
        """On the success path _run_commit calls queue_client.enqueue_task with
        OP_BUILD_GRAPH_SNAPSHOT and returns the commit result unchanged.
        """
        from chaoscypher_core.constants import OP_BUILD_GRAPH_SNAPSHOT, QUEUE_OPERATIONS

        service = self._make_service()
        settings = self._make_settings()

        fake_commit_result = {
            "created_nodes": ["n1"],
            "created_edges": [],
            "created_templates": [],
        }

        with (
            patch(
                "chaoscypher_core.services.sources.engine.commit.SourceCommitService",
                _make_mock_commit_service(fake_commit_result),
            ),
            patch(
                "chaoscypher_core.app_config.engine_factory.build_engine_settings",
                return_value=MagicMock(),
            ),
            patch("chaoscypher_core.operations.importing.import_service.queue_client") as mock_qc,
        ):
            mock_qc.enqueue_task = AsyncMock(return_value="task-snap-123")

            result = await service._run_commit(
                file_id="src-abc",
                commit_data={},
                file_info_dict={"filename": "test.pdf"},
                auto_enable=False,
                settings=settings,
            )

        # Commit result is returned unchanged
        assert result == fake_commit_result

        # enqueue_task was called exactly once with correct args
        mock_qc.enqueue_task.assert_called_once()
        call_kwargs = mock_qc.enqueue_task.call_args.kwargs
        assert call_kwargs["queue"] == QUEUE_OPERATIONS
        assert call_kwargs["operation"] == OP_BUILD_GRAPH_SNAPSHOT
        assert call_kwargs["data"] == {"database_name": "default"}
        assert call_kwargs["priority"] == 50
        assert call_kwargs["metadata"]["trigger"] == "post_commit"
        assert call_kwargs["metadata"]["source_id"] == "src-abc"

    @pytest.mark.asyncio
    async def test_commit_success_does_not_raise_when_enqueue_fails(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """If enqueue_task raises, _run_commit still returns success and logs a
        warning — the commit result is not undone.
        """
        service = self._make_service()
        settings = self._make_settings()

        fake_commit_result: dict = {
            "created_nodes": [],
            "created_edges": [],
            "created_templates": [],
        }

        with (
            patch(
                "chaoscypher_core.services.sources.engine.commit.SourceCommitService",
                _make_mock_commit_service(fake_commit_result),
            ),
            patch(
                "chaoscypher_core.app_config.engine_factory.build_engine_settings",
                return_value=MagicMock(),
            ),
            patch("chaoscypher_core.operations.importing.import_service.queue_client") as mock_qc,
        ):
            mock_qc.enqueue_task = AsyncMock(side_effect=RuntimeError("valkey unavailable"))

            with structlog.testing.capture_logs() as captured:
                result = await service._run_commit(
                    file_id="src-xyz",
                    commit_data={},
                    file_info_dict={"filename": "boom.pdf"},
                    auto_enable=False,
                    settings=settings,
                )

        # No exception escaped — commit result still returned
        assert result == fake_commit_result

        # Warning log was emitted
        warning_events = [
            e for e in captured if e.get("event") == "graph_snapshot_refresh_enqueue_failed"
        ]
        assert len(warning_events) == 1
        assert warning_events[0]["log_level"] == "warning"
        assert warning_events[0]["error_type"] == "RuntimeError"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _sync_ctx_manager(*args: object, **kwargs: object):  # type: ignore[override]
    """Trivial sync context manager used to stub adapter.transaction()."""
    yield


def _make_mock_commit_service(result: dict) -> type:
    """Return a mock SourceCommitService class whose .commit() returns *result*."""

    class _MockCommitService:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def commit(self, *args: object, **kwargs: object) -> dict:
            return result

    return _MockCommitService
