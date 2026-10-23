# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The CLI extraction path records truncation / loop-abort counters.

``_record_integrity_counters`` is the CLI half of the #651 fix: before it,
``AIEntityExtractor``'s ``finish_reason`` and ``aborted_by_loop`` were
discarded on the standalone path, so every ``chaoscypher load`` of a source
whose chunks were cut off at the token budget reported a clean ingest -- and
because truncation *raises* extraction scores, the failure surfaced as an
inflated quality reading rather than an error.

The method and its call site in ``_extract_and_finalize`` had no test of any
kind; the audit in ``tests/test_llm_metrics_and_quality_counters.py`` that
exists to notice counter-site changes globs ``packages/core`` only and so
cannot see the CLI's own site. These tests drive the real call site through
``_extract_and_finalize``, so deleting it -- i.e. re-introducing bug 2 of
 #651 verbatim -- fails them.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cli.sources.service import CLISourceProcessingService
from chaoscypher_core.services.quality.counters import QualityCounter


_BASE = "chaoscypher_core.services.sources.engine.extraction"
_INCREMENT_PATH = "chaoscypher_core.services.quality.counters.increment_quality_counter"


async def _counters_for(
    ctx: MagicMock, chunk_metrics: dict[str, Any] | None, plan: Any
) -> list[QualityCounter]:
    """Drive one CLI extraction group and return the counters it incremented.

    The group is shorter than ``empty_output_retry_min_chars``: an answer
    that keeps nothing is no degenerate output there (a heading holds
    nothing), so it completes on its first try.
    """
    ctx.storage_adapter.update_step_progress = MagicMock()
    ctx.settings.extraction.empty_output_retry_min_chars = 200

    extractor = MagicMock()
    extractor.extract_single_chunk = AsyncMock(return_value=([], [], 10, 5, chunk_metrics))

    extraction_service = MagicMock()
    extraction_service.finalize_distributed_extraction = AsyncMock(
        return_value={"entities": [], "relationships": []}
    )

    spend_tracker = MagicMock()
    spend_tracker.check_and_raise = MagicMock()
    spend_tracker.record = MagicMock()

    seen: list[QualityCounter] = []

    async def fake_increment(
        *, adapter: Any, source_id: str, database_name: str, counter: QualityCounter, n: int = 1
    ) -> None:
        assert source_id == "fid", source_id
        assert n == 1, n
        seen.append(counter)

    collector = MagicMock()
    collector.attempts = [{"call": 1}]

    with (
        patch(f"{_BASE}.utils.ai_entities.AIEntityExtractor", return_value=extractor),
        patch(f"{_BASE}.service.ExtractionService", return_value=extraction_service),
        patch(
            "chaoscypher_core.services.llm.spend.get_llm_spend_tracker",
            return_value=spend_tracker,
        ),
        patch(_INCREMENT_PATH, AsyncMock(side_effect=fake_increment)),
    ):
        service = CLISourceProcessingService(ctx)
        await service._extract_and_finalize(
            groups_to_process=[{"combined_content": "group zero"}],
            file_id="fid",
            plan=plan,
            metrics_collector=collector,
            file_record={},
            detected_domain_name=None,
            forced_domain=None,
            total_groups=1,
            depth="full",
        )

    extractor.extract_single_chunk.assert_awaited_once()
    # The fake keeps nothing from its 5 output tokens: the zero-yield counter
    # (audit wave 5) fires on every case here and has its own test below.
    assert seen.count(QualityCounter.LLM_CHUNKS_ZERO_YIELD) == 1
    return [c for c in seen if c is not QualityCounter.LLM_CHUNKS_ZERO_YIELD]


@pytest.mark.asyncio
async def test_truncated_chunk_increments_truncation_counter(
    mock_cli_context_with_llm: MagicMock,
    stub_extraction_plan: Any,
) -> None:
    """``finish_reason == "length"`` is the #651 bug-2 case: budget exhausted."""
    seen = await _counters_for(
        mock_cli_context_with_llm, {"finish_reason": "length"}, stub_extraction_plan
    )
    assert seen == [QualityCounter.LLM_CHUNKS_TRUNCATED]


@pytest.mark.asyncio
async def test_loop_aborted_chunk_increments_loop_abort_counter(
    mock_cli_context_with_llm: MagicMock,
    stub_extraction_plan: Any,
) -> None:
    """``aborted_by_loop`` is the other signal the CLI path used to drop.

    Since audit wave 6 a loop abort is degenerate output and retries on the
    decoding ladder while a retry is left; the counter fires on the attempt
    whose partial output is kept, so this runs the group at its last attempt.
    """
    mock_cli_context_with_llm.settings.retries.extraction_chunk_max = 0
    seen = await _counters_for(
        mock_cli_context_with_llm,
        {"finish_reason": "stop", "aborted_by_loop": True},
        stub_extraction_plan,
    )
    assert seen == [QualityCounter.LLM_CHUNKS_ABORTED_BY_LOOP]


@pytest.mark.asyncio
async def test_both_signals_increment_both_counters(
    mock_cli_context_with_llm: MagicMock,
    stub_extraction_plan: Any,
) -> None:
    """A chunk that truncated AND loop-aborted records both, in that order."""
    mock_cli_context_with_llm.settings.retries.extraction_chunk_max = 0  # see above
    seen = await _counters_for(
        mock_cli_context_with_llm,
        {"finish_reason": "length", "aborted_by_loop": True},
        stub_extraction_plan,
    )
    assert seen == [
        QualityCounter.LLM_CHUNKS_TRUNCATED,
        QualityCounter.LLM_CHUNKS_ABORTED_BY_LOOP,
    ]


@pytest.mark.asyncio
async def test_clean_chunk_increments_nothing(
    mock_cli_context_with_llm: MagicMock, stub_extraction_plan: Any
) -> None:
    """A clean finish must not bump either counter (no false truncation)."""
    seen = await _counters_for(
        mock_cli_context_with_llm,
        {"finish_reason": "stop", "aborted_by_loop": False},
        stub_extraction_plan,
    )
    assert seen == []


@pytest.mark.asyncio
async def test_missing_metrics_dict_is_a_noop(
    mock_cli_context_with_llm: MagicMock, stub_extraction_plan: Any
) -> None:
    """An extractor returning no metrics must not raise on the CLI path."""
    seen = await _counters_for(mock_cli_context_with_llm, {}, stub_extraction_plan)
    assert seen == []


@pytest.mark.asyncio
async def test_a_group_that_keeps_nothing_counts_as_zero_yield(
    mock_cli_context_with_llm: MagicMock, stub_extraction_plan: Any
) -> None:
    """Tokens produced, no entity and no relationship kept: ``LLM_CHUNKS_ZERO_YIELD`` (wave 5)."""
    from chaoscypher_core.services.sources.engine.extraction.chunk_failures import zero_yield

    await _counters_for(mock_cli_context_with_llm, {"finish_reason": "stop"}, stub_extraction_plan)
    assert zero_yield(output_tokens=5, entities=[], relationships=[])
    assert not zero_yield(output_tokens=0, entities=[], relationships=[])
    assert not zero_yield(output_tokens=5, entities=[{"name": "x"}], relationships=[])
