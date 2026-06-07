# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: commit failures propagate as exceptions, not success=False dicts."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.operations.importing.import_service import ImportOperationsService


@pytest.mark.asyncio
async def test_run_commit_raises_on_commit_service_error() -> None:
    """When commit_service.commit raises, _run_commit re-raises (no success=False dict)."""
    service = ImportOperationsService.__new__(ImportOperationsService)
    service.graph_repository = MagicMock()
    service.source_repository = MagicMock()
    service.search_repository = MagicMock()
    service.trigger_service = None
    service.engine_settings = None

    settings = MagicMock()
    settings.current_database = "default"

    failing_commit_service = MagicMock()
    failing_commit_service.commit = AsyncMock(side_effect=RuntimeError("graph write failed"))

    # Mock the adapter.transaction() context manager
    service.source_repository.transaction = MagicMock()
    service.source_repository.transaction.return_value.__enter__ = MagicMock(return_value=None)
    service.source_repository.transaction.return_value.__exit__ = MagicMock(return_value=None)

    # Mock SourceCommitService and build_engine_settings at their module levels
    with (
        patch(
            "chaoscypher_core.services.sources.engine.commit.SourceCommitService",
            return_value=failing_commit_service,
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=MagicMock(),
        ),
    ):
        with pytest.raises(RuntimeError, match="graph write failed"):
            await service._run_commit(
                file_id="src_1",
                commit_data={"entities": [], "relationships": []},
                file_info_dict={"filename": "doc.pdf"},
                auto_enable=True,
                settings=settings,
            )

    service.source_repository.fail_commit.assert_called_once_with("src_1", "graph write failed")


@pytest.mark.asyncio
async def test_run_commit_still_raises_when_fail_commit_itself_fails() -> None:
    """If fail_commit itself raises, the ORIGINAL exception still propagates (after warning log)."""
    service = ImportOperationsService.__new__(ImportOperationsService)
    service.graph_repository = MagicMock()
    service.source_repository = MagicMock()
    service.source_repository.fail_commit.side_effect = RuntimeError("session is broken")
    service.search_repository = MagicMock()
    service.trigger_service = None
    service.engine_settings = None

    settings = MagicMock()
    settings.current_database = "default"

    failing_commit_service = MagicMock()
    failing_commit_service.commit = AsyncMock(side_effect=RuntimeError("graph write failed"))

    # Mock the adapter.transaction() context manager
    service.source_repository.transaction = MagicMock()
    service.source_repository.transaction.return_value.__enter__ = MagicMock(return_value=None)
    service.source_repository.transaction.return_value.__exit__ = MagicMock(return_value=None)

    # Mock SourceCommitService and build_engine_settings at their module levels
    with (
        patch(
            "chaoscypher_core.services.sources.engine.commit.SourceCommitService",
            return_value=failing_commit_service,
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=MagicMock(),
        ),
    ):
        # The ORIGINAL exception ("graph write failed") must propagate, not the secondary
        # ("session is broken"). The secondary should be logged but not re-raise on its own.
        with pytest.raises(RuntimeError, match="graph write failed"):
            await service._run_commit(
                file_id="src_1",
                commit_data={"entities": [], "relationships": []},
                file_info_dict={"filename": "doc.pdf"},
                auto_enable=True,
                settings=settings,
            )
