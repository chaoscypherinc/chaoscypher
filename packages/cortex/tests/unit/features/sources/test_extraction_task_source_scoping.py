# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GET /sources/{source_id}/extraction/tasks/{task_id} must honor source_id.

The handler fetched by task_id alone, so ANY source_id in the path
resolved any task — /sources/other-source/extraction/tasks/t1 returned
source-A's task. Mirror the sibling ``get_chunk`` guard: 404 when the
task's job does not belong to the path's source.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.sources.extraction_api import get_extraction_task
from chaoscypher_cortex.features.sources.service import SourceService


def _make_service_with_adapter(
    *,
    task: dict | None,
    job: dict | None,
) -> SourceService:
    """Real SourceService over a stubbed storage adapter."""
    adapter = MagicMock()
    adapter.get_extraction_task_detail.return_value = task
    adapter.get_extraction_job.return_value = job
    service = SourceService.__new__(SourceService)
    service.storage_adapter = adapter
    return service


@pytest.mark.unit
class TestExtractionTaskSourceScoping:
    """Task lookups are scoped to the source in the path."""

    @pytest.mark.asyncio
    async def test_mismatched_source_id_returns_404(self) -> None:
        from fastapi import HTTPException

        service = _make_service_with_adapter(
            task={"id": "task-1", "job_id": "job-1"},
            job={"id": "job-1", "source_id": "src-owner"},
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_extraction_task(
                _=MagicMock(),
                source_id="src-other",
                task_id="task-1",
                service=service,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_matching_source_id_returns_task(self) -> None:
        service = _make_service_with_adapter(
            task={"id": "task-1", "job_id": "job-1"},
            job={"id": "job-1", "source_id": "src-owner"},
        )

        result = await get_extraction_task(
            _=MagicMock(),
            source_id="src-owner",
            task_id="task-1",
            service=service,
        )

        assert result["id"] == "task-1"

    @pytest.mark.asyncio
    async def test_unknown_task_returns_404(self) -> None:
        from fastapi import HTTPException

        service = _make_service_with_adapter(task=None, job=None)

        with pytest.raises(HTTPException) as exc_info:
            await get_extraction_task(
                _=MagicMock(),
                source_id="src-owner",
                task_id="missing",
                service=service,
            )

        assert exc_info.value.status_code == 404

    def test_service_without_scope_keeps_legacy_behavior(self) -> None:
        """service.get_extraction_task(task_id) without source_id stays unscoped."""
        service = _make_service_with_adapter(
            task={"id": "task-1", "job_id": "job-1"},
            job={"id": "job-1", "source_id": "src-owner"},
        )
        assert service.get_extraction_task("task-1") == {"id": "task-1", "job_id": "job-1"}
