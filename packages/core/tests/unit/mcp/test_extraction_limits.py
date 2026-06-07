# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for MCP extraction payload-size caps and per-source rate limiting."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.mcp.extraction import ExtractionOrchestrator


def _make_source(chunks_total: int = 3, chunk_indices: list[int] | None = None) -> dict:
    return {
        "id": "src_001",
        "database_name": "default",
        "filename": "t.pdf",
        "status": "mcp_extracting",
        "extraction_chunk_indices": chunk_indices or [0, 1, 2],
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
def mock_engine():
    engine = MagicMock()
    engine.settings.current_database = "default"
    engine.settings.mcp.max_extraction_payload_bytes = 1024  # tiny cap for test
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
    engine.graph_repository = MagicMock()
    engine.extraction_service = AsyncMock()
    engine.commit_service = AsyncMock()
    return engine


@pytest.fixture
def orchestrator(mock_engine):
    from tests.unit.mcp.conftest import install_chunk_indices_shortcut

    orch = ExtractionOrchestrator(engine=mock_engine)
    install_chunk_indices_shortcut(orch)
    return orch


class TestPayloadSizeCap:
    """submit_chunk rejects payloads larger than configured limit."""

    @pytest.mark.asyncio
    async def test_oversized_entities_text_rejected(self, orchestrator):
        big_payload = "E|" + ("X" * 2000) + "|P||0.9|S1|d"
        result = await orchestrator.submit_chunk(
            source_id="src_001",
            chunk_group_index=0,
            entities_text=big_payload,
            relationships_text="",
        )
        assert result["success"] is False
        assert result["error_code"] == "PAYLOAD_TOO_LARGE"
        assert "1024" in result["error"]

    @pytest.mark.asyncio
    async def test_oversized_relationships_text_rejected(self, orchestrator):
        big_rel = "R|0|1|t|0.9|S1|" + ("Y" * 2000)
        result = await orchestrator.submit_chunk(
            source_id="src_001",
            chunk_group_index=0,
            entities_text="E|A|P||0.9|S1|d",
            relationships_text=big_rel,
        )
        assert result["success"] is False
        assert result["error_code"] == "PAYLOAD_TOO_LARGE"

    @pytest.mark.asyncio
    async def test_combined_size_is_checked(self, orchestrator):
        half = "X" * 600
        result = await orchestrator.submit_chunk(
            source_id="src_001",
            chunk_group_index=0,
            entities_text=f"E|A|P||0.9|S1|{half}",
            relationships_text=f"R|0|1|t|0.9|S1|{half}",
        )
        # Each individually fits in 1024, but combined exceeds it.
        assert result["success"] is False
        assert result["error_code"] == "PAYLOAD_TOO_LARGE"


class TestRateLimit:
    """submit_chunk enforces per-source rate limit."""

    @pytest.mark.asyncio
    async def test_rate_limit_rejects_excess_submissions(self, mock_engine):
        from tests.unit.mcp.conftest import install_chunk_indices_shortcut

        mock_engine.settings.mcp.extraction_rate_limit_per_minute = 3
        mock_engine.settings.mcp.max_extraction_payload_bytes = 10 * 1024
        orchestrator = ExtractionOrchestrator(engine=mock_engine)
        install_chunk_indices_shortcut(orchestrator)

        # First 3 submissions succeed.
        for i in range(3):
            r = await orchestrator.submit_chunk(
                source_id="src_001",
                chunk_group_index=i,
                entities_text="E|A|P||0.9|S1|d",
                relationships_text="",
            )
            assert r["success"] is True, f"attempt {i} should succeed"

        # 4th submission within the window is rejected.
        r = await orchestrator.submit_chunk(
            source_id="src_001",
            chunk_group_index=3,
            entities_text="E|A|P||0.9|S1|d",
            relationships_text="",
        )
        assert r["success"] is False
        assert r["error_code"] == "RATE_LIMIT_EXCEEDED"
        assert "3" in r["error"]

    @pytest.mark.asyncio
    async def test_rate_limit_is_per_source(self, mock_engine):
        from tests.unit.mcp.conftest import install_chunk_indices_shortcut

        mock_engine.settings.mcp.extraction_rate_limit_per_minute = 2
        orchestrator = ExtractionOrchestrator(engine=mock_engine)
        install_chunk_indices_shortcut(orchestrator)

        await orchestrator.submit_chunk(
            source_id="src_A",
            chunk_group_index=0,
            entities_text="E|A|P||0.9|S1|d",
            relationships_text="",
        )
        await orchestrator.submit_chunk(
            source_id="src_A",
            chunk_group_index=1,
            entities_text="E|A|P||0.9|S1|d",
            relationships_text="",
        )
        # Different source_id should NOT share the bucket.
        mock_engine.storage_adapter.get_source.return_value = {
            **_make_source(),
            "id": "src_B",
        }
        r = await orchestrator.submit_chunk(
            source_id="src_B",
            chunk_group_index=0,
            entities_text="E|A|P||0.9|S1|d",
            relationships_text="",
        )
        assert r["success"] is True

    @pytest.mark.asyncio
    async def test_rate_limit_window_expires(self, mock_engine, monkeypatch):
        from tests.unit.mcp.conftest import install_chunk_indices_shortcut

        mock_engine.settings.mcp.extraction_rate_limit_per_minute = 2
        orchestrator = ExtractionOrchestrator(engine=mock_engine)
        install_chunk_indices_shortcut(orchestrator)

        fake_now = [1000.0]

        def now() -> float:
            return fake_now[0]

        monkeypatch.setattr("chaoscypher_core.mcp.extraction._monotonic", now, raising=False)

        for _ in range(2):
            await orchestrator.submit_chunk(
                source_id="src_001",
                chunk_group_index=0,
                entities_text="E|A|P||0.9|S1|d",
                relationships_text="",
            )

        # Advance past the 60s window.
        fake_now[0] += 61.0
        r = await orchestrator.submit_chunk(
            source_id="src_001",
            chunk_group_index=0,
            entities_text="E|A|P||0.9|S1|d",
            relationships_text="",
        )
        assert r["success"] is True
