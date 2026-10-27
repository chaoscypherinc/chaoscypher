# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``embed_missing``: the rows written while vector search was off get their vectors, once.

Two engines share one database: a keyword-only one (vector search off, the default
dimensions) writes two memories and a node, then a vector one (8 dimensions, a fake
embedder) writes one memory and one node with their vectors. The service then runs
with the vector engine's repositories.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from sqlalchemy import text

from chaoscypher_core import Engine
from chaoscypher_core.services.graph.management.release import reembed_nodes
from chaoscypher_core.services.search.engine.embed_missing import (
    EmbedMissingCounts,
    embed_missing,
)
from chaoscypher_core.services.search.engine.index import index_source_chunk_vectors
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.embedding import FakeEmbeddingProvider
from chaoscypher_core.utils.id import generate_hex_id


if TYPE_CHECKING:
    from chaoscypher_core.models import BatchEmbedResult


DIMS = 8


class _RecordingProvider(FakeEmbeddingProvider):
    """The fake embedder, keeping every text it was asked to embed."""

    def __init__(self, dimensions: int = DIMS) -> None:
        super().__init__(dimensions=dimensions)
        self.texts: list[str] = []

    async def batch_embed(self, texts: list[str], batch_size: int = 64) -> BatchEmbedResult:
        self.texts.extend(texts)
        return await super().batch_embed(texts, batch_size=batch_size)


def _engine(path: Path, *, vectors: bool, provider: Any = None) -> Engine:
    settings = EngineSettings()
    settings.search.enable_vector_search = vectors
    if vectors:
        settings.search.vector_dimensions = DIMS
    with patch(
        "chaoscypher_core.adapters.embedding.factory.create_embedding_provider",
        return_value=provider,
    ):
        return Engine(str(path / "databases" / "test"), settings=settings)


async def _remember(engine: Engine, text: str) -> str:
    source_id = generate_hex_id()
    await engine.process_document(
        text,
        source_id=source_id,
        filename="memory.txt",
        auto_confirm=True,
        source_type="memory",
        extract=False,
    )
    return source_id


@pytest.fixture
async def two_engines(tmp_path: Path) -> Generator[SimpleNamespace]:
    # the keyword-only period first (its database opened at the default dimensions) ...
    off = _engine(tmp_path, vectors=False)
    lise = await _remember(off, "Lise sings.")
    anna = await _remember(off, "Anna keeps bees in Lyon.")
    off.add_node("Person", "Lise")
    off.close()

    # ... then vector search on, at another dimension, with rows of its own
    provider = _RecordingProvider()
    on = _engine(tmp_path, vectors=True, provider=provider)
    pierre = await _remember(on, "Pierre Bezukhov moved to Lyon.")
    index_source_chunk_vectors(
        on.storage_adapter, on.search_repository, pierre, page_size=100
    )  # as a commit pushes them
    node = on.add_node("Person", "Pierre Bezukhov")
    await reembed_nodes(on.graph_repository, [node.id], provider, search=on.search_repository)

    provider.texts.clear()
    yield SimpleNamespace(
        engine=on, provider=provider, pierre=pierre, lise=lise, anna=anna, node=node.id
    )
    on.close()


async def _run(world: SimpleNamespace, provider: Any = None) -> EmbedMissingCounts:
    engine = world.engine
    return await embed_missing(
        storage=engine.storage_adapter,
        graph=engine.graph_repository,
        search=engine.search_repository,
        provider=provider or world.provider,
        settings=engine.settings,
    )


def _chunk(engine: Engine, source_id: str) -> dict[str, Any]:
    engine.storage_adapter.session.expire_all()
    (chunk,), _ = engine.storage_adapter.get_chunks_by_source(source_id, include_embeddings=True)
    return chunk


async def test_embed_missing_embeds_each_missing_row_once_and_pushes_the_vectors(
    two_engines: SimpleNamespace,
) -> None:
    search = two_engines.engine.search_repository
    assert search.count_vectors("chunk") == 1 and search.count_vectors("node") == 1

    counts = await _run(two_engines)

    assert counts == EmbedMissingCounts(chunks=2, nodes=1)
    assert sorted(two_engines.provider.texts) == sorted(
        ["Lise sings.", "Anna keeps bees in Lyon.", "Lise"]
    )
    assert search.count_vectors("chunk") == 3 and search.count_vectors("node") == 2
    assert _chunk(two_engines.engine, two_engines.lise)["embedding"] is not None
    assert await _run(two_engines) == EmbedMissingCounts(chunks=0, nodes=0)


async def test_a_committed_chunk_keeps_its_status(two_engines: SimpleNamespace) -> None:
    engine = two_engines.engine
    engine.storage_adapter.update_chunk_status(two_engines.anna, "committed")

    await _run(two_engines)

    chunk = _chunk(engine, two_engines.anna)
    assert chunk["status"] == "committed"
    assert chunk["embedding"] is not None


async def test_a_node_the_provider_cannot_embed_ends_the_run(
    two_engines: SimpleNamespace,
) -> None:
    class _NoVectorForLise(_RecordingProvider):
        async def batch_embed(self, texts: list[str], batch_size: int = 64) -> BatchEmbedResult:
            result = await super().batch_embed(texts, batch_size=batch_size)
            embeddings = [
                [] if t == "Lise" else v for t, v in zip(texts, result.embeddings, strict=True)
            ]
            return result.model_copy(update={"embeddings": embeddings})

    graph = two_engines.engine.graph_repository
    calls: list[str | None] = []
    original = graph.list_nodes_without_embeddings

    def _spy(*, after_id: str | None = None, limit: int) -> Any:
        calls.append(after_id)
        return original(after_id=after_id, limit=limit)

    two_engines.engine.settings.batching.embedding_wave_size = 1
    with patch.object(graph, "list_nodes_without_embeddings", _spy):
        await _run(two_engines, provider=_NoVectorForLise())

    lise = next(n for n in graph.list_nodes(include_embedding=True) if n.label == "Lise")
    assert not lise.embedding
    assert calls[0] is None
    assert calls[1:] == [lise.id]  # the cursor moved past Lise and the run ended


async def test_each_touched_source_is_pushed_once(
    two_engines: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaoscypher_core.services.search.engine import embed_missing as module

    engine = two_engines.engine
    long_text = "\n\n".join(
        f"Paragraph {i}. Lise sang in Lyon, and the hall was full of people who had come "
        "to hear her; the evening went on, and the songs were long and slow." * 3
        for i in range(6)
    )
    source = await _remember(engine, long_text)
    engine.storage_adapter.session.execute(
        text("UPDATE document_chunks SET embedding = NULL WHERE source_id = :s"), {"s": source}
    )
    engine.storage_adapter.session.commit()
    _, total = engine.storage_adapter.get_chunks_by_source(source)
    assert total >= 2
    pushed: list[str] = []
    original = module.index_source_chunk_vectors

    def _count(adapter: Any, search: Any, source_id: str, *, page_size: int) -> int:
        pushed.append(source_id)
        return original(adapter, search, source_id, page_size=page_size)

    monkeypatch.setattr(module, "index_source_chunk_vectors", _count)
    engine.settings.batching.embedding_wave_size = 1

    counts = await _run(two_engines)

    assert counts.chunks == total + 2
    assert sorted(pushed) == sorted({source, two_engines.lise, two_engines.anna})
