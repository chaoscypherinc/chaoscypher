# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The embedding cache wrapper: misses go to the inner provider in one batch, in input order."""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.cache.embedding import EMBEDDINGS_NAMESPACE, CachedEmbeddingProvider
from chaoscypher_core.cache.sqlite_store import SqliteCacheStore
from chaoscypher_core.exceptions import LLMError, ValidationError
from chaoscypher_core.models import BatchEmbedResult, EmbedResult
from chaoscypher_core.ports.embedding import EmbeddingHealthStatus, EmbeddingProviderProtocol


_BUILD = "ollama:qwen3-embedding:4b:sha256:abc@0.12.1"


class _FakeEmbedder:
    """An embedding provider that counts what it is asked and answers a vector per text."""

    query_prefix = ""

    def __init__(self, dims: int = 4) -> None:
        self.model_name = "qwen3-embedding:4b"
        self.vector_dimensions = dims
        self.native_dimensions = 2560
        self.calls: list[list[str]] = []

    @property
    def provider_type(self) -> str:
        return "ollama"

    def vector(self, text: str) -> list[float]:
        """The deterministic vector of ``text``."""
        return [float(len(text)) + i / 10 for i in range(self.vector_dimensions)]

    async def embed(self, text: str) -> EmbedResult:
        self.calls.append([text])
        return EmbedResult(embedding=self.vector(text), provider="ollama")

    async def batch_embed(self, texts: list[str], batch_size: int = 64) -> BatchEmbedResult:
        self.calls.append(list(texts))
        return BatchEmbedResult(
            embeddings=[self.vector(t) for t in texts], total=len(texts), provider="ollama"
        )

    async def check_health(self) -> EmbeddingHealthStatus:
        return EmbeddingHealthStatus(healthy=True, provider="ollama", model=self.model_name)


def _store(tmp_path: Path) -> SqliteCacheStore:
    return SqliteCacheStore(tmp_path / "e.db", max_bytes=1 << 24)


@pytest.mark.asyncio
async def test_a_batch_with_two_misses_and_three_hits_calls_inner_once_with_the_misses(
    tmp_path: Path,
) -> None:
    inner = _FakeEmbedder(dims=4)
    store = _store(tmp_path)
    cached = CachedEmbeddingProvider(inner, store, model_build=_BUILD)
    await cached.batch_embed(["a", "b", "c"])
    out = await cached.batch_embed(["a", "dd", "b", "eee", "c"])
    assert inner.calls == [["a", "b", "c"], ["dd", "eee"]]
    assert out.total == 5 and out.provider == "ollama"
    assert out.embeddings == [inner.vector(t) for t in ["a", "dd", "b", "eee", "c"]]
    s = store.stats(EMBEDDINGS_NAMESPACE, 1 << 24)
    assert (s.hits, s.misses, s.entries) == (3, 5, 5)


@pytest.mark.asyncio
async def test_a_batch_of_hits_only_never_calls_inner(tmp_path: Path) -> None:
    inner = _FakeEmbedder()
    cached = CachedEmbeddingProvider(inner, _store(tmp_path), model_build=_BUILD)
    await cached.batch_embed(["a", "b"])
    out = await cached.batch_embed(["b", "a"])
    assert inner.calls == [["a", "b"]]
    assert out.embeddings == [inner.vector("b"), inner.vector("a")]


@pytest.mark.asyncio
async def test_a_text_twice_in_one_batch_is_embedded_once(tmp_path: Path) -> None:
    inner = _FakeEmbedder()
    cached = CachedEmbeddingProvider(inner, _store(tmp_path), model_build=_BUILD)
    out = await cached.batch_embed(["a", "b", "a"])
    assert inner.calls == [["a", "b"]]
    assert out.embeddings == [inner.vector("a"), inner.vector("b"), inner.vector("a")]


@pytest.mark.asyncio
async def test_embed_and_batch_embed_share_entries(tmp_path: Path) -> None:
    inner = _FakeEmbedder()
    store = _store(tmp_path)
    cached = CachedEmbeddingProvider(inner, store, model_build=_BUILD)
    first = await cached.embed("query")
    second = await cached.embed("query")
    batch = await cached.batch_embed(["query"])
    assert inner.calls == [["query"]]
    assert first.embedding == second.embedding == batch.embeddings[0] == inner.vector("query")
    assert second.provider == "ollama"
    assert store.stats(EMBEDDINGS_NAMESPACE, 1 << 24).hits == 2


@pytest.mark.asyncio
async def test_another_model_build_or_dimension_misses(tmp_path: Path) -> None:
    store = _store(tmp_path)
    inner = _FakeEmbedder(dims=4)
    await CachedEmbeddingProvider(inner, store, model_build=_BUILD).embed("a")
    await CachedEmbeddingProvider(inner, store, model_build=_BUILD + "x").embed("a")
    other_dims = _FakeEmbedder(dims=8)
    await CachedEmbeddingProvider(other_dims, store, model_build=_BUILD).embed("a")
    assert inner.calls == [["a"], ["a"]] and other_dims.calls == [["a"]]


@pytest.mark.asyncio
async def test_disabled_passes_through_and_records_bypasses(tmp_path: Path) -> None:
    inner = _FakeEmbedder()
    store = _store(tmp_path)
    cached = CachedEmbeddingProvider(inner, store, model_build=_BUILD, enabled=False)
    await cached.batch_embed(["a", "b"])
    await cached.batch_embed(["a", "b"])
    await cached.embed("a")
    assert inner.calls == [["a", "b"], ["a", "b"], ["a"]]
    s = store.stats(EMBEDDINGS_NAMESPACE, 1 << 24)
    assert (s.entries, s.hits, s.misses, s.bypasses) == (0, 0, 0, 5)


@pytest.mark.asyncio
async def test_the_model_build_is_resolved_lazily_once(tmp_path: Path) -> None:
    inner = _FakeEmbedder()
    asked: list[int] = []

    async def resolve() -> str | None:
        asked.append(1)
        return _BUILD

    cached = CachedEmbeddingProvider(inner, _store(tmp_path), resolve_build=resolve)
    assert asked == []  # no network at wrap time
    await cached.embed("a")
    await cached.embed("a")
    assert asked == [1] and inner.calls == [["a"]]


@pytest.mark.asyncio
async def test_an_unresolvable_build_bypasses_instead_of_keying_on_nothing(
    tmp_path: Path,
) -> None:
    inner = _FakeEmbedder()
    store = _store(tmp_path)

    async def resolve() -> str | None:
        return None

    cached = CachedEmbeddingProvider(inner, store, resolve_build=resolve)
    await cached.embed("a")
    await cached.embed("a")
    assert inner.calls == [["a"], ["a"]]
    assert store.stats(EMBEDDINGS_NAMESPACE, 1 << 24).bypasses == 2


@pytest.mark.asyncio
async def test_require_hit_refuses_a_miss(tmp_path: Path) -> None:
    inner = _FakeEmbedder()
    store = _store(tmp_path)
    await CachedEmbeddingProvider(inner, store, model_build=_BUILD).embed("a")
    replay = CachedEmbeddingProvider(inner, store, model_build=_BUILD, require_hit=True)
    assert (await replay.embed("a")).embedding == inner.vector("a")
    with pytest.raises(ValidationError, match="replay miss"):
        await replay.batch_embed(["a", "b"])
    assert inner.calls == [["a"]]


@pytest.mark.asyncio
async def test_a_broken_store_never_breaks_an_embedding(tmp_path: Path) -> None:
    inner = _FakeEmbedder()
    store = _store(tmp_path)
    store.close()  # every store call now raises sqlite3.ProgrammingError
    cached = CachedEmbeddingProvider(inner, store, model_build=_BUILD)
    out = await cached.batch_embed(["a", "b"])
    assert out.embeddings == [inner.vector("a"), inner.vector("b")]


@pytest.mark.asyncio
async def test_everything_else_delegates_and_the_protocol_holds(tmp_path: Path) -> None:
    inner = _FakeEmbedder()
    cached = CachedEmbeddingProvider(inner, _store(tmp_path), model_build=_BUILD)
    assert isinstance(cached, EmbeddingProviderProtocol)
    assert cached.inner is inner
    assert cached.provider_type == "ollama" and cached.model_name == "qwen3-embedding:4b"
    assert cached.native_dimensions == 2560 and cached.vector_dimensions == 4
    assert cached.query_prefix == ""
    assert (await cached.check_health()).healthy is True
    with pytest.raises(AttributeError):
        _ = cached.no_such_attribute


@pytest.mark.asyncio
async def test_an_empty_batch_is_the_inner_providers_answer(tmp_path: Path) -> None:
    inner = _FakeEmbedder()
    out = await CachedEmbeddingProvider(inner, _store(tmp_path), model_build=_BUILD).batch_embed([])
    assert out.total == 0 and out.embeddings == []


@pytest.mark.asyncio
async def test_a_provider_that_breaks_the_all_or_nothing_contract_raises_and_stores_nothing(
    tmp_path: Path,
) -> None:
    class _Short(_FakeEmbedder):
        async def batch_embed(self, texts: list[str], batch_size: int = 64) -> BatchEmbedResult:
            self.calls.append(list(texts))
            return BatchEmbedResult(embeddings=[self.vector(texts[0])], total=1, provider="ollama")

    inner = _Short()
    store = _store(tmp_path)
    with pytest.raises(LLMError, match="1 vectors for 2 texts"):
        await CachedEmbeddingProvider(inner, store, model_build=_BUILD).batch_embed(["a", "b"])
    assert inner.calls == [["a", "b"]]
    assert store.stats(EMBEDDINGS_NAMESPACE, 1 << 24).entries == 0
