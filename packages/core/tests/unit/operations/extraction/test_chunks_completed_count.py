# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""chunks_completed_count is written to the source row at finalization.

Phase 7 audit-remediation 2026-05-09: Closes P2 (LLM extraction —
chunks completion surface metric).  The stat write happens via
``update_source_columns`` immediately after
``get_completed_chunk_results`` aggregates, so operators see
"X of Y chunks succeeded" directly without subtracting failure
counters from ``total_chunks``.

2026-08-12: these tests were rewritten to drive the real
``_finalize_extraction_inner`` with the adapter / queue boundaries mocked.
The previous bodies hand-wrote the production snippet onto a ``MagicMock``
and then asserted the mock had recorded it, so every one of them stayed
green with the production write (extraction_finalizer.py:338-350) deleted
outright.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.operations.extraction import extraction_finalizer


class _HaltAfterStatWriteError(Exception):
    """Sentinel raised at the aggregation step to bound a non-empty run.

    ``_finalize_extraction_inner`` performs the ``chunks_completed_count``
    write and then, for a job with completed chunks, continues into dedup /
    embedding / commit — machinery far outside this file's subject. Raising
    at ``aggregate_chunk_results`` (the very next production step) stops the
    run immediately after the write under test while still executing every
    real line that produces it.
    """


def _make_adapter(*, completed: int) -> MagicMock:
    """Build a storage-adapter double for a job with ``completed`` chunk results."""
    adapter = MagicMock()
    adapter.get_extraction_job.return_value = {
        "detected_domain": None,
        "forced_domain": None,
    }
    adapter.get_chunk_tasks_by_job.return_value = [
        {"id": f"task-{i}", "status": "completed"} for i in range(completed)
    ]
    adapter.get_completed_chunk_results.return_value = [
        {"id": f"task-{i}", "raw_entities": [], "raw_relationships": []} for i in range(completed)
    ]
    adapter.get_file.return_value = {"filename": "doc.txt", "chunk_count": completed}
    return adapter


async def _run_finalizer(
    adapter: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    *,
    job_id: str = "job-1",
    source_id: str = "src-1",
    database_name: str = "default",
) -> dict[str, Any]:
    """Run the real finalizer body with the queue boundaries stubbed out."""
    monkeypatch.setattr(extraction_finalizer, "_queue_commit_phase", AsyncMock())
    monkeypatch.setattr(extraction_finalizer, "trigger_next_waiting_extraction", AsyncMock())
    return await extraction_finalizer._finalize_extraction_inner(
        graph_repository=MagicMock(),
        llm_service=MagicMock(),
        chunk_extraction_service=MagicMock(),
        adapter=adapter,
        data={},
        job_id=job_id,
        source_id=source_id,
        database_name=database_name,
        generate_embeddings=False,
        settings=MagicMock(),
        engine_settings=MagicMock(),
    )


def _stat_write_calls(adapter: MagicMock) -> list[dict[str, Any]]:
    """Every ``update_source_columns`` call that carried the stat column."""
    return [
        call.kwargs
        for call in adapter.update_source_columns.call_args_list
        if "chunks_completed_count" in (call.kwargs.get("updates") or {})
    ]


@pytest.mark.unit
class TestChunksCompletedCountWrite:
    """chunks_completed_count stat write at the finalizer boundary."""

    @pytest.mark.asyncio
    async def test_finalizer_writes_count_from_completed_chunk_results(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The finalizer writes len(get_completed_chunk_results(job_id)).

        The adapter reports 7 completed chunk results, so the value that
        reaches ``update_source_columns`` must be 7 — pinning the count to
        the adapter query rather than to any constant.
        """
        adapter = _make_adapter(completed=7)
        monkeypatch.setattr(
            "chaoscypher_core.services.sources.engine.extraction.orchestration"
            ".aggregate_chunk_results",
            MagicMock(side_effect=_HaltAfterStatWriteError),
        )

        with pytest.raises(_HaltAfterStatWriteError):
            await _run_finalizer(adapter, monkeypatch, job_id="job-1", source_id="src-1")

        assert _stat_write_calls(adapter) == [
            {
                "source_id": "src-1",
                "database_name": "default",
                "updates": {"chunks_completed_count": 7},
            }
        ]

    @pytest.mark.asyncio
    async def test_finalizer_writes_zero_when_all_chunks_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no chunk completed, the count is written as 0 rather than skipped.

        The write must happen even for zero-result jobs so the source row
        shows 0 rather than the reset-default 0 being stale. This drives the
        complete empty-result path, which returns ``committed_empty``.
        """
        adapter = _make_adapter(completed=0)

        result = await _run_finalizer(adapter, monkeypatch, job_id="job-2", source_id="src-2")

        assert result["status"] == "committed_empty"
        assert _stat_write_calls(adapter) == [
            {
                "source_id": "src-2",
                "database_name": "default",
                "updates": {"chunks_completed_count": 0},
            }
        ]

    @pytest.mark.asyncio
    async def test_write_failure_is_swallowed_and_logs_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing update_source_columns call logs at WARNING and continues.

        Counter visibility must not block the pipeline: finalization still
        reaches its normal ``committed_empty`` outcome, and the failure is
        surfaced as ``chunks_completed_count_write_failed``.
        """
        import structlog.testing

        adapter = _make_adapter(completed=0)
        adapter.update_source_columns.side_effect = RuntimeError("db gone")

        with structlog.testing.capture_logs() as captured:
            result = await _run_finalizer(adapter, monkeypatch, job_id="job-3", source_id="src-3")

        assert result["status"] == "committed_empty", "stat-write failure blocked finalization"
        matched = [e for e in captured if e["event"] == "chunks_completed_count_write_failed"]
        assert len(matched) == 1, (
            f"expected one warning, got events: {[e['event'] for e in captured]}"
        )
        assert matched[0]["source_id"] == "src-3"
        assert matched[0]["chunks_completed"] == 0

    @pytest.mark.asyncio
    async def test_written_column_exists_on_the_sources_table(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every column key the finalizer writes is a real ``sources`` column.

        Guards against a typo drifting the finalizer away from the schema:
        migration 0029 and ``SourceRow.chunks_completed_count`` define the
        name, and a silent rename on either side would make the stat write a
        no-op (or an error) in production.
        """
        adapter = _make_adapter(completed=5)
        monkeypatch.setattr(
            "chaoscypher_core.services.sources.engine.extraction.orchestration"
            ".aggregate_chunk_results",
            MagicMock(side_effect=_HaltAfterStatWriteError),
        )

        with pytest.raises(_HaltAfterStatWriteError):
            await _run_finalizer(adapter, monkeypatch, job_id="job-4", source_id="src-4")

        writes = _stat_write_calls(adapter)
        assert len(writes) == 1
        source_columns = set(SourceRow.__table__.columns.keys())
        assert set(writes[0]["updates"]) <= source_columns
        assert writes[0]["updates"]["chunks_completed_count"] == 5
