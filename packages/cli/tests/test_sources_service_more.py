# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Additional unit tests for CLISourceProcessingService.

Complements ``test_source_service.py`` by exercising the still-uncovered
slices of ``chaoscypher_cli.sources.service``:

- Context-manager / event-loop lifecycle (``close``, ``_run_async``,
  ``_cancel_pending_tasks``, ``llm_provider``).
- ``upload_file`` / ``upload_url`` skip-duplicate + URL-import branches
  (binary, empty, fetch-error, happy path).
- ``index_file`` failure path (loader returns nothing -> fail_indexing).
- ``_generate_embeddings`` happy + per-batch-failure paths.
- ``_apply_vision_processing`` no-provider / no-model / no-images early
  returns and the standalone-image happy path.
- ``detect_domain_for_source`` (missing source, no chunks, happy path).
- ``extract_entities`` happy path + no-chunks branch + failure handler,
  with the heavy Core orchestration mocked at its import boundaries.
- ``_extract_and_finalize`` exercised directly with fake extractor +
  finalizer.
- ``commit_to_graph`` stored-payload branch + fail_commit branch.
- ``reset_for_re_extraction`` happy path.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cli.sources.service import CLISourceProcessingService


if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Shared loader-registry patch (upload_file / index_file both consult it)
# ---------------------------------------------------------------------------


def _mock_loader_registry(
    documents: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Create a mock LoaderRegistry with standard extensions."""
    mock_registry = MagicMock()
    mock_registry.list_supported_extensions.return_value = [
        ".csv",
        ".html",
        ".htm",
        ".json",
        ".md",
        ".pdf",
        ".txt",
    ]
    mock_registry.load_document.return_value = (
        documents if documents is not None else [{"content": "text", "metadata": {}}]
    )
    return mock_registry


@pytest.fixture(autouse=True)
def _patch_loader_registry():
    """Mock LoaderRegistry for tests that call upload_file or index_file."""
    with patch(
        "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
        return_value=_mock_loader_registry(),
    ):
        yield


# ===========================================================================
# Lifecycle / event-loop helpers
# ===========================================================================


class TestLifecycle:
    """Context-manager, event-loop, and provider accessors."""

    def test_context_manager_returns_self_and_closes_loop(
        self, mock_cli_context: MagicMock
    ) -> None:
        """__enter__ returns self; __exit__ closes a live loop."""
        with CLISourceProcessingService(mock_cli_context) as service:
            assert isinstance(service, CLISourceProcessingService)
            # Force a loop to exist so close() has something to tear down.
            service._run_async(asyncio.sleep(0))
            assert service._loop is not None
        # After __exit__ the loop is released.
        assert service._loop is None

    def test_run_async_reuses_loop(self, mock_cli_context: MagicMock) -> None:
        """_run_async creates a loop once and reuses it across calls."""
        service = CLISourceProcessingService(mock_cli_context)
        try:
            assert service._run_async(_aval(7)) == 7
            first_loop = service._loop
            assert service._run_async(_aval(9)) == 9
            assert service._loop is first_loop
        finally:
            service.close()

    def test_llm_provider_passthrough(self, mock_cli_context_with_llm: MagicMock) -> None:
        """llm_provider property returns the context's provider."""
        service = CLISourceProcessingService(mock_cli_context_with_llm)
        assert service.llm_provider is mock_cli_context_with_llm.llm_provider

    def test_close_is_idempotent(self, mock_cli_context: MagicMock) -> None:
        """close() is safe to call when no loop was ever created and twice."""
        service = CLISourceProcessingService(mock_cli_context)
        service.close()  # no loop yet -> no-op
        service._run_async(_aval(1))
        service.close()
        service.close()  # second close after already closed -> no-op
        assert service._loop is None

    def test_cancel_pending_tasks_no_loop_is_noop(self, mock_cli_context: MagicMock) -> None:
        """_cancel_pending_tasks returns quietly with no live loop."""
        service = CLISourceProcessingService(mock_cli_context)
        # Should not raise even though no loop exists.
        service._cancel_pending_tasks()

    def test_run_async_keyboardinterrupt_cancels_then_reraises(
        self, mock_cli_context: MagicMock
    ) -> None:
        """KeyboardInterrupt from the coroutine triggers task cancellation + re-raise."""
        service = CLISourceProcessingService(mock_cli_context)

        async def boom() -> None:
            raise KeyboardInterrupt

        cancel_spy = MagicMock(wraps=service._cancel_pending_tasks)
        service._cancel_pending_tasks = cancel_spy  # type: ignore[method-assign]
        try:
            with pytest.raises(KeyboardInterrupt):
                service._run_async(boom())
            cancel_spy.assert_called_once()
        finally:
            service.close()

    def test_cancel_pending_tasks_cancels_live_task(self, mock_cli_context: MagicMock) -> None:
        """A pending task on the loop is cancelled and gathered."""
        service = CLISourceProcessingService(mock_cli_context)
        try:
            # Force a loop to exist.
            service._run_async(_aval(0))
            loop = service._loop
            assert loop is not None

            async def _forever() -> None:
                await asyncio.sleep(3600)

            task = loop.create_task(_forever())
            # Let the task start running, then cancel everything pending.
            service._cancel_pending_tasks()
            assert task.cancelled()
        finally:
            service.close()


async def _aval(value: Any) -> Any:
    """Tiny coroutine returning *value* (used by _run_async tests)."""
    return value


# ===========================================================================
# upload_file — duplicate detection
# ===========================================================================


class TestUploadFileDuplicates:
    """skip_duplicates short-circuit in upload_file."""

    def test_skip_duplicate_returns_dict_without_uploading(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """When an identical hash exists, upload_file returns a skip dict."""
        existing = {"id": "dup-123", "status": "committed"}
        mock_cli_context.storage_adapter.find_by_content_hash = MagicMock(return_value=existing)
        upload_spy = MagicMock()
        mock_cli_context.storage_adapter.upload_source = upload_spy

        service = CLISourceProcessingService(mock_cli_context)
        result = service.upload_file(sample_text_file, skip_duplicates=True)

        assert isinstance(result, dict)
        assert result["skipped_duplicate"] is True
        assert result["existing_status"] == "committed"
        assert result["filename"] == "sample.txt"
        upload_spy.assert_not_called()

    def test_skip_duplicate_no_match_uploads_normally(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """No existing hash -> normal upload, returns a real file id."""
        mock_cli_context.storage_adapter.find_by_content_hash = MagicMock(return_value=None)

        service = CLISourceProcessingService(mock_cli_context)
        result = service.upload_file(sample_text_file, skip_duplicates=True)

        assert isinstance(result, str)
        record = mock_cli_context.storage_adapter.get_file(result, mock_cli_context.database_name)
        assert record is not None
        assert record["status"] == "uploaded"


# ===========================================================================
# upload_url
# ===========================================================================


def _fetch_result(**overrides: Any) -> MagicMock:
    """Build a fake WebScraper FetchResult."""
    result = MagicMock()
    result.error = None
    result.is_binary = False
    result.content = "x" * 200
    result.title = "Cool Page!"
    result.content_type = "text/html"
    return _apply(result, overrides)


def _apply(obj: MagicMock, overrides: dict[str, Any]) -> MagicMock:
    for key, value in overrides.items():
        setattr(obj, key, value)
    return obj


def _patch_scraper(fetch_result: MagicMock) -> Any:
    """Patch WebScraper so extract_full_content yields *fetch_result*."""
    scraper_instance = MagicMock()
    scraper_instance.extract_full_content = AsyncMock(return_value=fetch_result)
    return patch(
        "chaoscypher_core.adapters.web.search.WebScraper",
        return_value=scraper_instance,
    )


class TestUploadURL:
    """upload_url fetch -> stage path and all its guard rails."""

    def test_fetch_error_raises(self, mock_cli_context: MagicMock) -> None:
        with _patch_scraper(_fetch_result(error="boom")):
            service = CLISourceProcessingService(mock_cli_context)
            with pytest.raises(ValueError, match="Failed to fetch URL"):
                service.upload_url("https://example.com")
            service.close()

    def test_binary_content_raises(self, mock_cli_context: MagicMock) -> None:
        with _patch_scraper(_fetch_result(is_binary=True, content_type="application/pdf")):
            service = CLISourceProcessingService(mock_cli_context)
            with pytest.raises(ValueError, match="binary content"):
                service.upload_url("https://example.com/file.pdf")
            service.close()

    def test_short_content_raises(self, mock_cli_context: MagicMock) -> None:
        with _patch_scraper(_fetch_result(content="tiny")):
            service = CLISourceProcessingService(mock_cli_context)
            with pytest.raises(ValueError, match="too short or empty"):
                service.upload_url("https://example.com")
            service.close()

    def test_happy_path_stages_markdown(self, mock_cli_context: MagicMock) -> None:
        with _patch_scraper(_fetch_result()):
            service = CLISourceProcessingService(mock_cli_context)
            file_id, page_title = service.upload_url("https://example.com", domain="technology")
            service.close()

        assert page_title == "Cool Page!"
        record = mock_cli_context.storage_adapter.get_file(file_id, mock_cli_context.database_name)
        assert record is not None
        # Title sanitized into a .md filename ("Cool Page!" -> "Cool_Page.md").
        assert record["filename"].endswith(".md")
        assert record["forced_domain"] == "technology"

    def test_untitled_page_uses_fallback_filename(self, mock_cli_context: MagicMock) -> None:
        """Empty/punctuation-only title falls back to web_import.md."""
        with _patch_scraper(_fetch_result(title="!!!")):
            service = CLISourceProcessingService(mock_cli_context)
            file_id, _ = service.upload_url("https://example.com")
            service.close()

        record = mock_cli_context.storage_adapter.get_file(file_id, mock_cli_context.database_name)
        assert record["filename"] == "web_import.md"

    def test_skip_duplicate_returns_skip_tuple(self, mock_cli_context: MagicMock) -> None:
        existing = {"id": "dup-url", "status": "indexed"}
        mock_cli_context.storage_adapter.find_by_content_hash = MagicMock(return_value=existing)
        upload_spy = MagicMock()
        mock_cli_context.storage_adapter.upload_source = upload_spy

        with _patch_scraper(_fetch_result()):
            service = CLISourceProcessingService(mock_cli_context)
            skip_dict, page_title = service.upload_url("https://example.com", skip_duplicates=True)
            service.close()

        assert skip_dict["skipped_duplicate"] is True
        assert skip_dict["existing_status"] == "indexed"
        assert page_title == "Cool Page!"
        upload_spy.assert_not_called()


# ===========================================================================
# index_file — failure path
# ===========================================================================


class TestIndexFileFailure:
    """index_file fails the source when the loader yields no content."""

    def test_no_content_marks_failed_and_raises(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        file_id = service.upload_file(sample_text_file)

        empty_registry = _mock_loader_registry(documents=[])
        with patch(
            "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
            return_value=empty_registry,
        ):
            with pytest.raises(ValueError, match="No content extracted"):
                # vision off so we go straight to the loader/empty check
                service.index_file(file_id, skip_embeddings=True, enable_vision=False)

        record = mock_cli_context.storage_adapter.get_file(file_id, mock_cli_context.database_name)
        assert record["status"] == "failed"
        assert "No content extracted" in record["error"]

    def test_no_content_with_vision_enabled_marks_failed(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """Empty loader output with vision ON still fails (vision returns empty docs)."""
        llm = MagicMock()
        llm.chat_provider = None  # vision early-returns the (empty) docs
        mock_cli_context.settings.llm = llm

        service = CLISourceProcessingService(mock_cli_context)
        file_id = service.upload_file(sample_text_file)

        empty_registry = _mock_loader_registry(documents=[])
        with patch(
            "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
            return_value=empty_registry,
        ):
            with pytest.raises(ValueError, match="No content extracted"):
                service.index_file(file_id, skip_embeddings=True, enable_vision=True)

        record = mock_cli_context.storage_adapter.get_file(file_id, mock_cli_context.database_name)
        assert record["status"] == "failed"

    def test_wrong_status_raises_before_touching_loader(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        file_id = service.upload_file(sample_text_file)
        mock_cli_context.storage_adapter._files[file_id]["status"] = "committed"

        with pytest.raises(ValueError, match="Cannot index"):
            service.index_file(file_id, skip_embeddings=True, enable_vision=False)

    def test_index_not_found_raises(self, mock_cli_context: MagicMock) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        with pytest.raises(ValueError, match="File not found"):
            service.index_file("does-not-exist", enable_vision=False)


class TestIndexFileHappyPath:
    """Full index_file pass with normalization + embeddings + completion."""

    def test_normalizes_chunks_embeds_and_completes(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        from chaoscypher_core.models import ChunksResult

        service = CLISourceProcessingService(mock_cli_context)
        file_id = service.upload_file(sample_text_file)

        # Real embedding service so _generate_embeddings runs end-to-end.
        from chaoscypher_core.models import BatchEmbedResult

        embedding_service = MagicMock()
        embedding_service.model_name = "fake-embed"

        async def _batch_embed(texts: list[str], batch_size: int = 50) -> BatchEmbedResult:
            return BatchEmbedResult(
                embeddings=[[0.5, 0.6] for _ in texts],
                total=len(texts),
                failed=0,
                provider="mock",
            )

        embedding_service.batch_embed = _batch_embed
        mock_cli_context.embedding_service = embedding_service
        mock_cli_context.settings.batching.embedding_api_batch_size = 8
        mock_cli_context.storage_adapter.update_chunk_embedding = MagicMock()
        # Vision ON but no chat_provider -> _apply_vision_processing returns the
        # docs unchanged, exercising the enable_vision call site in index_file.
        mock_cli_context.settings.llm = MagicMock()
        mock_cli_context.settings.llm.chat_provider = None

        chunk_result = ChunksResult(
            small_chunks=[
                {"id": f"{file_id}:c0", "content": "alpha", "token_count": 3},
                {"id": f"{file_id}:c1", "content": "beta", "token_count": 4},
            ],
            hierarchical_groups=[],
            total_small_chunks=2,
            total_groups=1,
            total_original_chunks=2,
            total_original_groups=1,
        )

        chunking_instance = MagicMock()
        chunking_instance.create_chunks = AsyncMock(return_value=chunk_result)
        chunking_instance.store_chunks = MagicMock()

        # Normalizer returns a slightly cleaned text.
        normalized = MagicMock()
        normalized.content = "alpha beta"
        normalized.quality_metrics.overall_score.return_value = 0.9
        normalizer_instance = MagicMock()
        normalizer_instance.normalize.return_value = normalized

        with (
            patch(
                "chaoscypher_core.utils.chunk.ChunkingService",
                return_value=chunking_instance,
            ),
            patch(
                "chaoscypher_core.services.sources.normalizer.service.ContentNormalizerService",
                return_value=normalizer_instance,
            ),
        ):
            result = service.index_file(
                file_id,
                skip_embeddings=False,
                enable_normalization=True,
                enable_vision=True,
            )

        assert result["chunks_count"] == 2
        assert result["tokens_count"] == 7
        assert result["embedding_model"] == "fake-embed"
        assert result["failed_embeddings"] == 0

        record = mock_cli_context.storage_adapter.get_file(file_id, mock_cli_context.database_name)
        assert record["status"] == "indexed"
        # Normalizer was actually consulted, chunks were stored, embeddings written.
        normalizer_instance.normalize.assert_called_once()
        chunking_instance.store_chunks.assert_called_once()
        assert mock_cli_context.storage_adapter.update_chunk_embedding.call_count == 2

    def test_skip_embeddings_and_page_texts_location_index(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        """skip_embeddings short-circuits embedding; _page_texts rebuilds the index."""
        from chaoscypher_core.models import ChunksResult

        service = CLISourceProcessingService(mock_cli_context)
        file_id = service.upload_file(sample_text_file)

        # Loader returns a doc carrying _page_texts so the PDF index rebuild runs.
        registry = _mock_loader_registry(
            documents=[
                {
                    "content": "page text",
                    "metadata": {"_page_texts": ["page text"]},
                }
            ]
        )

        chunk_result = ChunksResult(
            small_chunks=[{"id": f"{file_id}:c0", "content": "page text", "token_count": 2}],
            hierarchical_groups=[],
            total_small_chunks=1,
            total_groups=0,
            total_original_chunks=1,
            total_original_groups=0,
        )
        chunking_instance = MagicMock()
        chunking_instance.create_chunks = AsyncMock(return_value=chunk_result)
        chunking_instance.store_chunks = MagicMock()

        with (
            patch(
                "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
                return_value=registry,
            ),
            patch(
                "chaoscypher_core.utils.chunk.ChunkingService",
                return_value=chunking_instance,
            ),
        ):
            result = service.index_file(
                file_id,
                skip_embeddings=True,
                enable_normalization=False,
                enable_vision=False,
            )

        assert result["chunks_count"] == 1
        assert result["embedding_model"] == "none"
        # skip_embeddings -> the chunk-embedding adapter is never touched.
        mock_cli_context.storage_adapter.update_chunk_embedding.assert_not_called()


class TestLoadDocumentText:
    """_load_document_text joins loader output / raises on empty."""

    def test_joins_document_contents(self, mock_cli_context: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("ignored")
        registry = _mock_loader_registry(documents=[{"content": "one"}, {"content": "two"}])
        service = CLISourceProcessingService(mock_cli_context)
        with patch(
            "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
            return_value=registry,
        ):
            assert service._load_document_text(f) == "one\n\ntwo"

    def test_empty_documents_raises(self, mock_cli_context: MagicMock, tmp_path: Path) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("ignored")
        registry = _mock_loader_registry(documents=[])
        service = CLISourceProcessingService(mock_cli_context)
        with patch(
            "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
            return_value=registry,
        ):
            with pytest.raises(ValueError, match="No content extracted"):
                service._load_document_text(f)


# ===========================================================================
# _generate_embeddings
# ===========================================================================


class TestGenerateEmbeddings:
    """_generate_embeddings batching + persistence + failure accounting."""

    def _ctx_with_embedding(self, mock_cli_context: MagicMock, *, fail: bool = False) -> MagicMock:
        from chaoscypher_core.models import BatchEmbedResult

        embedding_service = MagicMock()
        embedding_service.model_name = "fake-embed"

        if fail:
            embedding_service.batch_embed = AsyncMock(side_effect=RuntimeError("embed down"))
        else:

            async def _batch_embed(texts: list[str], batch_size: int = 50) -> BatchEmbedResult:
                return BatchEmbedResult(
                    embeddings=[[0.1, 0.2, 0.3, 0.4] for _ in texts],
                    total=len(texts),
                    failed=0,
                    provider="mock",
                )

            embedding_service.batch_embed = _batch_embed

        mock_cli_context.embedding_service = embedding_service
        mock_cli_context.settings.batching.embedding_api_batch_size = 2
        return mock_cli_context

    def test_embeddings_persisted_and_dimensions_returned(
        self, mock_cli_context: MagicMock
    ) -> None:
        ctx = self._ctx_with_embedding(mock_cli_context)
        update_spy = MagicMock()
        ctx.storage_adapter.update_chunk_embedding = update_spy

        service = CLISourceProcessingService(ctx)
        chunks = [{"id": f"c{i}", "content": f"text {i}"} for i in range(3)]
        try:
            model, dims, failed = service._generate_embeddings("file-1", chunks)
        finally:
            service.close()

        assert model == "fake-embed"
        assert dims == 4
        assert failed == 0
        # One persistence call per chunk.
        assert update_spy.call_count == 3
        # Each call writes model_name + "embedded" status.
        first_call = update_spy.call_args_list[0]
        assert first_call.args[2] == "fake-embed"
        assert first_call.args[4] == "embedded"

    def test_batch_failure_counts_failed_chunks(self, mock_cli_context: MagicMock) -> None:
        ctx = self._ctx_with_embedding(mock_cli_context, fail=True)
        update_spy = MagicMock()
        ctx.storage_adapter.update_chunk_embedding = update_spy

        service = CLISourceProcessingService(ctx)
        chunks = [{"id": f"c{i}", "content": f"t{i}"} for i in range(3)]
        try:
            model, dims, failed = service._generate_embeddings("file-1", chunks)
        finally:
            service.close()

        # 3 chunks, batch_size 2 -> batches of [2, 1]; both fail.
        assert failed == 3
        assert dims == 0
        update_spy.assert_not_called()


# ===========================================================================
# _apply_vision_processing
# ===========================================================================


class TestApplyVisionProcessing:
    """Early-return guards plus the standalone-image happy path."""

    def test_returns_unchanged_when_no_chat_provider(self, mock_cli_context: MagicMock) -> None:
        mock_cli_context.settings.llm = MagicMock()
        mock_cli_context.settings.llm.chat_provider = None
        service = CLISourceProcessingService(mock_cli_context)
        docs = [{"content": "x", "metadata": {}}]
        assert service._apply_vision_processing(docs, "fid", "f.pdf") is docs

    def test_returns_unchanged_when_no_vision_model(self, mock_cli_context: MagicMock) -> None:
        llm = MagicMock()
        llm.chat_provider = "openai"
        llm.openai_vision_model = None
        mock_cli_context.settings.llm = llm
        service = CLISourceProcessingService(mock_cli_context)
        docs = [{"content": "x", "metadata": {}}]
        assert service._apply_vision_processing(docs, "fid", "f.pdf") is docs

    def test_returns_unchanged_when_no_image_pages(self, mock_cli_context: MagicMock) -> None:
        llm = MagicMock()
        llm.chat_provider = "openai"
        llm.openai_vision_model = "gpt-4o"
        mock_cli_context.settings.llm = llm
        service = CLISourceProcessingService(mock_cli_context)
        # No pages with has_images and no vision_pending metadata.
        docs = [{"content": "x", "metadata": {"pages": [{"page_number": 1}]}}]
        assert service._apply_vision_processing(docs, "fid", "f.pdf") is docs

    def test_standalone_image_described_and_merged(
        self, mock_cli_context: MagicMock, tmp_path: Path
    ) -> None:
        llm = MagicMock()
        llm.chat_provider = "openai"
        llm.openai_vision_model = "gpt-4o"
        mock_cli_context.settings.llm = llm
        mock_cli_context.settings.paths.data_dir = str(tmp_path / "data")

        image_path = tmp_path / "pic.png"
        image_path.write_bytes(b"\x89PNG fake bytes")

        docs = [
            {
                "content": "placeholder",
                "metadata": {
                    "extraction_method": "vision_pending",
                    "image_path": str(image_path),
                },
            }
        ]

        vision_result = MagicMock()
        vision_result.description = "A friendly robot."
        vision_service = MagicMock()
        vision_service.describe_image = AsyncMock(return_value=vision_result)

        with (
            patch(
                "chaoscypher_core.services.vision.create_vision_provider",
                return_value=MagicMock(),
            ),
            patch(
                "chaoscypher_core.services.vision.VisionService",
                return_value=vision_service,
            ),
        ):
            service = CLISourceProcessingService(mock_cli_context)
            try:
                out = service._apply_vision_processing(docs, "fid", str(image_path))
            finally:
                service.close()

        assert out[0]["content"] == "A friendly robot."
        assert out[0]["metadata"]["extraction_method"] == "vision"
        vision_service.describe_image.assert_awaited_once()

    def test_pdf_page_rendered_and_description_merged(
        self, mock_cli_context: MagicMock, tmp_path: Path
    ) -> None:
        """PDF page with images is rendered (fake pypdfium2) and merged into _page_texts."""
        import sys

        llm = MagicMock()
        llm.chat_provider = "openai"
        llm.openai_vision_model = "gpt-4o"
        mock_cli_context.settings.llm = llm
        mock_cli_context.settings.paths.data_dir = str(tmp_path / "data")

        docs = [
            {
                "content": "page one text",
                "metadata": {
                    "pages": [{"page_number": 1, "has_images": True}],
                    "_page_texts": ["page one text"],
                },
            }
        ]

        # Fake pypdfium2 module so the render branch runs without a real PDF.
        fake_pil = MagicMock()
        fake_pil.save = MagicMock(
            side_effect=lambda p: __import__("pathlib").Path(p).write_bytes(b"PNG")
        )
        fake_bitmap = MagicMock()
        fake_bitmap.to_pil.return_value = fake_pil
        fake_page = MagicMock()
        fake_page.render.return_value = fake_bitmap
        fake_pdf_doc = MagicMock()
        fake_pdf_doc.__getitem__ = MagicMock(return_value=fake_page)
        fake_pdfium = MagicMock()
        fake_pdfium.PdfDocument.return_value = fake_pdf_doc

        vision_result = MagicMock()
        vision_result.description = "A chart of revenue."
        vision_service = MagicMock()
        vision_service.describe_image = AsyncMock(return_value=vision_result)

        with (
            patch.dict(sys.modules, {"pypdfium2": fake_pdfium}),
            patch(
                "chaoscypher_core.services.vision.create_vision_provider",
                return_value=MagicMock(),
            ),
            patch(
                "chaoscypher_core.services.vision.VisionService",
                return_value=vision_service,
            ),
        ):
            service = CLISourceProcessingService(mock_cli_context)
            try:
                out = service._apply_vision_processing(docs, "fid", "doc.pdf")
            finally:
                service.close()

        fake_pdfium.PdfDocument.assert_called_once_with("doc.pdf")
        # Description merged into the page text + recombined content.
        assert "[Visual Content]" in out[0]["content"]
        assert "A chart of revenue." in out[0]["content"]

    def test_pdf_render_failure_leaves_docs_unchanged(
        self, mock_cli_context: MagicMock, tmp_path: Path
    ) -> None:
        """When page render raises, the batch is empty and docs are returned as-is."""
        import sys

        llm = MagicMock()
        llm.chat_provider = "openai"
        llm.openai_vision_model = "gpt-4o"
        mock_cli_context.settings.llm = llm
        mock_cli_context.settings.paths.data_dir = str(tmp_path / "data")

        docs = [
            {
                "content": "page one",
                "metadata": {"pages": [{"page_number": 1, "has_images": True}]},
            }
        ]

        fake_pdfium = MagicMock()
        fake_pdfium.PdfDocument.side_effect = RuntimeError("cannot open pdf")

        # describe_image must never be called since the batch stays empty.
        vision_service = MagicMock()
        vision_service.describe_image = AsyncMock()

        with (
            patch.dict(sys.modules, {"pypdfium2": fake_pdfium}),
            patch(
                "chaoscypher_core.services.vision.create_vision_provider",
                return_value=MagicMock(),
            ),
            patch(
                "chaoscypher_core.services.vision.VisionService",
                return_value=vision_service,
            ),
        ):
            service = CLISourceProcessingService(mock_cli_context)
            try:
                out = service._apply_vision_processing(docs, "fid", "doc.pdf")
            finally:
                service.close()

        assert out is docs
        vision_service.describe_image.assert_not_awaited()

    def test_failed_description_is_skipped(
        self, mock_cli_context: MagicMock, tmp_path: Path
    ) -> None:
        """A None description from vision is logged and skipped, not merged."""
        llm = MagicMock()
        llm.chat_provider = "openai"
        llm.openai_vision_model = "gpt-4o"
        mock_cli_context.settings.llm = llm
        mock_cli_context.settings.paths.data_dir = str(tmp_path / "data")

        image_path = tmp_path / "pic.png"
        image_path.write_bytes(b"PNG")
        docs = [
            {
                "content": "placeholder",
                "metadata": {
                    "extraction_method": "vision_pending",
                    "image_path": str(image_path),
                },
            }
        ]

        vision_result = MagicMock()
        vision_result.description = None
        vision_service = MagicMock()
        vision_service.describe_image = AsyncMock(return_value=vision_result)

        with (
            patch(
                "chaoscypher_core.services.vision.create_vision_provider",
                return_value=MagicMock(),
            ),
            patch(
                "chaoscypher_core.services.vision.VisionService",
                return_value=vision_service,
            ),
        ):
            service = CLISourceProcessingService(mock_cli_context)
            try:
                out = service._apply_vision_processing(docs, "fid", str(image_path))
            finally:
                service.close()

        # Description was None -> content unchanged, no "vision" method stamp.
        assert out[0]["content"] == "placeholder"
        assert out[0]["metadata"]["extraction_method"] == "vision_pending"


# ===========================================================================
# detect_domain_for_source
# ===========================================================================


class TestDetectDomainForSource:
    """Pre-extraction domain detection without status mutation."""

    def test_missing_source_returns_none(self, mock_cli_context: MagicMock) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        assert service.detect_domain_for_source("nope") is None

    def test_no_chunks_returns_none(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        file_id = service.upload_file(sample_text_file)
        mock_cli_context.storage_adapter.get_chunks_for_extraction = MagicMock(return_value=[])
        assert service.detect_domain_for_source(file_id) is None

    def test_happy_path_returns_recommendation(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        file_id = service.upload_file(sample_text_file)
        mock_cli_context.storage_adapter.get_chunks_for_extraction = MagicMock(
            return_value=[{"content": "some text"}]
        )

        domain_result = {
            "detected_domain": "technical",
            "confidence": 0.91,
            "ranking": [{"domain": "technical", "score": 0.91}],
            "low_confidence": False,
            "domain": MagicMock(),
        }
        with (
            patch(
                "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
                return_value=MagicMock(),
            ),
            patch(
                "chaoscypher_core.services.sources.engine.extraction.domains.create_domain_sample_text",
                return_value="sample",
            ),
            patch(
                "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
                return_value=domain_result,
            ),
        ):
            out = service.detect_domain_for_source(file_id)

        assert out == {
            "detected_domain": "technical",
            "confidence": 0.91,
            "ranking": [{"domain": "technical", "score": 0.91}],
            "low_confidence": False,
        }


# ===========================================================================
# extract_entities (orchestration mocked at the boundary)
# ===========================================================================


def _domain_obj() -> MagicMock:
    """Build a domain object exposing every accessor extract_entities probes."""
    domain = MagicMock()
    domain.get_edge_type_constraints.return_value = {}
    domain.get_extraction_limits.return_value = {}
    domain.get_filtering_mode.return_value = None
    domain.get_entity_exclusions.return_value = []
    domain.get_strict_entity_types.return_value = False
    domain.get_templates.return_value = {"node_templates": [{"name": "Person"}]}
    return domain


def _patch_extract_orchestration(domain_obj: MagicMock) -> list[Any]:
    """Return context managers patching every Core symbol extract_entities imports."""
    domain_result = {
        "detected_domain": "technical",
        "domain": domain_obj,
        "entity_guidance": "g",
        "relationship_guidance": "rg",
    }
    template_result = {
        "node_templates": "NT",
        "edge_templates": "ET",
        "entity_examples": None,
        "relationship_examples": None,
    }
    base = "chaoscypher_core.services.sources.engine.extraction"
    return [
        patch(f"{base}.domains.get_domain_registry", return_value=MagicMock()),
        patch(f"{base}.domains.create_domain_sample_text", return_value="sample"),
        patch(
            f"{base}.orchestration.detect_extraction_domain",
            return_value=domain_result,
        ),
        patch(f"{base}.orchestration.resolve_content_exclusions", return_value=[]),
        patch(
            f"{base}.orchestration.build_extraction_groups",
            return_value=[{"combined_content": "g0"}],
        ),
        patch(
            f"{base}.orchestration.format_extraction_templates",
            return_value=template_result,
        ),
        patch(
            f"{base}.orchestration.apply_depth_strategy",
            return_value=[{"combined_content": "g0"}],
        ),
        patch(f"{base}.orchestration.cache_quality_scores"),
        patch(
            f"{base}.utils.filtering_config.resolve_filtering_config",
            return_value=MagicMock(),
        ),
    ]


def _llm_ctx(mock_cli_context_with_llm: MagicMock) -> MagicMock:
    """Wire the settings + adapter surface extract_entities needs."""
    ctx = mock_cli_context_with_llm
    ctx.settings.llm.chat_provider = "openai"
    ctx.settings.llm.openai_extraction_model = "gpt-extract"
    ctx.settings.llm.openai_chat_model = "gpt-chat"
    ctx.settings.llm.extraction_examples_enabled = False
    ctx.settings.llm.extraction_examples_max_chars = 1000
    ctx.settings.extraction = MagicMock()
    ctx.settings.extraction.extraction_filtering_mode = "balanced"
    ctx.settings.analysis = MagicMock()
    ctx.settings.analysis.quick_sample_size = 5

    adapter = ctx.storage_adapter
    adapter.get_chunks_for_extraction = MagicMock(return_value=[{"content": "c"}])
    adapter.create_llm_call_metrics_batch = MagicMock()
    adapter.compute_llm_summary = MagicMock(return_value={"llm_total_calls": 1})
    adapter.update_source_columns = MagicMock()
    return ctx


class TestExtractEntities:
    """extract_entities happy path, no-chunks short-circuit, and failure handler."""

    def test_file_not_found_raises(self, mock_cli_context_with_llm: MagicMock) -> None:
        ctx = _llm_ctx(mock_cli_context_with_llm)
        service = CLISourceProcessingService(ctx)
        with pytest.raises(ValueError, match="File not found"):
            service.extract_entities("ghost")

    def test_wrong_status_raises(
        self, mock_cli_context_with_llm: MagicMock, sample_text_file: Path
    ) -> None:
        ctx = _llm_ctx(mock_cli_context_with_llm)
        service = CLISourceProcessingService(ctx)
        file_id = service.upload_file(sample_text_file)
        # "uploaded" is not indexed/extracted/failed.
        with pytest.raises(ValueError, match="Cannot extract"):
            service.extract_entities(file_id)

    def test_domain_callback_and_content_exclusions_run(
        self, mock_cli_context_with_llm: MagicMock, sample_text_file: Path
    ) -> None:
        ctx = _llm_ctx(mock_cli_context_with_llm)
        service = CLISourceProcessingService(ctx)
        file_id = service.upload_file(sample_text_file)
        ctx.storage_adapter._files[file_id]["status"] = "indexed"

        finalized = {"entities": [], "relationships": [], "metadata": {}}
        seen_domains: list[str] = []

        domain = _domain_obj()
        patches = _patch_extract_orchestration(domain)
        # Make content-exclusion path active: resolve_content_exclusions
        # returns matchers, then filter_and_strip_chunks returns stats.
        base = "chaoscypher_core.services.sources.engine.extraction"
        filter_stats = MagicMock()
        filter_stats.excluded_chunks = 1
        filter_stats.categories_matched = ["junk"]
        extra = [
            patch(
                f"{base}.orchestration.resolve_content_exclusions",
                return_value=[MagicMock()],
            ),
            patch(
                f"{base}.orchestration.filter_and_strip_chunks",
                return_value=([{"content": "kept"}], filter_stats),
            ),
        ]
        try:
            for p in patches:
                p.start()
            for p in extra:
                p.start()
            with patch.object(
                service,
                "_extract_and_finalize",
                new=AsyncMock(return_value=finalized),
            ):
                with patch(
                    "chaoscypher_core.analytics.llm_metrics.LLMMetricsCollector"
                ) as collector_cls:
                    collector = MagicMock()
                    collector.attempts = []
                    collector.get_summary.return_value = {
                        "total_calls": 0,
                        "retry_calls": 0,
                        "estimated_cost_usd": 0.0,
                    }
                    collector_cls.return_value = collector

                    service.extract_entities(
                        file_id,
                        domain_callback=seen_domains.append,
                    )
        finally:
            for p in extra:
                p.stop()
            for p in patches:
                p.stop()
            service.close()

        assert seen_domains == ["technical"]
        # No attempts collected -> metrics batch NOT written, but summary still
        # computed + aggregated onto the row.
        ctx.storage_adapter.create_llm_call_metrics_batch.assert_not_called()
        ctx.storage_adapter.update_source_columns.assert_called_once()

    def test_no_chunks_returns_empty_tuples(
        self, mock_cli_context_with_llm: MagicMock, sample_text_file: Path
    ) -> None:
        ctx = _llm_ctx(mock_cli_context_with_llm)
        service = CLISourceProcessingService(ctx)
        file_id = service.upload_file(sample_text_file)
        ctx.storage_adapter._files[file_id]["status"] = "indexed"
        ctx.storage_adapter.get_chunks_for_extraction = MagicMock(return_value=[])

        domain = _domain_obj()
        patches = _patch_extract_orchestration(domain)
        try:
            for p in patches:
                p.start()
            results, summary = service.extract_entities(file_id)
        finally:
            for p in patches:
                p.stop()
            service.close()

        assert results == {}
        assert summary == {}

    def test_happy_path_persists_summary_and_completes(
        self, mock_cli_context_with_llm: MagicMock, sample_text_file: Path
    ) -> None:
        ctx = _llm_ctx(mock_cli_context_with_llm)
        service = CLISourceProcessingService(ctx)
        file_id = service.upload_file(sample_text_file)
        ctx.storage_adapter._files[file_id]["status"] = "indexed"
        ctx.storage_adapter._files[file_id]["chunk_count"] = 1

        finalized = {
            "entities": [{"name": "A"}, {"name": "B"}],
            "relationships": [{"src": "A", "dst": "B"}],
            "metadata": {"filtering_log": {"dropped": 0}},
        }

        domain = _domain_obj()
        patches = _patch_extract_orchestration(domain)
        try:
            for p in patches:
                p.start()
            # Replace the heavy extract+finalize coroutine wholesale.
            with patch.object(
                service,
                "_extract_and_finalize",
                new=AsyncMock(return_value=finalized),
            ):
                # Seed a collected attempt so the metrics-persist branch runs.
                with patch(
                    "chaoscypher_core.analytics.llm_metrics.LLMMetricsCollector"
                ) as collector_cls:
                    collector = MagicMock()
                    collector.attempts = [{"call": 1}]
                    collector.get_all_attempts.return_value = [{"call": 1}]
                    collector.get_summary.return_value = {
                        "total_calls": 1,
                        "retry_calls": 0,
                        "estimated_cost_usd": 0.001,
                    }
                    collector_cls.return_value = collector

                    results, summary = service.extract_entities(file_id)
        finally:
            for p in patches:
                p.stop()
            service.close()

        assert results is finalized
        assert summary["total_calls"] == 1
        # LLM metrics persisted + aggregated onto the source row.
        ctx.storage_adapter.create_llm_call_metrics_batch.assert_called_once()
        ctx.storage_adapter.update_source_columns.assert_called_once()
        # Extraction marked complete with the filtering log forwarded.
        ctx.storage_adapter.complete_extraction.assert_called_once()
        complete_kwargs = ctx.storage_adapter.complete_extraction.call_args.kwargs
        assert complete_kwargs["cross_chunk_filtering_log"] == {"dropped": 0}
        assert len(complete_kwargs["entities"]) == 2

    def test_failure_persists_metrics_and_fails_source(
        self, mock_cli_context_with_llm: MagicMock, sample_text_file: Path
    ) -> None:
        ctx = _llm_ctx(mock_cli_context_with_llm)
        service = CLISourceProcessingService(ctx)
        file_id = service.upload_file(sample_text_file)
        ctx.storage_adapter._files[file_id]["status"] = "indexed"
        # Make the nested metrics-persist also blow up so the inner
        # except-persist_error branch (lines 1313-1314) is exercised; the
        # original RuntimeError must still propagate and fail the source.
        ctx.storage_adapter.compute_llm_summary = MagicMock(
            side_effect=RuntimeError("summary persist failed")
        )

        domain = _domain_obj()
        patches = _patch_extract_orchestration(domain)
        try:
            for p in patches:
                p.start()
            with patch.object(
                service,
                "_extract_and_finalize",
                new=AsyncMock(side_effect=RuntimeError("extract blew up")),
            ):
                with patch(
                    "chaoscypher_core.analytics.llm_metrics.LLMMetricsCollector"
                ) as collector_cls:
                    collector = MagicMock()
                    collector.attempts = [{"call": 1}]
                    collector.get_all_attempts.return_value = [{"call": 1}]
                    collector_cls.return_value = collector

                    with pytest.raises(RuntimeError, match="extract blew up"):
                        service.extract_entities(file_id)
        finally:
            for p in patches:
                p.stop()
            service.close()

        # Failure handler still flushed metrics and marked the source failed,
        # even though the nested summary-persist raised.
        ctx.storage_adapter.create_llm_call_metrics_batch.assert_called_once()
        ctx.storage_adapter.fail_extraction.assert_called_once()
        assert ctx.storage_adapter.fail_extraction.call_args.args[0] == file_id


# ===========================================================================
# _extract_and_finalize (direct)
# ===========================================================================


class TestExtractAndFinalize:
    """Drive the async extract+finalize helper directly with fakes."""

    def test_extracts_aggregates_and_finalizes(self, mock_cli_context_with_llm: MagicMock) -> None:
        ctx = mock_cli_context_with_llm
        adapter = ctx.storage_adapter
        adapter.update_step_progress = MagicMock()

        # Fake extractor returns one entity + one relationship per group.
        extractor = MagicMock()
        extractor.extract_single_chunk = AsyncMock(
            return_value=(
                [{"name": "E1"}],
                [{"name": "R1"}],
                10,  # in tokens
                5,  # out tokens
                {},  # metrics
            )
        )

        finalized: dict[str, Any] = {
            "entities": [{"name": "E1"}],
            "relationships": [{"name": "R1"}],
        }
        extraction_service = MagicMock()
        extraction_service.finalize_distributed_extraction = AsyncMock(return_value=finalized)

        spend_tracker = MagicMock()
        spend_tracker.check_and_raise = MagicMock()
        spend_tracker.record = MagicMock()

        collector = MagicMock()
        collector.attempts = [{"call": 1}]

        progress_calls: list[tuple[int, int]] = []

        base = "chaoscypher_core.services.sources.engine.extraction"
        with (
            patch(f"{base}.utils.ai_entities.AIEntityExtractor", return_value=extractor),
            patch(f"{base}.service.ExtractionService", return_value=extraction_service),
            patch(
                "chaoscypher_core.services.llm.spend.get_llm_spend_tracker",
                return_value=spend_tracker,
            ),
        ):
            service = CLISourceProcessingService(ctx)
            try:
                result = service._run_async(
                    service._extract_and_finalize(
                        groups_to_process=[
                            {"combined_content": "group zero"},
                            {"combined_content": "group one"},
                        ],
                        file_id="fid",
                        node_templates="NT",
                        edge_templates="ET",
                        entity_guidance="g",
                        relationship_guidance="rg",
                        entity_examples=None,
                        relationship_examples=None,
                        entity_exclusions=None,
                        domain_extraction_limits=None,
                        filtering_mode="balanced",
                        metrics_collector=collector,
                        file_record={"id": "fid"},
                        detected_domain_name="technical",
                        forced_domain=None,
                        total_groups=2,
                        depth="full",
                        progress_callback=lambda cur, tot: progress_calls.append((cur, tot)),
                    )
                )
            finally:
                service.close()

        # Two groups extracted -> the real aggregate_chunk_results collected
        # both groups' entities (one per group). Relationships referencing
        # entities by index are dropped by aggregation (our fakes carry no
        # source/target idx) — that is real Core behaviour, not a bug here.
        assert extractor.extract_single_chunk.await_count == 2
        finalize_kwargs = extraction_service.finalize_distributed_extraction.call_args.kwargs
        assert len(finalize_kwargs["raw_entities"]) == 2
        # Spend recorded once per successful group; cap checked once per group.
        assert spend_tracker.record.call_count == 2
        assert spend_tracker.check_and_raise.call_count == 2
        # Progress + stats wired through.
        assert progress_calls == [(1, 2), (2, 2)]
        assert result["stats"]["groups_processed"] == 2
        assert result["stats"]["groups_total"] == 2
        assert result["stats"]["extraction_depth"] == "full"

    def test_group_failure_is_swallowed_per_group(
        self, mock_cli_context_with_llm: MagicMock
    ) -> None:
        ctx = mock_cli_context_with_llm
        ctx.storage_adapter.update_step_progress = MagicMock()

        extractor = MagicMock()
        extractor.extract_single_chunk = AsyncMock(side_effect=RuntimeError("group fail"))

        extraction_service = MagicMock()
        extraction_service.finalize_distributed_extraction = AsyncMock(
            return_value={"entities": [], "relationships": []}
        )

        spend_tracker = MagicMock()
        collector = MagicMock()
        collector.attempts = []

        base = "chaoscypher_core.services.sources.engine.extraction"
        with (
            patch(f"{base}.utils.ai_entities.AIEntityExtractor", return_value=extractor),
            patch(f"{base}.service.ExtractionService", return_value=extraction_service),
            patch(
                "chaoscypher_core.services.llm.spend.get_llm_spend_tracker",
                return_value=spend_tracker,
            ),
        ):
            service = CLISourceProcessingService(ctx)
            try:
                result = service._run_async(
                    service._extract_and_finalize(
                        groups_to_process=[{"combined_content": "g0"}],
                        file_id="fid",
                        node_templates="NT",
                        edge_templates="ET",
                        entity_guidance=None,
                        relationship_guidance=None,
                        entity_examples=None,
                        relationship_examples=None,
                        entity_exclusions=None,
                        domain_extraction_limits=None,
                        filtering_mode="balanced",
                        metrics_collector=collector,
                        file_record={"id": "fid"},
                        detected_domain_name="technical",
                        forced_domain=None,
                        total_groups=1,
                        depth="full",
                    )
                )
            finally:
                service.close()

        # Group failed -> no entities aggregated, but finalize still ran.
        finalize_kwargs = extraction_service.finalize_distributed_extraction.call_args.kwargs
        assert finalize_kwargs["raw_entities"] == []
        # Failed group means record() never called (spend only on success).
        spend_tracker.record.assert_not_called()
        assert result["stats"]["groups_processed"] == 1


# ===========================================================================
# commit_to_graph
# ===========================================================================


class TestCommitToGraph:
    """Stored-payload branch and the failure handler."""

    def _patch_commit(self, commit_return: dict[str, Any]) -> Any:
        commit_service = MagicMock()
        commit_service.commit = AsyncMock(return_value=commit_return)
        return (
            patch(
                "chaoscypher_core.adapters.sqlite.repos.GraphRepository",
                return_value=MagicMock(),
            ),
            patch(
                "chaoscypher_core.services.sources.engine.commit.service.SourceCommitService",
                return_value=commit_service,
            ),
            commit_service,
        )

    def test_uses_stored_payload_and_returns_counts(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        file_id = service.upload_file(sample_text_file)
        mock_cli_context.storage_adapter._files[file_id]["status"] = "extracted"

        stored_payload = {"entities": [{"name": "A"}], "relationships": []}
        mock_cli_context.storage_adapter.get_source_commit_payload = MagicMock(
            return_value=stored_payload
        )
        list_entities = MagicMock()
        mock_cli_context.storage_adapter.list_source_entities = list_entities
        mock_cli_context.storage_adapter.session = MagicMock()

        p_repo, p_service, commit_service = self._patch_commit(
            {
                "created_nodes": [1, 2, 3],
                "created_edges": [1, 2],
                "created_templates": [1],
            }
        )
        with p_repo, p_service:
            try:
                result = service.commit_to_graph(file_id)
            finally:
                service.close()

        assert result == {
            "nodes_created": 3,
            "edges_created": 2,
            "templates_created": 1,
        }
        # Stored payload short-circuits the per-source table fallback.
        list_entities.assert_not_called()
        commit_kwargs = commit_service.commit.call_args.kwargs
        assert commit_kwargs["commit_data"] is stored_payload

    def test_falls_back_to_source_tables_when_no_payload(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        file_id = service.upload_file(sample_text_file)
        mock_cli_context.storage_adapter._files[file_id]["status"] = "indexed"

        mock_cli_context.storage_adapter.get_source_commit_payload = MagicMock(return_value=None)
        mock_cli_context.storage_adapter.list_source_entities = MagicMock(
            return_value=[{"name": "A"}]
        )
        mock_cli_context.storage_adapter.list_source_relationships = MagicMock(return_value=[])
        mock_cli_context.storage_adapter.session = MagicMock()

        p_repo, p_service, commit_service = self._patch_commit(
            {"created_nodes": [], "created_edges": [], "created_templates": []}
        )
        with p_repo, p_service:
            try:
                result = service.commit_to_graph(file_id)
            finally:
                service.close()

        assert result["nodes_created"] == 0
        commit_kwargs = commit_service.commit.call_args.kwargs
        assert commit_kwargs["commit_data"]["entities"] == [{"name": "A"}]
        assert commit_kwargs["commit_data"]["suggested_templates"] == []

    def test_commit_failure_marks_failed_and_raises(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        file_id = service.upload_file(sample_text_file)
        mock_cli_context.storage_adapter._files[file_id]["status"] = "extracted"
        mock_cli_context.storage_adapter.get_source_commit_payload = MagicMock(
            return_value={"entities": [], "relationships": []}
        )
        mock_cli_context.storage_adapter.session = MagicMock()

        commit_service = MagicMock()
        commit_service.commit = AsyncMock(side_effect=RuntimeError("commit boom"))
        with (
            patch(
                "chaoscypher_core.adapters.sqlite.repos.GraphRepository",
                return_value=MagicMock(),
            ),
            patch(
                "chaoscypher_core.services.sources.engine.commit.service.SourceCommitService",
                return_value=commit_service,
            ),
        ):
            try:
                with pytest.raises(RuntimeError, match="commit boom"):
                    service.commit_to_graph(file_id)
            finally:
                service.close()

        mock_cli_context.storage_adapter.fail_commit.assert_called_once()
        assert mock_cli_context.storage_adapter.fail_commit.call_args.args[0] == file_id

    def test_missing_source_raises(self, mock_cli_context: MagicMock) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        with pytest.raises(ValueError, match="File not found"):
            service.commit_to_graph("nope")

    def test_wrong_status_raises(self, mock_cli_context: MagicMock, sample_text_file: Path) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        file_id = service.upload_file(sample_text_file)  # status "uploaded"
        with pytest.raises(ValueError, match="Cannot commit"):
            service.commit_to_graph(file_id)


# ===========================================================================
# get_file_status
# ===========================================================================


class TestGetFileStatus:
    """get_file_status passes through to the adapter."""

    def test_returns_record_for_known_file(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        file_id = service.upload_file(sample_text_file)
        record = service.get_file_status(file_id)
        assert record is not None
        assert record["id"] == file_id

    def test_returns_none_for_unknown_file(self, mock_cli_context: MagicMock) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        assert service.get_file_status("ghost") is None


# ===========================================================================
# reset_for_re_extraction (happy path)
# ===========================================================================


class TestResetForReExtraction:
    """Happy-path reset delegates to force_re_extract with adapter wiring."""

    def test_reset_returns_artifact_counts(
        self, mock_cli_context: MagicMock, sample_text_file: Path
    ) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        file_id = service.upload_file(sample_text_file)
        mock_cli_context.storage_adapter._files[file_id]["status"] = "committed"

        # transaction() context manager + a connected session.
        mock_cli_context.storage_adapter.session = MagicMock()
        mock_cli_context.storage_adapter.transaction = MagicMock()
        mock_cli_context.storage_adapter.transaction.return_value.__enter__ = MagicMock()
        mock_cli_context.storage_adapter.transaction.return_value.__exit__ = MagicMock(
            return_value=False
        )
        mock_cli_context.storage_adapter.reset_for_re_extraction = MagicMock()
        mock_cli_context.graph_repository.delete_source_artifacts = MagicMock(
            return_value={"nodes_deleted": 7, "edges_deleted": 3, "templates_deleted": 1}
        )

        removed = service.reset_for_re_extraction(file_id)

        assert removed == {"nodes_deleted": 7, "edges_deleted": 3, "templates_deleted": 1}
        mock_cli_context.graph_repository.delete_source_artifacts.assert_called_once_with(
            file_id, session=mock_cli_context.storage_adapter.session
        )
        mock_cli_context.storage_adapter.reset_for_re_extraction.assert_called_once_with(
            source_id=file_id, database_name=mock_cli_context.database_name
        )

    def test_reset_missing_source_raises(self, mock_cli_context: MagicMock) -> None:
        service = CLISourceProcessingService(mock_cli_context)
        with pytest.raises(ValueError, match="Source not found"):
            service.reset_for_re_extraction("ghost")
