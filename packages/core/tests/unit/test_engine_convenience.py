# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Engine convenience methods.

Covers add_document, add_documents, search, index_source, rebuild_indexes,
and analysis_depth passthrough.
"""

from unittest.mock import AsyncMock, patch

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
class TestAddDocument:
    """Tests for Engine.add_document()."""

    @pytest.mark.asyncio
    async def test_add_document_returns_processing_result(self, engine, tmp_path):
        """add_document() loads file and returns ProcessingResult."""
        from chaoscypher_core.models import ProcessingResult

        test_file = tmp_path / "test.txt"
        test_file.write_text("This is test content.")

        with patch.object(engine, "process_document", new_callable=AsyncMock) as mock_pd:
            mock_pd.return_value = ProcessingResult(
                source_id="auto-id", nodes=["n1"], edges=[], templates=[]
            )
            result = await engine.add_document(str(test_file))

            assert isinstance(result, ProcessingResult)
            assert result.nodes == ["n1"]
            mock_pd.assert_called_once()
            # Verify filename was extracted from path
            _, kwargs = mock_pd.call_args
            assert kwargs["filename"] == "test.txt"

    @pytest.mark.asyncio
    async def test_add_document_accepts_path_object(self, engine, tmp_path):
        """add_document() accepts Path objects."""
        from chaoscypher_core.models import ProcessingResult

        test_file = tmp_path / "doc.txt"
        test_file.write_text("Content here.")

        with patch.object(engine, "process_document", new_callable=AsyncMock) as mock_pd:
            mock_pd.return_value = ProcessingResult(source_id="x")
            await engine.add_document(test_file)  # Path object, not str
            mock_pd.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_document_passes_source_id(self, engine, tmp_path):
        """add_document() forwards source_id to process_document."""
        from chaoscypher_core.models import ProcessingResult

        test_file = tmp_path / "test.txt"
        test_file.write_text("Content.")

        with patch.object(engine, "process_document", new_callable=AsyncMock) as mock_pd:
            mock_pd.return_value = ProcessingResult(source_id="custom-id")
            await engine.add_document(str(test_file), source_id="custom-id")
            _, kwargs = mock_pd.call_args
            assert kwargs["source_id"] == "custom-id"


@pytest.mark.unit
@pytest.mark.core
class TestSearch:
    """Tests for Engine.search()."""

    @pytest.mark.asyncio
    async def test_search_returns_search_results(self, engine):
        """search() returns list of EngineSearchResult models."""
        from chaoscypher_core.models import EngineSearchResult

        mock_raw = {
            "data": [
                {
                    "result_type": "node",
                    "node": {"id": "n1", "label": "Einstein", "template_id": "person"},
                    "score": 0.95,
                },
                {
                    "result_type": "chunk",
                    "chunk": {"id": "c1", "content": "Chapter 3 text", "filename": "paper.pdf"},
                    "score": 0.87,
                },
            ],
            "type": "hybrid",
        }

        with patch.object(
            engine.search_service, "hybrid_search", new_callable=AsyncMock
        ) as mock_hs:
            mock_hs.return_value = mock_raw
            results = await engine.search("quantum")

            assert isinstance(results, list)
            assert len(results) == 2
            assert all(isinstance(r, EngineSearchResult) for r in results)

            assert results[0].label == "Einstein"
            assert results[0].result_type == "node"
            assert results[0].score == 0.95
            assert results[0].template_id == "person"

            assert results[1].result_type == "chunk"
            assert results[1].source == "paper.pdf"
            assert results[1].content == "Chapter 3 text"

    @pytest.mark.asyncio
    async def test_search_keyword_mode(self, engine):
        """search(mode='keyword') uses keyword_search."""
        with patch.object(engine.search_service, "keyword_search") as mock_ks:
            mock_ks.return_value = {"data": [], "type": "keyword"}
            await engine.search("test", mode="keyword")
            mock_ks.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_semantic_mode(self, engine):
        """search(mode='semantic') uses semantic_search."""
        with patch.object(
            engine.search_service, "semantic_search", new_callable=AsyncMock
        ) as mock_ss:
            mock_ss.return_value = {"data": [], "type": "semantic"}
            await engine.search("test", mode="semantic")
            mock_ss.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_passes_limit(self, engine):
        """search() forwards limit parameter."""
        with patch.object(
            engine.search_service, "hybrid_search", new_callable=AsyncMock
        ) as mock_hs:
            mock_hs.return_value = {"data": [], "type": "hybrid"}
            await engine.search("test", limit=5)
            _, kwargs = mock_hs.call_args
            assert kwargs["limit"] == 5

    @pytest.mark.asyncio
    async def test_search_auto_wires_embedding_callback(self, engine):
        """search() provides embedding callback for hybrid/semantic."""
        with patch.object(
            engine.search_service, "hybrid_search", new_callable=AsyncMock
        ) as mock_hs:
            mock_hs.return_value = {"data": [], "type": "hybrid"}
            await engine.search("test")
            _, kwargs = mock_hs.call_args
            assert "embedding_provider_callback" in kwargs
            assert kwargs["embedding_provider_callback"] is not None

    @pytest.mark.asyncio
    async def test_search_empty_results(self, engine):
        """search() handles empty results."""
        with patch.object(
            engine.search_service, "hybrid_search", new_callable=AsyncMock
        ) as mock_hs:
            mock_hs.return_value = {"data": [], "type": "hybrid"}
            results = await engine.search("nothing")
            assert results == []


@pytest.mark.unit
@pytest.mark.core
class TestIndexSource:
    """Tests for Engine.index_source()."""

    @pytest.mark.asyncio
    async def test_index_source_returns_indexing_result(self, engine):
        """index_source() returns IndexingResult model."""
        from chaoscypher_core.models import IndexingResult

        mock_raw = {
            "chunks_count": 42,
            "embedding_model": "snowflake-arctic-embed2",
            "embedding_dimensions": 1024,
        }

        # index_source first checks for existing chunks via the chunking
        # service's get_small_chunks(); stub it so we don't need a real DB.
        with (
            patch.object(
                engine.chunking_service,
                "get_small_chunks",
                return_value=[{"id": "c1"}],
            ),
            patch.object(
                engine.indexing_service, "create_index", new_callable=AsyncMock
            ) as mock_ci,
        ):
            mock_ci.return_value = mock_raw
            result = await engine.index_source("src_001")

            assert isinstance(result, IndexingResult)
            assert result.chunks_count == 42
            assert result.embedding_model == "snowflake-arctic-embed2"
            assert result.embedding_dimensions == 1024
            mock_ci.assert_called_once_with(source_id="src_001")


@pytest.mark.unit
@pytest.mark.core
class TestRebuildIndexes:
    """Tests for Engine.rebuild_indexes()."""

    def test_rebuild_indexes_returns_rebuild_result(self, engine):
        """rebuild_indexes() returns RebuildResult model."""
        from chaoscypher_core.models import RebuildResult

        mock_raw = {
            "success": True,
            "total_nodes": 100,
            "nodes_with_embeddings": 85,
            "chunks_indexed": 420,
            "message": "Rebuilt indexes: 100 nodes ...",
        }

        with patch.object(engine.search_service, "rebuild_indexes") as mock_ri:
            mock_ri.return_value = mock_raw
            result = engine.rebuild_indexes()

            assert isinstance(result, RebuildResult)
            assert result.total_nodes == 100
            assert result.nodes_with_embeddings == 85
            assert result.chunks_indexed == 420


@pytest.mark.unit
@pytest.mark.core
class TestAddDocuments:
    """Tests for Engine.add_documents()."""

    @pytest.mark.asyncio
    async def test_add_documents_with_list(self, engine, tmp_path):
        """add_documents() processes a list of file paths."""
        from chaoscypher_core.models import ProcessingResult

        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("Content A.")
        f2.write_text("Content B.")

        with patch.object(engine, "add_document", new_callable=AsyncMock) as mock_ad:
            mock_ad.side_effect = [
                ProcessingResult(source_id="s1", nodes=["n1"]),
                ProcessingResult(source_id="s2", nodes=["n2"]),
            ]
            results = await engine.add_documents([str(f1), str(f2)])

            assert len(results) == 2
            assert results[0].source_id == "s1"
            assert results[1].source_id == "s2"
            assert mock_ad.call_count == 2

    @pytest.mark.asyncio
    async def test_add_documents_with_glob(self, engine, tmp_path):
        """add_documents() expands glob patterns."""
        from chaoscypher_core.models import ProcessingResult

        (tmp_path / "doc1.txt").write_text("Content 1.")
        (tmp_path / "doc2.txt").write_text("Content 2.")
        (tmp_path / "image.png").write_bytes(b"\x89PNG")

        with patch.object(engine, "add_document", new_callable=AsyncMock) as mock_ad:
            mock_ad.return_value = ProcessingResult(source_id="s")
            results = await engine.add_documents(str(tmp_path / "*.txt"))

            assert len(results) == 2
            assert mock_ad.call_count == 2


@pytest.mark.unit
@pytest.mark.core
class TestAnalysisDepth:
    """Tests for analysis_depth parameter on process_document and add_document."""

    @pytest.mark.asyncio
    async def test_process_document_passes_analysis_depth(self, engine):
        """process_document() forwards analysis_depth to chunking_service.process()."""
        from chaoscypher_core.models import ChunksResult, ExtractionResult

        mock_chunks = ChunksResult(
            small_chunks=[],
            hierarchical_groups=[],
            total_small_chunks=0,
            total_groups=0,
            total_original_chunks=0,
            total_original_groups=0,
        )
        mock_extraction = ExtractionResult(entities=[], relationships=[])
        mock_finalized = {"entities": [], "relationships": [], "suggested_templates": []}
        mock_commit = {"nodes": [], "edges": [], "templates": []}
        mock_index = {"chunks_count": 0, "embedding_model": "m", "embedding_dimensions": 1}

        with (
            patch.object(
                engine.chunking_service, "create_chunks", new_callable=AsyncMock
            ) as mock_cc,
            patch.object(engine.chunking_service, "process", new_callable=AsyncMock) as mock_proc,
            patch.object(
                engine.indexing_service, "create_index", new_callable=AsyncMock
            ) as mock_idx,
        ):
            mock_cc.return_value = mock_chunks
            mock_proc.return_value = mock_extraction
            mock_idx.return_value = mock_index
            engine._extraction_service = AsyncMock()
            engine._extraction_service.finalize_distributed_extraction = AsyncMock(
                return_value=mock_finalized
            )
            engine._commit_service = AsyncMock()
            engine._commit_service.commit = AsyncMock(return_value=mock_commit)

            await engine.process_document("text", analysis_depth="quick")

            _, kwargs = mock_proc.call_args
            assert kwargs.get("analysis_depth") == "quick"

    @pytest.mark.asyncio
    async def test_add_document_passes_analysis_depth(self, engine, tmp_path):
        """add_document() forwards analysis_depth to process_document()."""
        from chaoscypher_core.models import ProcessingResult

        test_file = tmp_path / "test.txt"
        test_file.write_text("Content.")

        with patch.object(engine, "process_document", new_callable=AsyncMock) as mock_pd:
            mock_pd.return_value = ProcessingResult(source_id="x")
            await engine.add_document(str(test_file), analysis_depth="quick")
            _, kwargs = mock_pd.call_args
            assert kwargs["analysis_depth"] == "quick"
