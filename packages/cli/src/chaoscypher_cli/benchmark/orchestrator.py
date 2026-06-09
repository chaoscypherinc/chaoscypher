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
        chat: Async callable ``(model, prompt, context, embedder) -> str`` that
            produces a chat completion given the retrieved context.
        judge_call: Async callable ``(judge_model, prompt) -> Any`` that
            invokes the LLM judge and returns its raw response.
    """

    cache: GraphCache
    graph_provider_factory: Callable[[Path], GraphProvider]
    embed_query: Callable[[str, Any], Awaitable[list[float]]]
    vector_search: Callable[[list[float], Any, int], Awaitable[list[tuple[str, float]]]]
    graphrag_search: Callable[[str, Any], Awaitable[dict[str, Any]]]
    chat: Callable[[ModelConfig, str, dict[str, Any], Any], Awaitable[str]]
    judge_call: Callable[[ModelConfig, str], Awaitable[Any]]


async def run_full_benchmark(
    config: BenchmarkConfig,
    bundles: list[DatasetBundle],
    *,
    wiring: OrchestratorWiring,
) -> list[BenchmarkResult]:
    """Run all three stages over all bundles, returning a single result list.

    Stages are run in order: extraction → embedding → chat. Stages 2 and 3
    are skipped when their corresponding role lists (``embedders``, ``chats``)
    are absent from the config, so an extractors-only config still works.

    Args:
        config: The benchmark configuration describing which models to run for
            each stage and which datasets to include.
        bundles: Pre-resolved dataset bundles (corpus + queries + extraction
            dataset) to evaluate.
        wiring: Injected callables and adapters. Production passes real
            LLMProvider wrappers; tests inject fakes.

    Returns:
        Flat list of ``BenchmarkResult`` rows across all stages and bundles,
        in stage-then-bundle iteration order.
    """
    results: list[BenchmarkResult] = []
    for bundle in bundles:
        ext_results: list[BenchmarkResult] = []
        # Stage 1: extraction (also produces the cache snapshots used downstream).
        if config.extractors:
            import shutil

            # Keep the per-run temp DB alive so we can copy it into the cache
            # without re-running extraction a second time.
            bundle.extraction_dataset.keep_db = True  # orchestrator owns cleanup via cache
            ext_results = await run_benchmark(
                config.extractors,
                [bundle.extraction_dataset],
                config_name=config.config_name,
                seed=config.seed,
                temperature=config.temperature,
            )
            results.extend(ext_results)

            # Copy the scored-run's snapshot into the cache for stages 2/3.
            # Skip extractors whose run failed (no snapshot to copy).
            failed_extractors = {r.model_id for r in ext_results if not r.success}
            for extractor in config.extractors:
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
                    shutil.copyfile(_src, target)

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
                )
                _stamp_provenance(emb_results, extractor_id=extractor.model_id)
                results.extend(emb_results)

        # Stage 3: chat.
        if (
            config.chats
            and config.embedders
            and config.extractors
            and config.judge is not None
            and bundle.queries is not None
        ):
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
                    )
                    chat_results = await run_benchmark(
                        config.chats,
                        [chat_ds],
                        config_name=config.config_name,
                        seed=config.seed,
                        temperature=config.temperature,
                    )
                    _stamp_provenance(
                        chat_results,
                        extractor_id=extractor.model_id,
                        embedder_id=embedder.model_id,
                    )
                    results.extend(chat_results)

    return results


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


def default_wiring(*, workspace: Path) -> OrchestratorWiring:  # noqa: PLR0915 - benchmark wiring with documented helper closures
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
        ctx = CLIContext(database_name=db_dir.name)
        # Patch database_dir so the Engine opens the correct snapshot copy.
        ctx.database_dir = db_dir
        return ctx

    async def _reindex(ctx: Any, embedder: ModelConfig) -> None:
        """Re-embed all graph nodes with the candidate embedder.

        Mutates ``ctx.settings.embedding`` to target the requested embedder,
        resets the cached provider so the factory rebuilds it, then writes a
        new embedding for every node via ``index_node_embedding``.

        Uses ``batch_embed`` to match the production indexing path in
        ``sources/service.py`` and to avoid sequential per-node HTTP round-trips.
        """
        from chaoscypher_core.adapters.embedding import create_embedding_provider

        ctx.settings.embedding.provider = embedder.provider
        ctx.settings.embedding.model = embedder.model
        # Reset cached provider so the next access creates a fresh one.
        ctx._embedding_provider = None  # noqa: SLF001 - intentional reset

        provider = create_embedding_provider(ctx.settings)
        nodes = ctx.graph_repository.list_nodes(
            limit=get_settings().benchmark.reindex_node_batch_limit,
        )

        # Collect (node_id, text) pairs then batch-embed in one call,
        # matching the production path in sources/service.py.
        node_ids = [node.id for node in nodes]
        texts = [
            (node.label or "") + ". " + (getattr(node, "description", "") or "") for node in nodes
        ]
        batch_result = await provider.batch_embed(texts)
        for node_id, embedding in zip(node_ids, batch_result.embeddings, strict=True):
            ctx.search_repository.index_node_embedding(node_id, embedding)

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
            indexing_repository=None,
            source_storage=None,
            embedding_callback=ctx.embedding_service.embed,
            settings=ctx.settings,
            database_name=ctx.database_name,
        )
        return await handlers.graphrag_search(query=query)

    async def _chat(
        chat_model: ModelConfig, query: str, retrieved: dict[str, Any], ctx: Any
    ) -> str:
        """Answer the query with the candidate chat model and retrieved context."""
        from chaoscypher_core.adapters.llm.provider import LLMProvider
        from chaoscypher_core.settings import EngineSettings, LLMSettings

        # Build a minimal EngineSettings wired to the requested provider/model.
        chat_field = f"{chat_model.provider}_chat_model"
        llm_kwargs: dict[str, Any] = {"chat_provider": chat_model.provider}
        # Copy Ollama instance config from the current ctx if provider matches.
        if chat_model.provider == "ollama" and hasattr(ctx.settings.llm, "ollama_instances"):
            llm_kwargs["ollama_instances"] = ctx.settings.llm.ollama_instances
        # Carry over API keys for cloud providers.
        for key_field in ("openai_api_key", "anthropic_api_key", "gemini_api_key"):
            val = getattr(ctx.settings.llm, key_field, None)
            if val is not None:
                llm_kwargs[key_field] = val
        llm_kwargs[chat_field] = chat_model.model
        settings = EngineSettings(llm=LLMSettings(**llm_kwargs))
        provider = LLMProvider(settings=settings)

        entities = retrieved.get("graph_context", {}).get("seed_entities", [])
        entities += retrieved.get("graph_context", {}).get("related_entities", [])
        retrieved_text = "\n".join(f"- {e.get('label', e.get('name', ''))}" for e in entities)
        prompt = (
            "Answer the question using ONLY the retrieved context.\n\n"
            f"Retrieved context:\n{retrieved_text}\n\nQuestion: {query}\n\nAnswer:"
        )
        res = await provider.chat([{"role": "user", "content": prompt}])
        return str(res.content)

    async def _judge_call(judge: ModelConfig, prompt: str) -> str:
        """Send a single judge prompt and return the model's reply text."""
        from chaoscypher_core.adapters.llm.provider import LLMProvider
        from chaoscypher_core.settings import EngineSettings, LLMSettings

        chat_field = f"{judge.provider}_chat_model"
        # Let LLMSettings defaults handle provider-specific config (e.g. Ollama
        # instances) — no hardcoded URLs. This matches how _chat inherits from
        # ctx.settings.llm rather than constructing its own config.
        llm_kwargs: dict[str, Any] = {
            "chat_provider": judge.provider,
            chat_field: judge.model,
        }
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


__all__ = ["OrchestratorWiring", "default_wiring", "run_full_benchmark"]
