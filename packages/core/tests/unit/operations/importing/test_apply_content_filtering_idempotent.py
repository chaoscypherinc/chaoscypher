# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: filter_stats persisted on resume path too.

Audit fix #H/core (apply_content_filtering gated). Filter stats were
only written when existing_job was None — so a resumed job silently
lost its 'X% content stripped' label after a crash + recovery cycle.
Filtering is deterministic; the persist call is idempotent.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.engine.extraction.orchestration import FilterStats


@pytest.mark.asyncio
async def test_persist_filter_stats_called_on_fresh_path() -> None:
    """_persist_filter_stats writes stats when no existing job (fresh path)."""
    from chaoscypher_core.operations.importing.import_service import _persist_filter_stats

    adapter = MagicMock()
    stats = FilterStats(
        total_chunks=10,
        excluded_chunks=3,
        avg_content_stripped_ratio=0.2,
    )

    _persist_filter_stats(adapter, "job-001", stats)

    adapter.update_extraction_job.assert_called_once_with(
        "job-001",
        {
            "filtered_chunks": 3,
            "filtered_content_ratio": 0.2,
        },
    )


@pytest.mark.asyncio
async def test_persist_filter_stats_called_on_resume_path() -> None:
    """_persist_filter_stats writes stats when an existing job is present (resume path).

    This is the regression guard: previously the persist was gated on
    existing_job is None, so resumed jobs never got their filter stats written.
    """
    from chaoscypher_core.operations.importing.import_service import _persist_filter_stats

    adapter = MagicMock()
    stats = FilterStats(
        total_chunks=10,
        excluded_chunks=3,
        avg_content_stripped_ratio=0.2,
    )

    # Simulate resume path: existing_job is truthy, but _persist_filter_stats
    # should not care — it no longer has an existing_job parameter.
    _persist_filter_stats(adapter, "job-001", stats)

    adapter.update_extraction_job.assert_called_once_with(
        "job-001",
        {
            "filtered_chunks": 3,
            "filtered_content_ratio": 0.2,
        },
    )


@pytest.mark.asyncio
async def test_persist_filter_stats_skips_all_zeros() -> None:
    """_persist_filter_stats does NOT write when stats are all zero (no-op filtering)."""
    from chaoscypher_core.operations.importing.import_service import _persist_filter_stats

    adapter = MagicMock()
    stats = FilterStats(
        total_chunks=10,
        excluded_chunks=0,
        avg_content_stripped_ratio=0.0,
    )

    _persist_filter_stats(adapter, "job-001", stats)

    adapter.update_extraction_job.assert_not_called()


@pytest.mark.asyncio
async def test_persist_filter_stats_skips_none() -> None:
    """_persist_filter_stats is a no-op when filter_stats is None."""
    from chaoscypher_core.operations.importing.import_service import _persist_filter_stats

    adapter = MagicMock()

    _persist_filter_stats(adapter, "job-001", None)

    adapter.update_extraction_job.assert_not_called()


@pytest.mark.asyncio
async def test_persist_filter_stats_writes_when_only_ratio_nonzero() -> None:
    """_persist_filter_stats writes when excluded_chunks=0 but ratio > 0 (stripping only)."""
    from chaoscypher_core.operations.importing.import_service import _persist_filter_stats

    adapter = MagicMock()
    stats = FilterStats(
        total_chunks=10,
        excluded_chunks=0,
        avg_content_stripped_ratio=0.15,
    )

    _persist_filter_stats(adapter, "job-002", stats)

    adapter.update_extraction_job.assert_called_once_with(
        "job-002",
        {
            "filtered_chunks": 0,
            "filtered_content_ratio": 0.15,
        },
    )
