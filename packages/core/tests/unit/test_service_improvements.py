# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for service API improvements."""

from unittest.mock import AsyncMock, MagicMock

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
class TestExtractionServiceAlias:
    """Test ExtractionService.extract() delegates correctly."""

    @pytest.mark.asyncio
    async def test_extract_delegates(self):
        """extract() calls finalize_distributed_extraction."""
        from chaoscypher_core import ExtractionService

        service = ExtractionService(
            graph_repository=MagicMock(),
            llm_provider=MagicMock(),
            settings=MagicMock(),
            embedding_service=None,
        )
        service.finalize_distributed_extraction = AsyncMock(
            return_value={"entities": [], "relationships": []}
        )

        result = await service.extract(
            entities=[{"name": "Alice"}],
            relationships=[],
            domain="literary",
        )

        service.finalize_distributed_extraction.assert_called_once_with(
            raw_entities=[{"name": "Alice"}],
            raw_relationships=[],
            generate_embeddings=True,
            detected_domain="literary",
            edge_type_constraints=None,
            filtering_config=None,
        )
        assert result == {"entities": [], "relationships": []}

    @pytest.mark.asyncio
    async def test_extract_defaults_embeddings_true(self):
        """extract() defaults generate_embeddings to True."""
        from chaoscypher_core import ExtractionService

        service = ExtractionService(
            graph_repository=MagicMock(),
            llm_provider=MagicMock(),
            settings=MagicMock(),
            embedding_service=None,
        )
        service.finalize_distributed_extraction = AsyncMock(return_value={})

        await service.extract(entities=[], relationships=[])
        kwargs = service.finalize_distributed_extraction.call_args[1]
        assert kwargs["generate_embeddings"] is True


@pytest.mark.unit
@pytest.mark.core
class TestFromEngineFactories:
    """Test from_engine() class methods."""

    def test_commit_service_from_engine(self, engine):
        """SourceCommitService.from_engine() wires dependencies."""
        from chaoscypher_core import SourceCommitService

        service = SourceCommitService.from_engine(engine)
        assert service.graph_repository is engine.graph_repository
        assert service.search_repository is engine.search_repository
        assert service.source_repository is engine.storage_adapter

    def test_search_service_from_engine(self, engine):
        """SearchService.from_engine() wires dependencies."""
        from chaoscypher_core import SearchService

        service = SearchService.from_engine(engine)
        assert service.graph_repository is engine.graph_repository
        assert service.search_repository is engine.search_repository


@pytest.mark.unit
@pytest.mark.core
class TestCreateChunksStore:
    """Test store parameter on create_chunks."""

    @pytest.mark.asyncio
    async def test_auto_store_when_repo_available(self):
        """create_chunks auto-stores when repository is available."""
        from chaoscypher_core.settings import EngineSettings
        from chaoscypher_core.utils.chunk import ChunkingService

        mock_repo = MagicMock()
        service = ChunkingService(
            settings=EngineSettings(current_database="test"),
            repository=mock_repo,
        )
        await service.create_chunks(full_text="Test document text.")
        # store_chunks_and_groups should have been called (auto-store)
        assert mock_repo.store_chunks_and_groups.called

    @pytest.mark.asyncio
    async def test_no_store_when_false(self):
        """create_chunks skips storage when store=False."""
        from chaoscypher_core.settings import EngineSettings
        from chaoscypher_core.utils.chunk import ChunkingService

        mock_repo = MagicMock()
        service = ChunkingService(
            settings=EngineSettings(current_database="test"),
            repository=mock_repo,
        )
        await service.create_chunks(full_text="Test document.", store=False)
        mock_repo.store_chunks_and_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_store_when_no_repo(self):
        """create_chunks works fine without repository."""
        from chaoscypher_core.settings import EngineSettings
        from chaoscypher_core.utils.chunk import ChunkingService

        service = ChunkingService(settings=EngineSettings(current_database="test"))
        result = await service.create_chunks(full_text="Test document.")
        assert result.total_small_chunks >= 0
