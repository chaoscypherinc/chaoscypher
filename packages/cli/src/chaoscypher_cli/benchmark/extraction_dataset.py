# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ExtractionDataset - drives chaoscypher_cli.sources.SourcePipeline against a corpus."""

from __future__ import annotations

import asyncio
import gc
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_cli.benchmark.dataset import DatasetSource, RawOutput
from chaoscypher_cli.benchmark.scorers.v7 import V7ExtractionScorer


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_cli.benchmark.dataset import DatasetScorer
    from chaoscypher_cli.benchmark.models import ModelConfig
    from chaoscypher_cli.context import CLIContext


logger = structlog.get_logger(__name__)


# Provider field name conventions in chaoscypher_core.settings.LLMSettings.
# Each provider has `<provider>_chat_model` and `<provider>_extraction_model`
# fields. Setting both ensures extraction uses the requested model regardless
# of which one the engine reads.
_PROVIDER_MODEL_FIELDS: dict[str, tuple[str, str]] = {
    "ollama": ("ollama_chat_model", "ollama_extraction_model"),
    "openai": ("openai_chat_model", "openai_extraction_model"),
    "anthropic": ("anthropic_chat_model", "anthropic_extraction_model"),
    "gemini": ("gemini_chat_model", "gemini_extraction_model"),
}


@dataclass
class ExtractionDataset:
    """A dataset that runs the extraction pipeline against a fixed corpus.

    Attributes:
        id: Unique dataset identifier (e.g. "war_and_peace_tiny").
        version: Dataset version from manifest.yaml; bumps when corpus or
            domain template changes.
        corpus_path: Absolute path to the corpus text file.
        domain: Domain name passed to extraction (e.g. "literary").
        source: Where this dataset was discovered ("builtin" | "user").
            Surfaced in the leaderboard so reviewers can tell what's
            reproducible from the package alone vs what required user setup.
        keep_db: When True, the per-run temp database created under the
            user data dir is preserved after the run completes - useful for
            post-hoc inspection of what a model produced. When False
            (default), the temp DB is removed in the run's finally block.
            v7 metrics are already in the result row's metrics dict, so
            losing the DB does not lose the score breakdown.
    """

    id: str
    version: str
    corpus_path: Path
    domain: str
    source: DatasetSource = "builtin"
    thinking: bool = False
    seed: int = 42
    temperature: float = 0.0
    keep_db: bool = False
    """When True, preserve the per-run temp DB after the run completes.

    The orchestrator sets this to True before calling ``run`` so it can copy
    the snapshot into the GraphCache (see ``orchestrator.default_wiring``).
    When False (default), the temp DB is removed in the run's finally block.
    v7 metrics are already in the result row's metrics dict, so losing the DB
    does not lose the score breakdown.
    """
    # Commit the extracted entities into graph nodes and edges after scoring.
    # Off for the plain extraction benchmark (it scores the pre-commit graph and
    # needs no commit); the orchestrator turns it on so the cached snapshot the
    # embedding and chat stages read is a real graph. Until 2026-09-25 those
    # stages ran against an uncommitted snapshot: zero nodes, zero retrieval.
    commit_graph: bool = False
    thinking_honoured: bool | None = field(default=None, init=False)
    """Whether the model actually honoured the requested thinking mode.

    Set during ``run`` by probing the model. Models accept ``think`` and may
    silently ignore it, so a run must verify rather than assume — otherwise the
    result row mislabels its own configuration.
    """
    kind: str = field(default="extraction", init=False)
    scorer: DatasetScorer = field(default_factory=V7ExtractionScorer, init=False)
    fixture: Any = field(default=None, init=False)

    def __post_init__(self) -> None:
        """Validate corpus_path exists at construction time."""
        if not self.corpus_path.exists():
            msg = f"corpus not found: {self.corpus_path}"
            raise FileNotFoundError(msg)

    @property
    def corpus_text(self) -> str:
        """Read and return the corpus text."""
        return self.corpus_path.read_text(encoding="utf-8")

    async def run(self, model: ModelConfig) -> RawOutput:
        """Run extraction against the corpus using the given model.

        Drives ``chaoscypher_cli.sources.SourcePipeline`` against an isolated
        temp database, captures the resulting entities/relationships and
        LLM-side metrics, then disconnects.

        The timed call is always the extract-only run (index without
        embeddings, extract, no commit), so ``latency_ms`` and the per-chunk
        latencies are the same with and without ``commit_graph``. With
        ``commit_graph`` a second, untimed step (:meth:`_embed_and_commit`)
        then embeds the chunks and commits the extracted source; its time is
        not part of the row.

        Returns:
            A RawOutput. On failure, fields are zeroed/empty and ``error``
            is set.
        """
        # Imports deferred so importing this module doesn't load CLI/Core
        # heavy machinery just for protocol checks in unit tests.
        from chaoscypher_cli.sources import (
            CLISourceProcessingService,
            SourcePipeline,
        )

        # Reset per-run state first. ``run_benchmark`` reuses one dataset object
        # across every model, and the probe below only runs for Ollama, so a
        # stale verdict would otherwise leak into a hosted model's row.
        self.thinking_honoured = None

        if model.provider == "ollama":
            from chaoscypher_cli.benchmark.thinking_probe import (
                probe_thinking,
                thinking_honoured,
            )

            probe = await probe_thinking(model.model)
            self.thinking_honoured = thinking_honoured(probe, requested=self.thinking)
            if self.thinking_honoured is False:
                logger.warning(
                    "thinking_mode_not_honoured",
                    model=model.model_id,
                    requested=self.thinking,
                    probe=probe,
                )

        t0 = time.perf_counter()
        ctx: CLIContext | None = None
        try:
            ctx = self._build_temp_context(model)
            # SourcePipeline.run is synchronous and internally drives async
            # extraction via its own event loop (CLISourceProcessingService
            # ._run_async). We're already inside the runner's event loop, so
            # calling pipeline.run directly would raise "Cannot run the event
            # loop while another loop is running". asyncio.to_thread runs the
            # sync function in a worker thread, which gets its own loop.
            with CLISourceProcessingService(ctx) as service:
                pipeline = SourcePipeline(service, console=None)
                result = await asyncio.to_thread(
                    pipeline.run,
                    file_path=self.corpus_path,
                    file_id=None,
                    url=None,
                    skip_index=False,
                    skip_extract=False,
                    skip_commit=True,
                    skip_embeddings=True,
                    enable_normalization=True,  # benchmark v2: score the post-normalized, pre-commit graph
                    enable_vision=False,
                    index_only=False,
                    extract_only=True,
                    extraction_depth="full",
                    domain=self.domain,
                    filtering_mode=None,
                    quiet=True,
                    verbose=False,
                )
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                commit_error: str | None = None
                if result.success and self.commit_graph:
                    commit_error = await asyncio.to_thread(
                        self._embed_and_commit, service, pipeline, ctx, result.file_id
                    )

            if not result.success:
                return RawOutput(
                    entities=[],
                    relationships=[],
                    latency_ms=elapsed_ms,
                    input_tokens=result.llm_total_input_tokens,
                    output_tokens=result.llm_total_output_tokens,
                    error=result.error or "pipeline_failed",
                )

            if commit_error is not None:
                return RawOutput(
                    entities=[],
                    relationships=[],
                    latency_ms=elapsed_ms,
                    input_tokens=result.llm_total_input_tokens,
                    output_tokens=result.llm_total_output_tokens,
                    error=commit_error,
                )

            # Read back the extracted entities/relationships from the
            # dedicated per-source tables (migration 0042 retired the
            # heavy extraction_results JSON column).
            entities = ctx.storage_adapter.list_source_entities(result.file_id, ctx.database_name)
            relationships = ctx.storage_adapter.list_source_relationships(
                result.file_id, ctx.database_name
            )

            if not entities and not relationships:
                truncated, aborted = self._integrity_counters(ctx, result.file_id)
                return RawOutput(
                    entities=[],
                    relationships=[],
                    latency_ms=elapsed_ms,
                    input_tokens=result.llm_total_input_tokens,
                    output_tokens=result.llm_total_output_tokens,
                    error="empty_extraction",
                    chunks_truncated=truncated,
                    chunks_aborted_by_loop=aborted,
                )

            truncated, aborted = self._integrity_counters(ctx, result.file_id)
            if truncated:
                logger.warning(
                    "extraction_truncated",
                    model=model.model_id,
                    dataset=self.id,
                    chunks_truncated=truncated,
                )

            return RawOutput(
                entities=entities,
                relationships=relationships,
                latency_ms=elapsed_ms,
                input_tokens=result.llm_total_input_tokens,
                output_tokens=result.llm_total_output_tokens,
                error=None,
                chunks_truncated=truncated,
                chunks_aborted_by_loop=aborted,
                per_chunk_latency_ms=self._estimate_per_chunk_latency(
                    elapsed_ms, result.chunks_count
                ),
                extras={"snapshot_db_path": str(ctx.database_dir / "app.db")},
            )

        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.exception(
                "extraction_dataset_failed",
                dataset_id=self.id,
                model_id=model.model_id,
                error_type=type(exc).__name__,
            )
            return RawOutput(
                entities=[],
                relationships=[],
                latency_ms=elapsed_ms,
                input_tokens=0,
                output_tokens=0,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            if ctx is not None:
                # Remember the database directory before disconnect clears
                # the engine reference.
                db_dir = ctx.database_dir
                ctx.disconnect()
                if not self.keep_db and db_dir.exists():
                    _remove_temp_db_dir(db_dir, dataset_id=self.id)

    @staticmethod
    def _embed_and_commit(service: Any, pipeline: Any, ctx: Any, file_id: str) -> str | None:
        """Embed an extracted source's chunks, then commit it to the graph (untimed).

        Runs after the timed extract-only call. The pipeline's index stage is
        the only place it generates chunk embeddings and a resumed run skips
        an already-indexed source, so the chunks are embedded here through
        the same service method that index stage calls; the resumed
        ``pipeline.run(file_id=..., skip_index=True, skip_extract=True)``
        then runs only the commit stage. Embeddings are not scored, but the
        commit stage skips chunks without them and GraphRAG search retrieves
        chunk text by vector - so a committed reference graph needs them.

        Returns:
            ``None`` on success, or the error string for the result row.
        """
        chunks, _ = ctx.storage_adapter.get_chunks_by_source(
            file_id, page=1, page_size=ctx.settings.batching.chunk_fetch_limit
        )
        if chunks:
            service._generate_embeddings(file_id, chunks)  # noqa: SLF001 - the index stage's own embedding path
        committed = pipeline.run(
            file_path=None,
            file_id=file_id,
            url=None,
            skip_index=True,
            skip_extract=True,
            skip_commit=False,
            skip_embeddings=True,
            enable_normalization=True,
            enable_vision=False,
            index_only=False,
            extract_only=False,
            extraction_depth="full",
            filtering_mode=None,
            quiet=True,
            verbose=False,
        )
        if committed.success:
            return None
        return f"commit_failed: {committed.error or 'pipeline_failed'}"

    def expected_snapshot_path(self, model: ModelConfig) -> Path:
        """Return the temp-DB snapshot path that a successful run() will produce.

        Only meaningful when ``keep_db`` is True (so the file survives cleanup).
        The orchestrator uses this to copy the scored-run's snapshot into the
        cache without re-running extraction.

        The path mirrors ``_build_temp_context``'s naming convention exactly:
        ``<data_dir>/databases/benchmark_<id>_<safe_model>_<pid>/app.db``.
        """
        from pathlib import Path

        import platformdirs

        data_dir = Path(
            os.getenv(
                "CHAOSCYPHER_DATA_DIR",
                platformdirs.user_data_dir("chaoscypher", appauthor=False),
            )
        )
        safe_model = re.sub(r"[^a-z0-9]+", "_", model.model_id.lower()).strip("_")
        db_name = f"benchmark_{self.id}_{safe_model}_{os.getpid()}"
        return data_dir / "databases" / db_name / "app.db"

    def _build_temp_context(self, model: ModelConfig) -> CLIContext:
        """Construct an isolated CLI context configured to use ``model``.

        Sets `CHAOSCYPHER_LLM_PROVIDER` so the EngineSettings default factory
        picks the right provider, builds a fresh CLIContext (bypassing the
        get_context singleton) with a per-run temp database, then mutates
        the connected settings to override the provider-specific model name.
        """
        from chaoscypher_cli.context import CLIContext

        # Per-dataset-and-model temp DB so concurrent runs are isolated even
        # if we ever parallelize.
        safe_model = re.sub(r"[^a-z0-9]+", "_", model.model_id.lower()).strip("_")
        db_name = f"benchmark_{self.id}_{safe_model}_{os.getpid()}"

        # The chat_provider field reads CHAOSCYPHER_LLM_PROVIDER at default
        # construction. Set it before instantiating CLIContext so the
        # settings pick it up.
        os.environ["CHAOSCYPHER_LLM_PROVIDER"] = model.provider

        ctx = CLIContext(database_name=db_name)
        ctx.connect()

        # After connect, override the provider-specific chat/extraction model.
        chat_field, extraction_field = _PROVIDER_MODEL_FIELDS.get(model.provider, ("", ""))
        if chat_field:
            setattr(ctx.settings.llm, chat_field, model.model)
        if extraction_field:
            setattr(ctx.settings.llm, extraction_field, model.model)

        # Pin determinism. Assign by real field name and fail loudly when the
        # settings schema moves: these were previously guarded by hasattr on
        # field names that do not exist ("temperature", "seed"), so every run
        # silently used product defaults while the result rows claimed
        # temperature 0 and a fixed seed.
        pins: tuple[tuple[str, object], ...] = (
            ("extraction_temperature", self.temperature),
            ("ai_temperature", self.temperature),
            ("seed", self.seed),
            ("thinking_for_extraction", self.thinking),
        )
        for field_name, value in pins:
            if not hasattr(ctx.settings.llm, field_name):
                msg = (
                    f"LLMSettings has no field {field_name!r}; the benchmark cannot "
                    f"pin run configuration and its results would be unreproducible."
                )
                raise AttributeError(msg)
            setattr(ctx.settings.llm, field_name, value)

        # Force LLM provider to be re-initialized on next access in case
        # CLIContext cached a provider built from the pre-mutation settings.
        ctx._llm_provider = None  # noqa: SLF001 - intentional reset
        ctx._llm_checked = False  # noqa: SLF001 - intentional reset

        return ctx

    @staticmethod
    def _integrity_counters(ctx: CLIContext, source_id: str) -> tuple[int, int]:
        """Return (chunks_truncated, chunks_aborted_by_loop) for a source.

        Core already records both: the chunk handler increments
        ``QualityCounter.LLM_CHUNKS_TRUNCATED`` whenever a chunk's LLM call
        ends with ``finish_reason == "length"``, and
        ``LLM_CHUNKS_ABORTED_BY_LOOP`` when the stream loop detector cuts a
        degenerate stream short. The benchmark simply never read them, so a
        truncated run was indistinguishable from a complete one in every
        output it produced.

        Reads through ``get_source_counters`` rather than ``get_file``: the
        latter's narrow ``load_only`` projection omits the counter columns, so
        it silently returned ``None`` for every one of them.

        Never raises: a missing row yields zeros rather than failing a run
        that otherwise succeeded.
        """
        try:
            counters = ctx.storage_adapter.get_source_counters(
                source_id=source_id, database_name=ctx.database_name
            )
        except Exception as exc:  # a bookkeeping read must not fail the run
            logger.warning("integrity_counters_unavailable", source_id=source_id, error=str(exc))
            return 0, 0
        return (
            int(counters.get("llm_chunks_truncated") or 0),
            int(counters.get("llm_chunks_aborted_by_loop") or 0),
        )

    @staticmethod
    def _estimate_per_chunk_latency(total_ms: int, chunks: int) -> list[int]:
        """Approximate per-chunk latency by even distribution.

        SourcePipeline doesn't expose per-chunk timings today - we synthesize
        a flat distribution so leaderboard p50 still has a value. Replace
        with real per-chunk timings when SourcePipeline grows that signal.
        """
        if chunks <= 0:
            return []
        per = max(1, total_ms // chunks)
        return [per] * chunks


def _remove_temp_db_dir(db_dir: Path, *, dataset_id: str) -> None:
    """Remove a benchmark temp database directory.

    The chaoscypher SQLite adapter caches engines globally by db_path
    (chaoscypher_core.adapters.sqlite.engine._engines), so
    ``CLIContext.disconnect`` only closes the session - the engine and
    its connection pool stay alive, holding the SQLite file open. On
    Windows that means a follow-up ``rmtree`` raises ``WinError 32``.
    The fix is to evict the engine from the cache, which calls
    ``engine.dispose()`` and releases all pooled connections + the file
    handle. A short retry loop with ``gc.collect()`` covers lingering
    references that the cache drop doesn't catch.

    Cleanup is best-effort - a leftover temp DB is annoying but not
    catastrophic, and surfacing a cleanup error would obscure the real
    run outcome.
    """
    db_path = db_dir / "app.db"
    try:
        from chaoscypher_core.adapters.sqlite.engine import evict_engine

        evict_engine(db_path)
    except Exception as exc:
        logger.debug(
            "extraction_dataset_engine_evict_failed",
            dataset_id=dataset_id,
            db_path=str(db_path),
            error=str(exc),
        )

    gc.collect()
    last_error: OSError | None = None
    for delay_ms in (0, 50, 200, 500, 1000):
        if delay_ms:
            time.sleep(delay_ms / 1000.0)
        try:
            shutil.rmtree(db_dir)
            logger.info(
                "extraction_dataset_temp_db_removed",
                dataset_id=dataset_id,
                db_dir=str(db_dir),
            )
            return
        except OSError as exc:
            last_error = exc
            gc.collect()  # Try once more before the next sleep.
    logger.warning(
        "extraction_dataset_temp_db_cleanup_failed",
        dataset_id=dataset_id,
        db_dir=str(db_dir),
        error=str(last_error) if last_error else "unknown",
    )


__all__ = ["ExtractionDataset"]
