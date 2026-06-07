# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Engine default changes and return types."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core import Engine


@pytest.fixture
def engine(tmp_path):
    """Create an Engine with temporary database."""
    db_dir = tmp_path / "databases" / "test"
    db_dir.mkdir(parents=True)
    eng = Engine(str(db_dir))
    yield eng
    eng.close()


@pytest.mark.unit
@pytest.mark.core
class TestInitializeDbDefault:
    """Test that initialize_db defaults to True."""

    def test_engine_initializes_db_by_default(self, tmp_path):
        """Engine creates tables without explicit initialize_db=True."""
        db_dir = tmp_path / "databases" / "auto_init"
        db_dir.mkdir(parents=True)
        with Engine(str(db_dir)) as engine:
            stats = engine.get_stats()
            assert stats.nodes == 0


@pytest.mark.unit
@pytest.mark.core
class TestGetStatsReturnType:
    """Test get_stats() returns DatabaseStats model."""

    def test_returns_database_stats_model(self, engine):
        """get_stats() returns DatabaseStats, not dict."""
        from chaoscypher_core.models import DatabaseStats

        stats = engine.get_stats()
        assert isinstance(stats, DatabaseStats)
        assert hasattr(stats, "nodes")
        assert hasattr(stats, "database_name")

    def test_model_dump_works(self, engine):
        """DatabaseStats.model_dump() returns dict."""
        stats = engine.get_stats()
        d = stats.model_dump()
        assert isinstance(d, dict)
        assert "nodes" in d


@pytest.mark.unit
@pytest.mark.core
class TestSourceIdAutoGeneration:
    """Test source_id auto-generates in create_chunks."""

    @pytest.mark.asyncio
    async def test_create_chunks_without_source_id(self):
        """create_chunks works without source_id."""
        from chaoscypher_core.settings import EngineSettings
        from chaoscypher_core.utils.chunk import ChunkingService

        service = ChunkingService(settings=EngineSettings(current_database="test"))
        result = await service.create_chunks(full_text="Test document text.")
        for chunk in result.small_chunks:
            assert chunk["source_id"] is not None
            assert len(chunk["source_id"]) == 36  # UUID

    @pytest.mark.asyncio
    async def test_create_chunks_with_explicit_source_id(self):
        """create_chunks uses provided source_id."""
        from chaoscypher_core.settings import EngineSettings
        from chaoscypher_core.utils.chunk import ChunkingService

        service = ChunkingService(settings=EngineSettings(current_database="test"))
        result = await service.create_chunks(
            source_id="my-custom-id", full_text="Test document text."
        )
        for chunk in result.small_chunks:
            assert chunk["source_id"] == "my-custom-id"


@pytest.mark.unit
@pytest.mark.core
class TestLLMProviderDefaults:
    """Test LLMProvider works without managers."""

    def test_create_without_managers(self):
        """LLMProvider works without explicit managers parameter."""
        from chaoscypher_core import EngineSettings, LLMProvider

        settings = EngineSettings(current_database="test")
        provider = LLMProvider(settings=settings)
        assert provider.managers == {}

    def test_create_with_managers(self):
        """LLMProvider still accepts explicit managers."""
        from chaoscypher_core import EngineSettings, LLMProvider

        settings = EngineSettings(current_database="test")
        managers = {"graph": MagicMock()}
        provider = LLMProvider(settings=settings, managers=managers)
        assert provider.managers is managers


@pytest.mark.unit
@pytest.mark.core
class TestProcessDocumentReturnType:
    """Test process_document() returns ProcessingResult."""

    @pytest.mark.asyncio
    async def test_returns_processing_result(self, engine):
        """process_document() returns ProcessingResult model."""
        from chaoscypher_core.models import ProcessingResult

        with (
            patch.object(
                engine.chunking_service, "create_chunks", new_callable=AsyncMock
            ) as mock_chunks,
            patch.object(engine.chunking_service, "store_chunks"),
            patch.object(
                engine.indexing_service, "create_index", new_callable=AsyncMock
            ) as mock_index,
            patch.object(
                engine.chunking_service, "process", new_callable=AsyncMock
            ) as mock_process,
        ):
            from chaoscypher_core.models import ChunksResult, ExtractionResult

            mock_index.return_value = {
                "chunks_count": 0,
                "embedding_model": "test-model",
                "embedding_dimensions": 1,
            }
            mock_chunks.return_value = ChunksResult(
                small_chunks=[],
                hierarchical_groups=[],
                total_small_chunks=0,
                total_groups=0,
                total_original_chunks=0,
                total_original_groups=0,
            )

            mock_process.return_value = ExtractionResult(
                entities=[],
                relationships=[],
                domain="generic",
                domain_confidence=0.0,
            )

            # Mock lazy properties
            engine._extraction_service = MagicMock()
            engine._extraction_service.finalize_distributed_extraction = AsyncMock(
                return_value={"entities": [], "relationships": []}
            )
            engine._commit_service = MagicMock()
            engine._commit_service.commit = AsyncMock(
                return_value={
                    "created_nodes": ["n1", "n2"],
                    "created_edges": ["e1"],
                    "created_templates": ["t1"],
                }
            )

            result = await engine.process_document("Test text", filename="test.txt")
            assert isinstance(result, ProcessingResult)
            assert result.nodes == ["n1", "n2"]
            assert result.edges == ["e1"]
            assert result.templates == ["t1"]
            assert result.source_id is not None


@pytest.mark.unit
@pytest.mark.core
class TestProcessDocumentConfirmationGate:
    """The engine extraction path honours the domain-confirmation gate.

    These exercise the REAL ``engine.process_document`` (real source-row
    creation, real chunk storage, real gate evaluation) — only the
    embedding/LLM stages are mocked. They prove the server-extraction bypass
    is closed: a ``confirmation_required`` source PARKS before extraction,
    while the default (auto_confirm) path extracts exactly as before.
    """

    @staticmethod
    def _mock_pipeline_stages(engine):
        """Patch the embedding/LLM stages, leaving chunking + the gate real."""
        from chaoscypher_core.models import ExtractionResult

        index_patch = patch.object(
            engine.indexing_service,
            "create_index",
            new_callable=AsyncMock,
            return_value={
                "chunks_count": 1,
                "embedding_model": "test-model",
                "embedding_dimensions": 1,
            },
        )
        process_patch = patch.object(
            engine.chunking_service,
            "process",
            new_callable=AsyncMock,
            return_value=ExtractionResult(
                entities=[],
                relationships=[],
                domain="generic",
                domain_confidence=0.0,
            ),
        )
        return index_patch, process_patch

    @pytest.mark.asyncio
    async def test_confirmation_required_source_parks(self, engine):
        """auto_confirm=False parks the source; extraction stages never run."""
        from chaoscypher_core.models import SourceStatus

        index_patch, process_patch = self._mock_pipeline_stages(engine)

        engine._extraction_service = MagicMock()
        engine._extraction_service.finalize_distributed_extraction = AsyncMock(
            return_value={"entities": [], "relationships": []}
        )
        engine._commit_service = MagicMock()
        engine._commit_service.commit = AsyncMock(
            return_value={"created_nodes": [], "created_edges": [], "created_templates": []}
        )

        with index_patch, process_patch as mock_process:
            result = await engine.process_document(
                "Some document text about a topic.",
                source_id="park-me",
                filename="report.pdf",
                auto_confirm=False,
            )

            # The gate parked before extraction: LLM/commit stages never ran.
            mock_process.assert_not_called()
            engine._extraction_service.finalize_distributed_extraction.assert_not_called()
            engine._commit_service.commit.assert_not_called()

        # Returned result reports the parked terminal status, no graph entities.
        assert result.status == SourceStatus.AWAITING_CONFIRMATION
        assert result.nodes == []
        assert result.edges == []

        # Persisted SourceRow reflects the park (the server re-read keys off it).
        row = engine.storage_adapter.get_source("park-me", engine.settings.current_database)
        assert row is not None
        assert row["status"] == SourceStatus.AWAITING_CONFIRMATION
        assert row["confirmation_required"] is True
        proposal = row.get("detection_proposal") or {}
        assert "detected_domain" in proposal

    @pytest.mark.asyncio
    async def test_auto_confirm_default_extracts_without_parking(self, engine):
        """The default (auto_confirm=True) path extracts; nothing parks."""
        index_patch, process_patch = self._mock_pipeline_stages(engine)

        engine._extraction_service = MagicMock()
        engine._extraction_service.finalize_distributed_extraction = AsyncMock(
            return_value={"entities": [], "relationships": []}
        )
        engine._commit_service = MagicMock()
        engine._commit_service.commit = AsyncMock(
            return_value={
                "created_nodes": ["n1"],
                "created_edges": ["e1"],
                "created_templates": [],
            }
        )

        with index_patch, process_patch as mock_process:
            result = await engine.process_document(
                "Some document text about a topic.",
                source_id="extract-me",
                filename="report.pdf",
            )

            # Extraction + commit ran exactly as before.
            mock_process.assert_awaited_once()
            engine._commit_service.commit.assert_awaited_once()
        assert result.status is None
        assert result.nodes == ["n1"]
        assert result.edges == ["e1"]

        # No SourceRow was created by the non-gated path (unchanged behaviour).
        row = engine.storage_adapter.get_source("extract-me", engine.settings.current_database)
        assert row is None

    @pytest.mark.asyncio
    async def test_forced_domain_suppresses_park(self, engine):
        """A forced domain proceeds even with auto_confirm=False."""
        index_patch, process_patch = self._mock_pipeline_stages(engine)

        engine._extraction_service = MagicMock()
        engine._extraction_service.finalize_distributed_extraction = AsyncMock(
            return_value={"entities": [], "relationships": []}
        )
        engine._commit_service = MagicMock()
        engine._commit_service.commit = AsyncMock(
            return_value={"created_nodes": [], "created_edges": [], "created_templates": []}
        )

        with index_patch, process_patch as mock_process:
            result = await engine.process_document(
                "Some document text about a topic.",
                source_id="forced",
                filename="report.pdf",
                auto_confirm=False,
                forced_domain="medical",
            )

            # forced_domain => confirmation_required is False => extraction runs.
            mock_process.assert_awaited_once()
            engine._commit_service.commit.assert_awaited_once()
        assert result.status is None


@pytest.mark.unit
@pytest.mark.core
class TestIndexingServiceFactory:
    """Test IndexingService.from_engine() factory method."""

    def test_from_engine_creates_service(self, engine):
        """IndexingService.from_engine() returns a configured service."""
        from chaoscypher_core import IndexingService

        service = IndexingService.from_engine(engine)
        assert service.repository is engine.storage_adapter
        assert service.settings is engine.settings

    def test_from_engine_returns_indexing_service(self, engine):
        """from_engine() returns an IndexingService instance."""
        from chaoscypher_core import IndexingService

        service = IndexingService.from_engine(engine)
        assert isinstance(service, IndexingService)
