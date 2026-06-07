# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration Tests for CLI Source Pipeline.

Tests the full SourcePipeline with progress UI:
- Full pipeline execution (upload -> index -> commit)
- Stage skipping options
- Resume from file ID
- Error handling
"""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from rich.console import Console

from chaoscypher_cli.sources.pipeline import PipelineResult, SourcePipeline
from chaoscypher_cli.sources.service import CLISourceProcessingService


if TYPE_CHECKING:
    from pathlib import Path


def _patch_loader_registry():
    """Return a patch for get_loader_registry that reads files directly."""
    from pathlib import Path as _Path

    mock_registry = MagicMock()

    def _load_document(filepath: str) -> list[dict]:
        content = _Path(filepath).read_text(encoding="utf-8", errors="ignore")
        return [{"content": content, "metadata": {}}]

    mock_registry.load_document = MagicMock(side_effect=_load_document)
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
    return patch(
        "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
        return_value=mock_registry,
    )


def _patch_chunking_service():
    """Return a patch for ChunkingService that produces realistic chunk results."""

    def _make_mock_create_chunks():
        async def mock_create_chunks(
            source_id: str,
            full_text: str,
            analysis_depth: str = "full",
            **kwargs,
        ):
            from chaoscypher_core.models import ChunksResult

            # Produce one chunk per ~200 chars to simulate real chunking
            chunk_size = 200
            chunks = []
            for i in range(0, max(1, len(full_text)), chunk_size):
                chunk_text = full_text[i : i + chunk_size]
                if chunk_text.strip():
                    chunks.append(
                        {
                            "id": f"{source_id}:chunk:{len(chunks)}",
                            "source_id": source_id,
                            "chunk_index": len(chunks),
                            "content": chunk_text,
                            "token_count": len(chunk_text.split()),
                        }
                    )
            return ChunksResult(
                small_chunks=chunks,
                hierarchical_groups=[],
                total_small_chunks=len(chunks),
                total_groups=0,
                total_original_chunks=len(chunks),
                total_original_groups=0,
            )

        return mock_create_chunks

    mock_cls = MagicMock()
    mock_instance = MagicMock()
    mock_instance.create_chunks = AsyncMock(side_effect=_make_mock_create_chunks())
    mock_instance.store_chunks = MagicMock()
    mock_cls.return_value = mock_instance
    return patch("chaoscypher_core.utils.chunk.ChunkingService", mock_cls)


def _patch_commit_service():
    """Return a patch for SourceCommitService that returns empty results."""
    mock_cls = MagicMock()
    mock_instance = MagicMock()
    mock_instance.commit = AsyncMock(return_value={"nodes": [], "edges": [], "templates": []})
    mock_cls.return_value = mock_instance
    return patch(
        "chaoscypher_core.services.sources.engine.commit.service.SourceCommitService", mock_cls
    )


class TestPipelineResult:
    """Tests for PipelineResult dataclass."""

    def test_default_values(self) -> None:
        """Test PipelineResult has sensible defaults."""
        result = PipelineResult(
            file_id="if_test123456",
            filename="test.txt",
            success=True,
            status="completed",
        )

        assert result.file_id == "if_test123456"
        assert result.filename == "test.txt"
        assert result.success is True
        assert result.stages_completed == []
        assert result.stages_skipped == []
        assert result.chunks_count == 0
        assert result.error is None


class TestDetermineStages:
    """Tests for stage determination logic."""

    def test_full_pipeline_stages(self, mock_cli_context: MagicMock) -> None:
        """Test full pipeline determines all stages."""
        service = CLISourceProcessingService(mock_cli_context)
        pipeline = SourcePipeline(service)

        stages = pipeline._determine_stages(
            file_id=None,
            skip_index=False,
            skip_extract=False,
            skip_commit=False,
            index_only=False,
            extract_only=False,
        )

        assert stages == ["upload", "index", "extract", "commit"]

    def test_resume_skips_upload(self, mock_cli_context: MagicMock) -> None:
        """Test resuming from file_id skips upload."""
        service = CLISourceProcessingService(mock_cli_context)
        pipeline = SourcePipeline(service)

        stages = pipeline._determine_stages(
            file_id="if_existing123",
            skip_index=False,
            skip_extract=False,
            skip_commit=False,
            index_only=False,
            extract_only=False,
        )

        assert "upload" not in stages
        assert stages == ["index", "extract", "commit"]

    def test_skip_extract_flag(self, mock_cli_context: MagicMock) -> None:
        """Test --skip-extract removes extract stage."""
        service = CLISourceProcessingService(mock_cli_context)
        pipeline = SourcePipeline(service)

        stages = pipeline._determine_stages(
            file_id=None,
            skip_index=False,
            skip_extract=True,
            skip_commit=False,
            index_only=False,
            extract_only=False,
        )

        assert stages == ["upload", "index", "commit"]

    def test_index_only_flag(self, mock_cli_context: MagicMock) -> None:
        """Test --index-only stops after index."""
        service = CLISourceProcessingService(mock_cli_context)
        pipeline = SourcePipeline(service)

        stages = pipeline._determine_stages(
            file_id=None,
            skip_index=False,
            skip_extract=False,
            skip_commit=False,
            index_only=True,
            extract_only=False,
        )

        assert stages == ["upload", "index"]

    def test_extract_only_flag(self, mock_cli_context: MagicMock) -> None:
        """Test --extract-only stops after extract."""
        service = CLISourceProcessingService(mock_cli_context)
        pipeline = SourcePipeline(service)

        stages = pipeline._determine_stages(
            file_id=None,
            skip_index=False,
            skip_extract=False,
            skip_commit=False,
            index_only=False,
            extract_only=True,
        )

        assert stages == ["upload", "index", "extract"]


class TestQuietMode:
    """Tests for quiet mode execution."""

    def test_quiet_mode_upload_and_index(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """Test quiet mode runs upload and index without UI."""
        service = CLISourceProcessingService(mock_cli_context)
        pipeline = SourcePipeline(service)

        with _patch_chunking_service(), _patch_loader_registry():
            result = pipeline.run(
                file_path=sample_text_file,
                skip_extract=True,
                skip_commit=True,
                quiet=True,
            )

        assert result.success is True
        assert len(result.file_id) == 36  # Plain UUID v4 (no prefix)
        assert result.file_id.count("-") == 4
        assert "upload" in result.stages_completed
        assert "index" in result.stages_completed
        assert result.chunks_count > 0

    def test_quiet_mode_skips_extract_without_llm(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """Test quiet mode marks extract as skipped without LLM."""
        service = CLISourceProcessingService(mock_cli_context)
        pipeline = SourcePipeline(service)

        with _patch_chunking_service(), _patch_loader_registry(), _patch_commit_service():
            result = pipeline.run(
                file_path=sample_text_file,
                skip_commit=True,
                quiet=True,
            )

        assert result.success is True
        assert "extract" in result.stages_skipped


class TestUIMode:
    """Tests for UI mode execution."""

    def test_ui_mode_with_progress(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """Test UI mode shows progress (captured output)."""
        service = CLISourceProcessingService(mock_cli_context)

        # Use StringIO to capture console output
        output = StringIO()
        console = Console(file=output, force_terminal=True, width=80)

        pipeline = SourcePipeline(service, console)

        with _patch_chunking_service(), _patch_loader_registry():
            result = pipeline.run(
                file_path=sample_text_file,
                skip_extract=True,
                skip_commit=True,
                quiet=False,
            )

        assert result.success is True
        # Pipeline should complete
        assert "upload" in result.stages_completed


class TestErrorHandling:
    """Tests for error handling in pipeline."""

    def test_upload_error_captured(self, mock_cli_context: MagicMock, temp_dir: Path) -> None:
        """Test upload error is captured in result."""
        service = CLISourceProcessingService(mock_cli_context)
        pipeline = SourcePipeline(service)

        nonexistent = temp_dir / "nonexistent.txt"

        result = pipeline.run(
            file_path=nonexistent,
            quiet=True,
        )

        assert result.success is False
        assert result.status == "failed"
        assert result.error is not None
        assert "not found" in result.error.lower() or "File not found" in result.error

    def test_duration_tracked(self, mock_cli_context: MagicMock, sample_text_file: Path) -> None:
        """Test duration is tracked even on failure."""
        service = CLISourceProcessingService(mock_cli_context)
        pipeline = SourcePipeline(service)

        with _patch_chunking_service(), _patch_loader_registry():
            result = pipeline.run(
                file_path=sample_text_file,
                skip_extract=True,
                skip_commit=True,
                quiet=True,
            )

        assert result.duration_seconds >= 0


class TestResumeFromFileId:
    """Tests for resuming from existing file ID."""

    def test_resume_from_indexed_file(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """Test resuming from an already indexed file skips to commit."""
        service = CLISourceProcessingService(mock_cli_context)

        # Upload first
        file_id = service.upload_file(sample_text_file)

        # Index with mocked ChunkingService and LoaderRegistry
        with _patch_chunking_service(), _patch_loader_registry():
            service.index_file(file_id)

        # Now resume - skip index since already done, skip extract (no LLM)
        pipeline = SourcePipeline(service)
        with _patch_commit_service():
            result = pipeline.run(
                file_id=file_id,
                skip_index=True,  # Already indexed
                skip_extract=True,  # No LLM
                quiet=True,
            )

        assert result.success is True
        assert result.file_id == file_id
        # Commit should be completed
        assert "commit" in result.stages_completed
