# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify LLM summary is always written, including on the all-chunks-failed path.

Phase 1 fix (2026-05-21 incident): the `llm_total_calls > 0` guard at
extraction_finalizer:920 was dropped so the writeback runs unconditionally.
Pre-fix, the guard silently hid the all-chunks-failed case — sources finalized
with llm_failed_calls=0 even when N chunk-level 404s appeared in the logs.

2026-08-12: these tests were rewritten to drive the real
``_complete_finalization`` with the adapter / service boundaries mocked. The
previous bodies hand-wrote the writeback onto a ``MagicMock`` and asserted the
mock had recorded it — a tautology that stayed green with the guard restored.
The old ``test_old_guard_would_have_skipped_zero_total`` (which re-implemented
the *removed* guard inline and asserted ``assert_not_called`` on its own logic)
and ``test_finalizer_module_guard_is_unconditional`` (an ``inspect.getsource``
substring check, defeated by any re-spelling of the guard) are replaced by the
behavioural tests below.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.operations.extraction import extraction_finalizer


ZERO_TOTAL_SUMMARY: dict[str, Any] = {
    "llm_total_calls": 0,
    "llm_successful_calls": 0,
    "llm_failed_calls": 0,
    "llm_retry_calls": 0,
    "llm_permanent_failures": 0,
    "llm_wasted_tokens": 0,
    "llm_estimated_cost_usd": None,
}

ALL_FAILED_SUMMARY: dict[str, Any] = {
    "llm_total_calls": 3,
    "llm_successful_calls": 0,
    "llm_failed_calls": 3,
    "llm_retry_calls": 0,
    "llm_permanent_failures": 3,
    "llm_wasted_tokens": 500,
    "llm_estimated_cost_usd": 0.0,
}


def _make_adapter(llm_summary: dict[str, Any] | None) -> MagicMock:
    """Build a storage-adapter double whose compute_llm_summary returns ``llm_summary``."""
    adapter = MagicMock()
    adapter.compute_llm_summary.return_value = llm_summary
    adapter.get_file.return_value = {"filename": "doc.txt", "chunk_count": 4}
    return adapter


async def _run_complete_finalization(
    adapter: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_id: str = "src-1",
    database_name: str = "default",
) -> dict[str, Any]:
    """Run the real ``_complete_finalization`` with its collaborators stubbed.

    Only the boundaries are mocked — the LLM-summary writeback under test is
    the module's own code, so restoring the removed ``> 0`` guard (in any
    spelling) turns these tests red.
    """
    extraction_service = MagicMock()
    extraction_service.build_extraction_results = AsyncMock(
        return_value={
            "entities": [],
            "relationships": [],
            "matched_templates": [],
            "metadata": {},
        }
    )
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.engine.extraction.service.ExtractionService",
        MagicMock(return_value=extraction_service),
    )
    monkeypatch.setattr(
        "chaoscypher_core.repo_factories.get_embedding_service",
        MagicMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.engine.extraction.orchestration.cache_quality_scores",
        MagicMock(),
    )
    monkeypatch.setattr(extraction_finalizer, "_store_entity_embeddings", MagicMock())
    monkeypatch.setattr(extraction_finalizer, "_queue_commit_phase", AsyncMock())
    monkeypatch.setattr(extraction_finalizer, "trigger_next_waiting_extraction", AsyncMock())

    return await extraction_finalizer._complete_finalization(
        adapter=adapter,
        graph_repository=MagicMock(),
        llm_service=MagicMock(),
        settings=MagicMock(),
        engine_settings=MagicMock(),
        job_id="job-1",
        source_id=source_id,
        database_name=database_name,
        generate_embeddings=False,
        detected_domain=None,
        forced_domain=None,
        entities=[],
        relationships=[],
        cached_embeddings=[],
        completed_chunks=0,
    )


def _llm_writes(adapter: MagicMock) -> list[dict[str, Any]]:
    """Every ``update_source_columns`` call carrying LLM-metric columns."""
    return [
        call.kwargs
        for call in adapter.update_source_columns.call_args_list
        if "llm_total_calls" in (call.kwargs.get("updates") or {})
    ]


def _make_all_chunks_failed_adapter(
    llm_summary: dict[str, Any] | None, *, failed: int = 3
) -> MagicMock:
    """Adapter double for a job where every chunk task ended in ``failed``."""
    adapter = _make_adapter(llm_summary)
    adapter.get_extraction_job.return_value = {
        "detected_domain": None,
        "forced_domain": None,
    }
    adapter.get_chunk_tasks_by_job.return_value = [
        {"id": f"task-{i}", "status": "failed", "error_message": "Ollama 404: model not found"}
        for i in range(failed)
    ]
    # Every chunk failed, so the completed-results query comes back empty —
    # this is what routes _finalize_extraction_inner into the failed branch.
    adapter.get_completed_chunk_results.return_value = []
    return adapter


async def _run_finalize_all_chunks_failed(
    adapter: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    *,
    source_id: str = "src-failed",
) -> dict[str, Any]:
    """Drive the real ``_finalize_extraction_inner`` down the failed-chunks path."""
    monkeypatch.setattr(extraction_finalizer, "trigger_next_waiting_extraction", AsyncMock())

    return await extraction_finalizer._finalize_extraction_inner(
        graph_repository=MagicMock(),
        llm_service=MagicMock(),
        chunk_extraction_service=MagicMock(),
        adapter=adapter,
        data={},
        job_id="job-failed",
        source_id=source_id,
        database_name="default",
        generate_embeddings=False,
        settings=MagicMock(),
        engine_settings=MagicMock(),
    )


@pytest.mark.unit
class TestLLMSummaryWriteback:
    """LLM summary writeback is unconditional — even when total_calls == 0."""

    @pytest.mark.asyncio
    async def test_writeback_happens_when_total_calls_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The summary reaches the source row even when llm_total_calls == 0.

        This is the 2026-05-21 incident regression: the pre-fix
        ``llm_total_calls > 0`` guard prevented failure counters from reaching
        the source row when every chunk failed before producing a successful
        LLM call metric. Re-introducing that guard — in any spelling — leaves
        ``update_source_columns`` uncalled and fails this test.
        """
        adapter = _make_adapter(ZERO_TOTAL_SUMMARY)

        await _run_complete_finalization(adapter, monkeypatch, source_id="src-1")

        assert _llm_writes(adapter) == [
            {
                "source_id": "src-1",
                "database_name": "default",
                "updates": ZERO_TOTAL_SUMMARY,
            }
        ]

    @pytest.mark.asyncio
    async def test_writeback_happens_on_the_all_chunks_failed_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The INCIDENT path itself: every chunk failed, llm_total_calls == 0.

        There are TWO ``compute_llm_summary`` + ``if llm_summary:`` writebacks
        in this module — the one in ``_complete_finalization`` covered above,
        and this one in ``_finalize_extraction_inner``'s failed-chunks branch
        (extraction_finalizer.py:388-399). The second is literally the shape
        this file's docstring describes: N chunk-level 404s, no successful LLM
        call, so ``compute_llm_summary`` reports zeros and the pre-fix ``> 0``
        guard skipped the write, finalizing the source with
        ``llm_failed_calls=0`` while the errors sat in the logs.

        Re-introducing that guard at :394 alone leaves every other test in
        this file green, so this case needs its own coverage.
        """
        adapter = _make_all_chunks_failed_adapter(ZERO_TOTAL_SUMMARY)

        result = await _run_finalize_all_chunks_failed(adapter, monkeypatch, source_id="src-failed")

        assert result["status"] == "extraction_failed"
        assert _llm_writes(adapter) == [
            {
                "source_id": "src-failed",
                "database_name": "default",
                "updates": ZERO_TOTAL_SUMMARY,
            }
        ]

    @pytest.mark.asyncio
    async def test_all_chunks_failed_writeback_precedes_the_failure_marking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Counters land on the row BEFORE _apply_failure clears the job ref.

        The production comment at :386-387 states the ordering explicitly;
        a refactor that moved the writeback after ``fail_extraction`` would
        drop the counters on the floor.
        """
        adapter = _make_all_chunks_failed_adapter(ALL_FAILED_SUMMARY)
        order: list[str] = []
        adapter.update_source_columns.side_effect = lambda **kw: order.append(
            "llm_write" if "llm_total_calls" in kw["updates"] else "stat_write"
        )
        adapter.fail_extraction.side_effect = lambda *_a, **_k: order.append("fail_extraction")

        await _run_finalize_all_chunks_failed(adapter, monkeypatch)

        assert "llm_write" in order, "LLM summary never written on the failed path"
        assert order.index("llm_write") < order.index("fail_extraction")

    @pytest.mark.asyncio
    async def test_writeback_carries_nonzero_failure_counters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When chunks wrote failed LLMCallMetric rows, those counters land on the row.

        Covers the case where the worker DID write metric rows but all were
        failures — e.g. the model returned error JSON rather than 404.
        """
        adapter = _make_adapter(ALL_FAILED_SUMMARY)

        await _run_complete_finalization(adapter, monkeypatch, source_id="src-2")

        writes = _llm_writes(adapter)
        assert len(writes) == 1
        written = writes[0]["updates"]
        assert written["llm_failed_calls"] == 3
        assert written["llm_total_calls"] == 3
        assert written["llm_successful_calls"] == 0

    @pytest.mark.asyncio
    async def test_writeback_skipped_only_when_summary_is_falsy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The single remaining guard is ``if llm_summary`` — nothing narrower.

        When the adapter has no summary to report at all there is nothing to
        write; this pins that the *only* condition gating the writeback is an
        absent summary, not a property of its contents.
        """
        adapter = _make_adapter(None)

        await _run_complete_finalization(adapter, monkeypatch, source_id="src-3")

        assert adapter.compute_llm_summary.call_count == 1
        assert _llm_writes(adapter) == []

    @pytest.mark.asyncio
    async def test_summary_is_computed_with_configured_token_costs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """compute_llm_summary receives the settings-driven per-million costs.

        The written cost column is only meaningful if the finalizer passes the
        operator's configured pricing through rather than defaults.
        """
        adapter = _make_adapter(ALL_FAILED_SUMMARY)
        settings = MagicMock()
        settings.llm.token_cost_input_per_million = 1.25
        settings.llm.token_cost_output_per_million = 9.5

        extraction_service = MagicMock()
        extraction_service.build_extraction_results = AsyncMock(
            return_value={
                "entities": [],
                "relationships": [],
                "matched_templates": [],
                "metadata": {},
            }
        )
        monkeypatch.setattr(
            "chaoscypher_core.services.sources.engine.extraction.service.ExtractionService",
            MagicMock(return_value=extraction_service),
        )
        monkeypatch.setattr(
            "chaoscypher_core.repo_factories.get_embedding_service",
            MagicMock(return_value=MagicMock()),
        )
        monkeypatch.setattr(
            "chaoscypher_core.services.sources.engine.extraction.orchestration"
            ".cache_quality_scores",
            MagicMock(),
        )
        monkeypatch.setattr(extraction_finalizer, "_store_entity_embeddings", MagicMock())
        monkeypatch.setattr(extraction_finalizer, "_queue_commit_phase", AsyncMock())
        monkeypatch.setattr(extraction_finalizer, "trigger_next_waiting_extraction", AsyncMock())

        await extraction_finalizer._complete_finalization(
            adapter=adapter,
            graph_repository=MagicMock(),
            llm_service=MagicMock(),
            settings=settings,
            engine_settings=MagicMock(),
            job_id="job-1",
            source_id="src-4",
            database_name="default",
            generate_embeddings=False,
            detected_domain=None,
            forced_domain=None,
            entities=[],
            relationships=[],
            cached_embeddings=[],
            completed_chunks=0,
        )

        call = adapter.compute_llm_summary.call_args
        assert call.args == ("src-4", "default")
        assert call.kwargs["custom_input_cost"] == 1.25
        assert call.kwargs["custom_output_cost"] == 9.5
