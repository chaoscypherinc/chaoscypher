# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_cli.benchmark.graph_provider import GraphProvider, IndexedGraph
from chaoscypher_cli.benchmark.models import ModelConfig


@pytest.mark.asyncio
async def test_indexed_graph_calls_reindex_with_embedder(tmp_path):
    snapshot = tmp_path / "app.db"
    snapshot.write_bytes(b"sqlite")

    fake_ctx = MagicMock()
    fake_ctx.connect = MagicMock()
    fake_ctx.disconnect = MagicMock()
    reindex = AsyncMock()

    provider = GraphProvider(
        snapshot_path=snapshot,
        ctx_factory=lambda db: fake_ctx,
        reindex=reindex,
        workspace=tmp_path / "ws",
    )

    embedder = ModelConfig(provider="ollama", model="qwen3-embedding:0.6b", label="N")
    async with provider.indexed_graph(embedder=embedder) as graph:
        assert isinstance(graph, IndexedGraph)
        assert graph.ctx is fake_ctx

    reindex.assert_awaited_once()
    # the staging ctx the index was built on, then the run's ctx
    assert fake_ctx.disconnect.call_count == 2


@pytest.mark.asyncio
async def test_indexed_graph_disconnects_on_exception(tmp_path):
    snapshot = tmp_path / "app.db"
    snapshot.write_bytes(b"sqlite")

    fake_ctx = MagicMock()
    reindex = AsyncMock(side_effect=RuntimeError("boom"))

    provider = GraphProvider(
        snapshot_path=snapshot,
        ctx_factory=lambda db: fake_ctx,
        reindex=reindex,
        workspace=tmp_path / "ws",
    )
    embedder = ModelConfig(provider="ollama", model="qwen3-embedding:0.6b", label="N")

    with pytest.raises(RuntimeError):
        async with provider.indexed_graph(embedder=embedder):
            pass

    fake_ctx.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_the_index_is_built_once_per_snapshot_and_embedder_and_each_run_selects_it(tmp_path):
    """Every run copies the kept index: one re-embed, however many models run on the graph."""
    snapshot = tmp_path / "app.db"
    snapshot.write_bytes(b"sqlite")
    built: list[str] = []
    selected: list[str] = []

    async def reindex(ctx, embedder):
        built.append(embedder.model)
        ctx.db.write_bytes(b"indexed with " + embedder.model.encode())

    def ctx_factory(db):
        ctx = MagicMock()
        ctx.db = db
        return ctx

    provider = GraphProvider(
        snapshot_path=snapshot,
        ctx_factory=ctx_factory,
        reindex=reindex,
        workspace=tmp_path / "ws",
        select_embedder=lambda ctx, embedder: selected.append(embedder.model),
    )
    a = ModelConfig(provider="ollama", model="qwen3-embedding:0.6b", label="A")
    b = ModelConfig(provider="ollama", model="bge-m3", label="B")
    for embedder in (a, a, a, b):
        async with provider.indexed_graph(embedder=embedder) as graph:
            assert graph.snapshot_copy.read_bytes() == b"indexed with " + embedder.model.encode()
    assert built == ["qwen3-embedding:0.6b", "bge-m3"]
    assert selected == ["qwen3-embedding:0.6b"] * 3 + ["bge-m3"]

    snapshot.write_bytes(b"another graph")  # a re-extracted graph builds its own index
    async with provider.indexed_graph(embedder=a):
        pass
    assert built == ["qwen3-embedding:0.6b", "bge-m3", "qwen3-embedding:0.6b"]

    async with provider.indexed_graph() as graph:  # no embedder: the snapshot as it is
        assert graph.snapshot_copy.read_bytes() == b"another graph"
    assert built == ["qwen3-embedding:0.6b", "bge-m3", "qwen3-embedding:0.6b"]


@pytest.mark.asyncio
async def test_an_embedder_of_unknown_size_is_refused_as_select_embedder_refuses_it(tmp_path):
    """One rule for an unregistered embedder: no index is built for it (audit wave 2).

    The kept-index key used to fall back to "cfgd" and build at the configured
    size while the orchestrator's select_embedder raised for the same model.
    """
    from chaoscypher_cli.benchmark.orchestrator import select_embedder
    from chaoscypher_core.benchmark.reference import ReferencePackError

    snapshot = tmp_path / "app.db"
    snapshot.write_bytes(b"sqlite")
    reindex = AsyncMock()
    provider = GraphProvider(
        snapshot_path=snapshot,
        ctx_factory=lambda db: MagicMock(),
        reindex=reindex,
        workspace=tmp_path / "ws",
    )
    unknown = ModelConfig(provider="ollama", model="never-registered-embed", label="U")
    with pytest.raises(ReferencePackError, match="native vector size"):
        async with provider.indexed_graph(embedder=unknown):
            pass
    reindex.assert_not_awaited()
    with pytest.raises(ReferencePackError, match="native vector size"):
        select_embedder(MagicMock(), unknown)
