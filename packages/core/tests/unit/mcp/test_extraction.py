# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ExtractionOrchestrator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.mcp.extraction import ExtractionOrchestrator
from chaoscypher_core.settings import EngineSettings


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #


def _make_template(name: str, description: str = "") -> MagicMock:
    """Build a fake Template object returned by graph_repository.list_templates."""
    t = MagicMock()
    t.name = name
    t.description = description
    return t


def _make_source(
    source_id: str = "src_001",
    status: str = "indexed",
    filename: str = "test.pdf",
    domain: str | None = None,
    chunks_total: int | None = None,
    chunks_submitted: int | None = None,
    chunk_indices: list[int] | None = None,
) -> dict:
    """Build a source dict as returned by storage_adapter.get_source."""
    stage_progress: dict = {}
    if chunks_total is not None:
        stage_progress["mcp_extraction"] = {
            "total": chunks_total,
            "processed": chunks_submitted or 0,
            "avg_ms": None,
            "started_at": None,
            "last_activity": None,
            "completed_at": None,
            "extras": None,
        }
    return {
        "id": source_id,
        "database_name": "default",
        "filename": filename,
        "status": status,
        "extraction_domain": domain,
        "_test_expected_chunk_indices": chunk_indices,
        "stage_progress": stage_progress,
    }


def _make_chunk(
    chunk_id: str,
    source_id: str,
    group_index: int,
    chunk_index: int,
    content: str,
) -> dict:
    """Build a chunk dict as returned by storage_adapter.list_chunks."""
    return {
        "id": chunk_id,
        "source_id": source_id,
        "database_name": "default",
        "group_index": group_index,
        "chunk_index": chunk_index,
        "content": content,
    }


@pytest.fixture
def mock_engine():
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
    # The extraction plan reads these sections (prompt inputs, presets,
    # limits): the real ``EngineSettings`` defaults.
    engine.settings.llm = EngineSettings().llm
    engine.settings.extraction = EngineSettings().extraction

    # Storage adapter (implements SourceStorageProtocol + ExtractionSubmissionStorageProtocol)
    engine.storage_adapter = MagicMock()
    # Stage-progress port methods are async — wire as AsyncMock so awaiting them works
    engine.storage_adapter.start_stage = AsyncMock()
    engine.storage_adapter.tick_stage = AsyncMock()
    engine.storage_adapter.complete_stage = AsyncMock()
    engine.storage_adapter.update_stage_extras = AsyncMock()

    # Graph repository
    engine.graph_repository = MagicMock()
    engine.graph_repository.list_templates.return_value = []

    # Extraction service (lazy property)
    engine.extraction_service = AsyncMock()

    # Commit service (lazy property)
    engine.commit_service = AsyncMock()

    return engine


@pytest.fixture
def orchestrator(mock_engine):
    """Create an ExtractionOrchestrator with mock engine + chunk-indices shortcut."""
    from tests.unit.mcp.conftest import install_chunk_indices_shortcut

    orch = ExtractionOrchestrator(engine=mock_engine)
    install_chunk_indices_shortcut(orch)
    return orch


# ------------------------------------------------------------------ #
#  TestGetTasks
# ------------------------------------------------------------------ #


class TestGetTasks:
    """ExtractionOrchestrator.get_tasks() tests."""

    @pytest.mark.asyncio
    async def test_returns_metadata_for_indexed_source(self, orchestrator, mock_engine):
        """Indexed source returns task metadata and transitions to mcp_extracting."""
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="indexed", filename="report.pdf"
        )
        mock_engine.storage_adapter.list_chunks.return_value = [
            _make_chunk("c1", "src_001", 0, 0, "chunk 0"),
            _make_chunk("c2", "src_001", 0, 1, "chunk 0b"),
            _make_chunk("c3", "src_001", 1, 2, "chunk 1"),
        ]
        # get_chunks_for_extraction returns lightweight chunk dicts for dynamic grouping
        mock_engine.storage_adapter.get_chunks_for_extraction.return_value = [
            {"id": "c1", "chunk_index": 0, "content": "chunk 0"},
            {"id": "c2", "chunk_index": 1, "content": "chunk 0b"},
            {"id": "c3", "chunk_index": 2, "content": "chunk 1"},
        ]
        mock_engine.graph_repository.list_templates.side_effect = [
            [_make_template("Person"), _make_template("Organization")],
            [_make_template("works_at")],
        ]

        result = await orchestrator.get_tasks("src_001")

        assert result["source_id"] == "src_001"
        assert result["filename"] == "report.pdf"
        assert result["status"] == "mcp_extracting"
        assert result["total_chunks"] >= 1  # dynamic grouping based on token budget
        assert "entity_instructions" in result
        assert "relationship_instructions" in result
        assert "existing_templates" in result
        assert len(result["existing_templates"]["node_templates"]) == 2
        assert len(result["existing_templates"]["edge_templates"]) == 1

        # Verify status transition was called
        calls = mock_engine.storage_adapter.update_source.call_args_list
        # First call sets status to mcp_extracting
        first_update = calls[0][0][1]
        assert first_update["status"] == "mcp_extracting"
        assert first_update["extraction_mode"] == "mcp"

    @pytest.mark.asyncio
    async def test_rejects_non_indexed_source(self, orchestrator, mock_engine):
        """Source in 'pending' status should be rejected."""
        mock_engine.storage_adapter.get_source.return_value = _make_source(status="pending")

        with pytest.raises(ValueError, match="expected 'indexed'"):
            await orchestrator.get_tasks("src_001")

    @pytest.mark.asyncio
    async def test_rejects_extracting_source(self, orchestrator, mock_engine):
        """Source in 'extracting' status should be rejected."""
        mock_engine.storage_adapter.get_source.return_value = _make_source(status="extracting")

        with pytest.raises(ValueError, match="expected 'indexed'"):
            await orchestrator.get_tasks("src_001")

    @pytest.mark.asyncio
    async def test_force_allows_committed_source(self, orchestrator, mock_engine):
        """Force=True allows re-extraction of committed source."""
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="committed", filename="committed.pdf"
        )
        mock_engine.storage_adapter.list_chunks.return_value = [
            _make_chunk("c1", "src_001", 0, 0, "chunk"),
        ]
        # get_chunks_for_extraction returns lightweight chunk dicts for dynamic grouping
        mock_engine.storage_adapter.get_chunks_for_extraction.return_value = [
            {"id": "c1", "chunk_index": 0, "content": "chunk"},
        ]
        mock_engine.graph_repository.list_templates.return_value = []

        result = await orchestrator.get_tasks("src_001", force=True)

        assert result["status"] == "mcp_extracting"
        assert result["total_chunks"] == 1
        # Should have cleared previous submissions
        mock_engine.storage_adapter.delete_extraction_submissions.assert_called_once_with(
            "src_001", "default"
        )

    @pytest.mark.asyncio
    async def test_source_not_found_raises(self, orchestrator, mock_engine):
        """Missing source raises ValueError."""
        mock_engine.storage_adapter.get_source.return_value = None

        with pytest.raises(ValueError, match="not found"):
            await orchestrator.get_tasks("nonexistent")

    @pytest.mark.asyncio
    async def test_uses_compare_and_swap_for_status_transition(self, orchestrator, mock_engine):
        """get_tasks uses transition_source_status for atomic indexed->mcp_extracting."""
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="indexed",
        )
        mock_engine.storage_adapter.get_chunks_for_extraction.return_value = [
            {"id": "c1", "chunk_index": 0, "content": "chunk"},
        ]
        mock_engine.storage_adapter.transition_source_status.return_value = True
        mock_engine.graph_repository.list_templates.return_value = []

        await orchestrator.get_tasks("src_001")

        mock_engine.storage_adapter.transition_source_status.assert_called_once_with(
            "src_001",
            from_status="indexed",
            to_status="mcp_extracting",
            database_name="default",
        )

    @pytest.mark.asyncio
    async def test_cas_failure_raises_conflict(self, orchestrator, mock_engine):
        """If another client already started extraction, get_tasks raises."""
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="indexed",
        )
        mock_engine.storage_adapter.transition_source_status.return_value = False

        with pytest.raises(ValueError, match="already.*extraction|status.*changed"):
            await orchestrator.get_tasks("src_001")

    @pytest.mark.asyncio
    async def test_auto_unforced_indexed_source_parks_before_cas(self, orchestrator, mock_engine):
        """An auto (no forced_domain) INDEXED source parks for confirmation
        BEFORE the indexed->mcp_extracting CAS is attempted (no slot move).
        """
        src = _make_source(status="indexed", filename="report.pdf", domain=None)
        # gate_decision reads these persisted fields; an unforced, unconfirmed,
        # INDEXED source with confirmation_required must park.
        src["forced_domain"] = None
        src["confirmation_required"] = True
        src["extraction_confirmed_at"] = None
        mock_engine.storage_adapter.get_source.return_value = src
        mock_engine.storage_adapter.list_chunks.return_value = [
            _make_chunk("c1", "src_001", 0, 0, "the patient was given 5mg of drug"),
        ]

        with (
            patch(
                "chaoscypher_core.mcp.extraction.detect_extraction_domain",
                return_value={
                    "domain": None,
                    "detected_domain": "medical",
                    "confidence": 2.4,
                    "ranking": [{"domain": "medical", "score": 2.4}],
                    "low_confidence": False,
                    "entity_guidance": "",
                    "relationship_guidance": "",
                },
            ),
            patch("chaoscypher_core.mcp.extraction.park_for_confirmation") as mock_park,
        ):
            result = await orchestrator.get_tasks("src_001")

        # Parked payload, not extraction metadata.
        assert result["status"] == "awaiting_confirmation"
        assert result["source_id"] == "src_001"
        assert result["detected_domain"] == "medical"
        assert result["confidence"] == 2.4
        assert "next_steps" in result
        # The CAS must NOT have run — parking claims no slot.
        mock_engine.storage_adapter.transition_source_status.assert_not_called()
        # park_for_confirmation persisted the proposal atomically.
        mock_park.assert_called_once()
        _, kwargs = mock_park.call_args
        proposal = kwargs.get("proposal") or mock_park.call_args.args[2]
        assert proposal["detected_domain"] == "medical"
        assert proposal["ranking"][0]["domain"] == "medical"

    @pytest.mark.asyncio
    async def test_forced_domain_indexed_source_bypasses_gate(self, orchestrator, mock_engine):
        """A forced_domain source proceeds through the CAS without parking."""
        src = _make_source(status="indexed", domain="medical")
        src["forced_domain"] = "medical"
        src["confirmation_required"] = False
        src["extraction_confirmed_at"] = None
        mock_engine.storage_adapter.get_source.return_value = src
        mock_engine.storage_adapter.get_chunks_for_extraction.return_value = [
            {"id": "c1", "chunk_index": 0, "content": "chunk"},
        ]
        mock_engine.storage_adapter.transition_source_status.return_value = True
        mock_engine.graph_repository.list_templates.return_value = []
        # The medical domain's content exclusions drop this one tiny chunk as
        # boilerplate, and a source with no groups is now rejected after the
        # CAS; this test is about the gate, so pin one real group.
        orchestrator._get_group_indices = lambda _sid: {0}

        with patch("chaoscypher_core.mcp.extraction.park_for_confirmation") as mock_park:
            result = await orchestrator.get_tasks("src_001")

        assert result["status"] == "mcp_extracting"
        mock_park.assert_not_called()
        mock_engine.storage_adapter.transition_source_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirmed_source_proceeds_even_when_unforced(self, orchestrator, mock_engine):
        """A source with extraction_confirmed_at set short-circuits to proceed
        (a re-dispatch of a confirmed source never re-parks).
        """
        src = _make_source(status="indexed", domain="medical")
        src["forced_domain"] = None
        src["confirmation_required"] = True
        src["extraction_confirmed_at"] = "2026-05-28T10:00:00+00:00"
        mock_engine.storage_adapter.get_source.return_value = src
        mock_engine.storage_adapter.get_chunks_for_extraction.return_value = [
            {"id": "c1", "chunk_index": 0, "content": "chunk"},
        ]
        mock_engine.storage_adapter.transition_source_status.return_value = True
        mock_engine.graph_repository.list_templates.return_value = []
        # The medical domain's content exclusions drop this one tiny chunk as
        # boilerplate, and a source with no groups is now rejected after the
        # CAS; this test is about the gate, so pin one real group.
        orchestrator._get_group_indices = lambda _sid: {0}

        with patch("chaoscypher_core.mcp.extraction.park_for_confirmation") as mock_park:
            result = await orchestrator.get_tasks("src_001")

        assert result["status"] == "mcp_extracting"
        mock_park.assert_not_called()

    @pytest.mark.asyncio
    async def test_source_with_no_groups_releases_the_claim(self, orchestrator, mock_engine):
        """No chunk groups: the CAS is rolled back and get_tasks raises.

        Previously quick depth divided by zero and full depth handed out zero
        tasks, both leaving the source at mcp_extracting until the staleness
        window; the pipeline fails such a source with the same message.
        """
        from chaoscypher_core.models import SourceStatus

        mock_engine.storage_adapter.get_source.return_value = _make_source(status="indexed")
        mock_engine.storage_adapter.get_chunks_for_extraction.return_value = []
        mock_engine.storage_adapter.transition_source_status.return_value = True
        orchestrator._get_group_indices = lambda _sid: set()

        with pytest.raises(ValueError, match="No extraction groups could be built"):
            await orchestrator.get_tasks("src_001")

        calls = mock_engine.storage_adapter.transition_source_status.call_args_list
        assert len(calls) == 2, "expected the claiming CAS plus the release"
        assert calls[1].kwargs["from_status"] == SourceStatus.MCP_EXTRACTING
        assert calls[1].kwargs["to_status"] == SourceStatus.INDEXED

    def test_quick_sampling_of_no_indices_is_empty(self, orchestrator):
        """The shared sampler no longer divides by ``min(sample_size, 0)``."""
        assert orchestrator._sample_quick_indices(set()) == set()
        assert orchestrator._sample_quick_indices({0, 1, 2}) == {0, 1, 2}


# ------------------------------------------------------------------ #
#  TestGetChunks
# ------------------------------------------------------------------ #


class TestGetChunks:
    """ExtractionOrchestrator.get_chunks() tests."""

    @pytest.mark.asyncio
    async def test_returns_chunk_text_for_requested_indices(self, orchestrator, mock_engine):
        """Returns combined text for requested group indices."""
        # Mock _build_source_groups to return pre-built groups
        groups = [
            {
                "group_index": 0,
                "combined_content": "First sentence of chunk 0.\n\nSecond part of chunk 0.",
                "small_chunk_ids": ["c1", "c2"],
            },
            {
                "group_index": 1,
                "combined_content": "Content of chunk 1. Another sentence here.",
                "small_chunk_ids": ["c3"],
            },
            {
                "group_index": 2,
                "combined_content": "Content of chunk 2.",
                "small_chunk_ids": ["c4"],
            },
        ]
        with patch.object(orchestrator, "_build_source_groups", return_value=groups):
            result = await orchestrator.get_chunks("src_001", [0, 1])

        assert result["source_id"] == "src_001"
        assert len(result["chunks"]) == 2

        # Chunk 0 should combine c1 and c2
        chunk_0 = result["chunks"][0]
        assert chunk_0["index"] == 0
        assert "First sentence" in chunk_0["text"]
        assert "Second part" in chunk_0["text"]
        assert len(chunk_0["sentences"]) > 0
        assert chunk_0["token_estimate"] > 0

        # Chunk 1 should have c3
        chunk_1 = result["chunks"][1]
        assert chunk_1["index"] == 1
        assert "Content of chunk 1" in chunk_1["text"]

    @pytest.mark.asyncio
    async def test_includes_sentences(self, orchestrator, mock_engine):
        """Sentences are split from combined text."""
        groups = [
            {
                "group_index": 0,
                "combined_content": "Hello world. How are you?",
                "small_chunk_ids": ["c1"],
            },
        ]
        with patch.object(orchestrator, "_build_source_groups", return_value=groups):
            result = await orchestrator.get_chunks("src_001", [0])

        assert len(result["chunks"]) == 1
        chunk = result["chunks"][0]
        # Should have at least 1 sentence
        assert len(chunk["sentences"]) >= 1

    @pytest.mark.asyncio
    async def test_skips_missing_indices(self, orchestrator, mock_engine):
        """Indices with no matching chunks are silently skipped."""
        groups = [
            {
                "group_index": 0,
                "combined_content": "Only chunk.",
                "small_chunk_ids": ["c1"],
            },
        ]
        with patch.object(orchestrator, "_build_source_groups", return_value=groups):
            result = await orchestrator.get_chunks("src_001", [0, 5, 10])

        # Only index 0 exists
        assert len(result["chunks"]) == 1
        assert result["chunks"][0]["index"] == 0


# ------------------------------------------------------------------ #
#  TestSubmitChunk
# ------------------------------------------------------------------ #


class TestSubmitChunk:
    """ExtractionOrchestrator.submit_chunk() tests."""

    @pytest.mark.asyncio
    async def test_rejects_source_not_under_mcp_extraction(self, orchestrator, mock_engine):
        """A committed (or UI-reset) source takes no submissions.

        Rows written against any other status survived a non-force get_tasks
        and were committed as if current.
        """
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="committed", chunks_total=3, chunk_indices=[0, 1, 2]
        )

        result = await orchestrator.submit_chunk(
            source_id="src_001",
            chunk_group_index=0,
            entities_text="E|Alice|Person||0.9|S1|A person",
            relationships_text="",
        )

        assert result["success"] is False
        assert result["error_code"] == "INVALID_STATUS"
        mock_engine.storage_adapter.create_extraction_submission.assert_not_called()

    @pytest.mark.asyncio
    async def test_stores_submission_and_returns_progress(self, orchestrator, mock_engine):
        """Stores submission and returns progress counts."""
        mock_engine.storage_adapter.count_extraction_submissions.return_value = 1
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting", chunks_total=3, chunk_indices=[0, 1, 2]
        )
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {"entity_count": 5, "relationship_count": 3},
        ]

        result = await orchestrator.submit_chunk(
            source_id="src_001",
            chunk_group_index=0,
            entities_text="E|Alice|Person|Alice A.|0.9|S1|A person\nE|Bob|Person||0.8|S2|Another person",
            relationships_text="R|0|1|knows|0.9|S1|They know each other",
        )

        assert result["success"] is True
        assert result["chunk_group_index"] == 0
        assert result["chunks_submitted"] == 1
        assert result["chunks_total"] == 3
        assert result["ready_to_finalize"] is False

        # Verify submission was created
        mock_engine.storage_adapter.create_extraction_submission.assert_called_once()
        call_data = mock_engine.storage_adapter.create_extraction_submission.call_args[0][0]
        assert call_data["source_id"] == "src_001"
        assert call_data["chunk_group_index"] == 0
        assert call_data["entity_count"] == 2
        assert call_data["relationship_count"] == 1

    @pytest.mark.asyncio
    async def test_ready_to_finalize_when_all_submitted(self, orchestrator, mock_engine):
        """ready_to_finalize is True when all chunks are submitted."""
        mock_engine.storage_adapter.count_extraction_submissions.return_value = 2
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting", chunks_total=2, chunk_indices=[0, 1]
        )
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {"entity_count": 3, "relationship_count": 2},
            {"entity_count": 4, "relationship_count": 1},
        ]

        result = await orchestrator.submit_chunk(
            source_id="src_001",
            chunk_group_index=1,
            entities_text="E|Charlie|Person||0.9|S1|A third person",
            relationships_text="",
        )

        assert result["ready_to_finalize"] is True
        assert result["chunks_submitted"] == 2
        assert result["chunks_total"] == 2

    @pytest.mark.asyncio
    async def test_rejects_out_of_range_index(self, orchestrator, mock_engine):
        """submit_chunk rejects indices outside the expected set."""
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting",
            chunks_total=3,
            chunk_indices=[0, 1, 2],
        )

        result = await orchestrator.submit_chunk(
            source_id="src_001",
            chunk_group_index=99,
            entities_text="E|A|P||0.9|S1|d",
            relationships_text="",
        )

        assert result["success"] is False
        assert result["error_code"] == "INVALID_CHUNK_INDEX"
        assert "99" in result["error"]
        assert "[0, 1, 2]" in result["error"] or "0" in result["error"]
        # No submission created.
        mock_engine.storage_adapter.create_extraction_submission.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_negative_index(self, orchestrator, mock_engine):
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting",
            chunks_total=3,
            chunk_indices=[0, 1, 2],
        )
        result = await orchestrator.submit_chunk(
            source_id="src_001",
            chunk_group_index=-1,
            entities_text="E|A|P||0.9|S1|d",
            relationships_text="",
        )
        assert result["success"] is False
        assert result["error_code"] == "INVALID_CHUNK_INDEX"


# ------------------------------------------------------------------ #
#  TestGetProgress
# ------------------------------------------------------------------ #


class TestGetProgress:
    """ExtractionOrchestrator.get_progress() tests."""

    @pytest.mark.asyncio
    async def test_returns_submitted_and_missing_indices(self, orchestrator, mock_engine):
        """Reports submitted and missing indices correctly."""
        # 2 submissions (indices 0 and 2)
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {"chunk_group_index": 0, "entity_count": 3},
            {"chunk_group_index": 2, "entity_count": 2},
        ]
        # Source stores expected indices (set by get_tasks)
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting",
            chunk_indices=[0, 1, 2],
        )

        result = await orchestrator.get_progress("src_001")

        assert result["source_id"] == "src_001"
        assert result["total_chunks"] == 3
        assert result["submitted_indices"] == [0, 2]
        assert result["missing_indices"] == [1]
        assert result["ready_to_finalize"] is False

    @pytest.mark.asyncio
    async def test_ready_when_all_submitted(self, orchestrator, mock_engine):
        """ready_to_finalize is True when no indices are missing."""
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {"chunk_group_index": 0},
            {"chunk_group_index": 1},
        ]
        # Source stores expected indices (set by get_tasks)
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting",
            chunk_indices=[0, 1],
        )

        result = await orchestrator.get_progress("src_001")

        assert result["ready_to_finalize"] is True
        assert result["missing_indices"] == []


# ------------------------------------------------------------------ #
#  TestFinalize
# ------------------------------------------------------------------ #


class TestFinalize:
    """ExtractionOrchestrator.finalize() tests."""

    @pytest.mark.asyncio
    async def test_submission_sent_refs_resolve_to_group_chunks(self, orchestrator, mock_engine):
        """The MCP path aggregates through the shared aggregator.

        A submission's ``S<n>`` numbers its group text and lands on the
        group's small chunk.
        """
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting", filename="evidence.txt", domain="technical"
        )
        group = {
            "group_index": 0,
            "small_chunk_ids": ["c1", "c2"],
            "combined_content": "Alice met Bob.\n\nBob left town.",
        }
        mock_engine.storage_adapter.get_chunks_for_extraction.return_value = [
            {"id": "c1", "chunk_index": 0, "content": "Alice met Bob."},
            {"id": "c2", "chunk_index": 1, "content": "Bob left town."},
        ]
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {
                "chunk_group_index": 0,
                "entities_text": "E|Alice|Person||0.9|S1|A\nE|Bob|Person||0.8|S2|B",
                "relationships_text": "R|0|1|knows|0.9|S1-S2|They meet",
            }
        ]
        mock_engine.extraction_service.finalize_distributed_extraction = AsyncMock(
            return_value={"entities": [], "relationships": [], "metadata": {}}
        )
        mock_engine.commit_service.commit = AsyncMock(
            return_value={"created_nodes": [], "created_edges": [], "created_templates": []}
        )
        with patch.object(orchestrator, "_build_source_groups", return_value=[group]):
            await orchestrator.finalize("src_001")

        call_kwargs = mock_engine.extraction_service.finalize_distributed_extraction.call_args[1]
        alice, bob = call_kwargs["raw_entities"]
        assert alice["cited_chunk_id"] == "c1"
        assert bob["cited_chunk_id"] == "c2"
        assert bob["evidence"][0]["text"] == "Bob left town."
        (rel,) = call_kwargs["raw_relationships"]
        assert [e["chunk_id"] for e in rel["evidence"]] == ["c1", "c2"]

    @pytest.mark.asyncio
    async def test_parses_and_remaps_two_chunks(self, orchestrator, mock_engine):
        """Two-chunk finalization correctly remaps indices and annotates chunk_index."""
        # Setup source
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting",
            filename="two_chunk.pdf",
            domain="technical",
        )

        # 2 chunk groups
        mock_engine.storage_adapter.list_chunks.return_value = [
            _make_chunk("c1", "src_001", 0, 0, "chunk 0"),
            _make_chunk("c2", "src_001", 1, 1, "chunk 1"),
        ]

        # 2 submissions
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {
                "chunk_group_index": 0,
                "entities_text": (
                    "E|Alice|Person||0.9|S1|A person\nE|Bob|Person||0.8|S2|Another person"
                ),
                "relationships_text": "R|0|1|knows|0.9|S1|They know each other",
            },
            {
                "chunk_group_index": 1,
                "entities_text": (
                    "E|Charlie|Person||0.9|S1|Third person\n"
                    "P|0|role|Manager\n"
                    "E|Dave|Person||0.8|S2|Fourth person"
                ),
                "relationships_text": "R|0|1|works_with|0.8|S1|They work together",
            },
        ]

        # Mock extraction service result
        mock_engine.extraction_service.finalize_distributed_extraction = AsyncMock(
            return_value={
                "entities": [
                    {"name": "Alice", "type": "Person"},
                    {"name": "Bob", "type": "Person"},
                    {"name": "Charlie", "type": "Person"},
                    {"name": "Dave", "type": "Person"},
                ],
                "relationships": [
                    {"source": 0, "target": 1, "type": "knows"},
                    {"source": 2, "target": 3, "type": "works_with"},
                ],
                "suggested_templates": [],
                "suggested_edge_templates": [],
                "metadata": {"total_entities": 4, "total_relationships": 2},
            }
        )

        # Mock commit service result
        mock_engine.commit_service.commit = AsyncMock(
            return_value={
                "created_nodes": ["node_1", "node_2", "node_3", "node_4"],
                "created_edges": ["edge_1", "edge_2"],
                "created_templates": ["tmpl_1"],
            }
        )

        mock_engine.storage_adapter.delete_extraction_submissions.return_value = 2

        result = await orchestrator.finalize("src_001")

        assert result["success"] is True
        assert result["nodes_created"] == 4
        assert result["edges_created"] == 2
        assert result["templates_created"] == 1
        assert result["status"] == "committed"

        # Verify extraction service was called with correct aggregated data
        finalize_call = mock_engine.extraction_service.finalize_distributed_extraction
        call_kwargs = finalize_call.call_args[1]
        raw_entities = call_kwargs["raw_entities"]
        raw_rels = call_kwargs["raw_relationships"]

        # 4 entities total (2 from chunk 0 + 2 from chunk 1)
        assert len(raw_entities) == 4
        # Chunk index annotations
        assert raw_entities[0]["chunk_index"] == 0  # Alice
        assert raw_entities[1]["chunk_index"] == 0  # Bob
        assert raw_entities[2]["chunk_index"] == 1  # Charlie
        assert raw_entities[3]["chunk_index"] == 1  # Dave

        # Properties applied to Charlie (entity_index=0 in chunk 1 -> global index 2)
        assert raw_entities[2].get("properties", {}).get("role") == "Manager"

        # 2 relationships total
        assert len(raw_rels) == 2
        # First chunk: R|0|1 -> stays 0, 1 (offset 0)
        assert raw_rels[0]["source"] == 0
        assert raw_rels[0]["target"] == 1
        assert raw_rels[0]["chunk_index"] == 0
        # Second chunk: R|0|1 -> remapped to 2, 3 (offset 2)
        assert raw_rels[1]["source"] == 2
        assert raw_rels[1]["target"] == 3
        assert raw_rels[1]["chunk_index"] == 1

        # Verify commit was called
        mock_engine.commit_service.commit.assert_called_once()
        commit_kwargs = mock_engine.commit_service.commit.call_args[1]
        assert commit_kwargs["file_id"] == "src_001"
        # ``file_info`` carries ``filtering_mode`` so the commit-side
        # orphan-drop honours the row's preset (Workstream 3, Task 3.5);
        # the legacy fixture omits ``filtering_mode``, so the orchestrator
        # falls back to the canonical ``balanced`` default.
        assert commit_kwargs["file_info"] == {
            "filename": "two_chunk.pdf",
            "filtering_mode": "balanced",
        }

        # Verify cleanup
        mock_engine.storage_adapter.delete_extraction_submissions.assert_called_once_with(
            "src_001", "default"
        )

    @pytest.mark.asyncio
    async def test_rejects_when_chunks_missing(self, orchestrator, mock_engine):
        """Raises ValueError when not all chunks are submitted."""
        # Source stores expected indices (set by get_tasks)
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting",
            chunk_indices=[0, 1, 2],
        )
        # Only 1 submission
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {
                "chunk_group_index": 0,
                "entities_text": "E|Alice|Person||0.9|S1|A person",
                "relationships_text": "",
            },
        ]

        with pytest.raises(ValueError, match=r"Incomplete.*Missing indices"):
            await orchestrator.finalize("src_001")

    @pytest.mark.asyncio
    async def test_calls_finalize_and_commit(self, orchestrator, mock_engine):
        """Verifies finalize_distributed_extraction and commit are called in order."""
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting", filename="single.txt"
        )
        mock_engine.storage_adapter.list_chunks.return_value = [
            _make_chunk("c1", "src_001", 0, 0, "chunk"),
        ]
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {
                "chunk_group_index": 0,
                "entities_text": "E|Alice|Person||0.9|S1|A person",
                "relationships_text": "",
            },
        ]
        mock_engine.extraction_service.finalize_distributed_extraction = AsyncMock(
            return_value={
                "entities": [{"name": "Alice"}],
                "relationships": [],
                "suggested_templates": [],
                "suggested_edge_templates": [],
                "metadata": {},
            }
        )
        mock_engine.commit_service.commit = AsyncMock(
            return_value={"created_nodes": ["n1"], "created_edges": [], "created_templates": []}
        )
        mock_engine.storage_adapter.delete_extraction_submissions.return_value = 1

        result = await orchestrator.finalize("src_001")

        assert result["success"] is True
        # Extraction service called before commit
        mock_engine.extraction_service.finalize_distributed_extraction.assert_called_once()
        mock_engine.commit_service.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_deletes_submissions_after_commit(self, orchestrator, mock_engine):
        """Submissions are deleted after successful commit."""
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting", filename="cleanup.txt"
        )
        mock_engine.storage_adapter.list_chunks.return_value = [
            _make_chunk("c1", "src_001", 0, 0, "chunk"),
        ]
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {
                "chunk_group_index": 0,
                "entities_text": "E|X|Thing||0.9|S1|Something",
                "relationships_text": "",
            },
        ]
        mock_engine.extraction_service.finalize_distributed_extraction = AsyncMock(
            return_value={
                "entities": [],
                "relationships": [],
                "suggested_templates": [],
                "suggested_edge_templates": [],
                "metadata": {},
            }
        )
        mock_engine.commit_service.commit = AsyncMock(
            return_value={"created_nodes": [], "created_edges": [], "created_templates": []}
        )
        mock_engine.storage_adapter.delete_extraction_submissions.return_value = 1

        await orchestrator.finalize("src_001")

        mock_engine.storage_adapter.delete_extraction_submissions.assert_called_once_with(
            "src_001", "default"
        )


# ------------------------------------------------------------------ #
#  Quick Mode (subset extraction)
# ------------------------------------------------------------------ #


class TestQuickModeProgress:
    """Verify get_progress uses stored chunk indices for quick mode."""

    @pytest.mark.asyncio
    async def test_uses_stored_indices_not_all_groups(self, orchestrator, mock_engine):
        """Quick mode with stored indices reports against subset, not all chunks."""
        # Source stores quick-mode subset [0, 28, 56]
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting",
            chunk_indices=[0, 28, 56],
        )
        # 2 of 3 submitted
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {"chunk_group_index": 0},
            {"chunk_group_index": 56},
        ]

        result = await orchestrator.get_progress("src_001")

        assert result["total_chunks"] == 3
        assert result["submitted_indices"] == [0, 56]
        assert result["missing_indices"] == [28]
        assert result["ready_to_finalize"] is False

    @pytest.mark.asyncio
    async def test_ready_when_quick_subset_complete(self, orchestrator, mock_engine):
        """Quick mode is ready when all subset indices are submitted."""
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting",
            chunk_indices=[0, 28, 56],
        )
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {"chunk_group_index": 0},
            {"chunk_group_index": 28},
            {"chunk_group_index": 56},
        ]

        result = await orchestrator.get_progress("src_001")

        assert result["total_chunks"] == 3
        assert result["missing_indices"] == []
        assert result["ready_to_finalize"] is True


class TestQuickModeFinalize:
    """Verify finalize uses stored chunk indices for quick mode."""

    @pytest.mark.asyncio
    async def test_finalize_succeeds_with_quick_subset(self, orchestrator, mock_engine):
        """Finalize succeeds when only the quick-mode subset is submitted."""
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting",
            filename="quick.txt",
            domain="generic",
            chunk_indices=[0, 28],
        )
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {
                "chunk_group_index": 0,
                "entities_text": "E|Alice|Person||0.9|S1|A person",
                "relationships_text": "",
            },
            {
                "chunk_group_index": 28,
                "entities_text": "E|Bob|Person||0.8|S1|Another person",
                "relationships_text": "",
            },
        ]
        mock_engine.extraction_service.finalize_distributed_extraction = AsyncMock(
            return_value={
                "entities": [{"name": "Alice"}, {"name": "Bob"}],
                "relationships": [],
                "suggested_templates": [],
                "suggested_edge_templates": [],
                "metadata": {},
            }
        )
        mock_engine.commit_service.commit = AsyncMock(
            return_value={
                "created_nodes": ["n1", "n2"],
                "created_edges": [],
                "created_templates": [],
            }
        )
        mock_engine.storage_adapter.delete_extraction_submissions.return_value = 2

        result = await orchestrator.finalize("src_001")

        assert result["success"] is True
        assert result["nodes_created"] == 2

    @pytest.mark.asyncio
    async def test_finalize_rejects_incomplete_quick_subset(self, orchestrator, mock_engine):
        """Finalize rejects when quick-mode subset has missing chunks."""
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting",
            chunk_indices=[0, 28, 56],
        )
        # Only 1 of 3 submitted
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {
                "chunk_group_index": 0,
                "entities_text": "E|Alice|Person||0.9|S1|A person",
                "relationships_text": "",
            },
        ]

        with pytest.raises(ValueError, match=r"Incomplete.*Missing indices"):
            await orchestrator.finalize("src_001")

    @pytest.mark.asyncio
    async def test_finalize_returns_quality_grade_label_and_breakdown(
        self, orchestrator, mock_engine
    ):
        """``finalize_extraction`` surfaces the cached v7 quality scores in
        the response. MCP clients show users the grade right after a
        successful commit; making them do a separate ``get_source`` round
        trip is the avoidable papercut this commit closes.

        The response must carry ``quality_grade`` (float 0-100),
        ``quality_label`` (Poor/Fair/Good/Excellent), and a
        ``quality_breakdown`` dict with the v7 component scores
        (richness, avg_entity_quality, avg_relationship_quality,
        topology_score, density_score, structural_penalty,
        pollution_penalty, hub_skew, reciprocal_rate, coverage_score,
        low_quality_*_count, scores_version).
        """
        # Source row with cached_quality_* populated as it would be
        # right after cache_quality_scores runs inside finalize.
        source_row = _make_source(status="mcp_extracting", filename="war_and_peace_tiny.txt")
        source_row.update(
            {
                "cached_quality_grade": 65.37,
                "cached_quality_label": "Good",
                "cached_richness_score": 3535.55,
                "cached_avg_entity_quality": 59.46,
                "cached_avg_relationship_quality": 86.82,
                "cached_topology_score": 74.35,
                "cached_density_score": 57.39,
                "cached_structural_penalty": 10.0,
                "cached_pollution_penalty": 0.0,
                "cached_hub_skew": 8.5,
                "cached_reciprocal_rate": 0.08,
                "cached_coverage_score": 100.0,
                "cached_low_quality_entity_count": 0,
                "cached_low_quality_relationship_count": 0,
                "cached_scores_version": 7,
            }
        )
        mock_engine.storage_adapter.get_source.return_value = source_row
        mock_engine.storage_adapter.list_chunks.return_value = [
            _make_chunk("c1", "src_001", 0, 0, "chunk 0"),
        ]
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {
                "chunk_group_index": 0,
                "entities_text": "E|Pierre|Character||0.9|S1|A Russian aristocrat",
                "relationships_text": "",
            },
        ]
        mock_engine.extraction_service.finalize_distributed_extraction = AsyncMock(
            return_value={
                "entities": [{"id": 0, "name": "Pierre", "type": "Character", "chunk_index": 0}],
                "relationships": [],
                "suggested_templates": [],
                "suggested_edge_templates": [],
                "metadata": {"total_entities": 1, "total_relationships": 0},
            }
        )
        mock_engine.commit_service.commit = AsyncMock(
            return_value={
                "created_nodes": ["n1"],
                "created_edges": [],
                "created_templates": [],
            }
        )
        mock_engine.storage_adapter.delete_extraction_submissions.return_value = 1

        result = await orchestrator.finalize("src_001", model="claude-sonnet-4-6")

        # Sanity check the existing fields still work.
        assert result["success"] is True
        assert result["status"] == "committed"

        # New quality-surfacing contract.
        assert result["quality_grade"] == 65.37, (
            f"quality_grade missing from finalize response: {result}"
        )
        assert result["quality_label"] == "Good"
        bd = result["quality_breakdown"]
        assert bd is not None, "quality_breakdown should be present"
        assert bd["richness"] == 3535.55
        assert bd["avg_entity_quality"] == 59.46
        assert bd["avg_relationship_quality"] == 86.82
        assert bd["topology_score"] == 74.35
        assert bd["density_score"] == 57.39
        assert bd["structural_penalty"] == 10.0
        assert bd["pollution_penalty"] == 0.0
        assert bd["hub_skew"] == 8.5
        assert bd["reciprocal_rate"] == 0.08
        assert bd["coverage_score"] == 100.0
        assert bd["low_quality_entity_count"] == 0
        assert bd["low_quality_relationship_count"] == 0
        assert bd["scores_version"] == 7

    @pytest.mark.asyncio
    async def test_finalize_returns_nones_when_quality_scores_not_cached(
        self, orchestrator, mock_engine
    ):
        """A source row whose ``cached_quality_*`` columns are absent
        (legacy row, scoring failed silently, fresh schema) must still
        produce a successful finalize response — commit already landed
        and a missing score is a soft degrade. ``quality_grade`` /
        ``quality_label`` come back as ``None`` and each
        ``quality_breakdown`` component is ``None`` so downstream JSON
        consumers can render "—" placeholders without crashing on
        KeyErrors.
        """
        # _make_source omits cached_quality_* fields by default, so the
        # readback's .get() falls through to None for each.
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting", filename="legacy.txt"
        )
        mock_engine.storage_adapter.list_chunks.return_value = [
            _make_chunk("c1", "src_001", 0, 0, "chunk 0"),
        ]
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {
                "chunk_group_index": 0,
                "entities_text": "E|Pierre|Character||0.9|S1|A Russian aristocrat",
                "relationships_text": "",
            },
        ]
        mock_engine.extraction_service.finalize_distributed_extraction = AsyncMock(
            return_value={
                "entities": [{"id": 0, "name": "Pierre", "type": "Character", "chunk_index": 0}],
                "relationships": [],
                "suggested_templates": [],
                "suggested_edge_templates": [],
                "metadata": {"total_entities": 1, "total_relationships": 0},
            }
        )
        mock_engine.commit_service.commit = AsyncMock(
            return_value={"created_nodes": ["n1"], "created_edges": [], "created_templates": []}
        )
        mock_engine.storage_adapter.delete_extraction_submissions.return_value = 1

        result = await orchestrator.finalize("src_001")

        assert result["success"] is True
        assert result["status"] == "committed"
        assert result["quality_grade"] is None
        assert result["quality_label"] is None
        # Breakdown dict is present so clients can iterate keys, but
        # every component is None until scoring has run.
        assert result["quality_breakdown"] is not None
        assert result["quality_breakdown"]["richness"] is None
        assert result["quality_breakdown"]["topology_score"] is None


# ------------------------------------------------------------------ #
#  TestFinalizeStateSafety
# ------------------------------------------------------------------ #


def _finalize_ready(mock_engine) -> None:
    """One complete submission plus a passing finalize/commit chain."""
    mock_engine.storage_adapter.list_chunks.return_value = [
        _make_chunk("c1", "src_001", 0, 0, "chunk"),
    ]
    mock_engine.storage_adapter.list_extraction_submissions.return_value = [
        {
            "chunk_group_index": 0,
            "entities_text": "E|Alice|Person||0.9|S1|A person",
            "relationships_text": "",
        },
    ]
    mock_engine.extraction_service.finalize_distributed_extraction = AsyncMock(
        return_value={
            "entities": [{"name": "Alice"}],
            "relationships": [],
            "suggested_templates": [],
            "suggested_edge_templates": [],
            "metadata": {},
        }
    )
    mock_engine.commit_service.commit = AsyncMock(
        return_value={"created_nodes": ["n1"], "created_edges": [], "created_templates": []}
    )
    mock_engine.storage_adapter.delete_extraction_submissions.return_value = 1


class TestFinalizeDomain:
    """finalize() runs the forced-over-detected domain, as the worker does."""

    @pytest.mark.asyncio
    async def test_forced_domain_reaches_finalize_fingerprint_and_scores(
        self, orchestrator, mock_engine
    ):
        """A domain pinned at add_document / confirm_extraction time is the one used.

        ``extraction_domain`` is still None at the first finalize of such a
        source; reading only that column ran the generic domain.
        """
        src = _make_source(status="mcp_extracting", filename="legal.txt", domain=None)
        src["forced_domain"] = "legal"
        mock_engine.storage_adapter.get_source.return_value = src
        _finalize_ready(mock_engine)

        with (
            patch(
                "chaoscypher_core.services.sources.engine.extraction.domains.fingerprint.resolve_domain_fingerprint",
                return_value=("1.0", "hash"),
            ) as fingerprint,
            patch(
                "chaoscypher_core.services.sources.engine.extraction.orchestration.cache_quality_scores"
            ) as scores,
        ):
            await orchestrator.finalize("src_001")

        fde = mock_engine.extraction_service.finalize_distributed_extraction
        assert fde.call_args.kwargs["forced_domain"] == "legal"
        assert fde.call_args.kwargs["detected_domain"] is None
        assert fingerprint.call_args.args[0] == "legal"
        assert scores.call_args.kwargs["domain_name"] == "legal"
        complete = mock_engine.storage_adapter.complete_extraction.call_args.kwargs
        assert complete["forced_domain"] == "legal"
        assert complete["detected_domain"] is None


class TestFinalizeStateSafety:
    """finalize() must not strand sources or leak per-source caches."""

    @pytest.mark.asyncio
    async def test_failure_before_commit_phase_rolls_status_back(self, orchestrator, mock_engine):
        """An embedding/dedup failure returns the source to mcp_extracting.

        Without the rollback the source stayed at 'committing', where the
        worker reconciler (no commit payload, no entity rows) dispatched an
        empty import_commit and zero-graph-committed it.
        """
        from chaoscypher_core.models import SourceStatus

        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting", filename="single.txt"
        )
        _finalize_ready(mock_engine)
        mock_engine.extraction_service.finalize_distributed_extraction = AsyncMock(
            side_effect=RuntimeError("embedder down")
        )

        with pytest.raises(RuntimeError, match="embedder down"):
            await orchestrator.finalize("src_001")

        calls = mock_engine.storage_adapter.transition_source_status.call_args_list
        assert len(calls) == 2, "expected the claiming CAS plus the rollback"
        assert calls[1].kwargs["from_status"] == SourceStatus.COMMITTING
        assert calls[1].kwargs["to_status"] == SourceStatus.MCP_EXTRACTING
        mock_engine.storage_adapter.complete_extraction.assert_not_called()
        assert "src_001" not in orchestrator._commit_phase

    @pytest.mark.asyncio
    async def test_failure_in_commit_phase_stays_committing(self, orchestrator, mock_engine):
        """Once the entity rows are written, a commit failure is left for recovery."""
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting", filename="single.txt"
        )
        _finalize_ready(mock_engine)
        mock_engine.commit_service.commit = AsyncMock(side_effect=RuntimeError("graph down"))

        with pytest.raises(RuntimeError, match="graph down"):
            await orchestrator.finalize("src_001")

        calls = mock_engine.storage_adapter.transition_source_status.call_args_list
        assert len(calls) == 1, "no rollback after complete_extraction"
        mock_engine.storage_adapter.complete_extraction.assert_called_once()
        assert "src_001" not in orchestrator._commit_phase

    @pytest.mark.asyncio
    async def test_incomplete_finalize_rolls_status_back(self, orchestrator, mock_engine):
        """A premature finalize returns the source to mcp_extracting.

        Without the rollback the source stays at 'committing' forever: a
        retried finalize can never win the MCP_EXTRACTING -> COMMITTING
        CAS, and no MCP-side recovery path touches 'committing'.
        """
        from chaoscypher_core.models import SourceStatus

        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting",
            chunk_indices=[0, 28, 56],
        )
        # Only 1 of 3 submitted — finalize must reject AND roll back.
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {
                "chunk_group_index": 0,
                "entities_text": "E|Alice|Person||0.9|S1|A person",
                "relationships_text": "",
            },
        ]

        with pytest.raises(ValueError, match=r"Incomplete.*Missing indices"):
            await orchestrator.finalize("src_001")

        calls = mock_engine.storage_adapter.transition_source_status.call_args_list
        assert len(calls) == 2, "expected the claiming CAS plus the rollback"
        rollback = calls[1]
        assert rollback.kwargs["from_status"] == SourceStatus.COMMITTING
        assert rollback.kwargs["to_status"] == SourceStatus.MCP_EXTRACTING

    @pytest.mark.asyncio
    async def test_successful_finalize_evicts_per_source_caches(self, orchestrator, mock_engine):
        """finalize() evicts both per-source cache entries it may hold.

        ``_plans``, ``_submission_timestamps`` and
        ``_group_indices`` are keyed by source_id and only exist to bridge
        get_tasks/submit_chunk into finalize; leaving any of them behind leaks
        one entry per finalized source for the lifetime of a long-lived MCP
        server process.
        """
        from collections import deque

        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="mcp_extracting",
            filename="quick.txt",
            domain="generic",
            chunk_indices=[0],
        )
        mock_engine.storage_adapter.list_extraction_submissions.return_value = [
            {
                "chunk_group_index": 0,
                "entities_text": "E|Alice|Person||0.9|S1|A person",
                "relationships_text": "",
            },
        ]
        mock_engine.extraction_service.finalize_distributed_extraction = AsyncMock(
            return_value={
                "entities": [{"name": "Alice"}],
                "relationships": [],
                "suggested_templates": [],
                "suggested_edge_templates": [],
                "metadata": {},
            }
        )
        mock_engine.commit_service.commit = AsyncMock(
            return_value={
                "created_nodes": ["n1"],
                "created_edges": [],
                "created_templates": [],
            }
        )
        mock_engine.storage_adapter.delete_extraction_submissions.return_value = 1

        # Simulate prior get_tasks / submit_chunk activity for this source.
        orchestrator._plans["src_001"] = orchestrator._plan_for(
            mock_engine.storage_adapter.get_source.return_value, ()
        )
        orchestrator._submission_timestamps["src_001"] = deque([0.0])
        orchestrator._group_indices["src_001"] = {0}

        result = await orchestrator.finalize("src_001")

        assert result["success"] is True
        assert "src_001" not in orchestrator._plans
        assert "src_001" not in orchestrator._submission_timestamps
        assert "src_001" not in orchestrator._group_indices


# ------------------------------------------------------------------ #
#  TestGroupIndicesMemo
# ------------------------------------------------------------------ #


class TestGroupIndicesMemo:
    """The chunk-group index set is derived once per protocol span.

    ``_build_source_groups`` reads every chunk of the source WITH content,
    does a full ``get_source``, runs the content-exclusion pass over all of
    them and re-packs token-budget groups. Four of the five MCP entry points
    want only the integer index set, and ``submit_chunk`` probes it once per
    chunk group purely to test one integer — so an N-group source paid N+3
    full rebuilds, quadratic in the document's content bytes. Pre-0030 the
    value was a column on the source row; re-deriving it is identical.

    These tests deliberately do NOT install the conftest chunk-indices
    shortcut, which bypasses the very chain under test.
    """

    @pytest.fixture
    def orchestrator(self, mock_engine):
        """An orchestrator with the real ``_build_source_groups`` chain."""
        return ExtractionOrchestrator(engine=mock_engine)

    @staticmethod
    def _wire(mock_engine, n_chunks: int) -> None:
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="indexed", filename="report.pdf", domain="generic"
        )
        # Real prose, not "chunk N": the generic domain's boilerplate
        # exclusion strips the latter, which leaves zero groups and would
        # make these tests pass vacuously.
        mock_engine.storage_adapter.get_chunks_for_extraction.return_value = [
            {
                "id": f"c{i}",
                "chunk_index": i,
                "content": (
                    f"Section {i}. Alice Marchetti joined the Riverside Institute in "
                    f"March and began work on the sediment survey with Boris Petrov. "
                    f"Their report was filed with the regional council that autumn."
                ),
            }
            for i in range(n_chunks)
        ]
        mock_engine.graph_repository.list_templates.side_effect = [[], []]

    @pytest.mark.asyncio
    async def test_indices_are_derived_once_for_the_whole_span(self, orchestrator, mock_engine):
        """get_tasks + every later probe costs ONE full-source read, not N+3."""
        self._wire(mock_engine, 6)

        await orchestrator.get_tasks("src_001")
        reads_after_get_tasks = mock_engine.storage_adapter.get_chunks_for_extraction.call_count

        # The probe submit_chunk / get_progress / finalize each make.
        for _ in range(5):
            assert orchestrator._get_group_indices("src_001")

        assert reads_after_get_tasks == 1
        assert mock_engine.storage_adapter.get_chunks_for_extraction.call_count == 1

    @pytest.mark.asyncio
    async def test_the_memo_returns_a_copy(self, orchestrator, mock_engine):
        """A caller mutating the returned set cannot poison the memo."""
        self._wire(mock_engine, 4)
        await orchestrator.get_tasks("src_001")

        first = orchestrator._get_group_indices("src_001")
        first.add(999)

        assert 999 not in orchestrator._get_group_indices("src_001")

    @pytest.mark.asyncio
    async def test_get_tasks_refreshes_a_stale_memo(self, orchestrator, mock_engine):
        """A force=True re-extract after a re-chunk must not reuse the old set."""
        self._wire(mock_engine, 2)
        await orchestrator.get_tasks("src_001")
        before = orchestrator._get_group_indices("src_001")

        # Re-chunked: many more chunks, so more groups.
        mock_engine.storage_adapter.get_source.return_value = _make_source(
            status="committed", filename="report.pdf", domain="generic"
        )
        mock_engine.storage_adapter.get_chunks_for_extraction.return_value = [
            {
                "id": f"c{i}",
                "chunk_index": i,
                "content": (
                    f"Paragraph {i}. Alice Marchetti and Boris Petrov reviewed the "
                    "sediment cores collected along the eastern bank. "
                )
                * 20,
            }
            for i in range(40)
        ]
        mock_engine.graph_repository.list_templates.side_effect = [[], []]

        await orchestrator.get_tasks("src_001", force=True)
        after = orchestrator._get_group_indices("src_001")

        assert len(after) > len(before)


# ------------------------------------------------------------------ #
#  One prompt source: the MCP prompts are the product's renders
# ------------------------------------------------------------------ #


_PROMPT_CHUNK = (
    "Captain Orsolya Darnell walked the harbour wall at dusk. "
    "The lamplighter Bram Darnell was her father. "
    "Margrave Oswin Kell camped beyond the marsh."
)


def _literary_stack():
    """The shipped floor plus literary (guidance, examples and exclusions all set), and its registry."""
    from chaoscypher_core.services.sources.engine.extraction.domains.registry import (
        DomainRegistry,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack

    registry = DomainRegistry()
    return DomainStack.resolve(registry, ["literary"]), registry


class TestPromptsAreTheProductRenders:
    """The MCP path prompts a client with the bytes a local model receives.

    ``get_tasks`` used to carry a hand-written copy of both extraction
    prompts that kept the relationship quota and the corpus examples after
    the shared prompts dropped them, so harness models were benchmarked
    under a different prompt than local ones.
    """

    @pytest.mark.asyncio
    async def test_get_tasks_runs_with_the_sources_extraction_plan(self, orchestrator, mock_engine):
        """``get_tasks`` caches the plan ``build_extraction_plan`` decides for the domain and row."""
        from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
            build_extraction_plan,
        )

        stack, registry = _literary_stack()
        src = _make_source(status="indexed", domain="literary")
        src["forced_domain"] = "literary"
        src["filtering_mode"] = "balanced"
        src["confirmation_required"] = False
        mock_engine.storage_adapter.get_source.return_value = src
        mock_engine.storage_adapter.transition_source_status.return_value = True
        # One group: ``get_tasks`` refuses a source that builds none.
        group = {"group_index": 0, "combined_content": _PROMPT_CHUNK, "small_chunk_ids": ["c1"]}

        with (
            patch("chaoscypher_core.mcp.extraction.get_domain_registry", return_value=registry),
            patch.object(orchestrator, "_build_source_groups", return_value=[group]),
        ):
            await orchestrator.get_tasks("src_001")

        plan = orchestrator._plans["src_001"]
        expected = build_extraction_plan(stack, mock_engine.settings, source_row=src)
        assert plan.chunk_kwargs == expected.chunk_kwargs
        assert plan.filtering_config == expected.filtering_config
        assert plan.edge_type_constraints == expected.edge_type_constraints
        # The domain's limits reach the MCP path (they used to be dropped).
        assert plan.chunk_kwargs["domain_extraction_limits"]["strict_edge_type_constraints"] is True
        assert plan.chunk_kwargs["entity_guidance"]
        assert plan.chunk_kwargs["entity_examples"]
        assert plan.chunk_kwargs["entity_exclusions"]

    @pytest.mark.asyncio
    async def test_get_tasks_and_get_chunks_render_the_extractors_bytes(
        self, orchestrator, mock_engine
    ):
        """Chunk sentences substituted into the instructions == AIEntityExtractor's render."""
        from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
            build_extraction_plan,
        )
        from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
            PROMPT_CHUNK_TEXT_PLACEHOLDER,
            PROMPT_PASS1_ENTITIES_PLACEHOLDER,
            AIEntityExtractor,
        )
        from chaoscypher_core.settings import EngineSettings

        settings = EngineSettings()
        stack, registry = _literary_stack()
        mock_engine.settings.llm = settings.llm
        src = _make_source(status="indexed", domain="literary")
        src["forced_domain"] = "literary"
        src["confirmation_required"] = False
        mock_engine.storage_adapter.get_source.return_value = src
        mock_engine.storage_adapter.transition_source_status.return_value = True
        group = {"group_index": 0, "combined_content": _PROMPT_CHUNK, "small_chunk_ids": ["c1"]}

        with (
            patch("chaoscypher_core.mcp.extraction.get_domain_registry", return_value=registry),
            patch.object(orchestrator, "_build_source_groups", return_value=[group]),
        ):
            tasks = await orchestrator.get_tasks("src_001")
            chunk = (await orchestrator.get_chunks("src_001", [0]))["chunks"][0]

        kwargs = build_extraction_plan(stack, settings).chunk_kwargs
        extractor = AIEntityExtractor(settings=settings)
        harvest = extractor.render_harvest_prompts(
            _PROMPT_CHUNK,
            kwargs["node_templates_formatted"],
            entity_guidance=kwargs["entity_guidance"],
            entity_examples=kwargs["entity_examples"],
            entity_exclusions=kwargs["entity_exclusions"],
            strict_entity_types=kwargs["strict_entity_types"],
        )

        assert tasks["prompt_placeholders"] == {
            "chunk_text": PROMPT_CHUNK_TEXT_PLACEHOLDER,
            "pass1_entities": PROMPT_PASS1_ENTITIES_PLACEHOLDER,
        }
        assert chunk["numbered_sentences"] == harvest.numbered_text
        entity_prompt = tasks["entity_instructions"].replace(
            PROMPT_CHUNK_TEXT_PLACEHOLDER, chunk["numbered_sentences"]
        )
        assert entity_prompt == harvest.entity_prompt
        assert tasks["relationship_instructions"] == extractor.render_relationship_prompt_template(
            kwargs["edge_templates_formatted"],
            relationship_guidance=kwargs["relationship_guidance"],
            relationship_examples=kwargs["relationship_examples"],
        )
