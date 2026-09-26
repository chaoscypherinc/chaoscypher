# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""reindex_graph: every node and every chunk re-embedded with one provider."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from chaoscypher_core.benchmark.reindex import reindex_graph
from chaoscypher_core.exceptions import ValidationError


class _FakeIndexing:
    """Records the provider it was built with and the sources it indexes."""

    built: list[dict[str, Any]]
    indexed: list[str]

    def __init__(self, **kwargs: Any) -> None:
        """Capture the constructor kwargs."""
        type(self).built.append(kwargs)

    async def create_index(self, source_id: str) -> dict[str, Any]:
        """Record the source; the chunkless one raises like the real service."""
        if source_id == "empty":
            raise ValidationError("no chunks", field="source_id")
        type(self).indexed.append(source_id)
        return {}


def _target(nodes: list[Any], sources: list[dict[str, str]]) -> MagicMock:
    """A fake engine with the three repositories reindex_graph reads."""
    target = MagicMock()
    target.graph_repository.list_nodes.return_value = nodes
    target.storage_adapter.list_sources.return_value = (sources, len(sources))
    vec = base64.b64encode(np.array([0.5, 0.25], dtype=np.float32).tobytes()).decode()
    target.storage_adapter.iter_chunk_embeddings.return_value = iter([("c1", vec)])
    return target


@pytest.mark.asyncio
async def test_nodes_are_batch_embedded_and_indexed() -> None:
    """One batch_embed over "<label>. <description>", then one index call per node."""
    nodes = [
        SimpleNamespace(id="n1", label="Alpha", description="first"),
        SimpleNamespace(id="n2", label="Beta", description=""),
    ]
    target = _target(nodes, [])
    provider = MagicMock()
    provider.batch_embed = AsyncMock(return_value=SimpleNamespace(embeddings=[[0.1], [0.2]]))
    settings = SimpleNamespace(batching=SimpleNamespace(chunk_fetch_limit=500))

    await reindex_graph(target, provider, settings=settings, node_limit=7)

    target.graph_repository.list_nodes.assert_called_once_with(limit=7)
    (texts,), _ = provider.batch_embed.call_args
    assert texts == ["Alpha. first", "Beta. "]
    calls = target.search_repository.index_node_embedding.call_args_list
    assert [c.args for c in calls] == [("n1", [0.1]), ("n2", [0.2])]


@pytest.mark.asyncio
async def test_chunks_are_reembedded_and_a_chunkless_source_is_skipped() -> None:
    """Chunks go through the product's IndexingService, then into vec_search_chunks."""
    _FakeIndexing.built, _FakeIndexing.indexed = [], []
    target = _target([], [{"id": "s1"}, {"id": "empty"}])
    provider = MagicMock()
    provider.batch_embed = AsyncMock(return_value=SimpleNamespace(embeddings=[]))
    settings = SimpleNamespace(batching=SimpleNamespace(chunk_fetch_limit=500))
    fake_index_mod = SimpleNamespace(IndexingService=_FakeIndexing)
    with patch.dict(
        "sys.modules", {"chaoscypher_core.services.search.engine.index": fake_index_mod}
    ):
        await reindex_graph(target, provider, settings=settings, node_limit=10)

    assert len(_FakeIndexing.built) == 1
    assert _FakeIndexing.built[0]["embedding_service"] is provider
    assert _FakeIndexing.built[0]["repository"] is target.storage_adapter
    assert _FakeIndexing.built[0]["settings"] is settings
    assert _FakeIndexing.indexed == ["s1"]
    target.search_repository.index_embeddings_batch.assert_called_once_with(
        [("chunk:c1", [0.5, 0.25])], item_type="chunk"
    )


@pytest.mark.asyncio
async def test_the_node_limit_defaults_to_the_benchmark_setting() -> None:
    """Without node_limit the benchmark settings' reindex_node_batch_limit applies."""
    from chaoscypher_core.app_config import BenchmarkSettings

    target = _target([], [])
    provider = MagicMock()
    provider.batch_embed = AsyncMock(return_value=SimpleNamespace(embeddings=[]))
    settings = SimpleNamespace(batching=SimpleNamespace(chunk_fetch_limit=500))
    await reindex_graph(target, provider, settings=settings)
    target.graph_repository.list_nodes.assert_called_once_with(
        limit=BenchmarkSettings().reindex_node_batch_limit
    )
