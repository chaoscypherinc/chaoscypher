# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Branch coverage for the orchestrator: failed-extractor skips, noop builder,
missing-snapshot error, and the default_wiring closures.

The wiring closures are exercised directly with fake CLIContext / provider /
handler objects so no real LLM, embedding, or graph infrastructure is touched.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cli.benchmark.config import BenchmarkConfig
from chaoscypher_cli.benchmark.discovery import DatasetBundle
from chaoscypher_cli.benchmark.extraction_dataset import ExtractionDataset
from chaoscypher_cli.benchmark.models import ModelConfig
from chaoscypher_cli.benchmark.orchestrator import (
    OrchestratorWiring,
    _noop_builder,
    default_wiring,
    run_full_benchmark,
)
from chaoscypher_cli.benchmark.queries import LabeledQuery, LabeledQuerySet
from chaoscypher_core.testing.session_fakes import empty_session_scope


def _bundle(tmp_path: Path) -> DatasetBundle:
    corpus = tmp_path / "demo.txt"
    corpus.write_text("text", encoding="utf-8")
    qs = LabeledQuerySet(
        version="1.0",
        queries=[
            LabeledQuery(
                id="q1",
                band="factual_single_hop",
                question="?",
                gold_entities=["A"],
                gold_answer="a",
            )
        ],
    )
    return DatasetBundle(
        id="demo",
        version="1.0",
        packs=("technical",),
        corpus_path=corpus,
        source="builtin",
        extraction_dataset=ExtractionDataset(
            id="demo", version="1.0", corpus_path=corpus, packs=("technical",)
        ),
        queries=qs,
    )


def _full_cfg() -> BenchmarkConfig:
    return BenchmarkConfig(
        name="full",
        description="",
        seed=42,
        temperature=0.0,
        dataset_ids=["demo"],
        extractors=[ModelConfig(provider="ollama", model="ext", label="E")],
        embedders=[ModelConfig(provider="ollama", model="emb", label="M")],
        chats=[ModelConfig(provider="ollama", model="chat", label="C")],
        judge=ModelConfig(provider="anthropic", model="claude-opus-4-7", label="J"),
        config_name="full",
        source="builtin",
    )


@pytest.mark.asyncio
async def test_noop_builder_always_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="cache miss with no rebuild path"):
        await _noop_builder(tmp_path / "x.db")


@pytest.mark.asyncio
async def test_failed_extractor_skips_cache_and_downstream(tmp_path: Path) -> None:
    """A failed extraction row must skip cache copy + embedding + chat stages."""
    bundle = _bundle(tmp_path)
    cfg = _full_cfg()

    # Extraction reports failure (success=False) -> all skip branches fire.
    bundle.extraction_dataset.run = AsyncMock(  # type: ignore[method-assign]
        return_value=MagicMock(
            error="empty_extraction",
            success=False,
            latency_ms=1,
            input_tokens=0,
            output_tokens=0,
            per_chunk_latency_ms=[],
            extras={},
            entities=[],
            relationships=[],
        )
    )

    cache = MagicMock()
    cache.get_or_build = AsyncMock()
    wiring = OrchestratorWiring(
        cache=cache,
        graph_provider_factory=MagicMock(),
        embed_query=AsyncMock(),
        vector_search=AsyncMock(),
        graphrag_search=AsyncMock(),
        chat=AsyncMock(),
        judge_call=AsyncMock(),
    )

    rows = await run_full_benchmark(cfg, [bundle], wiring=wiring)

    # Only the extraction (failed) row is produced.
    assert [r.dataset_kind for r in rows] == ["extraction"]
    assert all(not r.success for r in rows)
    # Skipped extractor => cache never consulted, downstream never called.
    cache.get_or_build.assert_not_called()
    wiring.graphrag_search.assert_not_called()
    wiring.embed_query.assert_not_called()


@pytest.mark.asyncio
async def test_missing_snapshot_raises_in_cache_builder(tmp_path: Path) -> None:
    """The cache _build closure raises when the expected snapshot is absent."""
    bundle = _bundle(tmp_path)
    cfg = BenchmarkConfig(
        name="ext",
        description="",
        seed=42,
        temperature=0.0,
        dataset_ids=["demo"],
        extractors=[ModelConfig(provider="ollama", model="ext", label="E")],
        embedders=None,
        chats=None,
        judge=None,
        config_name="ext",
        source="builtin",
    )
    bundle.extraction_dataset.run = AsyncMock(  # type: ignore[method-assign]
        return_value=MagicMock(
            error=None,
            success=True,
            latency_ms=1,
            input_tokens=0,
            output_tokens=0,
            per_chunk_latency_ms=[],
            extras={},
            entities=[],
            relationships=[],
        )
    )
    # expected_snapshot_path points at a file that does NOT exist.
    missing = tmp_path / "nope" / "app.db"
    bundle.extraction_dataset.expected_snapshot_path = lambda _m: missing  # type: ignore[method-assign]

    async def _invoke_builder(
        *, corpus_id, corpus_version, extractor, seed, temperature, builder, building=False
    ):
        await builder(tmp_path / "target.db")  # triggers the missing-snapshot RuntimeError
        return tmp_path / "target.db"

    cache = MagicMock()
    cache.get_or_build = _invoke_builder
    wiring = OrchestratorWiring(
        cache=cache,
        graph_provider_factory=MagicMock(),
        embed_query=AsyncMock(),
        vector_search=AsyncMock(),
        graphrag_search=AsyncMock(),
        chat=AsyncMock(),
        judge_call=AsyncMock(),
    )

    with pytest.raises(RuntimeError, match="expected snapshot at"):
        await run_full_benchmark(cfg, [bundle], wiring=wiring)


# ---------------------------------------------------------------------------
# default_wiring closures
# ---------------------------------------------------------------------------


def _model() -> ModelConfig:
    return ModelConfig(provider="ollama", model="m", label="M")


def test_default_wiring_ctx_factory_patches_database_dir(tmp_path: Path) -> None:
    """_ctx_factory builds a CLIContext and repoints database_dir at the snapshot."""
    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
    # GraphProvider holds the ctx_factory; pull it out and call it directly.
    provider = wiring.graph_provider_factory(tmp_path / "graph_abc" / "app.db")
    ctx_factory = provider.ctx_factory

    created: dict[str, Any] = {}

    def _fake_ctx(*, database_name: str) -> Any:
        ctx = SimpleNamespace(database_name=database_name, database_dir=None)
        created["ctx"] = ctx
        return ctx

    fake_context_mod = SimpleNamespace(CLIContext=_fake_ctx)
    snapshot = tmp_path / "graph_xyz" / "app.db"
    with patch.dict("sys.modules", {"chaoscypher_cli.context": fake_context_mod}):
        ctx = ctx_factory(snapshot)

    assert ctx.database_name == "graph_xyz"
    assert ctx.database_dir == snapshot.parent


@pytest.mark.asyncio
async def test_default_wiring_embed_query(tmp_path: Path) -> None:
    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
    ctx = MagicMock()
    ctx.embedding_service.embed = AsyncMock(return_value=SimpleNamespace(embedding=[0.1, 0.2, 0.3]))
    vec = await wiring.embed_query("what is x?", ctx)
    assert vec == [0.1, 0.2, 0.3]
    ctx.embedding_service.embed.assert_awaited_once_with("what is x?")


@pytest.mark.asyncio
async def test_default_wiring_vector_search(tmp_path: Path) -> None:
    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
    ctx = MagicMock()
    ctx.search_repository.vector_search.return_value = [("id-1", 0.9), ("id-2", 0.5)]
    hits = await wiring.vector_search([0.1, 0.2], ctx, 5)
    assert hits == [("id-1", 0.9), ("id-2", 0.5)]
    ctx.search_repository.vector_search.assert_called_once_with([0.1, 0.2], k=5)


@pytest.mark.asyncio
async def test_default_wiring_graphrag_search(tmp_path: Path) -> None:
    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
    ctx = MagicMock()
    ctx.settings.graphrag.chunk_limit = 10  # the budget the wiring reads (graphrag.chunk_limit)

    handler_instance = MagicMock()
    handler_instance.graphrag_search = AsyncMock(return_value={"entities": [{"id": "e1"}]})
    handlers_cls = MagicMock(return_value=handler_instance)
    fake_handlers_mod = SimpleNamespace(GraphRAGToolHandlers=handlers_cls)
    mod_path = "chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers"

    with patch.dict("sys.modules", {mod_path: fake_handlers_mod}):
        out = await wiring.graphrag_search("question?", ctx)

    assert out == {"entities": [{"id": "e1"}]}
    # the chat budget is explicit, the same constant the chat suite passes
    handler_instance.graphrag_search.assert_awaited_once_with(query="question?", limit=10)
    # the shape router's model reaches the handler, so rules_then_llm asks it (Task 10)
    assert callable(handlers_cls.call_args.kwargs["chat_callback"])


def test_the_pin_builders_router_is_one_callback_and_its_header_record() -> None:
    """The pin builder, the reach tools and the pin header share one router (Task 10 review)."""
    from chaoscypher_cli.benchmark.config import DEFAULT_SEED
    from chaoscypher_cli.benchmark.orchestrator import pin_router
    from chaoscypher_core.settings import EngineSettings

    settings = EngineSettings()
    settings.llm.chat_provider = "ollama"
    settings.llm.ollama_chat_model = "gemma4:31b"
    settings.graphrag.router = "rules_then_llm"  # a mode that asks the model
    callback, record = pin_router(settings)
    assert callable(callback)
    assert record == {"provider": "ollama", "model": "gemma4:31b", "seed": DEFAULT_SEED}
    settings.graphrag.router = "rules"  # the default: no router model, no record
    assert pin_router(settings)[1] is None


@pytest.mark.asyncio
async def test_the_router_callback_asks_the_run_chat_model_at_temperature_0_and_the_seed() -> None:
    """Built on first call from a copy of the run's settings: the seed, temperature 0, the caches."""
    from chaoscypher_cli.benchmark.orchestrator import router_chat_callback
    from chaoscypher_core.settings import EngineSettings

    built: list[Any] = []
    asked: list[dict[str, Any]] = []

    class _Provider:
        def __init__(self, *, settings: Any) -> None:
            built.append(settings)

        async def chat(self, **kwargs: Any) -> Any:
            asked.append(kwargs)
            return SimpleNamespace(content='{"shape": "path", "reason": "two people"}')

    run = EngineSettings()
    run.llm.ai_temperature = 0.3
    run.llm.seed = None
    callback = router_chat_callback(run, seed=42)
    mod = "chaoscypher_core.adapters.llm.provider"
    with patch(f"{mod}.LLMProvider", _Provider):
        assert built == []  # nothing is built until the router asks
        reply = await callback([{"role": "user", "content": "q"}], temperature=0.0, max_tokens=64)
        await callback([{"role": "user", "content": "q2"}])
    assert reply == {"content": '{"shape": "path", "reason": "two people"}'}
    assert len(built) == 1  # one provider per callback
    llm = built[0].llm
    assert (llm.seed, llm.ai_temperature) == (42, 0.0)
    assert built[0].cache == run.cache  # the run's completion cache (a replay's require_hit too)
    assert asked[0] == {
        "messages": [{"role": "user", "content": "q"}],
        "stream": False,
        "temperature": 0.0,
        "max_tokens": 64,
    }
    assert (run.llm.seed, run.llm.ai_temperature) == (None, 0.3)  # the run's own are untouched
    # with no seed given, the run's own (the chat stage pinned it to the config's)
    run.llm.seed = 7
    with patch(f"{mod}.LLMProvider", _Provider):
        await router_chat_callback(run)([{"role": "user", "content": "q"}])
    assert built[-1].llm.seed == 7


@pytest.mark.asyncio
def _streamed(text: str) -> Any:
    """What ``provider.chat(stream=True)`` returns: the answer as content and done chunks."""

    async def _gen() -> Any:
        yield {"type": "content", "delta": text, "accumulated": text}
        yield {
            "type": "done",
            "content": text,
            "finish_reason": "stop",
            "usage": {"completion_tokens": 3},
        }

    return _gen()


async def test_default_wiring_chat_builds_prompt_from_context(tmp_path: Path) -> None:
    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
    ctx = MagicMock()
    # ollama branch + ollama_instances copy.
    ctx.settings.llm = SimpleNamespace(
        ollama_instances=["http://localhost:11434"],
        openai_api_key=None,
        anthropic_api_key="sk-ant",
        gemini_api_key=None,
    )

    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=lambda **kw: _streamed("the answer"))
    provider_cls = MagicMock(return_value=provider)
    fake_provider_mod = SimpleNamespace(LLMProvider=provider_cls)
    fake_settings_mod = SimpleNamespace(
        EngineSettings=lambda **kw: SimpleNamespace(**kw),
        LLMSettings=lambda **kw: SimpleNamespace(**kw),
    )

    retrieved = {
        "graph_context": {
            "seed_entities": [{"label": "Alpha"}],
            "related_entities": [{"name": "Beta"}],
        }
    }
    chat_model = ModelConfig(provider="ollama", model="llama", label="L")

    with patch.dict(
        "sys.modules",
        {
            "chaoscypher_core.adapters.llm.provider": fake_provider_mod,
            "chaoscypher_core.settings": fake_settings_mod,
        },
    ):
        answer = await wiring.chat(chat_model, "What is Alpha?", retrieved, ctx)

    assert answer["answer"] == "the answer"
    assert answer["finish_reason"] == "stop" and answer["output_tokens"] == 3
    # The call streams under the product's loop guard, thinking on.
    assert provider.chat.call_args.kwargs["stream"] is True
    assert provider.chat.call_args.kwargs["enable_thinking"] is True
    # The prompt fed to the provider includes both retrieved entities + question.
    messages = provider.chat.call_args.kwargs["messages"]
    prompt = messages[0]["content"]
    assert "Alpha" in prompt
    assert "Beta" in prompt
    assert "What is Alpha?" in prompt
    # One prompt for the local run and the MCP bridge: Core builds both.
    from chaoscypher_core.benchmark.chat_prompt import (
        format_retrieved_context,
        grounded_chat_prompt,
    )

    assert prompt == grounded_chat_prompt("What is Alpha?", format_retrieved_context(retrieved))
    assert messages == [{"role": "user", "content": prompt}]


@pytest.mark.asyncio
async def test_default_wiring_chat_non_ollama_carries_api_keys(tmp_path: Path) -> None:
    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
    ctx = MagicMock()
    # No ollama_instances attr; provider is openai so that branch is skipped.
    ctx.settings.llm = SimpleNamespace(
        openai_api_key="sk-openai",
        anthropic_api_key=None,
        gemini_api_key=None,
    )

    captured: dict[str, Any] = {}

    def _llm_settings(**kw: Any) -> Any:
        captured.update(kw)
        return SimpleNamespace(**kw)

    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=lambda **kw: _streamed("ans"))
    fake_provider_mod = SimpleNamespace(LLMProvider=MagicMock(return_value=provider))
    fake_settings_mod = SimpleNamespace(
        EngineSettings=lambda **kw: SimpleNamespace(**kw),
        LLMSettings=_llm_settings,
    )

    chat_model = ModelConfig(provider="openai", model="gpt-4o", label="G")
    with patch.dict(
        "sys.modules",
        {
            "chaoscypher_core.adapters.llm.provider": fake_provider_mod,
            "chaoscypher_core.settings": fake_settings_mod,
        },
    ):
        answer = await wiring.chat(chat_model, "q?", {"graph_context": {}}, ctx)

    assert answer["answer"] == "ans"
    # The openai api key was carried into the LLMSettings kwargs.
    assert captured["openai_api_key"] == "sk-openai"
    assert captured["chat_provider"] == "openai"
    assert captured["openai_chat_model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_default_wiring_judge_call(tmp_path: Path) -> None:
    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")

    captured: dict[str, Any] = {}

    def _llm_settings(**kw: Any) -> Any:
        captured.update(kw)
        return SimpleNamespace(**kw)

    provider = MagicMock()
    provider.chat = AsyncMock(return_value=SimpleNamespace(content="5"))
    fake_provider_mod = SimpleNamespace(LLMProvider=MagicMock(return_value=provider))
    fake_settings_mod = SimpleNamespace(
        EngineSettings=lambda **kw: SimpleNamespace(**kw),
        LLMSettings=_llm_settings,
    )

    judge = ModelConfig(provider="anthropic", model="claude-opus-4-7", label="J")
    with patch.dict(
        "sys.modules",
        {
            "chaoscypher_core.adapters.llm.provider": fake_provider_mod,
            "chaoscypher_core.settings": fake_settings_mod,
        },
    ):
        out = await wiring.judge_call(judge, "score this")

    assert out == "5"
    # The judge runs on a copy of the operator's settings with the judge as chat model
    # (keys and instances travel with it; the LLMSettings constructor is not called).
    llm = fake_provider_mod.LLMProvider.call_args.kwargs["settings"].llm
    assert llm.chat_provider == "anthropic"
    assert llm.anthropic_chat_model == "claude-opus-4-7"
    assert not captured
    (messages,), _ = provider.chat.call_args
    assert messages[0]["content"] == "score this"


@pytest.mark.asyncio
async def test_default_wiring_reindex_batch_embeds_nodes(tmp_path: Path) -> None:
    """_reindex mutates embedding settings, batch-embeds, and indexes each node."""
    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
    provider = wiring.graph_provider_factory(tmp_path / "g" / "app.db")
    reindex = provider.reindex

    nodes = [
        SimpleNamespace(id="n1", label="Alpha", properties={"definition": "first"}),
        SimpleNamespace(id="n2", label="Beta", properties={}),
    ]
    ctx = MagicMock()
    ctx.settings.embedding = SimpleNamespace(provider=None, model=None)
    ctx.graph_repository.list_nodes.return_value = nodes
    ctx.storage_adapter.list_sources.return_value = ([], 0)
    ctx.storage_adapter.session_scope = empty_session_scope
    # reindex_graph checks every vector landed (audit R8): the store reports what it took
    ctx.search_repository.index_embeddings_batch.side_effect = lambda emb, **_: len(emb)
    ctx.search_repository.count_vectors.side_effect = {"node": 2, "chunk": 0}.__getitem__

    embed_provider = MagicMock()
    embed_provider.batch_embed = AsyncMock(return_value=SimpleNamespace(embeddings=[[0.1], [0.2]]))
    fake_embedding_mod = SimpleNamespace(
        create_embedding_provider=MagicMock(return_value=embed_provider)
    )

    embedder = ModelConfig(provider="ollama", model="qwen3-embedding:0.6b", label="N")
    with patch.dict(
        "sys.modules",
        {"chaoscypher_core.adapters.embedding": fake_embedding_mod},
    ):
        await reindex(ctx, embedder)

    # Embedding settings retargeted to the candidate embedder.
    assert ctx.settings.embedding.provider == "ollama"
    assert ctx.settings.embedding.model == "qwen3-embedding:0.6b"
    assert ctx._embedding_provider is None
    # batch_embed called once with the label+description text pairs.
    (texts,), _ = embed_provider.batch_embed.call_args
    assert texts == ["Alpha | first", "Beta"]
    # The node vectors replace the old ones in one batch.
    ctx.search_repository.clear_vectors.assert_any_call("node")
    ctx.search_repository.index_embeddings_batch.assert_any_call(
        [("n1", [0.1]), ("n2", [0.2])], item_type="node"
    )


@pytest.mark.asyncio
async def test_default_wiring_reindex_reembeds_chunks_with_stage_embedder(tmp_path: Path) -> None:
    """_reindex re-embeds every source's chunks with the stage embedder.

    The committed extraction wrote chunk vectors with the settings' default
    embedder; without a chunk re-embed, chunk retrieval compared vectors from
    two models. The chunks go through the product's IndexingService with the
    stage provider, then into vec_search_chunks; a chunkless source is skipped.
    """
    import base64

    import numpy as np

    from chaoscypher_core.exceptions import ValidationError

    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
    reindex = wiring.graph_provider_factory(tmp_path / "g" / "app.db").reindex

    ctx = MagicMock()
    ctx.settings.embedding = SimpleNamespace(provider=None, model=None)
    ctx.settings.batching.chunk_fetch_limit = 500
    ctx.graph_repository.list_nodes.return_value = []
    ctx.storage_adapter.session_scope = empty_session_scope
    ctx.storage_adapter.list_sources.return_value = ([{"id": "s1"}, {"id": "empty"}], 2)
    vec = base64.b64encode(np.array([0.5, 0.25], dtype=np.float32).tobytes()).decode()
    ctx.storage_adapter.iter_chunk_embeddings.return_value = iter([("c1", vec)])
    ctx.storage_adapter.get_chunks_by_source.side_effect = lambda sid, **_: (
        [],
        1 if sid == "s1" else 0,
    )
    ctx.search_repository.index_embeddings_batch.side_effect = lambda emb, **_: len(emb)
    ctx.search_repository.count_vectors.side_effect = {"node": 0, "chunk": 1}.__getitem__

    embed_provider = MagicMock()
    embed_provider.batch_embed = AsyncMock(return_value=SimpleNamespace(embeddings=[]))
    built: list[dict[str, Any]] = []
    indexed_sources: list[str] = []

    class _FakeIndexing:
        """Records the provider it was built with and the sources it indexes."""

        def __init__(self, **kwargs: Any) -> None:
            """Capture the constructor kwargs."""
            built.append(kwargs)

        async def create_index(self, source_id: str) -> dict[str, Any]:
            """Record the source; the chunkless one raises like the real service."""
            if source_id == "empty":
                raise ValidationError("no chunks", field="source_id")
            indexed_sources.append(source_id)
            return {}

    fake_embedding_mod = SimpleNamespace(
        create_embedding_provider=MagicMock(return_value=embed_provider)
    )
    # the real chunk text builder stays: reindex imports it from the same module (2026-10-04)
    from chaoscypher_core.services.search.engine.index import chunk_embedding_text

    fake_index_mod = SimpleNamespace(
        IndexingService=_FakeIndexing, chunk_embedding_text=chunk_embedding_text
    )
    embedder = ModelConfig(provider="ollama", model="qwen3-embedding:0.6b", label="N")
    with patch.dict(
        "sys.modules",
        {
            "chaoscypher_core.adapters.embedding": fake_embedding_mod,
            "chaoscypher_core.services.search.engine.index": fake_index_mod,
        },
    ):
        await reindex(ctx, embedder)

    assert len(built) == 1
    assert built[0]["embedding_service"] is embed_provider
    assert built[0]["repository"] is ctx.storage_adapter
    assert indexed_sources == ["s1"]
    ctx.search_repository.index_embeddings_batch.assert_any_call(
        [("chunk:c1", [0.5, 0.25])], item_type="chunk"
    )


async def test_indexed_graph_copies_the_snapshot_under_its_database_name(tmp_path: Path) -> None:
    """The copy's directory carries the snapshot's database_name, so the engine reads its rows.

    2026-09-25: the copy sat in a random folder, the engine named the
    database after it, and every repository query matched nothing.
    """
    import sqlite3
    from unittest.mock import MagicMock

    from chaoscypher_cli.benchmark.graph_provider import GraphProvider, snapshot_database_name

    snap = tmp_path / "snapshot.db"
    with sqlite3.connect(snap) as conn:
        conn.execute("CREATE TABLE sources (id TEXT, database_name TEXT)")
        conn.execute("INSERT INTO sources VALUES ('s1', 'benchmark_corpus_ollama_m_123')")
    assert snapshot_database_name(snap) == "benchmark_corpus_ollama_m_123"
    seen: dict = {}

    def ctx_factory(db_path: Path) -> MagicMock:
        seen["db_path"] = db_path
        return MagicMock()

    provider = GraphProvider(
        snapshot_path=snap, ctx_factory=ctx_factory, reindex=MagicMock(), workspace=tmp_path / "ws"
    )
    async with provider.indexed_graph():
        assert seen["db_path"].parent.name == "benchmark_corpus_ollama_m_123"
        assert seen["db_path"].exists()


@pytest.mark.asyncio
async def test_default_wiring_chat_runs_with_the_pinned_decoding(tmp_path: Path) -> None:
    """The chat stage runs with the context's LLM settings (seed, temperature, num_ctx), not a bare rebuild.

    Until 2026-10-06 it built LLMSettings from four fields, so every chat row ran
    at temperature 0.3 with no seed while recording seed 42 / temperature 0.
    """
    from chaoscypher_core.settings import LLMSettings

    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
    ctx = MagicMock()
    ctx.settings.llm = LLMSettings(
        chat_provider="ollama",
        ollama_chat_model="other",
        seed=7,
        ai_temperature=0.0,
        ollama_num_ctx=4096,
        anthropic_api_key="sk-ant",
    )
    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=lambda **kw: _streamed("the answer"))
    provider_cls = MagicMock(return_value=provider)
    with patch.dict(
        "sys.modules",
        {"chaoscypher_core.adapters.llm.provider": SimpleNamespace(LLMProvider=provider_cls)},
    ):
        await wiring.chat(
            ModelConfig(provider="ollama", model="llama", label="L"), "Q?", {"chunks": []}, ctx
        )
    llm = provider_cls.call_args.kwargs["settings"].llm
    assert (llm.seed, llm.ai_temperature, llm.ollama_num_ctx) == (7, 0.0, 4096)
    assert llm.ollama_chat_model == "llama" and llm.chat_provider == "ollama"
    assert llm.anthropic_api_key.get_secret_value() == "sk-ant"
    assert ctx.settings.llm.ollama_chat_model == "other"  # a copy: the context is not mutated


def _streamed_with_usage(text: str, prompt_tokens: int) -> Any:
    """A stream whose done chunk reports the prompt's tokens, as Ollama's usage does."""

    async def _gen() -> Any:
        yield {"type": "content", "delta": text, "accumulated": text}
        yield {
            "type": "done",
            "content": text,
            "finish_reason": "stop",
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 3},
        }

    return _gen()


@pytest.mark.asyncio
async def test_default_wiring_chat_caps_the_output_at_the_window_left_by_the_prompt(
    tmp_path: Path,
) -> None:
    """num_predict 65536 against num_ctx 32768 shifted prompts out of the window (H9)."""
    from chaoscypher_cli.benchmark.orchestrator import NUM_PREDICT_MARGIN_TOKENS, num_predict_cap
    from chaoscypher_core.benchmark.chat_prompt import (
        format_retrieved_context,
        grounded_chat_prompt,
    )
    from chaoscypher_core.settings import LLMSettings
    from chaoscypher_core.utils.tokens import estimate_tokens

    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
    ctx = MagicMock()
    ctx.settings.llm = LLMSettings(chat_provider="ollama", ollama_num_ctx=8192, ai_max_tokens=65536)
    ctx.settings.graphrag = SimpleNamespace(ask_for_citations=False, answer_policy=False)
    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=lambda **kw: _streamed_with_usage("the answer", 1234))
    provider_cls = MagicMock(return_value=provider)
    retrieved = {"chunks": [{"content": "x" * 4000}]}
    with patch.dict(
        "sys.modules",
        {"chaoscypher_core.adapters.llm.provider": SimpleNamespace(LLMProvider=provider_cls)},
    ):
        reply = await wiring.chat(
            ModelConfig(provider="ollama", model="llama", label="L"), "Q?", retrieved, ctx
        )
    estimate = estimate_tokens(grounded_chat_prompt("Q?", format_retrieved_context(retrieved)))
    cap = provider.chat.call_args.kwargs["max_tokens"]
    assert cap == num_predict_cap(8192, estimate, 65536)
    assert cap + estimate + NUM_PREDICT_MARGIN_TOKENS <= 8192 and cap % 256 == 0
    assert (reply["prompt_tokens"], reply["num_ctx"], reply["num_predict"]) == (1234, 8192, cap)
    assert "prompt_tokens_estimated" not in reply


def test_the_cap_needs_a_window_and_never_exceeds_the_settings_own() -> None:
    from chaoscypher_cli.benchmark.orchestrator import num_predict_cap

    assert num_predict_cap(None, 1000) is None  # a hosted provider: its own cap
    assert num_predict_cap(32768, 2000, 4096) == 4096
    assert num_predict_cap(32768, 2000) == (32768 - 2000 - 512) // 256 * 256
    assert num_predict_cap(1024, 5000) == 256  # never below one step


@pytest.mark.asyncio
async def test_default_wiring_chat_reports_a_retried_loop_and_its_reason(tmp_path: Path) -> None:
    """A retried answer ran without thinking: the record says so and keeps the first loop (H10)."""
    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
    ctx = MagicMock()
    ctx.settings.llm = SimpleNamespace(chat_provider="anthropic", anthropic_api_key="k")
    ctx.settings.graphrag = SimpleNamespace(ask_for_citations=False, answer_policy=False)
    ctx.settings.chat = SimpleNamespace(loop_max_consecutive_lines=3, loop_max_line_occurrences=25)
    calls: list[bool] = []

    def _chat(**kw: Any) -> Any:
        calls.append(kw["enable_thinking"])
        if kw["enable_thinking"]:
            text = "<think>\n" + "I should check the graph again.\n" * 5

            async def _loop() -> Any:
                acc = ""
                for line in text.splitlines(keepends=True):
                    acc += line
                    yield {"type": "content", "delta": line, "accumulated": acc}

            return _loop()
        return _streamed("Borís.")

    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=_chat)
    with patch.dict(
        "sys.modules",
        {
            "chaoscypher_core.adapters.llm.provider": SimpleNamespace(
                LLMProvider=MagicMock(return_value=provider)
            ),
            "chaoscypher_core.settings": SimpleNamespace(
                EngineSettings=lambda **kw: SimpleNamespace(**kw),
                LLMSettings=lambda **kw: SimpleNamespace(**kw),
            ),
        },
    ):
        reply = await wiring.chat(
            ModelConfig(provider="anthropic", model="m", label="M"), "Q?", {"chunks": []}, ctx
        )
    assert calls == [True, False]
    assert reply["answer"] == "Borís." and reply["loop_retried"] is True
    assert reply["thinking_retried"] is True and reply["loop_phase"] == "content"
    assert "3 times in a row" in reply["loop_reason"] and reply["thinking_chars"] > 0
    assert reply["prompt_tokens_estimated"] is True and reply["num_predict"] is None


class _IndexedCtx:
    """A connected context's shape: settings, and the engine's search repository behind a property."""

    def __init__(self, repo: Any) -> None:
        from chaoscypher_core.settings import EngineSettings

        self.settings = EngineSettings()
        self._engine = SimpleNamespace(search_repository=repo)
        self._embedding_provider = object()

    @property
    def search_repository(self) -> Any:
        return self._engine.search_repository


def test_the_local_path_indexes_at_the_embedders_own_size_and_reopens_at_it(
    tmp_path: Path,
) -> None:
    """The CLI path indexed at the 1024 base, the first 1024 of a 2560-d model (R1)."""
    from chaoscypher_cli.benchmark.orchestrator import select_embedder, stored_vector_dimensions
    from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
    from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository

    db = tmp_path / "databases" / "g" / "app.db"
    db.parent.mkdir(parents=True)
    assert stored_vector_dimensions(db) is None
    engine = get_engine(db)
    try:
        ctx = _IndexedCtx(SearchRepository(engine=engine, vector_dim=1024, embedding_model="old"))
        assert stored_vector_dimensions(db) == 1024
        big = ModelConfig(provider="ollama", model="qwen3-embedding:4b", label="Q4")
        # a run's copy is never re-sized: that would empty the index it reads
        with pytest.raises(RuntimeError, match="indexed at 1024 dimensions"):
            select_embedder(ctx, big)
        select_embedder(ctx, big, rebuild=True)  # the copy about to be re-embedded
        assert ctx.search_repository.vector_dim == ctx.settings.search.vector_dimensions == 2560
        assert (
            ctx._embedding_provider is None and ctx.settings.embedding.model == "qwen3-embedding:4b"
        )
        assert stored_vector_dimensions(db) == 2560
        # the next run's context opens the copy at the size it was written at
        wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
        opened = wiring.graph_provider_factory(db).ctx_factory(db)
        assert opened._create_engine_settings().search.vector_dimensions == 2560
    finally:
        evict_engine(db)


@pytest.mark.asyncio
async def test_the_judge_runs_at_the_runs_seed_and_temperature(tmp_path: Path) -> None:
    """Audit wave 2: the judge's provider settings carry the run's seed and temperature.

    Until then the judge copied the operator's settings whole: the product's
    ``ai_temperature`` 0.3 and no seed, so a judged row was a sample while it
    recorded seed 42 / temperature 0.
    """
    from chaoscypher_cli.benchmark.orchestrator import pinned_judge_call

    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
    provider = MagicMock()
    provider.chat = AsyncMock(return_value=SimpleNamespace(content="4"))
    fake_provider_mod = SimpleNamespace(LLMProvider=MagicMock(return_value=provider))
    judge = ModelConfig(provider="ollama", model="judge:7b", label="J")
    with patch.dict("sys.modules", {"chaoscypher_core.adapters.llm.provider": fake_provider_mod}):
        # the call the chat dataset makes, through the closure run_full_benchmark binds
        call = pinned_judge_call(wiring.judge_call, seed=43, temperature=0.0)
        assert await call(judge, "score this") == "4"
        llm = fake_provider_mod.LLMProvider.call_args.kwargs["settings"].llm
        assert (llm.seed, llm.ai_temperature, llm.extraction_temperature) == (43, 0.0, 0.0)
        assert llm.ollama_chat_model == "judge:7b"
        # called bare, the judge still runs at the benchmark's pinned defaults, never 0.3
        await wiring.judge_call(judge, "again")
        llm = fake_provider_mod.LLMProvider.call_args.kwargs["settings"].llm
        assert (llm.seed, llm.ai_temperature) == (42, 0.0)


@pytest.mark.asyncio
async def test_graph_provenance_names_the_domain_and_corpus_the_slot_was_keyed_with(
    tmp_path: Path,
) -> None:
    """Audit wave 2: a chat row records the extraction domain and corpus sha256 its graph's key holds.

    Wave 5: read from the slot's ``slot.json`` with the snapshot's sha256, never from the dataset.
    """
    import json

    from chaoscypher_cli.benchmark.graph_cache import SLOT_RECORD, SNAPSHOT_SHA256, GraphCache
    from chaoscypher_cli.benchmark.orchestrator import _graph_provenance

    cache = GraphCache(
        root=tmp_path,
        dedup_embedder="exact",
        extraction_recipe="e1",
        corpus_identity=lambda _cid: ("generic", "c" * 64),
    )
    bundle = SimpleNamespace(id="book", version="1.0")
    ext = ModelConfig(provider="ollama", model="x", label="X")

    async def build(target: Path) -> None:
        target.write_bytes(b"graph-bytes")  # noqa: ASYNC240 - a test builder

    snapshot = await cache.get_or_build(
        corpus_id="book",
        corpus_version="1.0",
        extractor=ext,
        seed=42,
        temperature=0.0,
        builder=build,
        building=True,
    )
    got = _graph_provenance(cache, bundle, ext, seed=42, temperature=0.0)
    assert (got["extraction_domain"], got["corpus_sha256"]) == ("generic", "c" * 64)
    key = cache.key_for(
        corpus_id="book", corpus_version="1.0", extractor=ext, seed=42, temperature=0.0
    )
    assert got["graph_cache_key"] == key
    assert got["extraction_recipe"] == "e1"
    import hashlib

    sha = hashlib.sha256(b"graph-bytes").hexdigest()
    assert got["graph_snapshot_sha256"] == sha
    record_path = snapshot.parent / SLOT_RECORD
    assert json.loads(record_path.read_text())[SNAPSHOT_SHA256] == sha

    # an adopted slot names the pack's own record, not the dataset's or today's key fields
    record = json.loads(record_path.read_text())
    record.pop(SNAPSHOT_SHA256)
    record["adopted_from"] = "pack-v3"
    record["adopted_record"] = {**record, "extraction_domain": None, "extraction_recipe": None}
    record_path.write_text(json.dumps(record))
    got = _graph_provenance(cache, bundle, ext, seed=42, temperature=0.0)
    assert got["graph_adopted_from"] == "pack-v3"
    assert (got["extraction_domain"], got["extraction_recipe"]) == (None, None)
    assert got["corpus_sha256"] == "c" * 64
    # the sha256 is computed once and cached in the record
    assert got["graph_snapshot_sha256"] == sha
    assert json.loads(record_path.read_text())[SNAPSHOT_SHA256] == sha

    # audit wave 6: an app.db replaced by hand is not the graph the record describes
    snapshot.write_bytes(b"another graph, copied in by hand")
    got = _graph_provenance(cache, bundle, ext, seed=42, temperature=0.0)
    assert got["graph_snapshot_sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
    assert got["graph_snapshot_sha256"] != sha
    assert got["extraction_domain"] is None and got["graph_adopted_from"] is None
    assert got["graph_cache_key"] == key


@pytest.mark.asyncio
async def test_a_degraded_live_retrieval_is_refused_on_a_real_context(tmp_path: Path) -> None:
    """Audit wave 2: a live (unpinned) graphrag or hybrid run is checked like a pin build."""
    from chaoscypher_core.benchmark.retrieval import DegradedRetrievalError

    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
    ctx = MagicMock()
    ctx.settings.graphrag.chunk_limit = 10
    ctx.search_repository.count_vectors = lambda item_type: 100  # a real index holds 100 chunks
    handler_instance = MagicMock()

    def _hybrid(chunks: list[dict[str, str]], faults: list[str] | None = None) -> dict[str, Any]:
        """What ``hybrid_retrieval`` returns: the chunks with their fault record (audit wave 3)."""
        return {
            "success": True,
            "chunks": chunks,
            "retrieval_stats": {"mode": "hybrid", "faults": [] if faults is None else faults},
        }

    handler_instance.hybrid_retrieval = AsyncMock(
        return_value=_hybrid([{"id": "c1"}, {"id": "c2"}])
    )
    mod_path = "chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers"
    fake_handlers_mod = SimpleNamespace(
        GraphRAGToolHandlers=MagicMock(return_value=handler_instance)
    )
    with (
        patch.dict("sys.modules", {mod_path: fake_handlers_mod}),
        pytest.raises(DegradedRetrievalError),
    ):
        await wiring.hybrid_search("question?", ctx)  # 2 of 10 with 100 indexed: degraded
    full = [{"id": f"c{i}"} for i in range(10)]
    handler_instance.hybrid_retrieval = AsyncMock(
        return_value=_hybrid(full, ["keyword_search:chunk"])
    )
    with (
        patch.dict("sys.modules", {mod_path: fake_handlers_mod}),
        pytest.raises(DegradedRetrievalError, match="keyword_search:chunk"),
    ):
        await wiring.hybrid_search("question?", ctx)  # a full budget hides a dead keyword arm
    handler_instance.hybrid_retrieval = AsyncMock(return_value={"chunks": full})
    with (
        patch.dict("sys.modules", {mod_path: fake_handlers_mod}),
        pytest.raises(DegradedRetrievalError, match="no fault record"),
    ):
        await wiring.hybrid_search("question?", ctx)  # the bare list carries no record
    handler_instance.hybrid_retrieval = AsyncMock(return_value=_hybrid(full))
    with patch.dict("sys.modules", {mod_path: fake_handlers_mod}):
        out = await wiring.hybrid_search("question?", ctx)
    assert len(out["chunks"]) == 10
    handler_instance.hybrid_retrieval.assert_awaited_once_with("question?", 10)


def test_the_chat_seed_falls_back_to_the_extraction_seed() -> None:
    """``chat_seed`` decodes the chat and judge stages; unset, they decode at ``seed``."""
    from chaoscypher_cli.benchmark.config import BenchmarkConfig

    base = {
        "name": "n",
        "description": "",
        "seed": 42,
        "temperature": 0.0,
        "dataset_ids": [],
        "extractors": None,
        "embedders": None,
        "chats": None,
        "judge": None,
        "config_name": "c",
        "source": "builtin",
    }
    assert BenchmarkConfig(**base).chat_seed_or_seed == 42
    assert BenchmarkConfig(**base, chat_seed=43).chat_seed_or_seed == 43


def _failed_stream(error_code: str | None, text: str = "LLM streaming failed") -> Any:
    """A stream that fails at once, as a provider reports a streaming failure."""

    async def _gen() -> Any:
        chunk: dict[str, Any] = {"type": "error", "error": text}
        if error_code is not None:
            chunk["error_code"] = error_code
        yield chunk

    return _gen()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_code", "text", "expected", "retried"),
    [
        ("LLM_MODEL_ERROR", "LLM streaming failed", "LLMModelError", False),
        ("LLM_SERVICE_ERROR", "LLM streaming failed", "LLMServiceError", True),
        (None, "model 'qwen3:8b' not found, try pulling it first", "LLMModelError", False),
        (None, "503 Service Unavailable", "LLMServiceError", True),
        (None, "LLM streaming failed", "LLMError", False),
    ],
)
async def test_a_model_error_on_the_stream_is_not_retried_as_a_dropped_connection(
    tmp_path: Path, error_code: str | None, text: str, expected: str, retried: bool
) -> None:
    """Audit wave 4: every stream error was raised retryable; a model error now is not.

    Wave 5: the event goes through the one stream classifier the chat loop uses
    (``error_from_stream_chunk``), not a code table of the benchmark's own.
    """
    from chaoscypher_cli.benchmark.chat_dataset import transient_failure
    from chaoscypher_core import exceptions

    wiring = default_wiring(workspace=tmp_path, dedup_embedder="exact")
    ctx = MagicMock()
    ctx.settings.llm = SimpleNamespace(
        ollama_instances=["http://localhost:11434"],
        openai_api_key=None,
        anthropic_api_key=None,
        gemini_api_key=None,
    )
    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=lambda **kw: _failed_stream(error_code, text))
    fake_provider_mod = SimpleNamespace(LLMProvider=MagicMock(return_value=provider))
    fake_settings_mod = SimpleNamespace(
        EngineSettings=lambda **kw: SimpleNamespace(**kw),
        LLMSettings=lambda **kw: SimpleNamespace(**kw),
    )
    chat_model = ModelConfig(provider="ollama", model="qwen3:8b", label="Q")
    with (
        patch.dict(
            "sys.modules",
            {
                "chaoscypher_core.adapters.llm.provider": fake_provider_mod,
                "chaoscypher_core.settings": fake_settings_mod,
            },
        ),
        pytest.raises(exceptions.LLMError) as raised,
    ):
        await wiring.chat(chat_model, "q?", {"chunks": []}, ctx)
    assert type(raised.value) is getattr(exceptions, expected)
    assert transient_failure(raised.value) is retried


def test_a_copy_opens_at_its_stored_size_and_its_stored_embedder(tmp_path: Path) -> None:
    """The repository compares the settings' embedder with the stored one on connect (2026-10-07)."""
    import sqlite3
    from types import SimpleNamespace

    from chaoscypher_cli.benchmark.orchestrator import (
        _open_at_stored_size,
        stored_embedding_model,
        stored_vector_dimensions,
    )

    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE search_metadata (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany(
        "INSERT INTO search_metadata VALUES (?, ?)",
        [("vector_dim", "2560"), ("embedding_model", "qwen3-embedding:4b")],
    )
    con.commit()
    con.close()
    assert (stored_vector_dimensions(db), stored_embedding_model(db)) == (
        2560,
        "qwen3-embedding:4b",
    )

    def build() -> Any:
        return SimpleNamespace(
            search=SimpleNamespace(vector_dimensions=1024),
            embedding=SimpleNamespace(model="qwen3-embedding:0.6b", provider="ollama"),
        )

    ctx = SimpleNamespace(_create_engine_settings=build)
    _open_at_stored_size(ctx, db)
    settings = ctx._create_engine_settings()
    assert (settings.search.vector_dimensions, settings.embedding.model) == (
        2560,
        "qwen3-embedding:4b",
    )
