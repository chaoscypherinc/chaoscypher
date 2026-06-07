# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CLI Source Processing Service - Orchestrates document processing pipeline.

Provides a synchronous interface for document processing that works
offline without requiring Cortex or Neuron backends.

Uses Core library services for all processing stages:
1. Upload: Stage file and create metadata record (SqliteAdapter)
2. Index: ChunkingService for text normalization + hierarchical chunking
3. Extract: DomainRegistry + AIEntityExtractor + ExtractionService finalization
4. Commit: SourceCommitService for full graph commit with citations

LLM Metrics:
Tracks per-call LLM metrics during extraction for retry/failure analysis.
Uses LLMMetricsCollector from core for framework-agnostic collection.
Metrics are persisted to SQLite and aggregated on source files.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

from chaoscypher_core.models import SourceStatus
from chaoscypher_core.services.sources.management.re_extraction import force_re_extract
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_cli.context import CLIContext
    from chaoscypher_core.ports.types import FilteringMode
    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        ExclusionRule,
    )


logger = structlog.get_logger(__name__)


class CLISourceProcessingService:
    """Service for CLI document source processing operations.

    Provides synchronous methods for the full source processing pipeline,
    delegating to Core library services for all processing stages:
    - upload_file: Stage file and create metadata (SqliteAdapter)
    - index_file: ChunkingService for hierarchical chunking + embeddings
    - extract_entities: DomainRegistry + AIEntityExtractor + finalization
    - commit_to_graph: SourceCommitService for full graph commit

    Example:
        ctx = get_context()
        service = CLISourceProcessingService(ctx)

        # Upload and index
        file_id = service.upload_file(Path("doc.pdf"))
        service.index_file(file_id)

        # Extract (requires LLM)
        if service.has_llm:
            service.extract_entities(file_id)

        # Commit to graph
        service.commit_to_graph(file_id)
    """

    def __init__(self, ctx: CLIContext):
        """Initialize source processing service.

        Args:
            ctx: CLI context with adapters and services
        """
        self.ctx = ctx
        self._loop: asyncio.AbstractEventLoop | None = None

    def __enter__(self) -> CLISourceProcessingService:
        """Enter context manager."""
        return self

    def __exit__(self, *_: Any) -> None:
        """Exit context manager and close event loop."""
        self.close()

    @property
    def has_llm(self) -> bool:
        """Check if LLM provider is available via context."""
        return self.ctx.has_llm

    @property
    def llm_provider(self) -> Any:
        """Get the LLM provider from context.

        Returns:
            LLM provider instance or None if not available
        """
        return self.ctx.llm_provider

    def _generate_file_id(self) -> str:
        """Generate a unique file ID."""
        return generate_id()

    def _run_async(self, coro: Any) -> Any:
        """Run an async coroutine synchronously.

        Uses a persistent event loop so httpx connection pools and cached
        async clients (Ollama, OpenAI) survive across pipeline stages.
        On KeyboardInterrupt, cancels all running tasks before re-raising.

        Args:
            coro: Async coroutine to run

        Returns:
            Result of the coroutine
        """
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        try:
            return self._loop.run_until_complete(coro)
        except KeyboardInterrupt:
            # Cancel all pending tasks so they don't leak warnings
            self._cancel_pending_tasks()
            raise

    def _cancel_pending_tasks(self) -> None:
        """Cancel all pending asyncio tasks on the persistent loop."""
        if not self._loop or self._loop.is_closed():
            return
        try:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except RuntimeError:
            pass  # Loop may already be stopping

    def close(self) -> None:
        """Close the persistent event loop and release async resources.

        Cancels all pending tasks first, then closes the loop. Suppresses
        any residual asyncio teardown warnings (e.g. "Task was destroyed
        but it is pending") that are harmless during shutdown.
        """
        if self._loop and not self._loop.is_closed():
            import os
            import warnings

            self._cancel_pending_tasks()

            # Suppress residual asyncio teardown noise on stderr
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                devnull_fd = os.open(os.devnull, os.O_WRONLY)
                old_stderr = os.dup(2)
                try:
                    os.dup2(devnull_fd, 2)
                    self._loop.close()
                finally:
                    os.dup2(old_stderr, 2)
                    os.close(devnull_fd)
                    os.close(old_stderr)
            self._loop = None

    # ========================================================================
    # Stage 1: Upload
    # ========================================================================

    def upload_file(
        self,
        file_path: Path,
        extraction_depth: str = "full",
        domain: str | None = None,
        skip_duplicates: bool = False,
        *,
        # Workstream 1 (2026-05-07): persist user upload settings on the
        # source row so the CLI has parity with /api/v1/sources.
        auto_analyze: bool = True,
        enable_normalization: bool | None = None,
        enable_vision: bool = True,
        content_filtering: bool = True,
        filtering_mode: FilteringMode = "balanced",
    ) -> str | dict[str, Any]:
        """Upload and stage a file for source processing.

        Args:
            file_path: Path to file to ingest
            extraction_depth: Depth of extraction (quick, full)
            domain: Domain for extraction (None or 'auto' = auto-detect)
            skip_duplicates: Skip upload if identical content (by SHA-256) already exists.
                When a duplicate is found, returns a dict with ``skipped_duplicate=True``
                and ``existing_status`` instead of uploading again.
            auto_analyze: Whether the upload flow should auto-queue
                analysis after indexing (CLI orchestrates each stage
                explicitly so this primarily affects re-extract /
                recovery). Persisted on the row.
            enable_normalization: ``None`` (default) defers to the
                file-type default; ``True`` / ``False`` is an explicit
                user override and is persisted on the source row.
            enable_vision: Use vision LLM on images and scanned PDFs.
                Persisted on the row.
            content_filtering: Apply domain content-exclusion rules
                during extraction. Persisted on the row.
            filtering_mode: Filtering preset (default ``"balanced"``).
                Persisted on the row.

        Returns:
            Generated file ID (str), or a dict with ``skipped_duplicate=True`` when
            ``skip_duplicates=True`` and identical content already exists.

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file type not supported
        """
        import hashlib

        if not file_path.exists():
            msg = f"File not found: {file_path}"
            raise FileNotFoundError(msg)

        # Validate file type against Core LoaderRegistry
        from chaoscypher_core.services.sources.loaders.factory import get_loader_registry

        registry = get_loader_registry(self.ctx.settings)
        supported = set(registry.list_supported_extensions())
        if file_path.suffix.lower() not in supported:
            msg = f"Unsupported file type: {file_path.suffix}. Supported: {', '.join(sorted(supported))}"
            raise ValueError(msg)

        # Read file once (needed for hash check and upload)
        file_content = file_path.read_bytes()

        # Always compute SHA-256 so upload_source can persist it regardless of
        # whether skip_duplicates is enabled.  Cost is ~ms even for 100 MB files.
        content_hash = hashlib.sha256(file_content).hexdigest()

        # Duplicate detection: check by SHA-256 hash before staging
        if skip_duplicates:
            existing = self.ctx.storage_adapter.find_by_content_hash(
                self.ctx.database_name, content_hash
            )
            if existing:
                logger.info(
                    "duplicate_source_skipped",
                    filename=file_path.name,
                    existing_id=existing["id"],
                    existing_status=existing.get("status"),
                    content_hash=content_hash,
                )
                result: dict[str, Any] = dict(existing)
                result["skipped_duplicate"] = True
                result["existing_status"] = existing.get("status")
                result["filename"] = file_path.name
                return result

        # Generate ID and determine staging directory
        file_id = self._generate_file_id()

        # Determine staging directory (must match core management service)
        staging_dir = self.ctx.database_dir / "sources"

        # Create file record (domain='auto' or None means auto-detect).
        # W1 (2026-05-07): forward every upload setting so it lands on the
        # source row at upload time (parity with /api/v1/sources).
        forced_domain = None if domain in (None, "auto") else domain
        self.ctx.storage_adapter.upload_source(
            source_id=file_id,
            database_name=self.ctx.database_name,
            filename=file_path.name,
            file_content=file_content,
            staging_dir=str(staging_dir),
            extraction_depth=extraction_depth,
            forced_domain=forced_domain,
            content_hash=content_hash,
            auto_analyze=auto_analyze,
            enable_normalization=enable_normalization,
            enable_vision=enable_vision,
            content_filtering=content_filtering,
            filtering_mode=filtering_mode,
        )

        logger.info(
            "file_uploaded",
            file_id=file_id,
            filename=file_path.name,
            size=len(file_content),
        )

        return file_id

    def upload_url(
        self,
        url: str,
        extraction_depth: str = "full",
        domain: str | None = None,
        skip_duplicates: bool = False,
        *,
        # Workstream 1 (2026-05-07): persist user upload settings on the row.
        auto_analyze: bool = True,
        enable_normalization: bool | None = None,
        enable_vision: bool = True,
        content_filtering: bool = True,
        filtering_mode: FilteringMode = "balanced",
    ) -> tuple[str, str] | tuple[dict[str, Any], str]:
        """Fetch URL content and stage as a markdown source file.

        Uses WebScraper.extract_full_content() to fetch and extract clean
        markdown content, then stages it through the normal upload pipeline
        with URL metadata.

        Args:
            url: URL to fetch and import
            extraction_depth: Depth of extraction (quick, full)
            domain: Domain for extraction (None or 'auto' = auto-detect)
            skip_duplicates: Skip upload if identical content (by SHA-256) already exists.
                When a duplicate is found, returns a tuple of (skip_dict, page_title)
                where skip_dict contains ``skipped_duplicate=True``.
            auto_analyze: Whether the upload flow should auto-queue analysis
                after indexing. Persisted on the source row.
            enable_normalization: ``None`` defers to the file-type default;
                ``True`` / ``False`` is an explicit user override and is
                persisted on the source row.
            enable_vision: Use vision LLM on images and scanned content.
                Persisted on the row.
            content_filtering: Apply domain content-exclusion rules during
                extraction. Persisted on the row.
            filtering_mode: Filtering preset (default ``"balanced"``).
                Persisted on the row.

        Returns:
            Tuple of (file_id, page_title), or (skip_dict, page_title) when a duplicate
            is found and ``skip_duplicates=True``.

        Raises:
            ValueError: If URL is invalid, fetch fails, or content is empty
        """
        import re

        from chaoscypher_core.adapters.web.search import WebScraper

        # Fetch and extract content. The CLI passes the upload allowlist
        # so URL imports follow the same contract as file uploads:
        # mismatched Content-Type raises ValidationError instead of
        # silently producing an empty source.
        cli_batching = getattr(self.ctx.settings, "batching", None)
        cli_allowlist = list(getattr(cli_batching, "upload_content_type_allowlist", []) or [])
        cli_max_bytes_raw = getattr(cli_batching, "max_upload_bytes", None)
        cli_max_bytes: int | None = (
            int(cli_max_bytes_raw) if isinstance(cli_max_bytes_raw, int) else None
        )

        scraper = WebScraper(
            allowlist=cli_allowlist,
            max_bytes=cli_max_bytes,
            web_settings=getattr(self.ctx.settings, "web", None),
        )
        result = self._run_async(scraper.extract_full_content(url, max_bytes=cli_max_bytes))

        if result.error:
            msg = f"Failed to fetch URL: {result.error}"
            raise ValueError(msg)

        if result.is_binary:
            # The CLI ``upload_url`` flow assumes a markdown stage path;
            # binary URLs need to go through ``upload_file`` against the
            # PDF / image loader instead. Surface the misuse loudly.
            msg = (
                f"URL returned binary content ({result.content_type}); "
                "use 'cc source add <url>' which routes binary URLs "
                "through the staging pipeline."
            )
            raise ValueError(msg)

        content = result.content
        if not content or len(content) < 50:
            msg = "Extracted content is too short or empty. The page may require JavaScript."
            raise ValueError(msg)

        # Generate safe filename from page title
        page_title = result.title or "Untitled Page"
        safe_title = re.sub(r"[^\w\s-]", "", page_title).strip()
        safe_title = re.sub(r"[\s]+", "_", safe_title)
        safe_title = safe_title[:100]
        if not safe_title:
            safe_title = "web_import"
        safe_filename = f"{safe_title}.md"

        # Generate ID and stage content
        import hashlib

        file_content = content.encode("utf-8")

        # Always compute SHA-256 so upload_source can persist it regardless of
        # whether skip_duplicates is enabled.  Cost is ~ms even for 100 MB files.
        content_hash = hashlib.sha256(file_content).hexdigest()

        # Duplicate detection: check by SHA-256 hash before staging
        if skip_duplicates:
            existing = self.ctx.storage_adapter.find_by_content_hash(
                self.ctx.database_name, content_hash
            )
            if existing:
                logger.info(
                    "duplicate_source_skipped",
                    url=url,
                    existing_id=existing["id"],
                    existing_status=existing.get("status"),
                    content_hash=content_hash,
                )
                skip_result: dict[str, Any] = dict(existing)
                skip_result["skipped_duplicate"] = True
                skip_result["existing_status"] = existing.get("status")
                skip_result["filename"] = safe_filename
                return skip_result, page_title

        file_id = self._generate_file_id()
        # Match the file-upload path (and core management service) so staged
        # bytes for CLI file and URL imports live under one convention.
        staging_dir = self.ctx.database_dir / "sources"

        forced_domain = None if domain in (None, "auto") else domain
        self.ctx.storage_adapter.upload_source(
            source_id=file_id,
            database_name=self.ctx.database_name,
            filename=safe_filename,
            file_content=file_content,
            staging_dir=str(staging_dir),
            extraction_depth=extraction_depth,
            forced_domain=forced_domain,
            origin_url=url,
            source_type_override="webpage",
            title_override=page_title,
            content_hash=content_hash,
            # W1 (2026-05-07): persist upload settings on the row.
            auto_analyze=auto_analyze,
            enable_normalization=enable_normalization,
            enable_vision=enable_vision,
            content_filtering=content_filtering,
            filtering_mode=filtering_mode,
        )

        logger.info(
            "url_uploaded",
            file_id=file_id,
            url=url,
            title=page_title,
            size=len(file_content),
        )

        return file_id, page_title

    # ========================================================================
    # Stage 2: Index (Chunking + Embeddings) — Core ChunkingService
    # ========================================================================

    def index_file(
        self,
        file_id: str,
        skip_embeddings: bool = False,
        enable_normalization: bool | None = None,
        enable_vision: bool = True,
    ) -> dict[str, Any]:
        """Index a file using Core's ChunkingService.

        Uses ChunkingService for:
        - Text normalization (PDF headers, mid-sentence joins, line wraps)
        - RecursiveCharacterTextSplitter with 8 separator levels
        - Hierarchical grouping (group_size=4, group_overlap=1)
        - Depth-based filtering (quick/full)

        Args:
            file_id: Source file ID
            skip_embeddings: If True, skip embedding generation (faster indexing)
            enable_normalization: Tri-state. ``True`` / ``False`` is an
                explicit user override; ``None`` (default) defers to the
                file-type default via ``resolve_normalization_default``
                (prose → True, structured CSV/JSON/XML → False). Matches
                the resolution Cortex's indexing_handler performs at
                line 301-303 — both paths must produce identical chunks
                for the same input, and unifying the tri-state here is
                the prerequisite. Historic regression (May 2026): the
                CLI pipeline passes ``None`` from its CLI flag default,
                old code accepted only ``bool`` and treated ``None`` as
                falsy, silently skipping normalization for every CLI
                upload of a prose file and leaving CRLF garbage in the
                chunks that reached the LLM.
            enable_vision: If True, apply vision LLM processing to image-heavy pages

        Returns:
            Dict with indexing stats (chunks_count, tokens_count)

        Raises:
            ValueError: If file not found or already indexed
        """
        # Get file record
        file_record = self.ctx.storage_adapter.get_file(file_id, self.ctx.database_name)
        if not file_record:
            msg = f"File not found: {file_id}"
            raise ValueError(msg)

        # Resolve the tri-state default the same way Cortex's
        # indexing_handler does inside its own resolve-defaults block.
        from chaoscypher_core.utils.normalization_default import (
            resolve_normalization_default,
        )

        if enable_normalization is None:
            enable_normalization = resolve_normalization_default(
                filename=file_record.get("filename") or ""
            )

        logger.info(
            "index_file_started",
            file_id=file_id,
            skip_embeddings=skip_embeddings,
            enable_normalization=enable_normalization,
        )

        # Check status
        status = file_record.get("status", "")
        if status not in (SourceStatus.PENDING, "uploaded", "failed"):
            msg = f"Cannot index file with status '{status}' - must be 'pending' or 'uploaded'"
            raise ValueError(msg)

        # Update status to indexing
        self.ctx.storage_adapter.start_indexing(file_id)

        try:
            # Load document
            filepath = Path(file_record["filepath"])
            logger.info("loading_document", file_id=file_id, filepath=str(filepath))

            from chaoscypher_core.services.sources.loaders.factory import get_loader_registry

            registry = get_loader_registry(self.ctx.settings)
            documents = registry.load_document(str(filepath))
            if not documents:
                msg = f"No content extracted from: {filepath.name}"
                raise ValueError(msg)

            # Vision processing (direct LLM calls, no queue)
            if enable_vision:
                documents = self._apply_vision_processing(
                    documents=documents,
                    file_id=file_id,
                    filepath=str(filepath),
                )

            text = "\n\n".join(doc["content"] for doc in documents)
            logger.info("document_loaded", file_id=file_id, text_length=len(text))

            # Optionally normalize content (OCR cleaning, encoding fixes)
            if enable_normalization:
                from chaoscypher_core.services.sources.normalizer.models import ContentType
                from chaoscypher_core.services.sources.normalizer.service import (
                    ContentNormalizerService,
                )

                normalizer = ContentNormalizerService(settings=self.ctx.settings)

                file_ext = filepath.suffix.lstrip(".").lower()
                content_type_map = {
                    "pdf": ContentType.PDF,
                    "html": ContentType.HTML,
                    "htm": ContentType.HTML,
                    "md": ContentType.MARKDOWN,
                    "csv": ContentType.CSV,
                    "json": ContentType.JSON,
                    "txt": ContentType.TEXT,
                }
                content_type = content_type_map.get(file_ext, ContentType.TEXT)

                normalized = normalizer.normalize(
                    content=text,
                    content_type=content_type,
                )
                text = normalized.content
                logger.info(
                    "content_normalized",
                    file_id=file_id,
                    quality_score=round(normalized.quality_metrics.overall_score(), 3),
                )

            # Use Core ChunkingService for text normalization + hierarchical chunking
            from chaoscypher_core.utils.chunk import (
                ChunkingService,
                build_pdf_location_index,
                merge_location_indexes,
            )

            chunking_service = ChunkingService(
                settings=self.ctx.settings,
                repository=self.ctx.storage_adapter,
            )

            # Rebuild location_index for PDFs from current _page_texts. Vision
            # may have augmented _page_texts after the PDF loader returned;
            # the loader's original location_index would then cover only the
            # pre-vision text. Loaders without _page_texts (EPUB/DOCX) keep
            # their loader-emitted index.
            for doc in documents:
                metadata = doc.get("metadata") or {}
                page_texts = metadata.get("_page_texts")
                if page_texts:
                    metadata["location_index"] = build_pdf_location_index(page_texts)

            # Merge per-document location_index entries (page_number for PDF,
            # section for EPUB/DOCX) into a single index over the joined text.
            cli_docs_with_indexes = [
                (doc.get("content", ""), doc.get("metadata", {}).get("location_index"))
                for doc in documents
            ]
            cli_location_index = merge_location_indexes(cli_docs_with_indexes, separator="\n\n")

            depth = file_record.get("extraction_depth", "full")
            chunk_result = self._run_async(
                chunking_service.create_chunks(
                    source_id=file_id,
                    full_text=text,
                    analysis_depth=depth,
                    location_index=cli_location_index or None,
                )
            )

            chunking_service.store_chunks(chunk_result, database_name=self.ctx.database_name)

            small_chunks = chunk_result.small_chunks
            chunks_count = chunk_result.total_small_chunks
            tokens_count = sum(c.get("token_count", 0) for c in small_chunks)

            logger.info(
                "chunking_complete",
                file_id=file_id,
                chunks_count=chunks_count,
                groups_count=chunk_result.total_groups,
            )

            # Generate embeddings unless explicitly skipped
            embedding_model = "none"
            embedding_dimensions = 0
            failed_embeddings = 0

            if skip_embeddings:
                logger.info("embeddings_skipped", file_id=file_id, reason="skip_embeddings flag")
            else:
                logger.info("generating_embeddings", file_id=file_id, chunk_count=chunks_count)
                embedding_model, embedding_dimensions, failed_embeddings = (
                    self._generate_embeddings(file_id, small_chunks)
                )
                logger.info(
                    "embeddings_generated",
                    file_id=file_id,
                    model=embedding_model,
                    dimensions=embedding_dimensions,
                    failed_chunks=failed_embeddings,
                )

            # Mark indexing complete
            self.ctx.storage_adapter.complete_indexing(
                source_id=file_id,
                chunks_count=chunks_count,
                embedding_model=embedding_model,
                embedding_dimensions=embedding_dimensions,
            )

            logger.info(
                "file_indexed",
                file_id=file_id,
                chunks_count=chunks_count,
                tokens_count=tokens_count,
            )

            return {
                "chunks_count": chunks_count,
                "tokens_count": tokens_count,
                "embedding_model": embedding_model,
                "failed_embeddings": failed_embeddings,
            }

        except Exception as e:
            self.ctx.storage_adapter.fail_indexing(file_id, str(e))
            raise

    def _apply_vision_processing(  # noqa: C901, PLR0912
        self,
        documents: list[dict[str, Any]],
        file_id: str,
        filepath: str,
    ) -> list[dict[str, Any]]:
        """Apply vision LLM processing to documents with images.

        Args:
            documents: Loader output with per-page metadata.
            file_id: Source file ID.
            filepath: Path to source file.

        Returns:
            Documents with vision descriptions merged into content.
        """
        # Check if vision model is configured
        provider = getattr(self.ctx.settings.llm, "chat_provider", None)
        if not provider:
            return documents
        vision_model = getattr(self.ctx.settings.llm, f"{provider}_vision_model", None)
        if not vision_model:
            return documents

        # Collect pages/images that need vision
        image_pages: list[dict[str, Any]] = []
        for doc_idx, doc in enumerate(documents):
            metadata = doc.get("metadata", {})
            image_pages.extend(
                {
                    "page_number": page_info["page_number"],
                    "is_standalone": False,
                    "doc_index": doc_idx,
                }
                for page_info in metadata.get("pages", [])
                if page_info.get("has_images")
            )
            if metadata.get("extraction_method") == "vision_pending":
                image_path = metadata.get("image_path")
                if image_path:
                    image_pages.append(
                        {
                            "image_path": image_path,
                            "is_standalone": True,
                            "doc_index": doc_idx,
                        }
                    )

        if not image_pages:
            return documents

        logger.info(
            "vision_processing_started",
            file_id=file_id,
            image_page_count=len(image_pages),
            vision_model=vision_model,
        )

        from chaoscypher_core.services.vision import VisionService, create_vision_provider
        from chaoscypher_core.services.vision.prompts import (
            PDF_PAGE_PROMPT,
            STANDALONE_IMAGE_PROMPT,
        )

        vision_provider = create_vision_provider(self.ctx.settings, vision_model)
        vision_service = VisionService(llm_provider=vision_provider)

        # Render and build batch
        images_dir = (
            Path(self.ctx.settings.paths.data_dir)
            / "databases"
            / self.ctx.database_name
            / "images"
            / file_id
        )
        images_dir.mkdir(parents=True, exist_ok=True)

        batch: list[dict[str, Any]] = []
        for page_info in image_pages:
            if page_info.get("is_standalone"):
                img_path = Path(page_info["image_path"])
                if img_path.exists():
                    batch.append(
                        {
                            "image_bytes": img_path.read_bytes(),
                            "prompt": STANDALONE_IMAGE_PROMPT,
                            "page_info": page_info,
                        }
                    )
            else:
                page_num = page_info["page_number"]
                try:
                    import pypdfium2 as pdfium  # type: ignore[import-untyped]

                    pdf_doc = pdfium.PdfDocument(filepath)
                    page = pdf_doc[page_num - 1]
                    bitmap = page.render(scale=150 / 72)
                    pil_image = bitmap.to_pil()
                    img_filename = f"page_{page_num}.png"
                    img_path = images_dir / img_filename
                    pil_image.save(str(img_path))
                    pdf_doc.close()

                    page_info["image_path"] = str(img_path)
                    batch.append(
                        {
                            "image_bytes": img_path.read_bytes(),
                            "prompt": PDF_PAGE_PROMPT,
                            "page_info": page_info,
                        }
                    )
                except Exception:
                    logger.warning(
                        "page_render_failed", file_id=file_id, page=page_num, exc_info=True
                    )

        if not batch:
            return documents

        # PR 2 (2026-05-13, Task 12): the batched ``describe_images``
        # helper was deleted alongside the indexing-handler rewire. The
        # CLI runs out-of-process from the worker (no QUEUE_LLM) so we
        # fall back to a serial loop of ``describe_image`` calls —
        # mirrors what the per-page handler does on the queue, just
        # without the queue.
        descriptions: list[str | None] = []
        for batch_item in batch:
            result = self._run_async(
                vision_service.describe_image(
                    image_bytes=batch_item["image_bytes"],
                    prompt=batch_item["prompt"],
                )
            )
            descriptions.append(result.description)

        # Merge descriptions
        for batch_item, description in zip(batch, descriptions, strict=True):
            page_info = batch_item["page_info"]
            if description is None:
                logger.warning(
                    "vision_page_failed", file_id=file_id, page=page_info.get("page_number")
                )
                continue

            doc_idx = page_info["doc_index"]
            if page_info.get("is_standalone"):
                documents[doc_idx]["content"] = description
                documents[doc_idx]["metadata"]["extraction_method"] = "vision"
            else:
                page_num = page_info["page_number"]
                page_texts = documents[doc_idx].get("metadata", {}).get("_page_texts", [])
                if page_texts and page_num <= len(page_texts):
                    page_texts[page_num - 1] += (
                        f"\n\n[Visual Content]\n{description}\n[/Visual Content]"
                    )
                    documents[doc_idx]["content"] = "\n\n".join(page_texts)

        logger.info("vision_processing_complete", file_id=file_id)
        return documents

    def _load_document_text(self, file_path: Path) -> str:
        """Load text content from a document using Core LoaderRegistry.

        Args:
            file_path: Path to document

        Returns:
            Extracted text content

        Raises:
            ValueError: If no content could be extracted
        """
        from chaoscypher_core.services.sources.loaders.factory import get_loader_registry

        registry = get_loader_registry(self.ctx.settings)
        documents = registry.load_document(str(file_path))

        if not documents:
            msg = f"No content extracted from: {file_path.name}"
            raise ValueError(msg)

        return "\n\n".join(doc["content"] for doc in documents)

    def _generate_embeddings(
        self, file_id: str, chunks: list[dict[str, Any]]
    ) -> tuple[str, int, int]:
        """Generate embeddings for chunks using local CPU EmbeddingService.

        Args:
            file_id: File ID
            chunks: List of chunk dicts (from ChunkingService, keyed by 'id' and 'content')

        Returns:
            Tuple of (model_name, dimensions, failed_chunks_count)
        """
        embedding_service = self.ctx.embedding_service
        model_name = embedding_service.model_name
        batch_size = self.ctx.settings.batching.embedding_api_batch_size
        dimensions = 0
        failed_chunks = 0

        async def generate_all_embeddings() -> None:
            """Generate embeddings for all batches in a single async context."""
            nonlocal dimensions, failed_chunks

            total_batches = (len(chunks) + batch_size - 1) // batch_size
            for i in range(0, len(chunks), batch_size):
                batch_num = (i // batch_size) + 1
                batch = chunks[i : i + batch_size]
                texts = [c["content"] for c in batch]

                logger.info(
                    "embedding_batch_started",
                    batch=batch_num,
                    total=total_batches,
                    texts_in_batch=len(texts),
                )

                try:
                    batch_result = await embedding_service.batch_embed(texts, batch_size=batch_size)
                    logger.info(
                        "embedding_batch_completed",
                        batch=batch_num,
                        total=total_batches,
                        embeddings_count=batch_result.total,
                    )

                    for j, embedding in enumerate(batch_result.embeddings):
                        chunk = batch[j]
                        chunk_id = chunk["id"]
                        embedding_dims = len(embedding) if embedding else 0
                        embedding_str = (
                            base64.b64encode(
                                np.array(embedding, dtype=np.float32).tobytes()
                            ).decode("utf-8")
                            if embedding
                            else ""
                        )
                        self.ctx.storage_adapter.update_chunk_embedding(
                            chunk_id,
                            embedding_str,
                            model_name,
                            embedding_dims,
                            "embedded",
                        )

                        if dimensions == 0 and embedding:
                            dimensions = len(embedding)

                except Exception as e:
                    failed_chunks += len(batch)
                    logger.warning("embedding_batch_failed", batch=batch_num, error=str(e))

        self._run_async(generate_all_embeddings())

        return (model_name, dimensions, failed_chunks)

    # ========================================================================
    # Stage 3: Extract Entities — Core DomainRegistry + AIEntityExtractor
    # ========================================================================

    def detect_domain_for_source(self, file_id: str) -> dict[str, Any] | None:
        """Run domain detection without moving the source status.

        This is the gate pre-step: it must run BEFORE ``start_extraction``
        (which flips the row to EXTRACTING) so a parked source never moves
        status. Returns the canonical recommendation payload shared by the
        confirmation gate across CLI/API/MCP, or ``None`` when there are no
        chunks to detect against (nothing to extract yet).

        Args:
            file_id: Source file ID (expected INDEXED).

        Returns:
            ``{detected_domain, confidence, ranking, low_confidence}`` or
            ``None`` when the source has no extractable chunks.
        """
        from chaoscypher_core.services.sources.engine.extraction.domains import (
            create_domain_sample_text,
            get_domain_registry,
        )
        from chaoscypher_core.services.sources.engine.extraction.orchestration import (
            detect_extraction_domain,
        )

        file_record = self.ctx.storage_adapter.get_file(file_id, self.ctx.database_name)
        if not file_record:
            return None

        all_chunks = self.ctx.storage_adapter.get_chunks_for_extraction(
            source_id=file_id,
            database_name=self.ctx.database_name,
        )
        if not all_chunks:
            logger.warning("detect_no_chunks", file_id=file_id)
            return None

        registry = get_domain_registry(
            settings=self.ctx.settings,
            database_name=self.ctx.database_name,
        )
        sample_text = create_domain_sample_text(all_chunks, content_key="content")
        domain_result = detect_extraction_domain(
            registry=registry,
            forced_domain=file_record.get("forced_domain"),
            sample_text=sample_text,
            filename=file_record.get("filename", ""),
            metadata=file_record.get("metadata", {}),
        )
        return {
            "detected_domain": domain_result["detected_domain"],
            "confidence": domain_result["confidence"],
            "ranking": domain_result.get("ranking", []),
            "low_confidence": domain_result.get("low_confidence", False),
        }

    def extract_entities(
        self,
        file_id: str,
        progress_callback: Any | None = None,
        domain_callback: Any | None = None,
        filtering_mode: FilteringMode | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Extract entities from indexed file using Core services.

        Uses Core library for:
        - Domain auto-detection (DomainRegistry with multiple analyzers)
        - Domain-specific templates and guidance (template_formatter)
        - Single-pass pipe-delimited extraction (AIEntityExtractor)
        - Full entity finalization (ExtractionService.finalize_distributed_extraction)
          including dedup, hierarchical name resolution, template suggestions, embeddings

        Args:
            file_id: Source file ID
            progress_callback: Optional callable(current, total) for progress updates
            domain_callback: Optional callable(domain_name) fired after domain detection
            filtering_mode: Optional filtering mode preset override (overrides domain default)

        Returns:
            Tuple of (extraction_results, llm_metrics_summary)

        Raises:
            ValueError: If file not indexed or LLM not available
        """
        if not self.has_llm:
            msg = "LLM provider not configured - cannot extract entities"
            raise ValueError(msg)

        # Get file record
        file_record = self.ctx.storage_adapter.get_file(file_id, self.ctx.database_name)
        if not file_record:
            msg = f"File not found: {file_id}"
            raise ValueError(msg)

        # Check status
        status = file_record.get("status", "")
        if status not in (SourceStatus.INDEXED, SourceStatus.EXTRACTED, "failed"):
            msg = f"Cannot extract from file with status '{status}' - must be 'indexed'"
            raise ValueError(msg)

        # Update status
        depth = file_record.get("extraction_depth", "full")
        self.ctx.storage_adapter.start_extraction(file_id, depth)

        # Create LLM metrics collector
        from chaoscypher_core.analytics.llm_metrics import LLMMetricsCollector

        llm_settings = self.ctx.settings.llm
        provider = llm_settings.chat_provider
        extraction_model = getattr(llm_settings, f"{provider}_extraction_model", None)
        chat_model = getattr(llm_settings, f"{provider}_chat_model", "unknown")
        model_for_metrics = extraction_model or chat_model

        metrics_collector = LLMMetricsCollector(
            source_id=file_id,
            database_name=self.ctx.database_name,
            operation_type="entity_extraction",
            provider=provider,
            model=model_for_metrics,
        )

        try:
            # Step 1: Domain detection (shared Core logic)
            from chaoscypher_core.services.sources.engine.extraction.domains import (
                create_domain_sample_text,
                get_domain_registry,
            )
            from chaoscypher_core.services.sources.engine.extraction.orchestration import (
                apply_depth_strategy,
                build_extraction_groups,
                detect_extraction_domain,
                filter_and_strip_chunks,
                format_extraction_templates,
                resolve_content_exclusions,
            )

            registry = get_domain_registry(
                settings=self.ctx.settings,
                database_name=self.ctx.database_name,
            )

            # Fetch chunks for content filtering + dynamic group building
            all_chunks = self.ctx.storage_adapter.get_chunks_for_extraction(
                source_id=file_id,
                database_name=self.ctx.database_name,
            )

            if not all_chunks:
                logger.warning("extract_no_chunks", file_id=file_id)
                return ({}, {})

            # Detect domain using sample text from chunks
            forced_domain = file_record.get("forced_domain")
            sample_text = create_domain_sample_text(all_chunks, content_key="content")
            domain_result = detect_extraction_domain(
                registry=registry,
                forced_domain=forced_domain,
                sample_text=sample_text,
                filename=file_record.get("filename", ""),
                metadata=file_record.get("metadata", {}),
            )
            detected_domain_name = domain_result["detected_domain"]

            if domain_callback and detected_domain_name:
                domain_callback(detected_domain_name)

            # Apply content exclusion filtering
            content_matchers = resolve_content_exclusions(domain_result.get("domain"))
            if content_matchers:
                all_chunks, filter_stats = filter_and_strip_chunks(all_chunks, content_matchers)
                logger.info(
                    "content_exclusion_applied",
                    excluded_chunks=filter_stats.excluded_chunks,
                    categories=filter_stats.categories_matched,
                )

            # Build extraction groups with dynamic token-budget sizing
            groups = build_extraction_groups(
                all_chunks,
                target_tokens=900,
                overlap=1,
            )

            # Step 2: Format templates (shared Core logic)
            template_result = format_extraction_templates(
                domain_result["domain"],
                examples_enabled=self.ctx.settings.llm.extraction_examples_enabled,
                examples_max_chars=self.ctx.settings.llm.extraction_examples_max_chars,
            )

            # Step 3: Apply depth strategy (shared Core logic)
            groups_to_process = apply_depth_strategy(
                groups,
                depth,
                quick_sample_size=getattr(
                    getattr(self.ctx.settings, "analysis", None), "quick_sample_size", 5
                ),
            )

            logger.info(
                "extraction_started",
                depth=depth,
                total_groups=len(groups),
                groups_to_process=len(groups_to_process),
                domain=detected_domain_name,
            )

            # Resolve cross-chunk filtering inputs once so the post-dedup
            # filter pass inside ``finalize_distributed_extraction`` matches
            # what the per-chunk extractor saw. ``edge_type_constraints``
            # comes from the resolved domain; ``filtering_config`` mirrors
            # the resolution chain ``extract_entities_from_groups`` performs
            # for the standalone path.
            from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
                resolve_filtering_config,
            )

            domain_obj = domain_result["domain"]
            cli_edge_type_constraints: dict[str, dict[str, list[str]]] | None = (
                domain_obj.get_edge_type_constraints()
                if hasattr(domain_obj, "get_edge_type_constraints")
                else None
            ) or None
            domain_extraction_limits_resolved = domain_obj.get_extraction_limits()
            # Effective preset cascade: per-source override > domain jsonld
            # > engine default. Kept as its own variable — never inlined into
            # the limits dict (that conflation was the
            # ``domain_config_unknown_keys_dropped`` warning source).
            _domain_default_mode: str | None = (
                domain_obj.get_filtering_mode()
                if hasattr(domain_obj, "get_filtering_mode")
                else None
            )
            cli_filtering_mode = str(
                filtering_mode
                or _domain_default_mode
                or self.ctx.settings.extraction.extraction_filtering_mode
            )
            cli_filtering_config = resolve_filtering_config(
                mode=cli_filtering_mode,
                domain_overrides=(
                    dict(domain_extraction_limits_resolved)
                    if domain_extraction_limits_resolved
                    else None
                ),
            )

            # Step 4+5: Extract all groups + finalize in a single event loop.
            extraction_results = self._run_async(
                self._extract_and_finalize(
                    groups_to_process=groups_to_process,
                    file_id=file_id,
                    node_templates=template_result["node_templates"],
                    edge_templates=template_result["edge_templates"],
                    entity_guidance=domain_result["entity_guidance"],
                    relationship_guidance=domain_result["relationship_guidance"],
                    entity_examples=template_result["entity_examples"] or None,
                    relationship_examples=template_result["relationship_examples"] or None,
                    entity_exclusions=domain_obj.get_entity_exclusions() or None,
                    domain_extraction_limits=domain_extraction_limits_resolved,
                    filtering_mode=cli_filtering_mode,
                    strict_entity_types=(
                        domain_obj.get_strict_entity_types()
                        if hasattr(domain_obj, "get_strict_entity_types")
                        else False
                    ),
                    valid_entity_type_names={
                        t["name"]
                        for t in (domain_obj.get_templates() or {}).get("node_templates", [])
                        if t.get("name")
                    }
                    or None,
                    metrics_collector=metrics_collector,
                    file_record=file_record,
                    detected_domain_name=detected_domain_name,
                    forced_domain=forced_domain,
                    total_groups=len(groups),
                    depth=depth,
                    progress_callback=progress_callback,
                    edge_type_constraints=cli_edge_type_constraints,
                    filtering_config=cli_filtering_config,
                )
            )

            # Persist LLM call rows, then aggregate them into the source row's
            # ``llm_*`` columns via the canonical compute_llm_summary path
            # (matches the queue-driven extraction_finalizer in core).
            if metrics_collector.attempts:
                self.ctx.storage_adapter.create_llm_call_metrics_batch(
                    metrics_collector.get_all_attempts()
                )

            llm_summary = self.ctx.storage_adapter.compute_llm_summary(
                source_id=file_id, database_name=self.ctx.database_name
            )
            self.ctx.storage_adapter.update_source_columns(
                source_id=file_id,
                database_name=self.ctx.database_name,
                updates=llm_summary,
            )

            # Kept in scope for the structlog event below and for the
            # extract step's return value (caller's UI panel reads from
            # the same dict shape the collector emits, not the source-row
            # shape).
            collector_summary = metrics_collector.get_summary()

            # Cache quality scores (shared Core logic)
            from chaoscypher_core.services.sources.engine.extraction.orchestration import (
                cache_quality_scores,
            )

            # Get chunk_count for coverage score
            cli_source = self.ctx.storage_adapter.get_file(file_id, self.ctx.database_name)
            cli_chunk_count = (cli_source.get("chunk_count", 0) or 0) if cli_source else 0

            cache_quality_scores(
                adapter=self.ctx.storage_adapter,
                source_id=file_id,
                entities=extraction_results.get("entities", []),
                relationships=extraction_results.get("relationships", []),
                domain_name=forced_domain or detected_domain_name,
                database_name=self.ctx.database_name,
                chunk_count=cli_chunk_count,
            )

            # Mark extraction complete (migration 0042: per-source entity
            # / relationship rows live in dedicated tables; complete_extraction
            # writes them).
            _metadata = extraction_results.get("metadata")
            _filtering_log = _metadata.get("filtering_log") if isinstance(_metadata, dict) else None
            self.ctx.storage_adapter.complete_extraction(
                file_id,
                entities=extraction_results.get("entities", []),
                relationships=extraction_results.get("relationships", []),
                cross_chunk_filtering_log=_filtering_log,
            )

            entity_count = len(extraction_results.get("entities", []))
            rel_count = len(extraction_results.get("relationships", []))
            logger.info(
                "entities_extracted",
                file_id=file_id,
                entities=entity_count,
                relationships=rel_count,
                llm_calls=collector_summary["total_calls"],
                llm_retries=collector_summary["retry_calls"],
                llm_cost_usd=collector_summary["estimated_cost_usd"],
            )

            return extraction_results, collector_summary

        except Exception as e:
            # Still persist any metrics we collected before failure. Same
            # compute_llm_summary route as the happy path so the row's
            # llm_* columns land correctly even on a failed run.
            if metrics_collector.attempts:
                try:
                    self.ctx.storage_adapter.create_llm_call_metrics_batch(
                        metrics_collector.get_all_attempts()
                    )
                    llm_summary = self.ctx.storage_adapter.compute_llm_summary(
                        source_id=file_id, database_name=self.ctx.database_name
                    )
                    self.ctx.storage_adapter.update_source_columns(
                        source_id=file_id,
                        database_name=self.ctx.database_name,
                        updates=llm_summary,
                    )
                except Exception as persist_error:
                    logger.warning("failed_to_persist_metrics_on_error", error=str(persist_error))

            self.ctx.storage_adapter.fail_extraction(file_id, str(e))
            raise

    async def _extract_and_finalize(
        self,
        groups_to_process: list[dict[str, Any]],
        file_id: str,
        node_templates: str,
        edge_templates: str,
        entity_guidance: str | None,
        relationship_guidance: str | None,
        entity_examples: str | None,
        relationship_examples: str | None,
        entity_exclusions: list[ExclusionRule] | None,
        domain_extraction_limits: dict[str, Any] | None,
        filtering_mode: str | None,
        metrics_collector: Any,
        file_record: dict[str, Any],
        detected_domain_name: str | None,
        forced_domain: str | None,
        total_groups: int,
        depth: str,
        strict_entity_types: bool = False,
        valid_entity_type_names: set[str] | None = None,
        progress_callback: Any | None = None,
        *,
        edge_type_constraints: dict[str, dict[str, list[str]]] | None = None,
        filtering_config: Any | None = None,
    ) -> dict[str, Any]:
        """Extract entities from all groups and finalize in a single async context.

        Runs all LLM calls within one event loop to prevent httpx connection
        pool errors when the Ollama provider reuses connections across calls.

        Args:
            groups_to_process: Hierarchical groups to extract from
            file_id: Source file ID
            node_templates: Formatted node templates string
            edge_templates: Formatted edge templates string
            entity_guidance: Domain-specific guidance text
            relationship_guidance: Domain-specific relationship guidance
            entity_examples: Domain-specific entity examples
            relationship_examples: Domain-specific relationship examples
            entity_exclusions: Domain-specific exclusion rules for prompts
            domain_extraction_limits: Domain ``FilteringConfig`` field overrides
            filtering_mode: Effective preset selector (per-source override >
                domain default > engine default). Distinct from
                ``domain_extraction_limits`` — the two are passed separately so
                the selector never leaks into the overrides dict.
            metrics_collector: LLM metrics collector
            file_record: File record dict
            detected_domain_name: Auto-detected domain name
            forced_domain: User-forced domain name
            total_groups: Total number of groups
            depth: Extraction depth setting
            strict_entity_types: Whether to enforce strict type matching
            valid_entity_type_names: Set of allowed entity type names
            progress_callback: Optional callable(current, total) for progress updates
            edge_type_constraints: Domain edge-type constraint map applied during
                the post-dedup filter pass inside ``finalize_distributed_extraction``.
            filtering_config: Resolved filtering configuration (mode + domain
                overrides) used to ensure the post-dedup filter pass mirrors the
                per-chunk extractor's behaviour.

        Returns:
            Finalized extraction results dict with stats
        """
        from chaoscypher_core.services.sources.engine.extraction.orchestration import (
            aggregate_chunk_results,
        )
        from chaoscypher_core.services.sources.engine.extraction.service import (
            ExtractionService,
        )
        from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
            AIEntityExtractor,
        )

        # --- Phase 1: Extract entities from each group ---
        extractor = AIEntityExtractor(settings=self.ctx.settings)

        # Build chunk results in the same format as Cortex completed_tasks
        chunk_results: list[dict[str, Any]] = []

        # Spend-cap enforcement on the CLI standalone path (no queue involved).
        # The daily counter is persisted per-database in app.db, so a cap set in
        # settings.yaml is honored here too and survives across CLI invocations.
        from chaoscypher_core.services.llm.spend import get_llm_spend_tracker

        spend_tracker = get_llm_spend_tracker()

        for group_idx, group in enumerate(groups_to_process):
            group_text = group.get("combined_content", "")

            # Refuse before the LLM call when the per-source or per-day cap is
            # reached. Raised OUTSIDE the per-group try below so it is not
            # swallowed as a per-group failure — it propagates to
            # extract_entities' handler, which fails the source (no further
            # billing). Both caps are opt-in (settings default None).
            spend_tracker.check_and_raise(
                file_id,
                self.ctx.settings,
                adapter=self.ctx.storage_adapter,
                database_name=self.ctx.database_name,
            )

            try:
                (
                    entities,
                    relationships,
                    in_tokens,
                    out_tokens,
                    _metrics,
                ) = await extractor.extract_single_chunk(
                    chunk_content=group_text,
                    node_templates_formatted=node_templates,
                    edge_templates_formatted=edge_templates,
                    entity_guidance=entity_guidance,
                    relationship_guidance=relationship_guidance,
                    entity_examples=entity_examples,
                    relationship_examples=relationship_examples,
                    metrics_collector=metrics_collector,
                    domain_extraction_limits=domain_extraction_limits,
                    filtering_mode=filtering_mode,
                    entity_exclusions=entity_exclusions,
                    strict_entity_types=strict_entity_types,
                    valid_entity_type_names=valid_entity_type_names,
                )

                # Tag entities with chunk_index for citations
                for entity in entities:
                    entity["chunk_index"] = group_idx

                # Tag relationships with chunk_index
                for rel in relationships:
                    rel["chunk_index"] = group_idx

                chunk_results.append(
                    {
                        "raw_entities": entities,
                        "raw_relationships": relationships,
                        "input_text": group_text,
                    }
                )

                # Record this group's spend so the per-source / per-day caps
                # observe it (persisted to app.db). Recorded after the call
                # succeeds, mirroring the worker path.
                spend_tracker.record(
                    file_id,
                    (in_tokens or 0) + (out_tokens or 0),
                    adapter=self.ctx.storage_adapter,
                    database_name=self.ctx.database_name,
                )

                # Update last attempt with chunk info
                if metrics_collector.attempts:
                    last_attempt = metrics_collector.attempts[-1]
                    last_attempt["chunk_index"] = group_idx
                    last_attempt["chunk_size_chars"] = len(group_text)
                    last_attempt["entities_extracted"] = len(entities)
                    last_attempt["relationships_extracted"] = len(relationships)

            except Exception as e:
                logger.warning(
                    "group_extraction_failed",
                    group_idx=group_idx,
                    error=str(e),
                )

            # Update progress
            self.ctx.storage_adapter.update_step_progress(
                file_id,
                current_step=group_idx + 1,
                total_steps=len(groups_to_process),
                step_description=f"Extracting group {group_idx + 1}/{len(groups_to_process)}",
            )

            if progress_callback:
                progress_callback(group_idx + 1, len(groups_to_process))

        # --- Phase 2: Aggregate chunk results (shared Core logic) ---
        aggregated = aggregate_chunk_results(chunk_results)

        # --- Phase 3: Finalize extraction ---
        # embedding_service must be passed for semantic deduplication to run;
        # the default entity_deduplication_mode is "semantic" so omitting it
        # silently degrades dedup to exact-name-only (and skips final entity
        # embedding generation). Cortex/Neuron's extraction_finalizer and
        # the engine's lazy extraction_service property both pass it; the
        # CLI was the only caller missing it.
        extraction_service = ExtractionService(
            graph_repository=self.ctx.graph_repository,
            llm_provider=self.ctx.llm_provider,
            settings=self.ctx.settings,
            embedding_service=self.ctx.embedding_service,
        )

        finalized = await extraction_service.finalize_distributed_extraction(
            raw_entities=aggregated["entities"],
            raw_relationships=aggregated["relationships"],
            generate_embeddings=True,
            file_info=file_record,
            detected_domain=detected_domain_name,
            forced_domain=forced_domain,
            edge_type_constraints=edge_type_constraints,
            filtering_config=filtering_config,
        )

        # Merge finalized data with stats for pipeline compatibility
        finalized["stats"] = {
            "entities_count": len(finalized.get("entities", [])),
            "relationships_count": len(finalized.get("relationships", [])),
            "groups_processed": len(groups_to_process),
            "groups_total": total_groups,
            "extraction_depth": depth,
            "detected_domain": detected_domain_name,
            "forced_domain": forced_domain,
        }

        return finalized

    # ========================================================================
    # Stage 4: Commit to Graph — Core SourceCommitService
    # ========================================================================

    def commit_to_graph(self, file_id: str) -> dict[str, Any]:
        """Commit extracted entities to knowledge graph using Core SourceCommitService.

        Provides full commit with:
        - Source document node creation
        - Entity + relationship citations
        - Chunk status promotion
        - sqlite-vec vector indexing
        - FTS5 fulltext indexing
        - Template matching from extraction suggestions

        Args:
            file_id: Source file ID

        Returns:
            Dict with commit stats

        Raises:
            ValueError: If file not extracted
        """
        # Get file record
        file_record = self.ctx.storage_adapter.get_file(file_id, self.ctx.database_name)
        if not file_record:
            msg = f"File not found: {file_id}"
            raise ValueError(msg)

        # Check status
        status = file_record.get("status", "")
        if status not in (SourceStatus.EXTRACTED, SourceStatus.INDEXED):
            msg = f"Cannot commit file with status '{status}' - must be 'extracted' or 'indexed'"
            raise ValueError(msg)

        # SourceCommitService.commit() calls start_commit() internally,
        # so we do NOT call it here to avoid double-calling.

        try:
            from chaoscypher_core.adapters.sqlite.repos import GraphRepository
            from chaoscypher_core.services.sources.engine.commit.service import (
                SourceCommitService,
            )

            # Build a GraphRepository bound to ``storage_adapter.session``
            # rather than the Engine-default ``_graph_session``. They share
            # the same SQLite engine, but two SafeSessions on one file
            # cannot interleave writes inside ``adapter.transaction()``:
            # ``start_commit`` flushes via storage_adapter.session (write
            # lock acquired), then template_handler's ``INSERT INTO
            # graph_templates`` via _graph_session hits SQLITE_BUSY and
            # the whole commit cascades with PendingRollbackError.
            #
            # By pointing graph_repository at storage_adapter.session, both
            # writers participate in the SAME transaction the
            # ``adapter.transaction()`` context manages — atomic, lock-free.
            # Cortex's queue worker doesn't hit this because each task
            # builds a fresh Engine per dispatch; the CLI keeps the Engine
            # alive across stages, so the dual-session race is CLI-specific.
            cli_graph_repository = GraphRepository(
                self.ctx.storage_adapter.session,  # type: ignore[arg-type]
                self.ctx.database_name,
            )

            commit_service = SourceCommitService(
                graph_repository=cli_graph_repository,
                source_repository=self.ctx.storage_adapter,
                sources_repository=self.ctx.storage_adapter,
                indexing_repository=self.ctx.storage_adapter,
                search_repository=self.ctx.search_repository,
                settings=self.ctx.settings,
                reload_callback=None,
            )

            # Rebuild the commit payload from the persisted commit_payload
            # column (set by the finalizer's ``_queue_commit_phase``) and
            # fall back to the per-source entity / relationship tables
            # when the payload is missing. Migration 0042 retired the
            # heavy ``extraction_results`` JSON column; this is the
            # cli-side analogue of the cortex retry-source flow.
            stored_payload = self.ctx.storage_adapter.get_source_commit_payload(
                file_id, self.ctx.database_name
            )
            if stored_payload:
                extraction_results = stored_payload
            else:
                extraction_results = {
                    "entities": self.ctx.storage_adapter.list_source_entities(
                        file_id, self.ctx.database_name
                    ),
                    "relationships": self.ctx.storage_adapter.list_source_relationships(
                        file_id, self.ctx.database_name
                    ),
                    "suggested_templates": [],
                    "suggested_edge_templates": [],
                    "inverse_relationships": {},
                }

            # Release the storage_adapter session's auto-begun read txn from
            # the lookups above before handing control to the commit service.
            # ``GraphRepository`` lives on a separate SafeSession that shares
            # the same SQLite engine — when storage_adapter.session has an
            # open read transaction, the graph session's first INSERT (system
            # template "Item" inside the commit's write phase) hits
            # ``sqlite3.OperationalError: database is locked``. Cortex's
            # queue worker doesn't see this because each task runs with a
            # fresh storage_adapter session. Belt-and-suspenders rollback
            # here clears the read state cheaply — no writes are pending on
            # the read txn so nothing is lost.
            with contextlib.suppress(Exception):
                # Best-effort — never break the commit path over a hygiene op.
                if self.ctx.storage_adapter.session is not None:
                    self.ctx.storage_adapter.session.rollback()

            commit_result = self._run_async(
                commit_service.commit(
                    file_id=file_id,
                    commit_data=extraction_results,
                    file_info=file_record,
                    auto_enable=True,
                )
            )

            nodes_created = len(commit_result.get("created_nodes", []))
            edges_created = len(commit_result.get("created_edges", []))
            templates_created = len(commit_result.get("created_templates", []))

            logger.info(
                "committed_to_graph",
                file_id=file_id,
                nodes=nodes_created,
                edges=edges_created,
                templates=templates_created,
            )

            return {
                "nodes_created": nodes_created,
                "edges_created": edges_created,
                "templates_created": templates_created,
            }

        except Exception as e:
            self.ctx.storage_adapter.fail_commit(file_id, str(e))
            raise

    # ========================================================================
    # Utility Methods
    # ========================================================================

    def get_file_status(self, file_id: str) -> dict[str, Any] | None:
        """Get current status of a source processing file.

        Args:
            file_id: Source file ID

        Returns:
            File record dict or None if not found
        """
        return self.ctx.storage_adapter.get_file(file_id, self.ctx.database_name)

    def reset_for_re_extraction(self, source_id: str) -> dict[str, int]:
        """Reset a committed source so it can be re-extracted from scratch.

        Mirrors the Cortex trigger_extraction(force=True) flow:
        1. Delete graph artifacts (nodes, edges, templates) from the prior commit.
        2. Reset the source record back to INDEXED so extract_entities can run.

        Both writes run inside adapter.transaction() via force_re_extract so the
        adapter-side reset rolls back on any exception raised during the sequence.

        Args:
            source_id: Source file ID (must be in COMMITTED status).

        Returns:
            Dict with counts of removed graph artifacts
            (nodes_deleted, edges_deleted, templates_deleted).

        Raises:
            ValueError: If the source does not exist.
        """
        file_record = self.ctx.storage_adapter.get_file(source_id, self.ctx.database_name)
        if not file_record:
            msg = f"Source not found: {source_id}"
            raise ValueError(msg)

        return force_re_extract(
            source_id=source_id,
            database_name=self.ctx.database_name,
            storage_adapter=self.ctx.storage_adapter,
            graph_repository=self.ctx.graph_repository,
        )
