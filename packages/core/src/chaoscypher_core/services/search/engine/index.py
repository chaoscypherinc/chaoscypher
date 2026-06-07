# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Indexing Service for chaoscypher-engine.

Business logic for document indexing (RAG).
Generates embeddings for pre-chunked documents.
"""

import base64
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

from chaoscypher_core.exceptions import OperationError, ValidationError
from chaoscypher_core.services.quality.counters import QualityCounter, increment_quality_counter


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol
    from chaoscypher_core.ports.index import IndexingProtocol
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)


class IndexingService:
    """Business logic for document indexing (RAG).

    Works with pre-chunked data from ChunkingService.
    Responsible for generating embeddings and indexing chunks.
    """

    def __init__(
        self,
        repository: IndexingProtocol,
        settings: EngineSettings,
        embedding_service: EmbeddingProviderProtocol | None = None,
    ):
        """Initialize indexing service.

        Args:
            repository: IndexingProtocol implementation
            settings: Engine settings with LLM configuration
            embedding_service: Embedding provider for generating embeddings

        """
        self.repository = repository
        self.settings = settings
        self.embedding_service = embedding_service

    @classmethod
    def from_engine(cls, engine: Any) -> IndexingService:
        """Create IndexingService from an Engine instance.

        Args:
            engine: An Engine instance with storage_adapter and settings.

        Returns:
            Configured IndexingService.

        Example:
            service = IndexingService.from_engine(engine)
            result = await service.create_index(source_id)

        """
        return cls(repository=engine.storage_adapter, settings=engine.settings)

    @classmethod
    def from_adapter(
        cls,
        adapter: SqliteAdapter,
        settings: EngineSettings,
        *,
        embedding_service: EmbeddingProviderProtocol | None = None,
    ) -> IndexingService:
        """Create IndexingService from a storage adapter.

        Args:
            adapter: SqliteAdapter (or compatible) implementing IndexingProtocol.
            settings: Engine settings.
            embedding_service: Optional embedding provider override.

        Returns:
            Configured IndexingService.

        Example:
            from chaoscypher_core import IndexingService, SqliteAdapter, EngineSettings

            adapter = SqliteAdapter("app.db", "default")
            service = IndexingService.from_adapter(adapter, EngineSettings())

        """
        return cls(
            repository=adapter,
            settings=settings,
            embedding_service=embedding_service,
        )

    async def create_index(
        self,
        source_id: str,
        progress_callback: Callable[[int, int], None] | None = None,
        cancellation_check: Callable[[], Any] | None = None,
    ) -> dict[str, Any]:
        """Generate embeddings for pre-chunked data and prepare for indexing.

        Workflow:
        1. Get small chunks from ChunkingService (already created)
        2. Delegate embedding generation + persistence to embed_chunks
        3. Ready for commit to vector search index

        Args:
            source_id: Source ID
            progress_callback: Optional callback(processed, total) called after each batch.
            cancellation_check: Optional async callable returning True if task should stop.

        Returns:
            {
                'chunks_count': int,
                'embedding_model': str,
                'embedding_dimensions': int
            }

        Raises:
            ValidationError: If no chunks are found for ``source_id``.

        """
        try:
            logger.info("indexing_started", source_id=source_id)

            # Step 1: Get small chunks (already created by ChunkingService)
            # Note: get_chunks_by_source returns (chunks, total) tuple with pagination
            # Use large page_size to get all chunks for indexing
            small_chunks, _ = self.repository.get_chunks_by_source(
                source_id, page=1, page_size=self.settings.batching.chunk_fetch_limit
            )

            if not small_chunks:
                msg = f"No chunks found for source_id={source_id}"
                raise ValidationError(msg, field="source_id")

            logger.info(
                "indexing_chunks_found",
                source_id=source_id,
                chunk_count=len(small_chunks),
            )

            # Step 2: Embed the fetched chunks (shared code path with the
            # resumable embedding sub-stage used by indexing_handler).
            # IndexingProtocol intentionally omits ``database_name`` (it's
            # adapter scaffolding rather than a port concern); the production
            # repository (``SqliteAdapter``) exposes it as a public attribute.
            database_name = getattr(self.repository, "database_name", "default")
            indexed_count = await self.embed_chunks(
                chunks=small_chunks,
                source_id=source_id,
                database_name=database_name,
                progress_callback=progress_callback,
                cancellation_check=cancellation_check,
            )

            embedding_model = self._get_embedding_model_name()
            embedding_dimensions = self.settings.search.vector_dimensions

            return {
                "chunks_count": indexed_count,
                "embedding_model": embedding_model,
                "embedding_dimensions": embedding_dimensions,
            }

        except Exception as e:
            logger.exception(
                "indexing_create_failed",
                source_id=source_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    async def embed_chunks(
        self,
        *,
        chunks: list[dict[str, Any]],
        source_id: str,
        database_name: str,
        progress_callback: Callable[[int, int], None] | None = None,
        cancellation_check: Callable[[], Any] | None = None,
        expected_dimensions: int | None = None,
    ) -> int:
        """Embed a pre-fetched list of chunks and persist the vectors.

        This is the shared "vector-index write path" used by both
        ``create_index`` (fetches all chunks for a source) and the
        incremental embedding sub-stage invoked by the indexing_handler
        on resume (fetches only chunks where ``embedded_at IS NULL``).

        Accepting an explicit chunk list means the caller chooses the
        resume semantics — this method stays oblivious to whether it is
        running an initial pass or a restart continuation.

        Args:
            chunks: List of chunk dicts. Each must have ``id`` and
                ``content``. Typically the output of
                ``get_chunks_by_source`` or ``list_unembedded_chunks``.
            source_id: Source these chunks belong to (used for logging).
            database_name: Active database name. Currently unused by this
                method because the repository is already bound to a
                database, but threaded through so the signature mirrors
                the adapter-level methods for future multi-database
                workers.
            progress_callback: Optional callback(processed, total) called
                after each embedding wave.
            cancellation_check: Optional async callable returning True to
                abort.
            expected_dimensions: Optional dimension recorded on the
                source's ``SourceRow.embedding_dimensions`` from a prior
                embedding pass. When set, every returned vector must
                match this length or a :class:`ValidationError` is raised
                BEFORE any persistence. Catches the case where the
                operator changes the configured embedding model after a
                source has already been embedded — the per-source check
                is stricter than the global ``settings.search.vector_dimensions``
                check because it reflects what the existing rows actually
                contain. Pass ``None`` for a first-pass embedding (no
                prior dimension on record).

        Returns:
            Number of chunks successfully embedded (total minus any
            NotFound skips encountered during per-chunk persistence).

        Raises:
            ValidationError: When ``expected_dimensions`` is provided and
                any returned vector's length does not equal it. The
                exception details carry ``source_id``, ``chunk_index``,
                ``expected``, and ``actual`` so on-call can identify the
                mismatch without enabling debug logging.
        """
        if not chunks:
            return 0

        logger.info(
            "embed_chunks_started",
            source_id=source_id,
            count=len(chunks),
        )

        chunk_texts = [chunk["content"] for chunk in chunks]
        embeddings = await self._generate_chunk_embeddings(
            chunk_texts,
            batch_size=self.settings.batching.embedding_batch_size,
            concurrency=self.settings.batching.embedding_concurrency,
            progress_callback=progress_callback,
            cancellation_check=cancellation_check,
        )

        embedding_model = self._get_embedding_model_name()
        # Resolve the expected dimension: caller-supplied per-source value
        # (F28: SourceRow.embedding_dimensions) wins over the global
        # ``settings.search.vector_dimensions`` (F35). The per-source value
        # is stricter — it reflects what existing chunk rows actually contain
        # so it catches the "operator changed embedding model after first
        # pass" case. Settings is the default for first-pass embeddings.
        resolved_expected_dimensions = (
            expected_dimensions
            if expected_dimensions is not None
            else self.settings.search.vector_dimensions
        )

        logger.info(
            "embed_chunks_embeddings_generated",
            source_id=source_id,
            embedding_model=embedding_model,
            embedding_dimensions=resolved_expected_dimensions,
            embedding_count=len(embeddings),
        )

        # F35 + F28: validate count + per-vector shape BEFORE any persistence.
        # If the provider returned the wrong number of vectors or any vector
        # is malformed (wrong dim, NaN, Inf, empty), raise ValidationError
        # without touching the search backend or marking any chunk as
        # embedded. The per-source ``expected_dimensions`` (F28) is honored
        # here via ``resolved_expected_dimensions`` so a misconfigured model
        # can never write a single mis-shaped vector to the chunk index.
        #
        # Phase 7 audit-remediation (2026-05-09): P2 #3 — intercept the
        # dimension-mismatch ValidationError BEFORE re-raising so the counter
        # records the event even though the raise terminates this call.
        # _validate_embeddings is sync and has no adapter access, so the
        # counter is wired here in the async caller.
        try:
            self._validate_embeddings(
                chunks=chunks,
                embeddings=embeddings,
                expected_dimensions=resolved_expected_dimensions,
                source_id=source_id,
            )
        except ValidationError as val_err:
            if val_err.field == "embedding_dimensions":
                await increment_quality_counter(
                    adapter=self.repository,
                    source_id=source_id,
                    database_name=database_name,
                    counter=QualityCounter.EMBEDDING_DIMENSION_MISMATCHES,
                )
            raise

        # Race guard (2026-05-22): if the source was deleted while we
        # were generating embeddings (a multi-second Ollama call for
        # large sources), every per-chunk update_chunk_embedding below
        # would raise NotFoundError because CASCADE wiped the
        # DocumentChunk rows. One operator-reported incident produced
        # 419 warning logs + a stack trace from this exact shape. Probe
        # once before the write loop so the common "whole source gone"
        # case short-circuits with a single info-level log.
        _, live_chunk_count = self.repository.get_chunks_by_source(source_id, page=1, page_size=1)
        if live_chunk_count == 0:
            logger.info(
                "embed_chunks_source_deleted_mid_flight",
                source_id=source_id,
                requested_count=len(chunks),
            )
            return 0

        skipped_count = 0
        # strict=True is belt-and-braces: the explicit length check above
        # already rejects mismatches with a richer error, but this guards
        # against future refactors that might bypass _validate_embeddings.
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            # Convert embedding to base64 for storage
            embedding_array = np.array(embedding, dtype=np.float32)
            embedding_bytes = base64.b64encode(embedding_array.tobytes()).decode("utf-8")

            try:
                self.repository.update_chunk_embedding(
                    chunk_id=chunk["id"],
                    embedding=embedding_bytes,
                    embedding_model=embedding_model,
                    embedding_dimensions=resolved_expected_dimensions,
                    status="indexed",
                )
            except Exception as chunk_err:
                from chaoscypher_core.exceptions import NotFoundError

                if isinstance(chunk_err, NotFoundError):
                    # Phase 7 audit-remediation (2026-05-09): P1 #6 — per-chunk
                    # failure counter so operators see "23 of 25 succeeded"
                    # granularity rather than a binary indexing-failed signal.
                    await increment_quality_counter(
                        adapter=self.repository,
                        source_id=source_id,
                        database_name=database_name,
                        counter=QualityCounter.EMBEDDING_CHUNK_FAILURES,
                    )
                    skipped_count += 1
                    logger.warning(
                        "embed_chunks_not_found_skipped",
                        source_id=source_id,
                        chunk_id=chunk["id"],
                        error_type=type(chunk_err).__name__,
                        error=str(chunk_err),
                    )
                else:
                    raise

        indexed_count = len(chunks) - skipped_count
        if skipped_count > 0:
            logger.warning(
                "embed_chunks_skipped",
                source_id=source_id,
                skipped_count=skipped_count,
                indexed_count=indexed_count,
            )

        logger.info(
            "embed_chunks_complete",
            source_id=source_id,
            count=indexed_count,
        )
        return indexed_count

    def _validate_embeddings(
        self,
        *,
        chunks: list[dict[str, Any]],
        embeddings: list[Any],
        expected_dimensions: int,
        source_id: str,
    ) -> None:
        """Validate count and per-vector shape before any persistence.

        Enforces the F35 contract:

        1. ``len(embeddings) == len(chunks)`` — a provider returning N-1
           vectors for N chunks would otherwise silently drop the last
           chunk under ``zip(..., strict=False)``.
        2. Each vector is non-empty.
        3. Each vector has length == ``expected_dimensions``.
        4. Every component is finite (rejects NaN AND ±Inf).

        Raises ``ValidationError`` on the first failure encountered.
        Logs structured detail (``expected``, ``actual``, ``source_id``)
        so operators can see why the embedding wave was rejected.
        """
        expected_count = len(chunks)
        actual_count = len(embeddings)

        if actual_count != expected_count:
            logger.error(
                "embed_chunks_count_mismatch",
                source_id=source_id,
                expected=expected_count,
                actual=actual_count,
            )
            msg = (
                "Embedding provider returned mismatched count: "
                f"expected={expected_count}, actual={actual_count}"
            )
            raise ValidationError(
                msg,
                field="embeddings",
                details={
                    "expected": expected_count,
                    "actual": actual_count,
                    "source_id": source_id,
                },
            )

        for idx, embedding in enumerate(embeddings):
            chunk_id = chunks[idx].get("id", f"<index {idx}>")

            if embedding is None:
                logger.error(
                    "embed_chunks_null_vector",
                    source_id=source_id,
                    chunk_id=chunk_id,
                    chunk_index=idx,
                )
                msg = f"Embedding for chunk {chunk_id} is None"
                raise ValidationError(
                    msg,
                    field="embedding",
                    details={"chunk_id": chunk_id, "chunk_index": idx},
                )

            # numpy arrays expose len() and iteration just like list[float],
            # so this works for both shapes returned by _generate_chunk_embeddings.
            actual_dim = len(embedding)
            if actual_dim == 0:
                logger.error(
                    "embed_chunks_empty_vector",
                    source_id=source_id,
                    chunk_id=chunk_id,
                    chunk_index=idx,
                )
                msg = f"Embedding for chunk {chunk_id} is empty"
                raise ValidationError(
                    msg,
                    field="embedding",
                    details={"chunk_id": chunk_id, "chunk_index": idx},
                )

            if actual_dim != expected_dimensions:
                logger.error(
                    "embed_chunks_dimension_mismatch",
                    source_id=source_id,
                    chunk_id=chunk_id,
                    chunk_index=idx,
                    expected=expected_dimensions,
                    actual=actual_dim,
                )
                msg = (
                    f"Embedding for chunk {chunk_id} has wrong dimension: "
                    f"expected={expected_dimensions}, actual={actual_dim}"
                )
                raise ValidationError(
                    msg,
                    field="embedding_dimensions",
                    details={
                        "source_id": source_id,
                        "chunk_id": chunk_id,
                        "chunk_index": idx,
                        "expected": expected_dimensions,
                        "actual": actual_dim,
                    },
                )

            # np.isfinite vectorized — single C call rejects NaN and ±Inf for the entire vector
            if not bool(np.all(np.isfinite(np.asarray(embedding, dtype=np.float64)))):
                logger.error(
                    "embed_chunks_non_finite_vector",
                    source_id=source_id,
                    chunk_id=chunk_id,
                    chunk_index=idx,
                )
                msg = f"Embedding for chunk {chunk_id} contains non-finite values (NaN or Inf)"
                raise ValidationError(
                    msg,
                    field="embedding",
                    details={"chunk_id": chunk_id, "chunk_index": idx},
                )

    async def _generate_chunk_embeddings(
        self,
        chunks: list[str],
        batch_size: int,
        concurrency: int = 1,
        progress_callback: Callable[[int, int], None] | None = None,
        cancellation_check: Callable[[], Any] | None = None,
    ) -> list[np.ndarray]:
        """Generate embeddings for text chunks in concurrent batches.

        Processes batches in waves of ``concurrency`` parallel requests,
        with cancellation checks between waves.

        Args:
            chunks: List of text strings.
            batch_size: Number of chunks to process per API call.
            concurrency: Number of parallel embedding requests per wave.
            progress_callback: Optional callback(processed, total) after each wave.
            cancellation_check: Optional async callable returning True to cancel.

        Returns:
            List of numpy arrays (embeddings).

        Raises:
            OperationError: If no embedding provider is configured
                (``embedding_service`` is None).
            ValidationError: If the embedding provider returns an empty
                embedding vector for a chunk.
            asyncio.CancelledError: If cancellation_check returns True.

        """
        import asyncio

        if not self.embedding_service:
            msg = "Embedding provider not configured"
            raise OperationError(msg, operation="embed")

        total = len(chunks)
        logger.info(
            "indexing_embedding_generation_started",
            total_chunks=total,
            batch_size=batch_size,
            concurrency=concurrency,
        )

        # Split all chunks into batches
        batches = [chunks[i : i + batch_size] for i in range(0, total, batch_size)]
        all_embeddings: list[np.ndarray] = []
        processed = 0

        # Process in waves of `concurrency` parallel requests
        for wave_start in range(0, len(batches), concurrency):
            # Check for cancellation between waves
            if cancellation_check:
                if asyncio.iscoroutinefunction(cancellation_check):
                    cancelled = await cancellation_check()
                else:
                    cancelled = cancellation_check()
                if cancelled:
                    logger.info(
                        "indexing_embedding_cancelled",
                        processed=processed,
                        total=total,
                    )
                    raise asyncio.CancelledError

            wave = batches[wave_start : wave_start + concurrency]

            # Run wave concurrently — gather preserves order
            results = await asyncio.gather(
                *(
                    self.embedding_service.batch_embed(batch, batch_size=len(batch))
                    for batch in wave
                )
            )

            # Collect results in order
            for result in results:
                for embedding in result.embeddings:
                    if not embedding:
                        msg = "Empty embedding returned in batch"
                        raise ValidationError(msg, field="embedding")
                    all_embeddings.append(np.array(embedding, dtype=np.float32))
                processed += result.total

            if progress_callback:
                progress_callback(processed, total)

            if processed % (batch_size * 10) == 0 or processed == total:
                logger.info(
                    "indexing_embedding_batch_progress",
                    processed=processed,
                    total=total,
                    percent=round(processed / total * 100, 1),
                )

        return all_embeddings

    def _get_embedding_model_name(self) -> str:
        """Get the current embedding model name from settings."""
        return self.settings.embedding.model
