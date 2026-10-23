# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Replay: pinned builds, refusals under ``require_hit``, the shared value decode, degenerate answers."""

from __future__ import annotations

import copy
import sqlite3
import zlib
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.cache.build import pinned_build, pinned_builds
from chaoscypher_core.cache.chat import (
    COMPLETIONS_NAMESPACE,
    CachedChatProvider,
    is_degenerate_answer,
)
from chaoscypher_core.cache.embedding import EMBEDDINGS_NAMESPACE, CachedEmbeddingProvider
from chaoscypher_core.cache.keys import build_names_model, model_build_id
from chaoscypher_core.cache.sqlite_store import SqliteCacheStore
from chaoscypher_core.cache.store import CacheEntry, stored_value
from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.models import BatchEmbedResult, EmbedResult


_MESSAGES = [{"role": "user", "content": "Who kept the accounts?"}]
_CAP = 1 << 24
_CHAT_BUILD = model_build_id("ollama", "qwen3:4b", digest="abc123", server_version="0.12.1")
_EMBED_BUILD = model_build_id(
    "ollama", "qwen3-embedding:4b", digest="def456", server_version="0.12.1"
)


def _answer(content: str = "Pierre.", tool_calls: Any = None) -> dict[str, Any]:
    """A provider's clean non-stream answer."""
    return {
        "content": content,
        "tool_calls": tool_calls,
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        "finish_reason": "stop",
    }


class _Chat:
    """A pinned Ollama chat provider that answers a scripted response and counts its calls."""

    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.config = {"chat_provider": "ollama", "seed": 42, "ai_temperature": 0.0}
        self.chat_model = "qwen3:4b"
        self.response = response if response is not None else _answer()
        self.calls = 0

    async def chat(self, messages: Any, **_: Any) -> Any:
        self.calls += 1
        return copy.deepcopy(self.response)


class _Embedder:
    """An Ollama embedding provider that counts the texts it embeds."""

    query_prefix = ""
    vector_dimensions = 2

    def __init__(self) -> None:
        self.model_name = "qwen3-embedding:4b"
        self.calls = 0

    @property
    def provider_type(self) -> str:
        return "ollama"

    async def embed(self, text: str) -> EmbedResult:
        self.calls += 1
        return EmbedResult(embedding=[float(len(text)), 1.0], provider="ollama")

    async def batch_embed(self, texts: list[str], batch_size: int = 64) -> BatchEmbedResult:
        self.calls += 1
        return BatchEmbedResult(
            embeddings=[[float(len(t)), 1.0] for t in texts], total=len(texts), provider="ollama"
        )


async def _no_digest(*_: Any) -> str | None:
    """A resolver whose server cannot say the digest (Ollama unreachable)."""
    return None


def _store(tmp_path: Path) -> SqliteCacheStore:
    return SqliteCacheStore(tmp_path / "c.db", max_bytes=_CAP)


# --------------------------------------------------------------- pinned builds


def test_pinned_builds_nest_and_restore() -> None:
    assert pinned_build("ollama", "m") is None
    with pinned_builds({("ollama", "m"): "b1"}):
        with pinned_builds({("ollama", "n"): "b2"}):
            assert (pinned_build("ollama", "m"), pinned_build("ollama", "n")) == ("b1", "b2")
        assert pinned_build("ollama", "n") is None
    assert pinned_build("ollama", "m") is None


@pytest.mark.asyncio
async def test_a_replay_with_a_full_store_makes_zero_model_calls_and_asks_no_digest(
    tmp_path: Path,
) -> None:
    """Recorded under the pack's build, replayed with that build pinned and no server."""
    store = _store(tmp_path)
    recorded = _Chat()

    async def digest(_model: str) -> str:
        return _CHAT_BUILD

    await CachedChatProvider(recorded, store, build_resolver=digest).chat(_MESSAGES)
    assert recorded.calls == 1

    asked: list[str] = []

    async def must_not_ask(model: str) -> str | None:
        asked.append(model)
        return None

    inner = _Chat()
    replay = CachedChatProvider(inner, store, build_resolver=must_not_ask, require_hit=True)
    with pinned_builds({("ollama", "qwen3:4b"): _CHAT_BUILD}):
        assert await replay.chat(_MESSAGES) == _answer()
        with pytest.raises(ValidationError, match="replay miss"):
            await replay.chat([{"role": "user", "content": "A question never recorded"}])
    assert inner.calls == 0 and asked == []


@pytest.mark.asyncio
async def test_under_require_hit_an_unknown_build_refuses_in_both_wrappers(
    tmp_path: Path,
) -> None:
    """Ollama unreachable: no digest, so a replay cannot key the call and must not call the model."""
    chat = _Chat()
    replay_chat = CachedChatProvider(
        chat, _store(tmp_path), build_resolver=_no_digest, require_hit=True
    )
    with pytest.raises(ValidationError, match="replay needs the model build of ollama:qwen3:4b"):
        await replay_chat.chat(_MESSAGES)
    embedder = _Embedder()
    replay_embed = CachedEmbeddingProvider(
        embedder, _store(tmp_path), resolve_build=_no_digest, require_hit=True
    )
    with pytest.raises(ValidationError, match="replay needs the model build"):
        await replay_embed.embed("Pierre")
    assert chat.calls == 0 and embedder.calls == 0
    # outside a replay the same unknown build bypasses to the model
    assert await CachedChatProvider(chat, _store(tmp_path), build_resolver=_no_digest).chat(
        _MESSAGES
    )
    assert chat.calls == 1


@pytest.mark.asyncio
async def test_under_require_hit_a_call_the_store_cannot_key_refuses(tmp_path: Path) -> None:
    inner = _Chat()
    replay = CachedChatProvider(inner, _store(tmp_path), require_hit=True)
    with pytest.raises(ValidationError, match="replay cannot serve this call"):
        await replay.chat(_MESSAGES, temperature=0.7)
    assert inner.calls == 0


@pytest.mark.asyncio
async def test_an_embedding_replay_keys_on_the_pinned_build(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await CachedEmbeddingProvider(_Embedder(), store, model_build=_EMBED_BUILD).embed("Pierre")
    inner = _Embedder()
    replay = CachedEmbeddingProvider(inner, store, resolve_build=_no_digest, require_hit=True)
    with pinned_builds({("ollama", "qwen3-embedding:4b"): _EMBED_BUILD}):
        assert (await replay.embed("Pierre")).embedding == [6.0, 1.0]
        with pytest.raises(ValidationError, match="replay miss"):
            await replay.batch_embed(["Natasha"])
    assert inner.calls == 0


# --------------------------------------------------------------- shared decode


def _entry(value: bytes) -> CacheEntry:
    return CacheEntry(
        value=value,
        model_build=_CHAT_BUILD,
        call_site="other",
        seeded=True,
        latency_ms=1,
        bytes=len(value),
    )


@pytest.mark.parametrize(
    "value",
    [b"not zlib at all", zlib.compress(b"{not json"), zlib.compress(b"\xff\xfe")],
)
def test_the_shared_decode_reads_a_corrupt_value_as_a_miss(value: bytes) -> None:
    assert stored_value(_entry(value), namespace="n", key="k", shaped=lambda _v: True) is None


@pytest.mark.asyncio
async def test_a_corrupt_stored_value_is_a_miss_in_both_wrappers(tmp_path: Path) -> None:
    """Both wrappers read through the one decode: a damaged value costs a model call, never a crash."""
    store = _store(tmp_path)
    chat = _Chat()
    cached_chat = CachedChatProvider(chat, store, build_resolver=lambda _m: _const(_CHAT_BUILD))
    await cached_chat.chat(_MESSAGES)
    embedder = _Embedder()
    cached_embed = CachedEmbeddingProvider(embedder, store, model_build=_EMBED_BUILD)
    await cached_embed.embed("Pierre")
    for namespace in (COMPLETIONS_NAMESPACE, EMBEDDINGS_NAMESPACE):
        _damage(store, b"garbage", namespace)
    assert await cached_chat.chat(_MESSAGES) == _answer()
    assert (await cached_embed.embed("Pierre")).embedding == [6.0, 1.0]
    assert chat.calls == 2 and embedder.calls == 2


async def _const(value: str) -> str:
    return value


def _damage(store: SqliteCacheStore, value: bytes, namespace: str) -> None:
    """Overwrite every stored value of ``namespace`` from a second connection (a damaged file)."""
    with closing(sqlite3.connect(store.path)) as con:
        con.execute("UPDATE entries SET value = ? WHERE namespace = ?", (value, namespace))
        con.commit()


@pytest.mark.asyncio
async def test_a_stored_value_of_the_wrong_shape_is_a_miss(tmp_path: Path) -> None:
    """A vector entry that decodes to a dict was turned into its keys before the shared decode."""
    from chaoscypher_core.cache.store import encode_value

    store = _store(tmp_path)
    embedder = _Embedder()
    cached = CachedEmbeddingProvider(embedder, store, model_build=_EMBED_BUILD)
    await cached.embed("Pierre")
    _damage(store, encode_value({"a": 1}), EMBEDDINGS_NAMESPACE)
    assert (await cached.embed("Pierre")).embedding == [6.0, 1.0]
    assert embedder.calls == 2


# ------------------------------------------------------------ degenerate answers


def test_the_default_degenerate_check() -> None:
    assert is_degenerate_answer(_answer(content=""))
    assert is_degenerate_answer(_answer(content="  \n"))
    assert is_degenerate_answer({**_answer(content=""), "usage": {"completion_tokens": 900}})
    assert not is_degenerate_answer(_answer(content="", tool_calls=[{"name": "search"}]))
    assert not is_degenerate_answer(_answer())


@pytest.mark.asyncio
async def test_an_empty_answer_is_not_stored_and_the_next_pinned_call_reaches_the_model(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    inner = _Chat(response=_answer(content=""))
    cached = CachedChatProvider(inner, store)
    await cached.chat(_MESSAGES)
    await cached.chat(_MESSAGES)
    assert inner.calls == 2
    assert store.stats(COMPLETIONS_NAMESPACE, _CAP).entries == 0


@pytest.mark.asyncio
async def test_a_caller_passes_a_stricter_degenerate_check_through_wrap_chat(
    tmp_path: Path,
) -> None:
    """The extraction path's own check (an answer its parser rejects) through the factory's wrapper."""
    from chaoscypher_core.cache.wiring import close_stores, wrap_chat
    from chaoscypher_core.settings import EngineSettings

    inner = _Chat(response=_answer(content="no entity lines here"))
    settings = EngineSettings(paths={"data_dir": str(tmp_path)})
    try:
        cached = wrap_chat(
            inner, settings, degenerate=lambda answer: "|" not in str(answer.get("content"))
        )
        await cached.chat(_MESSAGES)
        await cached.chat(_MESSAGES)
        assert inner.calls == 2
    finally:
        close_stores()


@pytest.mark.asyncio
async def test_a_degenerate_stream_is_not_stored(tmp_path: Path) -> None:
    store = _store(tmp_path)

    class _Streaming(_Chat):
        async def chat(self, messages: Any, **kwargs: Any) -> Any:
            self.calls += 1

            async def stream() -> Any:
                yield {"type": "thinking", "delta": "hmm"}
                yield {**_answer(content=""), "type": "done"}

            return stream()

    inner = _Streaming()
    cached = CachedChatProvider(inner, store)
    for _ in range(2):
        async for _chunk in await cached.chat(_MESSAGES, stream=True):
            pass
    assert inner.calls == 2
    assert store.stats(COMPLETIONS_NAMESPACE, _CAP).entries == 0


# --------------------------------------------------------------- build matcher


@pytest.mark.parametrize(
    ("build", "provider", "model", "names"),
    [
        ("ollama:gemma4:31b:abc123@0.12.1", "ollama", "gemma4:31b", True),
        ("ollama:gemma4:31b:abc123", "ollama", "gemma4:31b", True),
        ("ollama:gemma4:31b@0.12.1", "ollama", "gemma4:31b", True),
        ("openai:gpt-4o", "openai", "gpt-4o", True),
        ("ollama:gemma4:31b:abc123@0.12.1", "ollama", "gemma4", False),
        ("ollama:gemma4:31b-it:abc123", "ollama", "gemma4:31b", False),
        ("ollama:gemma4:31b:abc123", "openai", "gemma4:31b", False),
    ],
)
def test_build_names_model_is_the_inverse_of_model_build_id(
    build: str, provider: str, model: str, names: bool
) -> None:
    assert build_names_model(build, provider, model) is names
