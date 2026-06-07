# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit Tests for CLI Source Processing Service.

Tests the CLISourceProcessingService methods:
- upload_file: File staging and metadata creation
- index_file: Document loading and chunking (via Core ChunkingService)
- extract_entities: LLM-based extraction (with mocked Core services)
- commit_to_graph: Writing to knowledge graph (via Core SourceCommitService)
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cli.sources.service import CLISourceProcessingService


if TYPE_CHECKING:
    from pathlib import Path


def _mock_loader_registry():
    """Create a mock LoaderRegistry with standard extensions."""
    mock_registry = MagicMock()
    mock_registry.list_supported_extensions.return_value = [
        ".csv",
        ".doc",
        ".docx",
        ".html",
        ".htm",
        ".json",
        ".jsonl",
        ".md",
        ".pdf",
        ".txt",
    ]
    mock_registry.load_document.return_value = [{"content": "text", "metadata": {}}]
    return mock_registry


@pytest.fixture(autouse=True)
def _patch_loader_registry():
    """Mock LoaderRegistry for all service tests that call upload_file or index_file."""
    with patch(
        "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
        return_value=_mock_loader_registry(),
    ):
        yield


class TestUploadFile:
    """Tests for CLISourceProcessingService.upload_file()."""

    def test_upload_text_file(self, mock_cli_context: MagicMock, sample_text_file: Path) -> None:
        """Test uploading a text file creates proper metadata."""
        service = CLISourceProcessingService(mock_cli_context)

        file_id = service.upload_file(sample_text_file)

        # Check file ID format — UUIDs (e.g. e86879a7-2b94-43cc-b78f-67606795a4a7)
        assert isinstance(file_id, str)
        assert len(file_id) == 36
        assert file_id.count("-") == 4

        # Check file was tracked
        file_record = mock_cli_context.storage_adapter.get_file(
            file_id, mock_cli_context.database_name
        )
        assert file_record is not None
        assert file_record["filename"] == "sample.txt"
        assert file_record["status"] == "uploaded"

    def test_upload_markdown_file(
        self, mock_cli_context: MagicMock, sample_markdown_file: Path
    ) -> None:
        """Test uploading a markdown file."""
        service = CLISourceProcessingService(mock_cli_context)

        file_id = service.upload_file(sample_markdown_file)

        file_record = mock_cli_context.storage_adapter.get_file(
            file_id, mock_cli_context.database_name
        )
        assert file_record["filename"] == "sample.md"

    def test_upload_nonexistent_file_raises(
        self, mock_cli_context: MagicMock, temp_dir: Path
    ) -> None:
        """Test uploading nonexistent file raises FileNotFoundError."""
        service = CLISourceProcessingService(mock_cli_context)
        nonexistent = temp_dir / "does_not_exist.txt"

        with pytest.raises(FileNotFoundError):
            service.upload_file(nonexistent)

    def test_upload_unsupported_type_raises(
        self, mock_cli_context: MagicMock, temp_dir: Path
    ) -> None:
        """Test uploading unsupported file type raises ValueError."""
        service = CLISourceProcessingService(mock_cli_context)
        unsupported = temp_dir / "file.xyz"
        unsupported.write_text("content")

        with pytest.raises(ValueError, match="Unsupported file type"):
            service.upload_file(unsupported)

    def test_upload_stores_domain(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """Test upload passes domain to upload_source."""
        service = CLISourceProcessingService(mock_cli_context)

        file_id = service.upload_file(sample_text_file, domain="technology")

        file_record = mock_cli_context.storage_adapter.get_file(
            file_id, mock_cli_context.database_name
        )
        assert file_record["forced_domain"] == "technology"


class TestIndexFile:
    """Tests for CLISourceProcessingService.index_file()."""

    def test_index_creates_chunks(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """Test indexing creates document chunks via Core ChunkingService."""
        service = CLISourceProcessingService(mock_cli_context)

        # Upload first
        file_id = service.upload_file(sample_text_file)

        # Mock ChunkingService.create_chunks
        from chaoscypher_core.models import ChunksResult

        mock_chunk_result = ChunksResult(
            small_chunks=[
                {"id": f"{file_id}:chunk:0", "content": "chunk 0", "token_count": 10},
                {"id": f"{file_id}:chunk:1", "content": "chunk 1", "token_count": 15},
            ],
            hierarchical_groups=[
                {"id": "g0", "group_index": 0, "combined_content": "chunk 0 chunk 1"}
            ],
            total_small_chunks=2,
            total_groups=1,
            total_original_chunks=2,
            total_original_groups=1,
        )

        with patch("chaoscypher_core.utils.chunk.ChunkingService") as mock_chunking_cls:
            mock_instance = MagicMock()
            mock_instance.create_chunks = AsyncMock(return_value=mock_chunk_result)
            mock_chunking_cls.return_value = mock_instance

            result = service.index_file(file_id, skip_embeddings=True)

        # Should have chunks
        assert result["chunks_count"] == 2
        assert result["tokens_count"] == 25
        assert result["failed_embeddings"] == 0

        # File status should be indexed
        file_record = mock_cli_context.storage_adapter.get_file(
            file_id, mock_cli_context.database_name
        )
        assert file_record["status"] == "indexed"

    def test_index_file_not_found_raises(self, mock_cli_context: MagicMock) -> None:
        """Test indexing nonexistent file raises ValueError."""
        service = CLISourceProcessingService(mock_cli_context)

        with pytest.raises(ValueError, match="File not found"):
            service.index_file("if_nonexistent1")

    def test_index_already_indexed_raises(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """Test re-indexing already indexed file raises ValueError."""
        service = CLISourceProcessingService(mock_cli_context)

        file_id = service.upload_file(sample_text_file)

        # Mock ChunkingService for first index
        from chaoscypher_core.models import ChunksResult

        mock_chunk_result = ChunksResult(
            small_chunks=[{"id": f"{file_id}:chunk:0", "content": "c", "token_count": 5}],
            hierarchical_groups=[],
            total_small_chunks=1,
            total_groups=0,
            total_original_chunks=1,
            total_original_groups=0,
        )

        with patch("chaoscypher_core.utils.chunk.ChunkingService") as mock_chunking_cls:
            mock_instance = MagicMock()
            mock_instance.create_chunks = AsyncMock(return_value=mock_chunk_result)
            mock_chunking_cls.return_value = mock_instance
            service.index_file(file_id)

        # Second index should fail
        with pytest.raises(ValueError, match="Cannot index"):
            service.index_file(file_id)


class TestHasLLM:
    """Tests for LLM availability checking."""

    def test_has_llm_false_when_not_configured(self, mock_cli_context: MagicMock) -> None:
        """Test has_llm returns False when no LLM."""
        service = CLISourceProcessingService(mock_cli_context)
        assert service.has_llm is False

    def test_has_llm_true_when_configured(self, mock_cli_context_with_llm: MagicMock) -> None:
        """Test has_llm returns True when LLM is available."""
        service = CLISourceProcessingService(mock_cli_context_with_llm)
        assert service.has_llm is True


class TestExtractEntities:
    """Tests for CLISourceProcessingService.extract_entities()."""

    def test_extract_without_llm_raises(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """Test extraction without LLM raises ValueError."""
        service = CLISourceProcessingService(mock_cli_context)

        file_id = service.upload_file(sample_text_file)

        # Manually set status to indexed (bypass index_file which needs ChunkingService)
        mock_cli_context.storage_adapter._files[file_id]["status"] = "indexed"

        with pytest.raises(ValueError, match="LLM provider not configured"):
            service.extract_entities(file_id)

    def test_extract_on_non_indexed_raises(
        self, mock_cli_context_with_llm: MagicMock, sample_text_file: Path
    ) -> None:
        """Test extraction on non-indexed file raises ValueError."""
        service = CLISourceProcessingService(mock_cli_context_with_llm)

        file_id = service.upload_file(sample_text_file)

        with pytest.raises(ValueError, match="Cannot extract"):
            service.extract_entities(file_id)


class TestCommitToGraph:
    """Tests for CLISourceProcessingService.commit_to_graph()."""

    def test_commit_empty_extraction(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """Test committing file with no extraction results."""
        service = CLISourceProcessingService(mock_cli_context)

        file_id = service.upload_file(sample_text_file)
        # Manually set status to indexed (bypass index_file)
        mock_cli_context.storage_adapter._files[file_id]["status"] = "indexed"

        # Mock SourceCommitService.commit
        with patch(
            "chaoscypher_core.services.sources.engine.commit.service.SourceCommitService"
        ) as mock_commit_cls:
            mock_instance = MagicMock()
            mock_instance.commit = AsyncMock(
                return_value={"nodes": [], "edges": [], "templates": []}
            )
            mock_commit_cls.return_value = mock_instance

            result = service.commit_to_graph(file_id)

        # Should succeed with zero counts
        assert result["nodes_created"] == 0
        assert result["edges_created"] == 0

    def test_commit_on_uploaded_raises(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """Test committing uploaded (not indexed) file raises."""
        service = CLISourceProcessingService(mock_cli_context)

        file_id = service.upload_file(sample_text_file)

        with pytest.raises(ValueError, match="Cannot commit"):
            service.commit_to_graph(file_id)


class TestGetFileStatus:
    """Tests for CLISourceProcessingService.get_file_status()."""

    def test_get_status_returns_record(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """Test getting file status returns record."""
        service = CLISourceProcessingService(mock_cli_context)

        file_id = service.upload_file(sample_text_file)
        status = service.get_file_status(file_id)

        assert status is not None
        assert status["id"] == file_id
        assert status["status"] == "uploaded"

    def test_get_status_nonexistent_returns_none(self, mock_cli_context: MagicMock) -> None:
        """Test getting status for nonexistent file returns None."""
        service = CLISourceProcessingService(mock_cli_context)

        status = service.get_file_status("if_doesnotexist")
        assert status is None
