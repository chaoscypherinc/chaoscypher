# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for MCP extraction line-shape validation."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.mcp.extraction import (
    ExtractionOrchestrator,
    _validate_extraction_lines,
)


def _make_source(chunk_indices: list[int] | None = None) -> dict:
    chunks_total = len(chunk_indices or [0])
    return {
        "id": "src_001",
        "database_name": "default",
        "filename": "t.pdf",
        "status": "mcp_extracting",
        "extraction_chunk_indices": chunk_indices or [0],
        "stage_progress": {
            "mcp_extraction": {
                "total": chunks_total,
                "processed": 0,
                "avg_ms": None,
                "started_at": None,
                "last_activity": None,
                "completed_at": None,
                "extras": None,
            }
        },
    }


@pytest.fixture
def orchestrator():
    engine = MagicMock()
    engine.settings.current_database = "default"
    engine.settings.mcp.max_extraction_payload_bytes = 10 * 1024 * 1024
    engine.settings.mcp.extraction_rate_limit_per_minute = 100
    engine.storage_adapter = MagicMock()
    engine.storage_adapter.count_extraction_submissions.return_value = 1
    engine.storage_adapter.get_source.return_value = _make_source()
    engine.storage_adapter.list_extraction_submissions.return_value = []
    # Stage-progress port methods are async — wire as AsyncMock so awaiting them works
    engine.storage_adapter.start_stage = AsyncMock()
    engine.storage_adapter.tick_stage = AsyncMock()
    engine.storage_adapter.complete_stage = AsyncMock()
    engine.storage_adapter.update_stage_extras = AsyncMock()
    engine.extraction_service = AsyncMock()
    engine.commit_service = AsyncMock()
    from tests.unit.mcp.conftest import install_chunk_indices_shortcut

    orch = ExtractionOrchestrator(engine=engine)
    install_chunk_indices_shortcut(orch)
    return orch


class TestValidateExtractionLines:
    """_validate_extraction_lines parses per-line shape and returns errors."""

    def test_valid_entity_line_no_errors(self):
        errors = _validate_extraction_lines(
            entities_text="E|Alice|Person|Alice A.|0.9|S1|A person",
            relationships_text="",
        )
        assert errors == []

    def test_entity_missing_pipes_reports_error(self):
        errors = _validate_extraction_lines(
            entities_text="E|Alice|Person",  # too few fields
            relationships_text="",
        )
        assert len(errors) == 1
        assert errors[0]["line_type"] == "E"
        assert "expected 7 pipe-separated fields" in errors[0]["error"].lower()
        assert errors[0]["line_number"] == 1

    def test_property_non_integer_index_reports_error(self):
        errors = _validate_extraction_lines(
            entities_text="E|Alice|Person||0.9|S1|d\nP|abc|title|Queen",
            relationships_text="",
        )
        assert any("entity_index must be integer" in e["error"].lower() for e in errors)

    def test_property_negative_index_reports_error(self):
        errors = _validate_extraction_lines(
            entities_text="E|Alice|Person||0.9|S1|d\nP|-1|title|Queen",
            relationships_text="",
        )
        assert any("non-negative" in e["error"].lower() for e in errors)

    def test_property_index_out_of_range_reports_error(self):
        errors = _validate_extraction_lines(
            entities_text="E|Alice|Person||0.9|S1|d\nP|5|title|Queen",
            relationships_text="",
        )
        assert any("out of range" in e["error"].lower() for e in errors)

    def test_relationship_line_missing_pipes_reports_error(self):
        errors = _validate_extraction_lines(
            entities_text="E|Alice|Person||0.9|S1|d\nE|Bob|Person||0.9|S1|d",
            relationships_text="R|0|1",  # too few fields
        )
        assert any(e["line_type"] == "R" for e in errors)

    def test_relationship_non_integer_source_reports_error(self):
        errors = _validate_extraction_lines(
            entities_text="E|Alice|Person||0.9|S1|d\nE|Bob|Person||0.9|S1|d",
            relationships_text="R|foo|1|knows|0.9|S1|they know",
        )
        assert any("source" in e["error"].lower() for e in errors)

    def test_relationship_index_out_of_range_reports_error(self):
        errors = _validate_extraction_lines(
            entities_text="E|Alice|Person||0.9|S1|d",
            relationships_text="R|0|9|knows|0.9|S1|they know",
        )
        assert any(
            "target" in e["error"].lower() and "out of range" in e["error"].lower() for e in errors
        )

    def test_blank_and_comment_lines_ignored(self):
        errors = _validate_extraction_lines(
            entities_text="\n  \nE|Alice|Person||0.9|S1|d\n",
            relationships_text="\n",
        )
        assert errors == []


class TestSubmitChunkRejectsMalformed:
    """submit_chunk rejects malformed submissions with error list."""

    @pytest.mark.asyncio
    async def test_malformed_entity_rejected(self, orchestrator):
        result = await orchestrator.submit_chunk(
            source_id="src_001",
            chunk_group_index=0,
            entities_text="E|Alice",  # too few fields
            relationships_text="",
        )
        assert result["success"] is False
        assert result["error_code"] == "INVALID_LINE_SHAPE"
        assert len(result["errors"]) >= 1
        assert result["errors"][0]["line_type"] == "E"

    @pytest.mark.asyncio
    async def test_malformed_relationship_rejected(self, orchestrator):
        result = await orchestrator.submit_chunk(
            source_id="src_001",
            chunk_group_index=0,
            entities_text="E|Alice|Person||0.9|S1|d",
            relationships_text="R|bad|fields",
        )
        assert result["success"] is False
        assert result["error_code"] == "INVALID_LINE_SHAPE"
