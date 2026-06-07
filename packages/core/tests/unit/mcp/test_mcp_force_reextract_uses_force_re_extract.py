# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: MCP force=True re-extract path calls force_re_extract helper.

Audit fix #C5. Before this fix, the MCP get_tasks(force=True) path on a
COMMITTED source only called delete_extraction_submissions + a direct
transition_source_status(COMMITTED -> MCP_EXTRACTING). It left
commit_complete=True and extraction_results intact. The next
finalize-extraction call hit complete_extraction's InvalidStateError
guard and bricked the source.

This test verifies that the canonical force_re_extract helper is called
so all flags are atomically reset inside a single adapter.transaction().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.mcp.extraction import ExtractionOrchestrator
from chaoscypher_core.models import SourceStatus


def _make_committed_source(source_id: str = "src_committed") -> dict:
    """Build a minimal source dict for a COMMITTED source."""
    return {
        "id": source_id,
        "database_name": "default",
        "filename": "report.pdf",
        "status": SourceStatus.COMMITTED,
        "extraction_depth": "full",
        "extraction_domain": None,
        "extraction_chunk_indices": None,
        "stage_progress": {},
    }


@pytest.fixture
def mock_engine() -> MagicMock:
    """Create a mock Engine with all required dependencies."""
    engine = MagicMock()

    # Settings
    engine.settings.current_database = "default"
    engine.settings.mcp.max_extraction_payload_bytes = 10 * 1024 * 1024
    engine.settings.mcp.extraction_rate_limit_per_minute = 100
    # Chunking settings used by ``_build_source_groups``. Workstream 3
    # Task 3.5 replaced hardcoded constants with reads from these fields.
    engine.settings.chunking.target_group_tokens = 900
    engine.settings.chunking.group_overlap = 1
    # Quick-mode sampling reads from ``analysis.quick_sample_size`` so
    # MCP and Cortex's ``import_service`` agree on the same source.
    engine.settings.analysis.quick_sample_size = 5

    # Storage adapter — returns COMMITTED source
    engine.storage_adapter = MagicMock()
    engine.storage_adapter.get_source.return_value = _make_committed_source()
    engine.storage_adapter.transition_source_status.return_value = True
    engine.storage_adapter.delete_extraction_submissions.return_value = None
    # Stage-progress port methods are async — wire as AsyncMock so awaiting them works
    engine.storage_adapter.start_stage = AsyncMock()
    engine.storage_adapter.tick_stage = AsyncMock()
    engine.storage_adapter.complete_stage = AsyncMock()
    engine.storage_adapter.update_stage_extras = AsyncMock()

    # Graph repository
    engine.graph_repository = MagicMock()
    engine.graph_repository.list_templates.return_value = []
    engine.graph_repository.delete_source_artifacts.return_value = {
        "nodes_deleted": 0,
        "edges_deleted": 0,
        "templates_deleted": 0,
    }

    return engine


@pytest.fixture
def orchestrator(mock_engine: MagicMock) -> ExtractionOrchestrator:
    """Create an ExtractionOrchestrator with mock engine + chunk-indices shortcut."""
    from tests.unit.mcp.conftest import install_chunk_indices_shortcut

    orch = ExtractionOrchestrator(engine=mock_engine)
    install_chunk_indices_shortcut(orch)
    return orch


@pytest.mark.asyncio
async def test_force_true_calls_force_re_extract(
    orchestrator: ExtractionOrchestrator,
    mock_engine: MagicMock,
) -> None:
    """get_tasks(force=True) on COMMITTED source must call force_re_extract.

    The canonical helper atomically resets commit_complete, extraction_complete,
    and extraction_results inside adapter.transaction() so a subsequent
    finalize-extraction call cannot hit complete_extraction's InvalidStateError
    guard and brick the source.
    """
    storage_adapter = mock_engine.storage_adapter
    graph_repository = mock_engine.graph_repository

    with patch("chaoscypher_core.mcp.extraction.force_re_extract") as mock_force_re_extract:
        # Downstream methods (get_chunks_for_extraction, etc.) may not be fully
        # wired — wrap in try/except; we only care about the reset call being made
        # before any downstream failure.
        try:
            await orchestrator.get_tasks("src_committed", force=True)
        except Exception:
            pass

        mock_force_re_extract.assert_called_once_with(
            source_id="src_committed",
            database_name="default",
            storage_adapter=storage_adapter,
            graph_repository=graph_repository,
        )


@pytest.mark.asyncio
async def test_force_true_cas_transitions_from_indexed(
    orchestrator: ExtractionOrchestrator,
    mock_engine: MagicMock,
) -> None:
    """After force_re_extract, CAS must use from_status=INDEXED.

    force_re_extract leaves the source at INDEXED (per its docstring).
    The subsequent transition_source_status must start from INDEXED so the
    compare-and-swap matches and the source is not double-transitioned.
    """
    mock_engine.storage_adapter.get_chunks_for_extraction.return_value = [
        {"id": "c1", "chunk_index": 0, "content": "chunk text"},
    ]

    with patch("chaoscypher_core.mcp.extraction.force_re_extract"):
        try:
            await orchestrator.get_tasks("src_committed", force=True)
        except Exception:
            pass

        mock_engine.storage_adapter.transition_source_status.assert_called_once_with(
            "src_committed",
            from_status=SourceStatus.INDEXED,
            to_status=SourceStatus.MCP_EXTRACTING,
            database_name="default",
        )
