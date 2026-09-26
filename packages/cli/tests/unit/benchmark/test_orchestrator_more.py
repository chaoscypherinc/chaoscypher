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
        domain="technical",
        corpus_path=corpus,
        source="builtin",
        extraction_dataset=ExtractionDataset(
            id="demo", version="1.0", corpus_path=corpus, domain="technical"
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

    async def _invoke_builder(*, corpus_id, corpus_version, extractor, builder):
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
    wiring = default_wiring(workspace=tmp_path)
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
    wiring = default_wiring(workspace=tmp_path)
    ctx = MagicMock()
    ctx.embedding_service.embed = AsyncMock(return_value=SimpleNamespace(embedding=[0.1, 0.2, 0.3]))
    vec = await wiring.embed_query("what is x?", ctx)
    assert vec == [0.1, 0.2, 0.3]
    ctx.embedding_service.embed.assert_awaited_once_with("what is x?")


@pytest.mark.asyncio
async def test_default_wiring_vector_search(tmp_path: Path) -> None:
    wiring = default_wiring(workspace=tmp_path)
    ctx = MagicMock()
    ctx.search_repository.vector_search.return_value = [("id-1", 0.9), ("id-2", 0.5)]
    hits = await wiring.vector_search([0.1, 0.2], ctx, 5)
    assert hits == [("id-1", 0.9), ("id-2", 0.5)]
    ctx.search_repository.vector_search.assert_called_once_with([0.1, 0.2], k=5)


@pytest.mark.asyncio
async def test_default_wiring_graphrag_search(tmp_path: Path) -> None:
    wiring = default_wiring(workspace=tmp_path)
    ctx = MagicMock()

    handler_instance = MagicMock()
    handler_instance.graphrag_search = AsyncMock(return_value={"entities": [{"id": "e1"}]})
    handlers_cls = MagicMock(return_value=handler_instance)
    fake_handlers_mod = SimpleNamespace(GraphRAGToolHandlers=handlers_cls)
    mod_path = "chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers"

    with patch.dict("sys.modules", {mod_path: fake_handlers_mod}):
        out = await wiring.graphrag_search("question?", ctx)

    assert out == {"entities": [{"id": "e1"}]}
    handler_instance.graphrag_search.assert_awaited_once_with(query="question?")


@pytest.mark.asyncio
async def test_default_wiring_chat_builds_prompt_from_context(tmp_path: Path) -> None:
    wiring = default_wiring(workspace=tmp_path)
    ctx = MagicMock()
    # ollama branch + ollama_instances copy.
    ctx.settings.llm = SimpleNamespace(
        ollama_instances=["http://localhost:11434"],
        openai_api_key=None,
        anthropic_api_key="sk-ant",
        gemini_api_key=None,
    )

    provider = MagicMock()
    provider.chat = AsyncMock(return_value=SimpleNamespace(content="the answer"))
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
    assert answer["finish_reason"] == "stop"
    # The prompt fed to the provider includes both retrieved entities + question.
    (messages,), _ = provider.chat.call_args
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
    wiring = default_wiring(workspace=tmp_path)
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
    provider.chat = AsyncMock(return_value=SimpleNamespace(content="ans"))
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
    wiring = default_wiring(workspace=tmp_path)

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
    assert captured["chat_provider"] == "anthropic"
    assert captured["anthropic_chat_model"] == "claude-opus-4-7"
    (messages,), _ = provider.chat.call_args
    assert messages[0]["content"] == "score this"


@pytest.mark.asyncio
async def test_default_wiring_reindex_batch_embeds_nodes(tmp_path: Path) -> None:
    """_reindex mutates embedding settings, batch-embeds, and indexes each node."""
    wiring = default_wiring(workspace=tmp_path)
    provider = wiring.graph_provider_factory(tmp_path / "g" / "app.db")
    reindex = provider.reindex

    nodes = [
        SimpleNamespace(id="n1", label="Alpha", description="first"),
        SimpleNamespace(id="n2", label="Beta", description=""),
    ]
    ctx = MagicMock()
    ctx.settings.embedding = SimpleNamespace(provider=None, model=None)
    ctx.graph_repository.list_nodes.return_value = nodes
    ctx.storage_adapter.list_sources.return_value = ([], 0)

    embed_provider = MagicMock()
    embed_provider.batch_embed = AsyncMock(return_value=SimpleNamespace(embeddings=[[0.1], [0.2]]))
    fake_embedding_mod = SimpleNamespace(
        create_embedding_provider=MagicMock(return_value=embed_provider)
    )

    embedder = ModelConfig(provider="ollama", model="nomic", label="N")
    with patch.dict(
        "sys.modules",
        {"chaoscypher_core.adapters.embedding": fake_embedding_mod},
    ):
        await reindex(ctx, embedder)

    # Embedding settings retargeted to the candidate embedder.
    assert ctx.settings.embedding.provider == "ollama"
    assert ctx.settings.embedding.model == "nomic"
    assert ctx._embedding_provider is None
    # batch_embed called once with the label+description text pairs.
    (texts,), _ = embed_provider.batch_embed.call_args
    assert texts == ["Alpha. first", "Beta. "]
    # Each node embedding indexed.
    calls = ctx.search_repository.index_node_embedding.call_args_list
    assert [c.args for c in calls] == [("n1", [0.1]), ("n2", [0.2])]


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

    wiring = default_wiring(workspace=tmp_path)
    reindex = wiring.graph_provider_factory(tmp_path / "g" / "app.db").reindex

    ctx = MagicMock()
    ctx.settings.embedding = SimpleNamespace(provider=None, model=None)
    ctx.settings.batching.chunk_fetch_limit = 500
    ctx.graph_repository.list_nodes.return_value = []
    ctx.storage_adapter.list_sources.return_value = ([{"id": "s1"}, {"id": "empty"}], 2)
    vec = base64.b64encode(np.array([0.5, 0.25], dtype=np.float32).tobytes()).decode()
    ctx.storage_adapter.iter_chunk_embeddings.return_value = iter([("c1", vec)])

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
    fake_index_mod = SimpleNamespace(IndexingService=_FakeIndexing)
    embedder = ModelConfig(provider="ollama", model="nomic", label="N")
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
    ctx.search_repository.index_embeddings_batch.assert_called_once_with(
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
