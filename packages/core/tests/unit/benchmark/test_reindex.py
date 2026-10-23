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
from chaoscypher_core.testing.session_fakes import empty_session_scope


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


class _FakeSearch:
    """A per-type vector store with the SearchRepository calls reindex makes.

    ``drop`` names item ids whose write is skipped, as the real repository
    skips a vector of the wrong dimension.
    """

    def __init__(self, drop: frozenset[str] = frozenset()) -> None:
        """Start with one stale vector per type, as a copied snapshot holds."""
        self.vectors: dict[str, dict[str, list[float]]] = {
            "node": {"stale-node": [9.0]},
            "chunk": {"chunk:stale": [9.0]},
        }
        self.batches: list[tuple[list[tuple[str, list[float]]], str]] = []
        self.drop = drop
        self.fulltext_nodes: list[str] | None = None
        self.flag_cleared = False

    def clear_vectors(self, item_type: str) -> None:
        """Forget every vector of one type."""
        self.vectors[item_type] = {}

    def index_embeddings_batch(
        self, embeddings: list[tuple[str, list[float]]], item_type: str = "node"
    ) -> int:
        """Store the vectors (all but the dropped ones) and return how many were stored."""
        self.batches.append((embeddings, item_type))
        kept = [(i, v) for i, v in embeddings if i not in self.drop]
        self.vectors[item_type].update(kept)
        return len(kept)

    def count_vectors(self, item_type: str) -> int:
        """How many vectors of one type are stored."""
        return len(self.vectors[item_type])

    def rebuild_node_fulltext(self, nodes: list[Any]) -> int:
        """Record the nodes the full-text index is rebuilt from."""
        self.fulltext_nodes = [n.id for n in nodes]
        return len(nodes)

    def clear_reindex_flag(self) -> None:
        """Record that the needs_full_reindex flag was cleared."""
        self.flag_cleared = True


def _target(
    nodes: list[Any],
    sources: list[dict[str, str]],
    *,
    chunks_per_source: int = 1,
    drop: frozenset[str] = frozenset(),
) -> MagicMock:
    """A fake engine with the three repositories reindex_graph reads."""
    target = MagicMock()
    target.graph_repository.list_nodes.return_value = nodes
    target.storage_adapter.list_sources.return_value = (sources, len(sources))
    target.storage_adapter.get_chunks_by_source.return_value = ([], chunks_per_source)
    vec = base64.b64encode(np.array([0.5, 0.25], dtype=np.float32).tobytes()).decode()
    target.storage_adapter.iter_chunk_embeddings.side_effect = lambda *_a, **_k: iter([("c1", vec)])
    target.search_repository = _FakeSearch(drop)
    target.storage_adapter.session_scope = empty_session_scope
    return target


@pytest.mark.asyncio
async def test_nodes_are_batch_embedded_and_indexed() -> None:
    """One batch_embed over the production entity text (label | description), then one index call per node."""
    nodes = [
        SimpleNamespace(id="n1", label="Alpha", properties={"definition": "first"}),
        SimpleNamespace(id="n2", label="Beta", properties={}),
    ]
    target = _target(nodes, [])
    provider = MagicMock()
    provider.batch_embed = AsyncMock(return_value=SimpleNamespace(embeddings=[[0.1], [0.2]]))
    settings = SimpleNamespace(batching=SimpleNamespace(chunk_fetch_limit=500))

    await reindex_graph(target, provider, settings=settings, node_limit=7)

    target.graph_repository.list_nodes.assert_called_once_with(limit=7)
    (texts,), _ = provider.batch_embed.call_args
    assert texts == ["Alpha | first", "Beta"]
    # the snapshot's stale vector is gone; exactly the re-embedded nodes remain
    assert target.search_repository.vectors["node"] == {"n1": [0.1], "n2": [0.2]}
    # the keyword index follows the same nodes, and the stale-index flag is cleared
    assert target.search_repository.fulltext_nodes == ["n1", "n2"]
    assert target.search_repository.flag_cleared is True


@pytest.mark.asyncio
async def test_chunks_are_reembedded_and_a_chunkless_source_is_skipped() -> None:
    """Chunks go through the product's IndexingService, then into vec_search_chunks."""
    _FakeIndexing.built, _FakeIndexing.indexed = [], []
    target = _target([], [{"id": "s1"}, {"id": "empty"}])
    target.storage_adapter.get_chunks_by_source.side_effect = lambda sid, **_: (
        [],
        1 if sid == "s1" else 0,
    )
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
    assert target.search_repository.vectors["chunk"] == {"chunk:c1": [0.5, 0.25]}


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


@pytest.mark.asyncio
async def test_a_node_the_index_did_not_take_fails_the_reindex() -> None:
    """A skipped node vector (wrong dimension) raises before anyone writes READY (audit R8)."""
    from chaoscypher_core.benchmark.reindex import ReindexIncompleteError

    nodes = [
        SimpleNamespace(id="n1", label="A", properties={}),
        SimpleNamespace(id="n2", label="B", properties={}),
    ]
    target = _target(nodes, [], drop=frozenset({"n2"}))
    provider = MagicMock()
    provider.batch_embed = AsyncMock(return_value=SimpleNamespace(embeddings=[[0.1], [0.2]]))
    settings = SimpleNamespace(batching=SimpleNamespace(chunk_fetch_limit=500))
    with pytest.raises(ReindexIncompleteError, match="node"):
        await reindex_graph(target, provider, settings=settings, node_limit=10)


@pytest.mark.asyncio
async def test_a_chunk_left_without_a_vector_fails_the_reindex() -> None:
    """A source with two chunks but one embedded vector raises (no READY over a short index)."""
    from chaoscypher_core.benchmark.reindex import ReindexIncompleteError

    _FakeIndexing.built, _FakeIndexing.indexed = [], []
    target = _target([], [{"id": "s1"}], chunks_per_source=2)
    provider = MagicMock()
    provider.batch_embed = AsyncMock(return_value=SimpleNamespace(embeddings=[]))
    settings = SimpleNamespace(batching=SimpleNamespace(chunk_fetch_limit=500))
    fake_index_mod = SimpleNamespace(IndexingService=_FakeIndexing)
    with (
        patch.dict(
            "sys.modules", {"chaoscypher_core.services.search.engine.index": fake_index_mod}
        ),
        pytest.raises(ReindexIncompleteError, match="chunk"),
    ):
        await reindex_graph(target, provider, settings=settings, node_limit=10)


@pytest.mark.asyncio
async def test_a_graph_at_the_node_limit_is_refused() -> None:
    """``list_nodes(limit)`` returning ``limit`` nodes may have cut the graph: refuse."""
    from chaoscypher_core.benchmark.reindex import ReindexIncompleteError

    nodes = [SimpleNamespace(id=f"n{i}", label="A", properties={}) for i in range(3)]
    target = _target(nodes, [])
    provider = MagicMock()
    provider.batch_embed = AsyncMock()
    settings = SimpleNamespace(batching=SimpleNamespace(chunk_fetch_limit=500))
    with pytest.raises(ReindexIncompleteError, match="at least 3 nodes"):
        await reindex_graph(target, provider, settings=settings, node_limit=3)
    provider.batch_embed.assert_not_called()


@pytest.mark.asyncio
async def test_the_stored_sentence_offsets_follow_the_live_splitter(tmp_path) -> None:
    """A reindex rewrites stale ``sentence_offsets`` (audit wave 3) through the migration's backfill."""
    import json
    import sqlite3

    from chaoscypher_core.benchmark.reindex import recompute_sentence_offsets
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences_with_offsets,
    )

    content = '"Stop," she said. He did not stop. "Why?"'
    conn = sqlite3.connect(tmp_path / "app.db")
    conn.execute(
        "CREATE TABLE document_chunks (id TEXT PRIMARY KEY, content TEXT, chunk_metadata TEXT)"
    )
    conn.execute(
        "INSERT INTO document_chunks VALUES (?, ?, ?)",
        ("c1", content, json.dumps({"sentence_offsets": [[0, len(content)]], "keep": 1})),
    )
    conn.commit()

    class _Session:
        """The adapter's session over the raw connection, counting commits."""

        commits = 0

        def execute(self, statement, params=None):
            return conn.execute(str(statement), params or {})

        def commit(self) -> None:
            conn.commit()
            _Session.commits += 1

    class _Scope:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *exc):
            return False

    target = MagicMock()
    target.storage_adapter.session_scope = lambda: _Scope()

    assert await recompute_sentence_offsets(target) == 1
    meta = json.loads(conn.execute("SELECT chunk_metadata FROM document_chunks").fetchone()[0])
    assert meta == {"sentence_offsets": split_into_sentences_with_offsets(content), "keep": 1}
    assert _Session.commits == 1
    assert await recompute_sentence_offsets(target) == 0  # idempotent: nothing to commit
    assert _Session.commits == 1
