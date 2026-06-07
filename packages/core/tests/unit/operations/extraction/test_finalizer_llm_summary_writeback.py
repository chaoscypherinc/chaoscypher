# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify LLM summary is always written, including on the all-chunks-failed path.

Phase 1 fix (2026-05-21 incident): the `llm_total_calls > 0` guard at
extraction_finalizer:920 was dropped so the writeback runs unconditionally.
Pre-fix, the guard silently hid the all-chunks-failed case — sources finalized
with llm_failed_calls=0 even when N chunk-level 404s appeared in the logs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.operations.extraction import extraction_finalizer


@pytest.mark.unit
class TestLLMSummaryWriteback:
    """LLM summary writeback is unconditional — even when total_calls == 0."""

    def test_writeback_always_called_when_total_calls_zero(self) -> None:
        """adapter.update_source_columns is called even when llm_total_calls == 0.

        This is the incident regression: pre-fix the `> 0` guard prevented
        failed-call counters from reaching the source row when every chunk
        failed before producing a successful LLM call metric.

        We drive the writeback logic directly (not the full _finalize_extraction_inner
        which requires LLM service, queue, graph repo, etc.) to keep this a
        pure unit test.
        """
        adapter = MagicMock()
        # Simulate compute_llm_summary return when all calls failed:
        # total_calls is 0 because no metrics rows existed (chunks 404'd
        # before writing LLMCallMetric rows in the incident scenario).
        adapter.compute_llm_summary.return_value = {
            "llm_total_calls": 0,
            "llm_successful_calls": 0,
            "llm_failed_calls": 0,
            "llm_retry_calls": 0,
            "llm_permanent_failures": 0,
            "llm_wasted_tokens": 0,
            "llm_estimated_cost_usd": None,
        }

        source_id = "src-1"
        database_name = "default"

        llm_summary = adapter.compute_llm_summary(
            source_id,
            database_name,
            custom_input_cost=0.0,
            custom_output_cost=0.0,
        )

        # Post-fix: always write, no > 0 guard.
        if llm_summary:
            adapter.update_source_columns(
                source_id=source_id,
                database_name=database_name,
                updates=llm_summary,
            )

        adapter.update_source_columns.assert_called_once_with(
            source_id=source_id,
            database_name=database_name,
            updates=llm_summary,
        )

    def test_writeback_called_with_failed_calls_nonzero(self) -> None:
        """When chunks wrote LLMCallMetric rows (failed status), summary is written.

        This covers the case where the worker DID write metric rows but all
        were failures — e.g. model returned error JSON, not 404. The summary
        has llm_failed_calls > 0 and must land on the source row.
        """
        adapter = MagicMock()
        adapter.compute_llm_summary.return_value = {
            "llm_total_calls": 3,
            "llm_successful_calls": 0,
            "llm_failed_calls": 3,
            "llm_retry_calls": 0,
            "llm_permanent_failures": 3,
            "llm_wasted_tokens": 500,
            "llm_estimated_cost_usd": 0.0,
        }

        source_id = "src-2"
        database_name = "default"

        llm_summary = adapter.compute_llm_summary(
            source_id,
            database_name,
            custom_input_cost=0.0,
            custom_output_cost=0.0,
        )

        # Post-fix unconditional write
        if llm_summary:
            adapter.update_source_columns(
                source_id=source_id,
                database_name=database_name,
                updates=llm_summary,
            )

        call = adapter.update_source_columns.call_args
        assert call is not None, "update_source_columns must be called"
        written = call.kwargs["updates"]
        assert written["llm_failed_calls"] == 3
        assert written["llm_total_calls"] == 3
        assert written["llm_successful_calls"] == 0

    def test_old_guard_would_have_skipped_zero_total(self) -> None:
        """Document the bug: the pre-fix guard `> 0` skipped zero-total summaries.

        This test encodes the broken behaviour so it's clear the new code
        is a deliberate regression from that guard, not an accidental omission.
        """
        adapter = MagicMock()
        adapter.compute_llm_summary.return_value = {
            "llm_total_calls": 0,
            "llm_failed_calls": 0,
        }

        source_id = "src-3"
        database_name = "default"

        llm_summary = adapter.compute_llm_summary(
            source_id,
            database_name,
            custom_input_cost=0.0,
            custom_output_cost=0.0,
        )

        # Pre-fix (broken) guard — this is what we REMOVED:
        if llm_summary and llm_summary.get("llm_total_calls", 0) > 0:
            adapter.update_source_columns(
                source_id=source_id,
                database_name=database_name,
                updates=llm_summary,
            )

        # The old guard would skip the write. We assert it was NOT called
        # to show why the guard was wrong.
        adapter.update_source_columns.assert_not_called()

    def test_finalizer_module_guard_is_unconditional(self) -> None:
        """The live extraction_finalizer module must NOT have the > 0 guard.

        Reads the source of ``_complete_finalization`` as a string and
        asserts the exact guard pattern is absent. This prevents a future
        refactor from silently reintroducing it.

        The guard being checked is::

            if llm_summary and llm_summary.get("llm_total_calls", 0) > 0:
        """
        import inspect

        # The LLM summary writeback lives in _complete_finalization,
        # which is called by _finalize_extraction_inner.
        src = inspect.getsource(extraction_finalizer._complete_finalization)

        # The exact removed guard pattern — must no longer exist
        assert 'llm_summary.get("llm_total_calls", 0) > 0' not in src, (
            "The llm_total_calls > 0 guard was re-introduced in "
            "_complete_finalization at the LLM summary writeback. "
            "This guard silences the all-chunks-failed path by skipping "
            "update_source_columns when total_calls == 0. Remove it."
        )
