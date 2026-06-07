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

    embedder = ModelConfig(provider="ollama", model="nomic-embed-text", label="N")
    async with provider.indexed_graph(embedder=embedder) as graph:
        assert isinstance(graph, IndexedGraph)
        assert graph.ctx is fake_ctx

    reindex.assert_awaited_once()
    fake_ctx.disconnect.assert_called_once()


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
    embedder = ModelConfig(provider="ollama", model="nomic", label="N")

    with pytest.raises(RuntimeError):
        async with provider.indexed_graph(embedder=embedder):
            pass

    fake_ctx.disconnect.assert_called_once()
