# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for stuck source recovery."""

from unittest.mock import MagicMock

import pytest

from chaoscypher_neuron.recovery.sources import recover_stuck_sources


@pytest.fixture
def mock_adapter():
    """Create a mock SQLite adapter."""
    return MagicMock()


# ============================================================================
# recover_stuck_sources
# ============================================================================


class TestRecoverStuckSources:
    """Tests for recover_stuck_sources."""

    @pytest.mark.asyncio
    async def test_returns_zeros_when_no_stuck(self, mock_adapter) -> None:
        mock_adapter.get_stuck_extracting_sources.return_value = []
        result = await recover_stuck_sources(mock_adapter, "test_db")
        assert result == {"reset": 0, "marked_failed": 0}

    @pytest.mark.asyncio
    async def test_marks_failed_job_as_failed(self, mock_adapter) -> None:
        mock_adapter.get_stuck_extracting_sources.return_value = [
            {"id": "src1", "extraction_job_status": "failed"},
        ]
        result = await recover_stuck_sources(mock_adapter, "test_db")
        assert result["marked_failed"] == 1
        assert result["reset"] == 0
        mock_adapter.fail_extraction.assert_called_once_with(
            "src1", "Extraction job failed (recovered on worker restart)"
        )

    @pytest.mark.asyncio
    async def test_resets_non_failed_to_indexed(self, mock_adapter) -> None:
        mock_adapter.get_stuck_extracting_sources.return_value = [
            {"id": "src1", "extraction_job_status": "running"},
        ]
        result = await recover_stuck_sources(mock_adapter, "test_db")
        assert result["reset"] == 1
        assert result["marked_failed"] == 0
        # recover_stuck_sources calls adapter.update_file(source_id,
        # database_name=..., updates=...). Source ID is positional; the
        # update dict is a kwarg. ``SourceStatus.INDEXED`` is a StrEnum so
        # equality with the literal ``"indexed"`` holds.
        call = mock_adapter.update_file.call_args
        assert call.args[0] == "src1"
        assert call.kwargs["updates"]["status"] == "indexed"

    @pytest.mark.asyncio
    async def test_resets_null_job_status(self, mock_adapter) -> None:
        mock_adapter.get_stuck_extracting_sources.return_value = [
            {"id": "src1", "extraction_job_status": None},
        ]
        result = await recover_stuck_sources(mock_adapter, "test_db")
        assert result["reset"] == 1

    @pytest.mark.asyncio
    async def test_handles_mixed_statuses(self, mock_adapter) -> None:
        mock_adapter.get_stuck_extracting_sources.return_value = [
            {"id": "src1", "extraction_job_status": "failed"},
            {"id": "src2", "extraction_job_status": "running"},
            {"id": "src3", "extraction_job_status": None},
        ]
        result = await recover_stuck_sources(mock_adapter, "test_db")
        assert result["marked_failed"] == 1
        assert result["reset"] == 2

    @pytest.mark.asyncio
    async def test_survives_individual_update_failure(self, mock_adapter) -> None:
        mock_adapter.get_stuck_extracting_sources.return_value = [
            {"id": "src1", "extraction_job_status": "running"},
            {"id": "src2", "extraction_job_status": "running"},
        ]
        mock_adapter.update_file.side_effect = [Exception("db error"), None]
        result = await recover_stuck_sources(mock_adapter, "test_db")
        # First fails, second succeeds
        assert result["reset"] == 1

    @pytest.mark.asyncio
    async def test_clears_extraction_metadata_on_reset(self, mock_adapter) -> None:
        mock_adapter.get_stuck_extracting_sources.return_value = [
            {"id": "src1", "extraction_job_status": "running"},
        ]
        await recover_stuck_sources(mock_adapter, "test_db")
        # ``updates`` is a kwarg on adapter.update_file (see the matching
        # production call in recover_stuck_sources).
        updates = mock_adapter.update_file.call_args.kwargs["updates"]
        assert updates["extraction_started_at"] is None
        assert updates["current_extraction_job_id"] is None
        assert updates["step_description"] is None
        assert updates["current_step"] is None
        assert updates["total_steps"] is None
