# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Three-stage orchestrator for full pipeline benchmark runs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel

from chaoscypher_cli.benchmark.chat_dataset import GraphRAGChatDataset
from chaoscypher_cli.benchmark.config import DEFAULT_SEED, DEFAULT_TEMPERATURE
from chaoscypher_cli.benchmark.determinism import pin_llm_determinism
from chaoscypher_cli.benchmark.embedding_dataset import EmbeddingRetrievalDataset
from chaoscypher_cli.benchmark.graph_cache import GraphCacheMissError
from chaoscypher_cli.benchmark.runner import run_benchmark
from chaoscypher_core.app_config import get_settings
from chaoscypher_core.benchmark.chat_prompt import format_retrieved_context, grounded_chat_prompt
from chaoscypher_core.benchmark.chat_run import chat_context_window
from chaoscypher_core.benchmark.retrieval import (
    embedder_slug,
    retrieval_dataset_suffix,
)
from chaoscypher_core.benchmark.snapshot import snapshot_sqlite as _snapshot_sqlite
from chaoscypher_core.cache.build import ModelRef, pinned_builds
from chaoscypher_core.cache.call_site import call_site


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.config import BenchmarkConfig
    from chaoscypher_cli.benchmark.discovery import DatasetBundle
    from chaoscypher_cli.benchmark.graph_cache import GraphCache
    from chaoscypher_cli.benchmark.graph_provider import GraphProvider
    from chaoscypher_cli.benchmark.models import ModelConfig
    from chaoscypher_cli.benchmark.results import BenchmarkResult
    from chaoscypher_core.benchmark.retrieval import NaiveRetrieverFactory
    from chaoscypher_core.services.workflows.tools.engine.handlers.question_shape import (
        ChatCallback,
    )


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
        judge_call: Async callable ``(judge_model, prompt, *, seed, temperature) -> Any``
            that invokes the LLM judge and returns its raw response; the
            orchestrator passes the run's seed and temperature
            (:func:`pinned_judge_call`), so the judge decodes as the row records.
        hybrid_search: Async callable ``(query, graph_ctx) -> dict`` with the
            hybrid chunk search alone (``{"chunks": [...]}``): the chat
            stage's ``hybrid`` rung. None when the config never asks for it.
        naive_retriever: The naive rung's index factory (the CLI's
            llama-index extra); None when the config never asks for it.
        naive_index_root: Where the naive rung caches its indexes.
    """

    cache: GraphCache
    graph_provider_factory: Callable[[Path], GraphProvider]
    embed_query: Callable[[str, Any], Awaitable[list[float]]]
    vector_search: Callable[[list[float], Any, int], Awaitable[list[tuple[str, float]]]]
    graphrag_search: Callable[[str, Any], Awaitable[dict[str, Any]]]
    chat: Callable[[ModelConfig, str, dict[str, Any], Any], Awaitable[str | dict[str, Any]]]
    judge_call: Callable[..., Awaitable[Any]]
    hybrid_search: Callable[[str, Any], Awaitable[dict[str, Any]]] | None = None
    naive_retriever: NaiveRetrieverFactory | None = None
    naive_index_root: Path | None = None


async def run_full_benchmark(
    config: BenchmarkConfig,
    bundles: list[DatasetBundle],
    *,
    wiring: OrchestratorWiring,
    model_timeout: float | None = None,
    on_row: Callable[[BenchmarkResult], None] | None = None,
    reuse_cached_graph: bool = False,
    sweep_set: str | None = None,
) -> list[BenchmarkResult]:
    """Run all three stages over all bundles, returning a single result list.

    Each bundle runs its stages in order: extraction → embedding → chat.
    ``model_timeout`` caps each (model, dataset) run and ``on_row`` receives
    every row as it lands, exactly as :func:`run_benchmark` takes them. Stages
    2 and 3 are skipped when their corresponding role lists (``embedders``,
    ``chats``) are absent from the config, so an extractors-only config still
    works; stage 2 is also skipped under pinned retrieval (``retrieval_pins``).

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
        reuse_cached_graph: When True, no extractor is re-extracted: Stage 1
            produces no extraction row, and Stages 2 and 3 read the cached
            graph, so their rows are comparable with the run that built it.
            An extractor whose slot is empty raises
            :class:`~chaoscypher_cli.benchmark.graph_cache.GraphCacheMissError`
            before anything runs, naming the slot and its recipes (until
            2026-10-06 it was extracted again, silently, for hours).
        sweep_set: A ``SWEEP_SET`` context-shape variant
            (``graphrag.<field>=<value>;...``) every chat row applies on top
            of its model's profile and records
            (:func:`~chaoscypher_core.benchmark.chat_run.resolve_run_graphrag`).

    Returns:
        Flat list of ``BenchmarkResult`` rows across all stages and bundles,
        in bundle-then-stage iteration order.
    """
    results: list[BenchmarkResult] = []
    for bundle in bundles:
        ext_results: list[BenchmarkResult] = []
        # Stage 1: extraction (also produces the cache snapshots used downstream).
        to_extract = _extractors_to_run(config, bundle, wiring.cache, reuse=reuse_cached_graph)
        if to_extract:
            # Keep the per-run temp DB alive so we can copy it into the cache
            # without re-running extraction a second time.
            bundle.extraction_dataset.keep_db = True  # orchestrator owns cleanup via cache
            bundle.extraction_dataset.commit_graph = True  # stages 2/3 need graph nodes
            # the merges the cache key names are the merges the graph gets
            bundle.extraction_dataset.dedup_embedder = wiring.cache.dedup_embedder
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
                    seed=config.seed,
                    temperature=config.temperature,
                    builder=_build,
                    # a fresh extraction replaces a slot install.py adopted (audit wave 3)
                    building=True,
                )

        # Stage 2: embedding. Skipped when the chat stage serves pinned
        # retrieval: nothing in a pinned run embeds, so the embedder never
        # loads on a card that is serving a chat model.
        if (
            config.embedders
            and config.extractors
            and bundle.queries is not None
            and not config.retrieval_pins
        ):
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
                    seed=config.seed,
                    temperature=config.temperature,
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
                    seed=config.seed,
                    temperature=config.temperature,
                    builder=_noop_builder,
                )
                for embedder in config.embedders:
                    provider = wiring.graph_provider_factory(snapshot)
                    chat_ds = GraphRAGChatDataset(
                        id=(
                            f"{bundle.id}__chat__"
                            f"{embedder_slug(extractor.model_id)}__"
                            f"{embedder_slug(embedder.model_id)}"
                            f"{retrieval_dataset_suffix(config.retrieval)}"
                        ),
                        version=bundle.version,
                        corpus_id=bundle.id,
                        # the packs' label, as the pack and its slot record it (pack_domain)
                        domain=bundle.extraction_domain,
                        queries=bundle.queries,
                        graph_provider=provider,
                        graphrag_search=wiring.graphrag_search,
                        chat=wiring.chat,
                        judge=config.judge,
                        judge_call=pinned_judge_call(
                            wiring.judge_call,
                            seed=config.chat_seed_or_seed,
                            temperature=config.temperature,
                        ),
                        source=bundle.source,
                        embedder=embedder,
                        retrieval=config.retrieval,
                        hybrid_search=wiring.hybrid_search,
                        naive_retriever=wiring.naive_retriever,
                        naive_index_root=wiring.naive_index_root,
                        pins_dir=Path(config.retrieval_pins) if config.retrieval_pins else None,
                        sweep_set=sweep_set,
                        queries_file=str(bundle.queries_path) if bundle.queries_path else None,
                        graph_provenance=_graph_provenance(
                            wiring.cache,
                            bundle,
                            extractor,
                            seed=config.seed,
                            temperature=config.temperature,
                        ),
                    )
                    chat_results = await run_benchmark(
                        config.chats,
                        [chat_ds],
                        config_name=config.config_name,
                        # The chat decoding seed; the graph keeps the extraction seed, so a
                        # second baseline pass at another seed (the adoption rule's band)
                        # never re-extracts or lands in another cache slot.
                        seed=config.chat_seed_or_seed,
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
    extractors: list[ModelConfig],
    bundle: DatasetBundle,
    cache: GraphCache,
    *,
    seed: int,
    temperature: float,
) -> list[ModelConfig]:
    """Return the extractors whose graph for ``bundle`` is not in ``cache`` yet.

    The lookup is keyed on the run's decoding pins too: a seed-7 run must not
    reuse the graph a seed-42 run produced. Each extractor left out is logged
    as reusing its cached graph.
    """
    to_extract: list[ModelConfig] = []
    for extractor in extractors:
        if cache.has(
            corpus_id=bundle.id,
            corpus_version=bundle.version,
            extractor=extractor,
            seed=seed,
            temperature=temperature,
        ):
            logger.info(
                "orchestrator_reusing_cached_graph",
                corpus_id=bundle.id,
                extractor_id=extractor.model_id,
                key=cache.key_for(
                    corpus_id=bundle.id,
                    corpus_version=bundle.version,
                    extractor=extractor,
                    seed=seed,
                    temperature=temperature,
                ),
            )
        else:
            to_extract.append(extractor)
    return to_extract


def _extractors_to_run(
    config: BenchmarkConfig, bundle: DatasetBundle, cache: GraphCache, *, reuse: bool
) -> list[ModelConfig]:
    """Stage 1's extractors: all of them, or none under ``reuse`` (every slot must be filled).

    Raises:
        GraphCacheMissError: Under ``reuse``, when an extractor's slot is empty.
    """
    extractors = list(config.extractors or [])
    if not reuse:
        return extractors
    missing = _extractors_without_cached_graph(
        extractors, bundle, cache, seed=config.seed, temperature=config.temperature
    )
    if missing:
        raise GraphCacheMissError(_cache_miss_message(missing, bundle, cache, config=config))
    return []


def _open_at_stored_size(ctx: Any, db_path: Path) -> None:
    """Make ``ctx`` connect at the vector size its copy's index was written at.

    A copy indexed at an embedder's native size opens at that size: the
    engine's search repository, built at ``search.vector_dimensions`` on
    connect, would otherwise drop and recreate the copy's vector tables.
    """
    stored = stored_vector_dimensions(db_path)
    model = stored_embedding_model(db_path)
    if stored is None and model is None:
        return
    build_settings = ctx._create_engine_settings  # noqa: SLF001 - the one seam before connect

    def _at_stored_size() -> Any:
        """The context's settings, at the size and the embedder the copy's vectors were written with.

        Both, or the repository flags the index on connect: it compares the
        settings' embedder with the stored one, and a copy opened with the
        data directory's default model carried needs_full_reindex into every
        retrieval of a chat run (2026-10-07, the first v4 smoke rows).
        """
        settings = build_settings()
        if stored is not None:
            settings.search.vector_dimensions = stored
        if model is not None:
            settings.embedding.model = model
        return settings

    ctx._create_engine_settings = _at_stored_size  # noqa: SLF001


def select_embedder(ctx: Any, embedder: ModelConfig, *, rebuild: bool = False) -> None:
    """Point a connected ctx's embedding settings and index size at ``embedder``.

    Mutates ``ctx.settings.embedding`` and ``search.vector_dimensions`` (the
    embedder's native size, :func:`~chaoscypher_core.benchmark.reference.embedder_dimensions`,
    as the reference pack's index uses; until 2026-10-06 the local path
    indexed and queried at the 1024 base, the first 1024 of a 2560-d model,
    audit R1) and resets the cached provider so the factory rebuilds it at
    that size. Called on every run's ctx (the index is built once and
    copied, see :class:`GraphProvider`), and with ``rebuild`` on the copy
    about to be re-embedded, whose search repository is recreated at that
    size (its vector tables dropped and made again).

    Raises:
        RuntimeError: Without ``rebuild``, when the copy's index was written
            at another size: recreating it would empty the index the run reads.
    """
    from chaoscypher_core.benchmark.reference import embedder_dimensions

    dims = embedder_dimensions(embedder.model_id)
    ctx.settings.embedding.provider = embedder.provider
    ctx.settings.embedding.model = embedder.model
    ctx.settings.search.vector_dimensions = dims
    current = getattr(ctx.search_repository, "vector_dim", None)
    if isinstance(current, int) and current != dims:
        if not rebuild:
            msg = (
                f"this graph copy was indexed at {current} dimensions and {embedder.model_id} "
                f"embeds at {dims}: delete the kept index (graph_workspace/indexed) so it is "
                "rebuilt at the embedder's size"
            )
            raise RuntimeError(msg)
        from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository

        ctx._engine.search_repository = SearchRepository(  # noqa: SLF001 - the engine's own slot
            engine=ctx.search_repository._engine,  # noqa: SLF001 - the same app.db
            vector_dim=dims,
            embedding_model=embedder.model,
        )
    # Reset cached provider so the next access creates a fresh one.
    ctx._embedding_provider = None  # noqa: SLF001 - intentional reset


def _stored_search_metadata(db_path: Path, key: str) -> str | None:
    """One ``search_metadata`` value of a database, or None (no file, no index yet, no row)."""
    import sqlite3

    if not db_path.exists():
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT value FROM search_metadata WHERE key = :k", {"k": key}).fetchone()
    except sqlite3.Error:  # no search index yet
        return None
    finally:
        con.close()
    return str(row[0]) if row and row[0] is not None else None


def stored_vector_dimensions(db_path: Path) -> int | None:
    """The vector size a database's search index was written at (``search_metadata``), or None."""
    value = _stored_search_metadata(db_path, "vector_dim")
    return int(value) if value is not None and value.isdigit() else None


def stored_embedding_model(db_path: Path) -> str | None:
    """The embedding model a database's search index was written with, or None."""
    return _stored_search_metadata(db_path, "embedding_model") or None


def _cache_miss_message(
    missing: list[ModelConfig], bundle: DatasetBundle, cache: GraphCache, *, config: BenchmarkConfig
) -> str:
    """Why ``REUSE_GRAPH`` cannot run: each empty slot with the recipes that keyed it."""
    slots = []
    for extractor in missing:
        key = cache.key_for(
            corpus_id=bundle.id,
            corpus_version=bundle.version,
            extractor=extractor,
            seed=config.seed,
            temperature=config.temperature,
        )
        slots.append(f"{extractor.model_id} -> {cache.slot_path(key) / 'app.db'}")
    return (
        f"REUSE_GRAPH: no cached graph for {bundle.id} {bundle.version} "
        f"({'; '.join(slots)}; seed {config.seed}, temperature {config.temperature}, "
        f"dedup {cache.dedup_embedder}, resolution {cache.resolution}, "
        f"extraction recipe {cache.recipe_for(bundle.id)}). Install the pack whose graph this is "
        "(internal/benchmarks/reference/install.py; its manifest must record this extraction "
        "recipe) or run without REUSE_GRAPH to extract it."
    )


def pinned_judge_call(
    judge_call: Callable[..., Awaitable[Any]], *, seed: int, temperature: float
) -> Callable[[ModelConfig, str], Awaitable[Any]]:
    """The wiring's judge call with the run's seed and temperature bound (audit wave 2).

    The chat dataset calls ``judge_call(judge, prompt)``; the decoding the
    row records travels with this closure into the wiring's call, which pins
    it through :func:`~chaoscypher_cli.benchmark.determinism.pin_llm_determinism`.
    """

    async def call(judge: ModelConfig, prompt: str) -> Any:
        """Ask the judge at the run's seed and temperature."""
        return await judge_call(judge, prompt, seed=seed, temperature=temperature)

    return call


def _graph_provenance(
    cache: GraphCache,
    bundle: DatasetBundle,
    extractor: ModelConfig,
    *,
    seed: int,
    temperature: float,
) -> dict[str, Any]:
    """What a chat row records about the graph it read: :meth:`GraphCache.served_graph` of its slot.

    The snapshot's sha256, the extraction domain, corpus sha256, resolver
    and extraction recipes the slot's ``slot.json`` records (for an adopted
    slot, the pack's own), the slot's key, and the pack an adopted slot was
    seeded from. Audit wave 5: the domain and corpus sha256 came from the
    dataset, not the slot, so a row served a generic-domain graph from an
    adopted slot was recorded as literary.
    """
    key = cache.key_for(
        corpus_id=bundle.id,
        corpus_version=bundle.version,
        extractor=extractor,
        seed=seed,
        temperature=temperature,
    )
    return cache.served_graph(key)


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


def _provider_llm_settings(model: ModelConfig, source_llm: Any) -> Any:
    """The run's ``LLMSettings`` with ``model`` as the chat model: a copy of ``source_llm``, not a rebuild.

    Until 2026-10-06 this built a bare ``LLMSettings`` from four fields
    (provider, model, Ollama instances, API keys), so the seed, the
    temperature, ``ollama_num_ctx`` and every other pinned decoding setting
    of the run fell back to the product defaults (temperature 0.3, no seed)
    while the result row recorded seed 42 / temperature 0. Copying the
    context's settings keeps every pin the dataset set
    (:func:`~chaoscypher_cli.benchmark.determinism.pin_llm_determinism`).
    """
    from chaoscypher_core.settings import LLMSettings

    # A real LLMSettings copies whole; a stub (the tests' SimpleNamespace) is rebuilt from
    # the four fields it may carry.
    llm = (
        source_llm.model_copy(deep=True)
        if callable(getattr(source_llm, "model_copy", None))
        else LLMSettings(**_provider_llm_kwargs_legacy(model, source_llm))
    )
    llm.chat_provider = model.provider
    setattr(llm, f"{model.provider}_chat_model", model.model)
    return llm


RUN_SECTIONS: tuple[str, ...] = ("paths", "cache", "database")
"""The run's settings sections a candidate's or judge's provider is built with besides ``llm``.

Where the LLM caches live (``paths``, ``cache``: the store dir, the caps,
``require_hit`` under ``--replay``) and how their store waits on a lock
(``database``). Until the record/replay work the chat and judge providers
were built from ``EngineSettings(llm=...)`` alone, so a replay's
``require_hit`` and a configured ``cache.dir`` never reached them.
"""


def candidate_engine_settings(llm: Any, run: Any) -> Any:
    """``EngineSettings`` of ``llm`` with the run's :data:`RUN_SECTIONS` (copies), for one provider.

    ``run`` is the run's settings (a context's ``EngineSettings``, or the app
    ``Settings``); a section that is not a settings model (a test's stub) is
    left to its default.
    """
    from chaoscypher_core.settings import EngineSettings

    sections: dict[str, Any] = {
        name: section.model_copy(deep=True)
        for name in RUN_SECTIONS
        if isinstance(section := getattr(run, name, None), BaseModel)
    }
    return EngineSettings(llm=llm, **sections)


def router_chat_callback(run_settings: Any, *, seed: int | None = None) -> ChatCallback:
    """The shape router's model for a benchmark retrieval: the run's chat model, at temperature 0.

    A copy of ``run_settings.llm`` pinned to temperature 0 and ``seed``
    (None: the run's own, which the chat stage pins to the config's seed;
    :func:`~chaoscypher_cli.benchmark.determinism.pin_llm_determinism`), on
    the run's :data:`RUN_SECTIONS`, so the router's call is cacheable and a
    replay serves it from the completion cache (the handler counts it under
    the ``router`` call site). The router model is the run's configured chat
    model, never the candidate: the pins every candidate is served are built
    with it (``pin_retrieval.py``), and the retrieval wiring knows no
    candidate. The provider is built on the first call, so a run whose rules
    decide every question builds none.

    Args:
        run_settings: The run's ``EngineSettings`` (a context's, or the pin builder's).
        seed: The seed to decode at; None keeps ``run_settings.llm.seed``.

    Returns:
        A ``ChatCallback`` (:func:`~chaoscypher_core.services.workflows.tools.engine.chat_callbacks.provider_chat_callback`)
        over that provider.
    """
    from chaoscypher_core.services.workflows.tools.engine.chat_callbacks import (
        provider_chat_callback,
    )

    built: list[ChatCallback] = []

    async def chat(
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """One router completion on the lazily built provider."""
        if not built:
            from chaoscypher_core.adapters.llm.provider import LLMProvider

            llm = run_settings.llm.model_copy(deep=True)
            pin_llm_determinism(llm, seed=llm.seed if seed is None else seed, temperature=0.0)
            provider = LLMProvider(settings=candidate_engine_settings(llm, run_settings))
            built.append(provider_chat_callback(provider))
        return await built[0](messages, temperature=temperature, max_tokens=max_tokens)

    return chat


def pin_router(run_settings: Any) -> tuple[ChatCallback, dict[str, Any] | None]:
    """The pin builder's shape router and what its pin header records about it.

    One place for the router the pins are built with (``pin_retrieval.py``)
    and the tools that retrieve as the pins do (``retrieval_mode_check.py``,
    ``router_check.py``, ``embedder_retrieval_check.py``): the run's chat
    model at temperature 0 and :data:`~chaoscypher_cli.benchmark.config.DEFAULT_SEED`
    (:func:`router_chat_callback`), and its record
    (:func:`~chaoscypher_core.benchmark.pins.router_model_record`; None when
    ``graphrag.router`` never asks a model).
    """
    from chaoscypher_core.benchmark.pins import router_model_record

    return (
        router_chat_callback(run_settings, seed=DEFAULT_SEED),
        router_model_record(run_settings, seed=DEFAULT_SEED),
    )


def _provider_llm_kwargs_legacy(model: ModelConfig, source_llm: Any) -> dict[str, Any]:
    """Kwargs for a context whose ``llm`` is not an ``LLMSettings`` (the tests' stubs): provider, model, instances, keys."""
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


async def _benchmark_chat(
    chat_model: ModelConfig, query: str, retrieved: dict[str, Any], ctx: Any
) -> dict[str, Any]:
    """Answer the query with the candidate chat model and the retrieved context.

    Returns the answer with what the chat probes check: completion,
    output tokens and the context text the model actually saw. Thinking is
    on, as the product runs chat models, and the call goes through the
    product's :func:`guarded_chat`: a looping stream is stopped and, in the
    thinking phase, asked again once without thinking (``finish_reason``
    ``"loop"``, ``loop_retried``), as the app's chat does.

    Also returned, for the row (audit H9, H10): ``prompt_tokens`` (the
    stream's usage; the estimate when the provider reports none, with
    ``prompt_tokens_estimated``), ``num_ctx`` and ``num_predict`` (the
    output cap :func:`num_predict_cap` sent, so prompt plus answer fit the
    window), and for a stopped stream ``loop_phase``, ``loop_reason``,
    ``thinking_chars`` and, after a retry, ``thinking_retried``.
    """
    from chaoscypher_core.adapters.llm.provider import LLMProvider
    from chaoscypher_core.streaming.chat.loop import guarded_chat
    from chaoscypher_core.streaming.chat.sinks import CollectingSink
    from chaoscypher_core.utils.tokens import estimate_tokens

    # The run's settings with the candidate as chat model: the seed, temperature
    # and num_ctx the dataset pinned on ctx travel with it (determinism.py), and
    # the run's caches (a replay refuses a miss here too).
    settings = candidate_engine_settings(
        _provider_llm_settings(chat_model, ctx.settings.llm), ctx.settings
    )
    provider = LLMProvider(settings=settings)
    context_text = format_retrieved_context(retrieved)
    # `is True`: a test context's settings may be a mock, whose attributes are truthy
    graphrag = getattr(ctx.settings, "graphrag", None)
    ask = getattr(graphrag, "ask_for_citations", False) is True
    policy = getattr(graphrag, "answer_policy", False) is True
    prompt = grounded_chat_prompt(query, context_text, ask_for_citations=ask, answer_policy=policy)
    num_ctx = chat_context_window(settings.llm)
    estimate = estimate_tokens(prompt)
    num_predict = num_predict_cap(num_ctx, estimate, getattr(settings.llm, "ai_max_tokens", None))
    sink = CollectingSink()
    content, thinking, _, stream_error, done = await guarded_chat(
        provider,
        [{"role": "user", "content": prompt}],
        sink=sink,
        chat_id="benchmark",
        settings=ctx.settings,
        enable_thinking=True,
        max_tokens=num_predict,
    )
    if stream_error:
        raise stream_failure(chat_model, sink.of_type("error"))
    done = done or {}
    usage = done.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    record: dict[str, Any] = {
        "answer": str(content),
        "finish_reason": done.get("finish_reason") or "stop",
        "output_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        "prompt_tokens": prompt_tokens or estimate,
        "num_ctx": num_ctx,
        "num_predict": num_predict,
        "loop_retried": bool(done.get("loop_retried")),
        "context_text": context_text,
    }
    if not prompt_tokens:
        record["prompt_tokens_estimated"] = True
    first_loop = done.get("first_loop") or {}
    if done.get("finish_reason") == "loop" or first_loop:
        record["loop_phase"] = first_loop.get("phase") or done.get("loop_phase")
        record["loop_reason"] = first_loop.get("reason") or done.get("loop_reason")
        record["thinking_chars"] = int(
            first_loop.get("thinking_chars") or done.get("thinking_chars") or len(thinking or "")
        )
    if done.get("thinking_retried"):
        record["thinking_retried"] = True
    return record


def stream_failure(chat_model: ModelConfig, errors: list[dict[str, Any]]) -> Exception:
    """The exception a failed chat stream raises, from the error events its sink received.

    The last error event through the one stream classifier
    (:func:`~chaoscypher_core.utils.stream_errors.error_from_stream_chunk`):
    a missing model is an ``LLMModelError``, which
    :func:`~chaoscypher_cli.benchmark.chat_dataset.transient_failure` never
    retries; a busy server, a timeout or a dropped stream an ``LLMServiceError``
    asked again (a crashed runner once); anything else a plain ``LLMError``,
    not asked again. Until audit wave 4 every stream error was retried as a
    dropped connection; until wave 5 this kept its own code table beside the
    chat loop's. A stream that failed with no error event is a dropped one.
    """
    from chaoscypher_core.exceptions import LLMServiceError
    from chaoscypher_core.utils.stream_errors import error_from_stream_chunk

    if not errors:
        return LLMServiceError(
            provider=chat_model.provider,
            reason="the chat stream failed (no error event)",
            model=chat_model.model,
        )
    last = errors[-1]
    raw_details = last.get("error_details")
    details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
    chunk = {
        **last,
        "error_details": {"provider": chat_model.provider, "model": chat_model.model, **details},
    }
    return error_from_stream_chunk(chunk)


NUM_PREDICT_MARGIN_TOKENS = 512
"""Room left between the prompt and the context window's end: the prompt is estimated
(:func:`~chaoscypher_core.utils.tokens.estimate_tokens`, four characters a token) before the
call, so the cap keeps a margin for the estimate's error."""

NUM_PREDICT_STEP = 256
"""The cap is rounded down to a multiple of this, so a handful of distinct caps (each a
cached client in the Ollama provider) serve a run instead of one per question."""


def num_predict_cap(num_ctx: int | None, prompt_tokens: int, max_tokens: Any = None) -> int | None:
    """The output cap that keeps prompt plus answer inside the window: ``num_ctx - prompt - margin``.

    Until 2026-10-06 the chat call ran with ``num_predict`` 65536 against a
    32768 window: 204 rows stopped near 30.5k tokens and 30 produced more
    than the window, the prompt shifting out of it (audit H9); the graphrag
    rung's longer prompt left it less room than the baselines. None (the
    provider's own cap) when the provider exposes no window. Never above the
    settings' ``ai_max_tokens``; at least one :data:`NUM_PREDICT_STEP`.
    """
    if num_ctx is None:
        return None
    room = num_ctx - prompt_tokens - NUM_PREDICT_MARGIN_TOKENS
    cap = max(NUM_PREDICT_STEP, room // NUM_PREDICT_STEP * NUM_PREDICT_STEP)
    if isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and max_tokens > 0:
        cap = min(cap, max_tokens)
    return cap


def check_replayable(settings: Any) -> None:
    """Refuse a replay the run's cache settings cannot serve: the completion cache must be on.

    Raises:
        ValidationError: When ``cache.completions`` is off.
    """
    from chaoscypher_core.exceptions import ValidationError

    if not settings.cache.completions.enabled:
        msg = "a replay serves from the completion cache; cache.completions is off"
        raise ValidationError(msg, field="cache")


@contextmanager
def replaying(settings: Any, builds: Mapping[ModelRef, str] | None = None) -> Iterator[None]:
    """Run the block as a replay: the completion cache refuses a miss, and ``builds`` are pinned.

    Only completions are replayed: a reference pack ships its completion
    store (what the model said) and not its embeddings (too large, and
    deterministic — the embedder recomputes them, and the pins already carry
    the query vectors), so the embedding cache keeps its configured policy.

    ``settings`` is the run's app ``Settings`` (``get_settings()``): every
    context the run builds copies its ``cache`` section, and the chat and
    judge providers take it (:func:`candidate_engine_settings`), so each
    wrapper refuses a call it cannot serve with ``ValidationError("replay
    miss: ...")`` instead of reaching the model. ``builds`` maps
    ``(provider, model)`` to the build each model was recorded under (a
    pack's manifest ``model_build``, :func:`replay_builds`), so the replay
    keys as the record run did and asks no server for a digest. Both are
    restored on exit.

    Raises:
        ValidationError: When the completion cache is off (a replay needs its store).
    """
    check_replayable(settings)
    store = settings.cache.completions
    saved = store.require_hit
    store.require_hit = True
    try:
        with pinned_builds(builds or {}):
            yield
    finally:
        store.require_hit = saved


def replay_builds(packs: list[Any]) -> dict[ModelRef, str]:
    """``{(provider, model): build}`` of the extractor of each pack (its manifest ``model_build``).

    Raises:
        ValidationError: When a pack ships no LLM store, or two packs record
            different builds of one extractor (the replay could key only one).
    """
    from chaoscypher_core.benchmark.reference import split_model_id
    from chaoscypher_core.exceptions import ValidationError

    builds: dict[ModelRef, str] = {}
    named: dict[ModelRef, str] = {}
    for pack in packs:
        if not pack.model_build:
            msg = f"pack {pack.name!r} ships no LLM store (no model_build in its manifest)"
            raise ValidationError(msg, field="replay_pack")
        ref = split_model_id(pack.extractor)
        if ref in builds and builds[ref] != pack.model_build:
            msg = (
                f"packs {named[ref]!r} and {pack.name!r} record different builds of "
                f"{pack.extractor} ({builds[ref]} vs {pack.model_build}); replay one at a time"
            )
            raise ValidationError(msg, field="replay_pack")
        builds[ref], named[ref] = pack.model_build, pack.name
    return builds


def default_wiring(
    *, workspace: Path, dedup_embedder: str, resolution: str | None = None
) -> OrchestratorWiring:
    """Build production OrchestratorWiring against real ChaosCypher infra.

    All callables close over ``workspace`` to persist the graph cache and
    reindex copies between stages. The cache lives at
    ``workspace / "graph_cache"``; reindex workspace copies live at
    ``workspace / "graph_workspace"``.

    Args:
        workspace: Root directory for the benchmark run artefacts. Will be
            created if it does not exist.
        dedup_embedder: ``provider/model`` the extraction stage merges
            entities with (:func:`graph_cache.dedup_embedder_for`); it keys
            the cache and is applied to every extraction's settings.
        resolution: The identity resolver's recipe when the settings run it
            after commit (:func:`graph_cache.resolution_for`); keys the cache.

    Returns:
        A fully wired :class:`OrchestratorWiring` ready to pass to
        :func:`run_full_benchmark`.
    """
    from chaoscypher_cli.benchmark.graph_cache import GraphCache
    from chaoscypher_cli.benchmark.graph_provider import GraphProvider

    cache = GraphCache(
        root=workspace / "graph_cache", dedup_embedder=dedup_embedder, resolution=resolution
    )

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
        _open_at_stored_size(ctx, db_path)
        return ctx

    async def _reindex(ctx: Any, embedder: ModelConfig) -> None:
        """Re-embed all graph nodes and source chunks with the candidate embedder.

        Selects the embedder (:func:`select_embedder`), then hands the
        re-embedding to Core's :func:`reindex_graph` (nodes with
        ``batch_embed``, as the production indexing path does, then every
        source's chunks: the committed extraction wrote them with the
        settings' default embedder, so without this, chunk retrieval would
        compare a query vector from the stage embedder against chunk vectors
        from a different model).
        """
        from chaoscypher_core.adapters.embedding import create_embedding_provider
        from chaoscypher_core.benchmark.reindex import reindex_graph

        select_embedder(ctx, embedder, rebuild=True)
        provider = create_embedding_provider(ctx.settings)
        with call_site("embeddings.index"):
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
            select_embedder=select_embedder,
            workspace=workspace / "graph_workspace",
        )

    async def _embed_query(text: str, ctx: Any) -> list[float]:
        """Embed a query string with the context's embedding service."""
        from chaoscypher_core.adapters.embedding import require_embedding_provider

        provider = require_embedding_provider(
            ctx.embedding_service, "The benchmark's vector retrieval"
        )
        with call_site("embeddings.query"):
            result = await provider.embed(text)
        return list(result.embedding)

    async def _vector_search(vec: list[float], ctx: Any, k: int) -> list[tuple[str, float]]:
        """Run a top-k vector search against the context's search repository."""
        hits = ctx.search_repository.vector_search(vec, k=k)
        return [(item_id, float(score)) for item_id, score in hits]

    def _handlers(ctx: Any) -> Any:
        """The production retrieval handlers over the context, as the chat suite wires them.

        The one benchmark wiring (:func:`~chaoscypher_core.benchmark.retrieval.retrieval_handlers`),
        with the shape router's model: without it ``rules_then_llm`` decides by the rules alone.
        """
        from chaoscypher_core.benchmark.retrieval import retrieval_handlers

        return retrieval_handlers(ctx, chat_callback=router_chat_callback(ctx.settings))

    def _complete(retrieved: dict[str, Any], mode: str, ctx: Any) -> dict[str, Any]:
        """A live retrieval is checked like a pinned one (audit W2-10): degraded, it raises.

        The tests' stub contexts carry no search repository; a real context does.
        """
        from chaoscypher_core.benchmark.retrieval import require_complete_retrieval

        search = getattr(ctx, "search_repository", None)
        count = getattr(search, "count_vectors", None)
        indexed = count("chunk") if callable(count) else None
        if isinstance(indexed, int) and not isinstance(indexed, bool):  # a mock's count is not one
            require_complete_retrieval(
                retrieved,
                mode=mode,
                chunk_limit=ctx.settings.graphrag.chunk_limit,
                indexed_chunks=indexed,
            )
        return retrieved

    async def _graphrag_search(query: str, ctx: Any) -> dict[str, Any]:
        """Run the production GraphRAG search handler against the context."""
        result: dict[str, Any] = await _handlers(ctx).graphrag_search(
            query=query, limit=ctx.settings.graphrag.chunk_limit
        )
        return _complete(result, "graphrag", ctx)

    async def _hybrid_search(query: str, ctx: Any) -> dict[str, Any]:
        """The hybrid chunk search alone, guarded: the chat stage's ``hybrid`` rung.

        ``hybrid_retrieval`` opens the fault collector and records
        ``retrieval_stats``, so a keyword arm that raised refuses the row like
        a degraded graphrag retrieval does (audit wave 3); until 2026-10-06
        this rung took the bare chunk list and was checked by its count alone.
        """
        result: dict[str, Any] = await _handlers(ctx).hybrid_retrieval(
            query, ctx.settings.graphrag.chunk_limit
        )
        return _complete(result, "hybrid", ctx)

    async def _judge_call(
        judge: ModelConfig,
        prompt: str,
        *,
        seed: int = DEFAULT_SEED,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        """Send a single judge prompt at the run's seed and temperature; return the reply text."""
        from chaoscypher_core.adapters.llm.provider import LLMProvider
        from chaoscypher_core.app_config import get_settings

        # Inherit runtime config from the operator's settings like _chat does
        # (judge_call's wiring contract carries no ctx, so read the canonical
        # global settings): Ollama instance config for a local judge, and the
        # cloud API keys for a commercial one. Building bare LLMSettings here
        # left the keys to the env-var default_factory, so a yaml-configured
        # key 401'd every judge call.
        llm = _provider_llm_settings(judge, get_settings().llm)
        # The judge decodes as the row records: until 2026-10-06 (audit wave 2) the
        # copy kept the product's ai_temperature 0.3 and no seed, so a judged score
        # was a sample even after the chat stage was pinned.
        pin_llm_determinism(llm, seed=seed, temperature=temperature)
        settings = candidate_engine_settings(llm, get_settings())
        provider = LLMProvider(settings=settings)
        res = await provider.chat([{"role": "user", "content": prompt}])
        return str(res.content)

    from chaoscypher_cli.benchmark.naive_retrieval import naive_retriever_factory

    return OrchestratorWiring(
        cache=cache,
        graph_provider_factory=_graph_provider_factory,
        embed_query=_embed_query,
        vector_search=_vector_search,
        graphrag_search=_graphrag_search,
        chat=_benchmark_chat,
        judge_call=_judge_call,
        hybrid_search=_hybrid_search,
        naive_retriever=naive_retriever_factory,
        naive_index_root=workspace / "naive_index",
    )


__all__ = [
    "NUM_PREDICT_MARGIN_TOKENS",
    "RUN_SECTIONS",
    "OrchestratorWiring",
    "candidate_engine_settings",
    "check_replayable",
    "default_wiring",
    "format_retrieved_context",
    "num_predict_cap",
    "pin_router",
    "pinned_judge_call",
    "replay_builds",
    "replaying",
    "router_chat_callback",
    "run_full_benchmark",
    "select_embedder",
    "stored_vector_dimensions",
]
