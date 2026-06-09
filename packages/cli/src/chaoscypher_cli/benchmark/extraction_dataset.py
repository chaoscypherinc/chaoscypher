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
    keep_db: bool = False
    """When True, preserve the per-run temp DB after the run completes.

    The orchestrator sets this to True before calling ``run`` so it can copy
    the snapshot into the GraphCache (see ``orchestrator.default_wiring``).
    When False (default), the temp DB is removed in the run's finally block.
    v7 metrics are already in the result row's metrics dict, so losing the DB
    does not lose the score breakdown.
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
                    skip_commit=True,  # benchmarking - no graph commit needed
                    skip_embeddings=True,  # benchmarking - embeddings not scored
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

            if not result.success:
                return RawOutput(
                    entities=[],
                    relationships=[],
                    latency_ms=elapsed_ms,
                    input_tokens=result.llm_total_input_tokens,
                    output_tokens=result.llm_total_output_tokens,
                    error=result.error or "pipeline_failed",
                )

            # Read back the extracted entities/relationships from the
            # dedicated per-source tables (migration 0042 retired the
            # heavy extraction_results JSON column).
            entities = ctx.storage_adapter.list_source_entities(result.file_id, ctx.database_name)
            relationships = ctx.storage_adapter.list_source_relationships(
                result.file_id, ctx.database_name
            )

            if not entities and not relationships:
                return RawOutput(
                    entities=[],
                    relationships=[],
                    latency_ms=elapsed_ms,
                    input_tokens=result.llm_total_input_tokens,
                    output_tokens=result.llm_total_output_tokens,
                    error="empty_extraction",
                )

            return RawOutput(
                entities=entities,
                relationships=relationships,
                latency_ms=elapsed_ms,
                input_tokens=result.llm_total_input_tokens,
                output_tokens=result.llm_total_output_tokens,
                error=None,
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

        # Pin determinism. These fields exist on LLMSettings (temperature,
        # seed); set them to make runs reproducible.
        if hasattr(ctx.settings.llm, "temperature"):
            ctx.settings.llm.temperature = 0.0
        if hasattr(ctx.settings.llm, "seed"):
            ctx.settings.llm.seed = 42

        # Force LLM provider to be re-initialized on next access in case
        # CLIContext cached a provider built from the pre-mutation settings.
        ctx._llm_provider = None  # noqa: SLF001 - intentional reset
        ctx._llm_checked = False  # noqa: SLF001 - intentional reset

        return ctx

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
