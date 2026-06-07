# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Additional coverage for stuck source recovery.

Complements ``test_stuck_sources.py`` with the missing-id skip branch.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

from chaoscypher_neuron.recovery.sources import recover_stuck_sources


@pytest.fixture
def mock_adapter():
    """Create a mock SQLite adapter."""
    return MagicMock()


class TestRecoverStuckSourcesMissingId:
    """Tests for the missing-id continue branch."""

    @pytest.mark.asyncio
    async def test_skips_source_with_missing_id(
        self, mock_adapter, structlog_for_caplog: Any
    ) -> None:
        """A stuck source dict with no ``id`` is logged and skipped."""
        mock_adapter.get_stuck_extracting_sources.return_value = [
            {"extraction_job_status": "running"},  # no "id"
        ]

        with capture_logs() as captured:
            result = await recover_stuck_sources(mock_adapter, "test_db")

        assert result == {"reset": 0, "marked_failed": 0}
        # Neither the reset nor the fail path ran for the id-less source.
        mock_adapter.update_file.assert_not_called()
        mock_adapter.fail_extraction.assert_not_called()

        events = [e["event"] for e in captured]
        assert "recovery_source_missing_id" in events

    @pytest.mark.asyncio
    async def test_missing_id_does_not_block_valid_sources(self, mock_adapter) -> None:
        """An id-less source is skipped while a valid sibling is still reset."""
        mock_adapter.get_stuck_extracting_sources.return_value = [
            {"extraction_job_status": "running"},  # no id -> skipped
            {"id": "src-ok", "extraction_job_status": "running"},  # reset
        ]

        result = await recover_stuck_sources(mock_adapter, "test_db")

        assert result["reset"] == 1
        mock_adapter.update_file.assert_called_once()
        assert mock_adapter.update_file.call_args.args[0] == "src-ok"
