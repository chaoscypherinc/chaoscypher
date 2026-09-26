# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Three-stage orchestrator for full pipeline benchmark runs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_cli.benchmark.chat_dataset import GraphRAGChatDataset
from chaoscypher_cli.benchmark.embedding_dataset import EmbeddingRetrievalDataset
from chaoscypher_cli.benchmark.runner import run_benchmark
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.benchmark.chat_prompt import format_retrieved_context, grounded_chat_prompt
from chaoscypher_core.benchmark.snapshot import snapshot_sqlite as _snapshot_sqlite


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_cli.benchmark.config import BenchmarkConfig
    from chaoscypher_cli.benchmark.discovery import DatasetBundle
    from chaoscypher_cli.benchmark.graph_cache import GraphCache
    from chaoscypher_cli.benchmark.graph_provider import GraphProvider
    from chaoscypher_cli.benchmark.models import ModelConfig
    from chaoscypher_cli.benchmark.results import BenchmarkResult


logger = structlog.get_logger(__name__)


@dataclass
class OrchestratorWiring:
    """Injected callables and adapters for the orchestrator.

    Production wires these to real LLMProvider / GraphRAGToolHandlers.
    Tests inject fakes.

    Attributes:
        cache: GraphCache instance used to persist and retrieve extracted-graph
            snapshots between the extraction stage and downstream stages.
        graph_provider_factory: Callable that takes a snapshot ``Path`` and
            returns a ``GraphProvider`` wrapping the loaded graph.
        embed_query: Async callable ``(query, embedder) -> list[float]`` that
            produces a dense vector for a query string.
        vector_search: Async callable ``(vector, graph_ctx, top_k) ->
            list[tuple[id, score]]`` performing ANN search against the indexed
            graph.
        graphrag_search: Async callable ``(query, graph_ctx) -> dict`` that
            performs full GraphRAG retrieval (entity + relationship expansion).
        chat: Async callable ``(model, prompt, context, embedder)`` that
            produces a chat completion given the retrieved context: either the
            answer string or a record ``{answer, finish_reason, output_tokens,
            context_text}`` for the chat probes.
        judge_call: Async callable ``(judge_model, prompt) -> Any`` that
            invokes the LLM judge and returns its raw response.
    """

    cache: GraphCache
    graph_provider_factory: Callable[[Path], GraphProvider]
    embed_query: Callable[[str, Any], Awaitable[list[float]]]
    vector_search: Callable[[list[float], Any, int], Awaitable[list[tuple[str, float]]]]
    graphrag_search: Callable[[str, Any], Awaitable[dict[str, Any]]]
    chat: Callable[[ModelConfig, str, dict[str, Any], Any], Awaitable[str | dict[str, Any]]]
    judge_call: Callable[[ModelConfig, str], Awaitable[Any]]


async def run_full_benchmark(
    config: BenchmarkConfig,
    bundles: list[DatasetBundle],
    *,
    wiring: OrchestratorWiring,
    model_timeout: float | None = None,
    on_row: Callable[[BenchmarkResult], None] | None = None,
    reuse_cached_graph: bool = False,
) -> list[BenchmarkResult]:
    """Run all three stages over all bundles, returning a single result list.

    Stages are run in order: extraction → embedding → chat. ``model_timeout`` caps
    each (model, dataset) run and ``on_row`` receives every row as it lands,
    exactly as :func:`run_benchmark` takes them. Stages 2 and 3
    are skipped when their corresponding role lists (``embedders``, ``chats``)
    are absent from the config, so an extractors-only config still works.

    Args:
        config: The benchmark configuration describing which models to run for
            each stage and which datasets to include.
        bundles: Pre-resolved dataset bundles (corpus + queries + extraction
            dataset) to evaluate.
        wiring: Injected callables and adapters. Production passes real
            LLMProvider wrappers; tests inject fakes.
        model_timeout: Wall-clock cap in seconds for one (model, dataset) run;
            over it the row is recorded as failed and the sweep continues.
        on_row: Called with each result row as soon as it exists.
        reuse_cached_graph: When True, an extractor whose ``wiring.cache``
            slot already holds a snapshot for the bundle is not re-extracted:
            Stage 1 produces no extraction row for it, and Stages 2 and 3 read
            the cached graph, so their rows are comparable with the run that
            built it. Extractors without a cached slot are extracted as usual.

    Returns:
        Flat list of ``BenchmarkResult`` rows across all stages and bundles,
        in stage-then-bundle iteration order.
    """
    results: list[BenchmarkResult] = []
    for bundle in bundles:
        ext_results: list[BenchmarkResult] = []
        # Stage 1: extraction (also produces the cache snapshots used downstream).
        to_extract = (
            _extractors_without_cached_graph(config.extractors or [], bundle, wiring.cache)
            if reuse_cached_graph
            else list(config.extractors or [])
        )
        if to_extract:
            # Keep the per-run temp DB alive so we can copy it into the cache
            # without re-running extraction a second time.
            bundle.extraction_dataset.keep_db = True  # orchestrator owns cleanup via cache
            bundle.extraction_dataset.commit_graph = True  # stages 2/3 need graph nodes
            ext_results = await run_benchmark(
                to_extract,
                [bundle.extraction_dataset],
                config_name=config.config_name,
                seed=config.seed,
                temperature=config.temperature,
                model_timeout=model_timeout,
                on_row=on_row,
            )
            results.extend(ext_results)

            # Copy the scored-run's snapshot into the cache for stages 2/3.
            # Skip extractors whose run failed (no snapshot to copy).
            failed_extractors = {r.model_id for r in ext_results if not r.success}
            for extractor in to_extract:
                if extractor.model_id in failed_extractors:
                    logger.warning(
                        "orchestrator_skipping_cache_for_failed_extractor",
                        extractor_id=extractor.model_id,
                        corpus_id=bundle.id,
                    )
                    continue

                src = bundle.extraction_dataset.expected_snapshot_path(extractor)

                async def _build(target: Path, _src: Path = src) -> None:
                    """Copy the just-built extraction snapshot into the cache slot."""
                    if not _src.exists():  # noqa: ASYNC240 - one-time bench scaffolding; offloading to anyio is unjustified overhead
                        msg = (
                            f"orchestrator expected snapshot at {_src} but it's missing; "
                            "did extraction fail or was keep_db not honored?"
                        )
                        raise RuntimeError(msg)
                    # SQLite's online backup, not a file copy: the kept database
                    # runs in WAL mode, so its rows live in app.db-wal until a
                    # checkpoint and a bare copyfile produced a 4 KB empty
                    # snapshot (2026-09-25: every chat question retrieved nothing).
                    _snapshot_sqlite(_src, target)

                await wiring.cache.get_or_build(
                    corpus_id=bundle.id,
                    corpus_version=bundle.version,
                    extractor=extractor,
                    builder=_build,
                )

        # Stage 2: embedding.
        if config.embedders and config.extractors and bundle.queries is not None:
            failed_extractors = {r.model_id for r in ext_results if not r.success}
            for extractor in config.extractors:
                if extractor.model_id in failed_extractors:
                    logger.warning(
                        "orchestrator_skipping_embedding_for_failed_extractor",
                        extractor_id=extractor.model_id,
                        corpus_id=bundle.id,
                    )
                    continue
                snapshot = await wiring.cache.get_or_build(
                    corpus_id=bundle.id,
                    corpus_version=bundle.version,
                    extractor=extractor,
                    builder=_noop_builder,
                )
                provider = wiring.graph_provider_factory(snapshot)
                emb_ds = EmbeddingRetrievalDataset(
                    id=f"{bundle.id}__emb__{extractor.model_id.replace('/', '_')}",
                    version=bundle.version,
                    corpus_id=bundle.id,
                    queries=bundle.queries,
                    graph_provider=provider,
                    embed_query=wiring.embed_query,
                    vector_search=wiring.vector_search,
                    top_k=10,
                    source=bundle.source,
                )
                emb_results = await run_benchmark(
                    config.embedders,
                    [emb_ds],
                    config_name=config.config_name,
                    seed=config.seed,
                    temperature=config.temperature,
                    model_timeout=model_timeout,
                    on_row=on_row,
                )
                _stamp_provenance(emb_results, extractor_id=extractor.model_id)
                results.extend(emb_results)

        # Stage 3: chat.
        # A judge is optional: without one the chat stage is scored by the
        # pass/fail chat probes (ChatProbeScorer).
        if config.chats and config.embedders and config.extractors and bundle.queries is not None:
            failed_extractors = {r.model_id for r in ext_results if not r.success}
            for extractor in config.extractors:
                if extractor.model_id in failed_extractors:
                    logger.warning(
                        "orchestrator_skipping_chat_for_failed_extractor",
                        extractor_id=extractor.model_id,
                        corpus_id=bundle.id,
                    )
                    continue
                snapshot = await wiring.cache.get_or_build(
                    corpus_id=bundle.id,
                    corpus_version=bundle.version,
                    extractor=extractor,
                    builder=_noop_builder,
                )
                for embedder in config.embedders:
                    provider = wiring.graph_provider_factory(snapshot)
                    chat_ds = GraphRAGChatDataset(
                        id=(
                            f"{bundle.id}__chat__"
                            f"{extractor.model_id.replace('/', '_')}__"
                            f"{embedder.model_id.replace('/', '_')}"
                        ),
                        version=bundle.version,
                        corpus_id=bundle.id,
                        queries=bundle.queries,
                        graph_provider=provider,
                        graphrag_search=wiring.graphrag_search,
                        chat=wiring.chat,
                        judge=config.judge,
                        judge_call=wiring.judge_call,
                        source=bundle.source,
                        embedder=embedder,
                    )
                    chat_results = await run_benchmark(
                        config.chats,
                        [chat_ds],
                        config_name=config.config_name,
                        seed=config.seed,
                        temperature=config.temperature,
                        model_timeout=model_timeout,
                        on_row=on_row,
                    )
                    _stamp_provenance(
                        chat_results,
                        extractor_id=extractor.model_id,
                        embedder_id=embedder.model_id,
                    )
                    results.extend(chat_results)

    return results


def _extractors_without_cached_graph(
    extractors: list[ModelConfig], bundle: DatasetBundle, cache: GraphCache
) -> list[ModelConfig]:
    """Return the extractors whose graph for ``bundle`` is not in ``cache`` yet.

    Each extractor left out is logged as reusing its cached graph.
    """
    to_extract: list[ModelConfig] = []
    for extractor in extractors:
        if cache.has(corpus_id=bundle.id, corpus_version=bundle.version, extractor=extractor):
            logger.info(
                "orchestrator_reusing_cached_graph",
                corpus_id=bundle.id,
                extractor_id=extractor.model_id,
                key=cache.key_for(
                    corpus_id=bundle.id, corpus_version=bundle.version, extractor=extractor
                ),
            )
        else:
            to_extract.append(extractor)
    return to_extract


async def _noop_builder(target: Path) -> None:
    """Cache lookups that should be hits raise RuntimeError on miss."""
    msg = f"orchestrator cache miss with no rebuild path: {target}"
    raise RuntimeError(msg)


def _stamp_provenance(rows: list[BenchmarkResult], **provenance: str) -> None:
    """Stamp the originating extractor/embedder ids onto downstream result rows.

    ``BenchmarkResult.metrics`` is a mutable dict on a non-frozen dataclass, so
    the in-place update is safe. Lets the composite join retrieval/chat scores
    back to the extractor (and embedder) that produced the graph being scored.
    """
    for r in rows:
        r.metrics.update(provenance)


def _provider_llm_kwargs(model: ModelConfig, source_llm: Any) -> dict[str, Any]:
    """Build LLMSettings kwargs for a benchmark model, inheriting runtime config.

    Copies Ollama instance config (for a local model) and the cloud API keys
    from ``source_llm`` so yaml-configured credentials reach the constructed
    provider — bare LLMSettings would fall back to env-var defaults only.
    """
    llm_kwargs: dict[str, Any] = {
        "chat_provider": model.provider,
        f"{model.provider}_chat_model": model.model,
    }
    if model.provider == "ollama" and getattr(source_llm, "ollama_instances", None):
        llm_kwargs["ollama_instances"] = source_llm.ollama_instances
    for key_field in ("openai_api_key", "anthropic_api_key", "gemini_api_key"):
        val = getattr(source_llm, key_field, None)
        if val is not None:
            llm_kwargs[key_field] = val
    return llm_kwargs


def default_wiring(*, workspace: Path) -> OrchestratorWiring:
    """Build production OrchestratorWiring against real ChaosCypher infra.

    All callables close over ``workspace`` to persist the graph cache and
    reindex copies between stages. The cache lives at
    ``workspace / "graph_cache"``; reindex workspace copies live at
    ``workspace / "graph_workspace"``.

    Args:
        workspace: Root directory for the benchmark run artefacts. Will be
            created if it does not exist.

    Returns:
        A fully wired :class:`OrchestratorWiring` ready to pass to
        :func:`run_full_benchmark`.
    """
    from chaoscypher_cli.benchmark.graph_cache import GraphCache
    from chaoscypher_cli.benchmark.graph_provider import GraphProvider

    cache = GraphCache(root=workspace / "graph_cache")

    def _ctx_factory(db_path: Path) -> Any:
        """Create a CLIContext pointed at an existing snapshot DB.

        GraphProvider copies the snapshot to ``db_path`` (e.g.
        ``.bench_workspace/graph_abc12345/app.db``). CLIContext expects the
        database to live at ``database_dir / "app.db"`` where
        ``database_dir = data_dir / "databases" / database_name``. Rather
        than restructuring the path we construct the context and then patch
        ``database_dir`` directly before ``connect()`` is called.
        """
        from chaoscypher_cli.context import CLIContext

        db_dir = db_path.parent
        # GraphProvider names the copy's directory after the database_name the
        # snapshot's rows are scoped by, so the engine (which names the
        # database after its directory) reads them.
        ctx = CLIContext(database_name=db_dir.name)
        # Patch database_dir so the Engine opens the correct snapshot copy.
        ctx.database_dir = db_dir
        return ctx

    async def _reindex(ctx: Any, embedder: ModelConfig) -> None:
        """Re-embed all graph nodes and source chunks with the candidate embedder.

        Mutates ``ctx.settings.embedding`` to target the requested embedder,
        resets the cached provider so the factory rebuilds it, then hands the
        re-embedding to Core's :func:`reindex_graph` (nodes with
        ``batch_embed``, as the production indexing path does, then every
        source's chunks: the committed extraction wrote them with the
        settings' default embedder, so without this, chunk retrieval would
        compare a query vector from the stage embedder against chunk vectors
        from a different model).
        """
        from chaoscypher_core.adapters.embedding import create_embedding_provider
        from chaoscypher_core.benchmark.reindex import reindex_graph

        ctx.settings.embedding.provider = embedder.provider
        ctx.settings.embedding.model = embedder.model
        # Reset cached provider so the next access creates a fresh one.
        ctx._embedding_provider = None  # noqa: SLF001 - intentional reset

        provider = create_embedding_provider(ctx.settings)
        await reindex_graph(
            ctx,
            provider,
            settings=ctx.settings,
            node_limit=get_settings().benchmark.reindex_node_batch_limit,
        )

    def _graph_provider_factory(snapshot: Path) -> GraphProvider:
        return GraphProvider(
            snapshot_path=snapshot,
            ctx_factory=_ctx_factory,
            reindex=_reindex,
            workspace=workspace / "graph_workspace",
        )

    async def _embed_query(text: str, ctx: Any) -> list[float]:
        """Embed a query string with the context's embedding service."""
        provider = ctx.embedding_service
        result = await provider.embed(text)
        return list(result.embedding)

    async def _vector_search(vec: list[float], ctx: Any, k: int) -> list[tuple[str, float]]:
        """Run a top-k vector search against the context's search repository."""
        hits = ctx.search_repository.vector_search(vec, k=k)
        return [(item_id, float(score)) for item_id, score in hits]

    async def _graphrag_search(query: str, ctx: Any) -> dict[str, Any]:
        """Run the production GraphRAG search handler against the context."""
        from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
            GraphRAGToolHandlers,
        )

        handlers = GraphRAGToolHandlers(
            graph_repository=ctx.graph_repository,
            search_repository=ctx.search_repository,
            # The storage adapter serves both ports, as in the product's
            # bootstrap. With None here the handler returns no chunks at all,
            # so the chat model only ever saw entity names (2026-09-25).
            indexing_repository=ctx.storage_adapter,
            source_storage=ctx.storage_adapter,
            embedding_callback=ctx.embedding_service.embed,
            settings=ctx.settings,
            database_name=ctx.database_name,
        )
        return await handlers.graphrag_search(query=query)

    async def _chat(
        chat_model: ModelConfig, query: str, retrieved: dict[str, Any], ctx: Any
    ) -> dict[str, Any]:
        """Answer the query with the candidate chat model and the retrieved context.

        Returns the answer with what the chat probes check: completion,
        output tokens and the context text the model actually saw. Thinking is
        on, as the product runs chat models.
        """
        from chaoscypher_core.adapters.llm.provider import LLMProvider
        from chaoscypher_core.settings import EngineSettings, LLMSettings

        # Build a minimal EngineSettings wired to the requested provider/model,
        # inheriting instance config + cloud API keys from the current ctx.
        llm_kwargs = _provider_llm_kwargs(chat_model, ctx.settings.llm)
        settings = EngineSettings(llm=LLMSettings(**llm_kwargs))
        provider = LLMProvider(settings=settings)
        context_text = format_retrieved_context(retrieved)
        prompt = grounded_chat_prompt(query, context_text)
        res = await provider.chat([{"role": "user", "content": prompt}], enable_thinking=True)
        usage = getattr(res, "usage", None)
        return {
            "answer": str(res.content),
            "finish_reason": getattr(res, "finish_reason", None) or "stop",
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "context_text": context_text,
        }

    async def _judge_call(judge: ModelConfig, prompt: str) -> str:
        """Send a single judge prompt and return the model's reply text."""
        from chaoscypher_core.adapters.llm.provider import LLMProvider
        from chaoscypher_core.app_config import get_settings
        from chaoscypher_core.settings import EngineSettings, LLMSettings

        # Inherit runtime config from the operator's settings like _chat does
        # (judge_call's wiring contract carries no ctx, so read the canonical
        # global settings): Ollama instance config for a local judge, and the
        # cloud API keys for a commercial one. Building bare LLMSettings here
        # left the keys to the env-var default_factory, so a yaml-configured
        # key 401'd every judge call.
        llm_kwargs = _provider_llm_kwargs(judge, get_settings().llm)
        settings = EngineSettings(llm=LLMSettings(**llm_kwargs))
        provider = LLMProvider(settings=settings)
        res = await provider.chat([{"role": "user", "content": prompt}])
        return str(res.content)

    return OrchestratorWiring(
        cache=cache,
        graph_provider_factory=_graph_provider_factory,
        embed_query=_embed_query,
        vector_search=_vector_search,
        graphrag_search=_graphrag_search,
        chat=_chat,
        judge_call=_judge_call,
    )


__all__ = [
    "OrchestratorWiring",
    "default_wiring",
    "format_retrieved_context",
    "run_full_benchmark",
]
