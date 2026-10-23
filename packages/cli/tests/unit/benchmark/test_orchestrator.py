# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_cli.benchmark.config import BenchmarkConfig
from chaoscypher_cli.benchmark.models import ModelConfig
from chaoscypher_cli.benchmark.orchestrator import OrchestratorWiring, run_full_benchmark
from chaoscypher_cli.benchmark.queries import LabeledQuery, LabeledQuerySet


def _bundle(tmp_path: Path):
    from chaoscypher_cli.benchmark.discovery import DatasetBundle
    from chaoscypher_cli.benchmark.extraction_dataset import ExtractionDataset

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
            id="demo",
            version="1.0",
            corpus_path=corpus,
            packs=("technical",),
        ),
        queries=qs,
    )


@pytest.mark.asyncio
async def test_full_run_invokes_all_three_stages(tmp_path):
    bundle = _bundle(tmp_path)
    cfg = BenchmarkConfig(
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

    fake_extraction_run = AsyncMock(
        return_value=MagicMock(
            error=None,
            success=True,
            latency_ms=1,
            input_tokens=0,
            output_tokens=0,
            per_chunk_latency_ms=[],
            extras={},
            entities=[{"id": "u1", "name": "A", "aliases": []}],
            relationships=[],
        )
    )
    bundle.extraction_dataset.run = fake_extraction_run  # type: ignore[method-assign]

    indexed = MagicMock()
    indexed.ctx = MagicMock()
    indexed.ctx.graph_repository.list_nodes = MagicMock(
        return_value=[SimpleNamespace(id="u1", label="A", aliases=[])]
    )

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder=None):
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph

    cache = MagicMock()
    cache.get_or_build = AsyncMock(return_value=tmp_path / "snapshot.db")
    (tmp_path / "snapshot.db").write_bytes(b"x")

    wiring = OrchestratorWiring(
        cache=cache,
        graph_provider_factory=lambda snapshot: provider,
        embed_query=AsyncMock(return_value=[0.1]),
        vector_search=AsyncMock(return_value=[("u1", 0.9)]),
        graphrag_search=AsyncMock(return_value={"entities": [{"id": "u1", "name": "A"}]}),
        chat=AsyncMock(return_value="answer"),
        judge_call=AsyncMock(side_effect=["5", "5"]),
    )

    rows = await run_full_benchmark(cfg, [bundle], wiring=wiring)
    kinds = [r.dataset_kind for r in rows]
    assert "extraction" in kinds
    assert "embedding" in kinds
    assert "chat" in kinds


@pytest.mark.asyncio
async def test_downstream_rows_stamped_with_extractor_and_embedder_id(tmp_path):
    """Embedding/chat rows carry the originating extractor (+embedder) ids.

    The composite must join retrieval/chat scores back to the extractor that
    built the graph; the extractor was previously only encoded in the derived
    dataset_id. This asserts the explicit ``metrics`` stamps.
    """
    bundle = _bundle(tmp_path)
    cfg = BenchmarkConfig(
        name="full",
        description="",
        seed=42,
        temperature=0.0,
        dataset_ids=["demo"],
        extractors=[ModelConfig(provider="ollama", model="llama3.1:8b", label="E")],
        embedders=[ModelConfig(provider="ollama", model="nomic-embed-text", label="Emb")],
        chats=[ModelConfig(provider="ollama", model="chat", label="C")],
        judge=ModelConfig(provider="anthropic", model="claude-opus-4-7", label="J"),
        config_name="full",
        source="builtin",
    )

    fake_extraction_run = AsyncMock(
        return_value=MagicMock(
            error=None,
            success=True,
            latency_ms=1,
            input_tokens=0,
            output_tokens=0,
            per_chunk_latency_ms=[],
            extras={},
            entities=[{"id": "u1", "name": "A", "aliases": []}],
            relationships=[],
        )
    )
    bundle.extraction_dataset.run = fake_extraction_run  # type: ignore[method-assign]

    indexed = MagicMock()
    indexed.ctx = MagicMock()
    indexed.ctx.graph_repository.list_nodes = MagicMock(
        return_value=[SimpleNamespace(id="u1", label="A", aliases=[])]
    )

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder=None):
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph

    cache = MagicMock()
    cache.get_or_build = AsyncMock(return_value=tmp_path / "snapshot.db")
    (tmp_path / "snapshot.db").write_bytes(b"x")

    wiring = OrchestratorWiring(
        cache=cache,
        graph_provider_factory=lambda snapshot: provider,
        embed_query=AsyncMock(return_value=[0.1]),
        vector_search=AsyncMock(return_value=[("u1", 0.9)]),
        graphrag_search=AsyncMock(return_value={"entities": [{"id": "u1", "name": "A"}]}),
        chat=AsyncMock(return_value="answer"),
        judge_call=AsyncMock(side_effect=["5", "5"]),
    )

    rows = await run_full_benchmark(cfg, [bundle], wiring=wiring)
    emb_rows = [r for r in rows if r.dataset_kind == "embedding"]
    chat_rows = [r for r in rows if r.dataset_kind == "chat"]
    assert emb_rows
    assert chat_rows
    assert emb_rows[0].metrics["extractor_id"] == "ollama/llama3.1:8b"
    assert chat_rows[0].metrics["extractor_id"] == "ollama/llama3.1:8b"
    assert chat_rows[0].metrics["embedder_id"] == "ollama/nomic-embed-text"


@pytest.mark.asyncio
async def test_extractors_only_skips_later_stages(tmp_path):
    bundle = _bundle(tmp_path)
    cfg = BenchmarkConfig(
        name="ext-only",
        description="",
        seed=42,
        temperature=0.0,
        dataset_ids=["demo"],
        extractors=[ModelConfig(provider="ollama", model="ext", label="E")],
        embedders=None,
        chats=None,
        judge=None,
        config_name="ext-only",
        source="builtin",
    )
    bundle.extraction_dataset.run = AsyncMock(
        return_value=MagicMock(
            error=None,
            latency_ms=1,
            input_tokens=0,
            output_tokens=0,
            per_chunk_latency_ms=[],
            extras={},
            entities=[],
            relationships=[],
        )
    )  # type: ignore[method-assign]

    wiring = OrchestratorWiring(
        cache=MagicMock(get_or_build=AsyncMock()),
        graph_provider_factory=MagicMock(),
        embed_query=AsyncMock(),
        vector_search=AsyncMock(),
        graphrag_search=AsyncMock(),
        chat=AsyncMock(),
        judge_call=AsyncMock(),
    )

    rows = await run_full_benchmark(cfg, [bundle], wiring=wiring)
    assert all(r.dataset_kind == "extraction" for r in rows)
    wiring.graphrag_search.assert_not_called()


@pytest.mark.asyncio
async def test_no_double_extraction_on_cold_cache(tmp_path):
    """Stage 1 must call extraction_dataset.run exactly once per extractor.

    Regression test for the double-extraction bug: the original code ran
    run_benchmark (which called dataset.run) and then called dataset.run again
    inside the cache _build closure.  The fix copies the scored-run's snapshot
    via expected_snapshot_path() instead of re-running extraction.
    """
    bundle = _bundle(tmp_path)
    extractor = ModelConfig(provider="ollama", model="ext", label="E")
    cfg = BenchmarkConfig(
        name="no-double",
        description="",
        seed=42,
        temperature=0.0,
        dataset_ids=["demo"],
        extractors=[extractor],
        embedders=None,
        chats=None,
        judge=None,
        config_name="no-double",
        source="builtin",
    )

    # Prepare a fake snapshot file that expected_snapshot_path will point to.
    snapshot_file = tmp_path / "snapshot.db"
    sqlite3.connect(
        snapshot_file
    ).close()  # a real (empty) SQLite file: the cache copies via backup()

    run_call_count = 0

    async def _fake_run(model: ModelConfig) -> MagicMock:
        nonlocal run_call_count
        run_call_count += 1
        return MagicMock(
            error=None,
            latency_ms=1,
            input_tokens=0,
            output_tokens=0,
            per_chunk_latency_ms=[],
            extras={},
            entities=[],
            relationships=[],
            success=True,
        )

    bundle.extraction_dataset.run = _fake_run  # type: ignore[method-assign]
    # Patch expected_snapshot_path to return our pre-created file.
    bundle.extraction_dataset.expected_snapshot_path = lambda _m: snapshot_file  # type: ignore[method-assign]

    cache_target = tmp_path / "cache_target.db"

    async def _fake_get_or_build(
        *,
        corpus_id: str,
        corpus_version: str,
        extractor: ModelConfig,
        seed: int,
        temperature: float,
        builder: object,
        building: bool = False,
    ) -> Path:
        # Invoke the builder so the copy logic runs.
        await builder(cache_target)  # type: ignore[operator]
        return cache_target

    cache = MagicMock()
    cache.get_or_build = _fake_get_or_build

    wiring = OrchestratorWiring(
        cache=cache,
        graph_provider_factory=MagicMock(),
        embed_query=AsyncMock(),
        vector_search=AsyncMock(),
        graphrag_search=AsyncMock(),
        chat=AsyncMock(),
        judge_call=AsyncMock(),
    )

    await run_full_benchmark(cfg, [bundle], wiring=wiring)

    assert run_call_count == 1, (
        f"extraction_dataset.run was called {run_call_count} times; expected 1. "
        "Double extraction regression detected."
    )


def _reuse_cfg() -> BenchmarkConfig:
    return BenchmarkConfig(
        name="full",
        description="",
        seed=42,
        temperature=0.0,
        dataset_ids=["demo"],
        extractors=[ModelConfig(provider="ollama", model="ext", label="E")],
        embedders=[ModelConfig(provider="ollama", model="emb", label="M")],
        chats=[ModelConfig(provider="ollama", model="chat", label="C")],
        judge=None,
        config_name="full",
        source="builtin",
    )


def _reuse_wiring(cache, snapshots: list[Path]) -> OrchestratorWiring:
    indexed = MagicMock()
    indexed.ctx = MagicMock()
    indexed.ctx.graph_repository.list_nodes = MagicMock(
        return_value=[SimpleNamespace(id="u1", label="A", aliases=[])]
    )

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder=None):
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph

    def _factory(snapshot: Path):
        snapshots.append(snapshot)
        return provider

    return OrchestratorWiring(
        cache=cache,
        graph_provider_factory=_factory,
        embed_query=AsyncMock(return_value=[0.1]),
        vector_search=AsyncMock(return_value=[("u1", 0.9)]),
        graphrag_search=AsyncMock(return_value={"entities": [{"id": "u1", "name": "A"}]}),
        chat=AsyncMock(return_value="answer"),
        judge_call=AsyncMock(return_value="5"),
    )


@pytest.mark.asyncio
async def test_reuse_cached_graph_skips_extraction_when_slot_is_populated(tmp_path):
    """With reuse_cached_graph and a cached slot, Stage 1 does not run; 2 and 3 do."""
    from chaoscypher_cli.benchmark.graph_cache import GraphCache

    bundle = _bundle(tmp_path)
    cfg = _reuse_cfg()
    assert cfg.extractors is not None
    cache = GraphCache(root=tmp_path / "cache", dedup_embedder="exact")
    key = cache.key_for(
        corpus_id=bundle.id,
        corpus_version=bundle.version,
        extractor=cfg.extractors[0],
        seed=cfg.seed,
        temperature=cfg.temperature,
    )
    cached = tmp_path / "cache" / key / "app.db"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"cached")

    fake_extraction_run = AsyncMock(side_effect=AssertionError("extraction must not run"))
    bundle.extraction_dataset.run = fake_extraction_run  # type: ignore[method-assign]

    snapshots: list[Path] = []
    rows = await run_full_benchmark(
        cfg, [bundle], wiring=_reuse_wiring(cache, snapshots), reuse_cached_graph=True
    )

    fake_extraction_run.assert_not_called()
    kinds = [r.dataset_kind for r in rows]
    assert "extraction" not in kinds
    assert "embedding" in kinds
    assert "chat" in kinds
    assert snapshots
    assert all(s == cached for s in snapshots)
    assert cached.read_bytes() == b"cached"


@pytest.mark.asyncio
async def test_reuse_cached_graph_with_an_empty_slot_raises_before_stage_one(tmp_path):
    """REUSE_GRAPH on a cache miss re-extracted silently for hours until 2026-10-06 (H11).

    It now stops before anything runs, naming the slot and the recipes that keyed it.
    """
    from chaoscypher_cli.benchmark.graph_cache import GraphCache, GraphCacheMissError

    bundle = _bundle(tmp_path)
    cfg = _reuse_cfg()
    cache = GraphCache(root=tmp_path / "cache", dedup_embedder="exact", extraction_recipe="r" * 12)
    fake_extraction_run = AsyncMock(side_effect=AssertionError("extraction must not run"))
    bundle.extraction_dataset.run = fake_extraction_run  # type: ignore[method-assign]

    snapshots: list[Path] = []
    with pytest.raises(GraphCacheMissError) as caught:
        await run_full_benchmark(
            cfg, [bundle], wiring=_reuse_wiring(cache, snapshots), reuse_cached_graph=True
        )
    assert cfg.extractors is not None
    key = cache.key_for(
        corpus_id=bundle.id,
        corpus_version=bundle.version,
        extractor=cfg.extractors[0],
        seed=cfg.seed,
        temperature=cfg.temperature,
    )
    message = str(caught.value)
    assert str(tmp_path / "cache" / key / "app.db") in message
    assert "extraction recipe rrrrrrrrrrrr" in message and "install.py" in message
    fake_extraction_run.assert_not_called()
    assert snapshots == []


@pytest.mark.asyncio
async def test_chat_rows_carry_the_configs_rung_in_their_dataset_id(tmp_path):
    """A baseline's rows suffix the dataset id and record the mode; graphrag rows keep today's id."""
    from dataclasses import replace

    bundle = _bundle(tmp_path)
    base_cfg = BenchmarkConfig(
        name="full",
        description="",
        seed=42,
        temperature=0.0,
        dataset_ids=[bundle.id],
        extractors=[ModelConfig(provider="ollama", model="x", label="X")],
        embedders=[ModelConfig(provider="ollama", model="e", label="E")],
        chats=[ModelConfig(provider="ollama", model="c", label="C")],
        judge=None,
        config_name="full",
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
            entities=[{"id": "u1", "name": "A", "aliases": []}],
            relationships=[],
        )
    )
    indexed = MagicMock()
    indexed.ctx.graph_repository.list_nodes = MagicMock(
        return_value=[SimpleNamespace(id="u1", label="A", aliases=[])]
    )

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder=None):
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph
    cache = MagicMock()
    cache.get_or_build = AsyncMock(return_value=tmp_path / "snapshot.db")
    (tmp_path / "snapshot.db").write_bytes(b"x")
    wiring = OrchestratorWiring(
        cache=cache,
        graph_provider_factory=lambda snapshot: provider,
        embed_query=AsyncMock(return_value=[0.1]),
        vector_search=AsyncMock(return_value=[("u1", 0.9)]),
        graphrag_search=AsyncMock(return_value={"chunks": [{"content": "g"}]}),
        chat=AsyncMock(return_value="answer"),
        judge_call=AsyncMock(),
        hybrid_search=AsyncMock(return_value={"chunks": [{"content": "h"}]}),
    )

    rows = await run_full_benchmark(replace(base_cfg, retrieval="hybrid"), [bundle], wiring=wiring)
    failed = [(r.dataset_kind, r.error) for r in rows if not r.success]
    assert not failed, failed
    chat_row = next(r for r in rows if r.dataset_kind == "chat")
    assert chat_row.dataset_id == f"{bundle.id}__chat__ollama_x__ollama_e__retrieval_hybrid"
    assert chat_row.extras["retrieval"] == "hybrid"
    wiring.graphrag_search.assert_not_awaited()

    rows = await run_full_benchmark(base_cfg, [bundle], wiring=wiring)
    chat_row = next(r for r in rows if r.dataset_kind == "chat")
    assert chat_row.dataset_id == f"{bundle.id}__chat__ollama_x__ollama_e"
    assert chat_row.extras["retrieval"] == "graphrag"


@pytest.mark.asyncio
async def test_a_pinned_chat_run_skips_the_embedding_stage(tmp_path):
    """With retrieval_pins the chat stage reads stored contexts, so no stage embeds anything."""
    from dataclasses import replace

    bundle = _bundle(tmp_path)
    cfg = BenchmarkConfig(
        name="pinned",
        description="",
        seed=42,
        temperature=0.0,
        dataset_ids=[bundle.id],
        extractors=[ModelConfig(provider="ollama", model="x", label="X")],
        embedders=[ModelConfig(provider="ollama", model="e", label="E")],
        chats=[ModelConfig(provider="ollama", model="c", label="C")],
        judge=None,
        config_name="pinned",
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
    seen_pins = []

    import chaoscypher_cli.benchmark.orchestrator as orch

    real = orch.GraphRAGChatDataset

    def _capture(**kw):
        seen_pins.append(kw.get("pins_dir"))
        return real(**kw)

    provider = MagicMock()

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder=None):
        yield MagicMock()

    provider.indexed_graph = fake_indexed_graph
    cache = MagicMock()
    cache.get_or_build = AsyncMock(return_value=tmp_path / "snapshot.db")
    (tmp_path / "snapshot.db").write_bytes(b"x")
    embed_query = AsyncMock(return_value=[0.1])
    wiring = OrchestratorWiring(
        cache=cache,
        graph_provider_factory=lambda snapshot: provider,
        embed_query=embed_query,
        vector_search=AsyncMock(return_value=[]),
        graphrag_search=AsyncMock(return_value={"chunks": []}),
        chat=AsyncMock(return_value="answer"),
        judge_call=AsyncMock(),
    )
    orch.GraphRAGChatDataset = _capture  # type: ignore[misc,assignment]
    try:
        rows = await run_full_benchmark(
            replace(cfg, retrieval_pins=str(tmp_path / "pins")), [bundle], wiring=wiring
        )
    finally:
        orch.GraphRAGChatDataset = real  # type: ignore[misc]
    assert "embedding" not in [r.dataset_kind for r in rows]
    embed_query.assert_not_awaited()
    assert seen_pins == [tmp_path / "pins"]


@pytest.mark.asyncio
async def test_the_chat_stage_judge_is_called_with_the_runs_seed_and_temperature(tmp_path):
    """Audit wave 2: run_full_benchmark hands the dataset a judge bound to the config's decoding."""
    bundle = _bundle(tmp_path)
    cfg = BenchmarkConfig(
        name="full",
        description="",
        seed=43,
        temperature=0.0,
        dataset_ids=["demo"],
        extractors=[ModelConfig(provider="ollama", model="ext", label="E")],
        embedders=[ModelConfig(provider="ollama", model="emb", label="M")],
        chats=[ModelConfig(provider="ollama", model="chat", label="C")],
        judge=ModelConfig(provider="ollama", model="judge", label="J"),
        config_name="full",
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
            entities=[{"id": "u1", "name": "A", "aliases": []}],
            relationships=[],
        )
    )
    indexed = MagicMock()
    indexed.ctx = MagicMock()
    indexed.ctx.graph_repository.list_nodes = MagicMock(
        return_value=[SimpleNamespace(id="u1", label="A", aliases=[])]
    )

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder=None):
        yield indexed

    provider = MagicMock()
    provider.indexed_graph = fake_indexed_graph
    cache = MagicMock()
    cache.get_or_build = AsyncMock(return_value=tmp_path / "snapshot.db")
    (tmp_path / "snapshot.db").write_bytes(b"x")
    judge_call = AsyncMock(side_effect=["5", "5"])
    wiring = OrchestratorWiring(
        cache=cache,
        graph_provider_factory=lambda snapshot: provider,
        embed_query=AsyncMock(return_value=[0.1]),
        vector_search=AsyncMock(return_value=[("u1", 0.9)]),
        graphrag_search=AsyncMock(return_value={"graph_context": {}}),
        chat=AsyncMock(return_value="answer"),
        judge_call=judge_call,
    )
    rows = await run_full_benchmark(cfg, [bundle], wiring=wiring)
    assert any(r.dataset_kind == "chat" for r in rows)
    assert judge_call.await_count == 2
    for call in judge_call.await_args_list:
        assert call.kwargs == {"seed": 43, "temperature": 0.0}


@pytest.mark.asyncio
async def test_a_chat_seed_pass_keeps_the_graph_slot_and_its_provenance_at_the_extraction_seed(
    tmp_path,
):
    """Audit wave 3, end to end: ``chat_seed: 43`` (the seed band's second pass) decodes the chat
    at 43 while the graph comes from the seed-42 slot, and the row says so.
    """
    from chaoscypher_cli.benchmark.graph_cache import SLOT_RECORD, GraphCache
    from chaoscypher_core.settings import EngineSettings

    bundle = _bundle(tmp_path)
    extractor = ModelConfig(provider="ollama", model="ext", label="E")
    cfg = BenchmarkConfig(
        name="band",
        description="",
        seed=42,
        temperature=0.0,
        chat_seed=43,
        dataset_ids=["demo"],
        extractors=[extractor],
        embedders=[ModelConfig(provider="ollama", model="qwen3-embedding:0.6b", label="M")],
        chats=[ModelConfig(provider="anthropic", model="chat", label="C")],
        judge=None,
        config_name="band",
        source="builtin",
    )
    cache = GraphCache(
        root=tmp_path / "gc",
        dedup_embedder="exact",
        extraction_recipe="e1",
        corpus_identity=lambda _cid: ("technical", "c" * 64),
    )
    pins = {"corpus_id": "demo", "corpus_version": "1.0", "extractor": extractor}
    key42 = cache.key_for(**pins, seed=42, temperature=0.0)
    slot = cache.slot_path(key42)
    slot.mkdir(parents=True)
    (slot / "app.db").write_bytes(b"the seed-42 graph")
    cache.write_slot_record(key42, cache.slot_record(**pins, seed=42, temperature=0.0))

    indexed = MagicMock()
    indexed.ctx.settings = EngineSettings()
    indexed.ctx.graph_repository.list_nodes = MagicMock(
        return_value=[SimpleNamespace(id="u1", label="A", aliases=[])]
    )
    served: list[Path] = []

    @asynccontextmanager
    async def fake_indexed_graph(*, embedder=None):
        yield indexed

    def provider_for(snapshot: Path):
        served.append(snapshot)
        return MagicMock(indexed_graph=fake_indexed_graph, snapshot_path=snapshot)

    wiring = OrchestratorWiring(
        cache=cache,
        graph_provider_factory=provider_for,
        embed_query=AsyncMock(return_value=[0.1]),
        vector_search=AsyncMock(return_value=[("u1", 0.9)]),
        graphrag_search=AsyncMock(return_value={"chunks": [{"content": "A is a."}]}),
        chat=AsyncMock(return_value="A."),
        judge_call=AsyncMock(),
    )
    rows = await run_full_benchmark(cfg, [bundle], wiring=wiring, reuse_cached_graph=True)
    chat = [r for r in rows if r.dataset_kind == "chat"]
    assert chat and all(r.seed == 43 for r in chat)
    assert served and set(served) == {slot / "app.db"}
    assert [p.name for p in (tmp_path / "gc").iterdir()] == [key42]  # no seed-43 slot
    for row in chat:
        run = row.extras["run_settings"]
        assert run["graph_cache_key"] == key42 and run["graph_adopted_from"] is None
        assert indexed.ctx.settings.llm.seed == 43  # the chat decoded at the chat seed
    assert (slot / SLOT_RECORD).is_file()
    assert cache.key_for(**pins, seed=43, temperature=0.0) != key42
