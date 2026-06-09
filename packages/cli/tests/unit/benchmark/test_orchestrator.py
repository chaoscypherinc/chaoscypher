# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
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
        domain="technical",
        corpus_path=corpus,
        source="builtin",
        extraction_dataset=ExtractionDataset(
            id="demo",
            version="1.0",
            corpus_path=corpus,
            domain="technical",
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
    indexed.ctx.storage_adapter.list_entities = MagicMock(
        return_value=[{"id": "u1", "name": "A", "aliases": []}]
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
    indexed.ctx.storage_adapter.list_entities = MagicMock(
        return_value=[{"id": "u1", "name": "A", "aliases": []}]
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
    snapshot_file.write_bytes(b"fake-db")

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
        *, corpus_id: str, corpus_version: str, extractor: ModelConfig, builder: object
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
