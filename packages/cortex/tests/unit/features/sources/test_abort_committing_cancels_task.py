# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: aborting a COMMITTING source cancels its OP_IMPORT_COMMIT task.

Audit fix #C3. Without the COMMITTING branch in ``abort_processing``, the
commit worker kept writing graph data after the user clicked Stop — a race
window equal to the full commit duration.

Verifies that ``cancel_by_metadata`` is called with the correct metadata
dict and queue when the source is in COMMITTING status.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.constants import OP_IMPORT_COMMIT, QUEUE_OPERATIONS
from chaoscypher_core.models import SourceStatus
from chaoscypher_cortex.features.sources.service import SourceService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings() -> MagicMock:
    """Return minimal settings stub for SourceService."""
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
    return SourceService(
        engine_service=engine_service or MagicMock(),
        database_name=database_name,
        settings=_settings(),
        storage_adapter=adapter or MagicMock(),
    )


def _committing_source(source_id: str) -> dict[str, object]:
    """Return a minimal source dict in COMMITTING status."""
    return {
        "id": source_id,
        "status": SourceStatus.COMMITTING,
        "current_extraction_job_id": None,
        "filepath": "/data/test.txt",
        "filename": "test.txt",
        "file_type": "text",
    }


# ---------------------------------------------------------------------------
# Regression test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abort_committing_source_cancels_commit_task() -> None:
    """abort_processing cancels the OP_IMPORT_COMMIT task for COMMITTING sources.

    Audit fix #C3 regression: the COMMITTING branch must call
    ``cancel_by_metadata`` with the commit operation metadata so the commit
    worker receives the cancellation signal before it finishes writing to the
    knowledge graph.
    """
    source_id = "src_committing"

    adapter = MagicMock()
    adapter.get_file.return_value = _committing_source(source_id)

    service = _make_service(adapter=adapter)

    cancel_mock = AsyncMock()

    with patch(
        "chaoscypher_core.queue.queue_client.cancel_by_metadata",
        cancel_mock,
    ):
        await service.abort_processing(source_id)

    cancel_mock.assert_awaited_once_with(
        metadata={"file_id": source_id, "operation_type": OP_IMPORT_COMMIT},
        queue=QUEUE_OPERATIONS,
    )
