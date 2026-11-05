# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The embed handler queues analysis first; a summary problem never fails an indexed source.

The import summary runs after ``complete_indexing`` and after the analysis is
queued. Whether building the summary call or making it raises, the handler
logs ``import_summary_failed``, returns ``indexed``, never calls
``fail_indexing`` and never re-raises (no queue retry).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from structlog.testing import capture_logs

from chaoscypher_core.operations.importing.embedding_handler import _run_embedding


HANDLER = "chaoscypher_core.operations.importing.embedding_handler"
SUMMARIES = "chaoscypher_core.services.sources.summaries"


def _adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.count_unembedded_chunks.return_value = 0
    adapter.list_unembedded_chunks.return_value = []
    adapter.get_chunks_by_source.return_value = ([], 1)
    adapter.get_source.return_value = {"id": "doc-a", "auto_analyze": True, "filename": "a.txt"}
    return adapter


def _indexing_service() -> MagicMock:
    service = MagicMock()
    service.settings.embedding.model = "stub-embedding-model"
    service.settings.search.vector_dimensions = 8
    return service


def _raising(*_args: Any, **_kwargs: Any) -> Any:
    msg = "summary broke"
    raise RuntimeError(msg)


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["import_summary_chat", "write_import_summary"])
async def test_analysis_is_queued_even_when_the_summary_step_raises(broken: str) -> None:
    adapter = _adapter()
    order: list[str] = []

    async def queue_analysis(*_args: Any, **_kwargs: Any) -> None:
        order.append("analysis")

    def broken_step(*args: Any, **kwargs: Any) -> Any:
        order.append("summary")
        return _raising(*args, **kwargs)

    with (
        patch(
            f"{HANDLER}._queue_post_indexing_analysis", new=AsyncMock(side_effect=queue_analysis)
        ),
        patch(f"{HANDLER}.event_bus"),
        patch(f"{SUMMARIES}.{broken}", new=broken_step),
        capture_logs() as logs,
    ):
        result = await _run_embedding(
            source_id="doc-a",
            file_info={"filename": "a.txt"},
            adapter=adapter,
            indexing_service=_indexing_service(),
            settings=MagicMock(),
            database_name="default",
            task_id=None,
        )

    assert result["status"] == "indexed"
    assert order == ["analysis", "summary"]
    adapter.complete_indexing.assert_called_once()
    adapter.fail_indexing.assert_not_called()
    failed = [e for e in logs if e["event"] == "import_summary_failed"]
    assert len(failed) == 1
    assert failed[0]["log_level"] == "warning"
    assert failed[0]["error_type"] == "RuntimeError"
