# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The completion cache wrapper: pinned answers served again, streams replayed chunk for chunk."""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.cache.call_site import call_site
from chaoscypher_core.cache.chat import COMPLETIONS_NAMESPACE, CachedChatProvider
from chaoscypher_core.cache.sqlite_store import SqliteCacheStore, read_entries
from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.utils.decoding import TurnDecoding, decoding_override, seed_override


_MESSAGES = [{"role": "user", "content": "Who kept the accounts?"}]
_CAP = 1 << 24


def _answer(finish_reason: str = "stop", content: str = "Pierre.") -> dict[str, Any]:
    """A provider's non-stream answer."""
    return {
        "content": content,
        "thinking": None,
        "tool_calls": None,
        "model": "qwen3:4b",
        "provider": "ollama",
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        "provider_timings": {"total_ms": 1234},
        "finish_reason": finish_reason,
    }


def _chunks(finish_reason: str = "stop") -> list[dict[str, Any]]:
    """A provider's streamed answer: two content deltas and the done chunk."""
    return [
        {"type": "content", "delta": "Pie", "accumulated": "Pie"},
        {"type": "content", "delta": "rre.", "accumulated": "Pierre."},
        {**_answer(finish_reason), "type": "done"},
    ]


class _FakeChat:
    """A chat provider that answers a scripted response and counts its calls."""

    def __init__(
        self,
        *,
        provider: str = "ollama",
        seed: int | None = 42,
        temperature: float | None = 0.0,
        response: dict[str, Any] | None = None,
        chunks: list[dict[str, Any]] | None = None,
        raise_after: int | None = None,
    ) -> None:
        self.config: dict[str, Any] = {
            "chat_provider": provider,
            "seed": seed,
            "ai_temperature": temperature,
            "ai_max_tokens": None,
            "ollama_num_ctx": 8192,
        }
        self.chat_model = "qwen3:4b"
        self.response = response if response is not None else _answer()
        self.chunks = chunks if chunks is not None else _chunks()
        self.raise_after = raise_after
        self.calls: list[dict[str, Any]] = []
        self.health_checks = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        stream: bool = False,
        enable_thinking: bool = False,
        high_priority: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        self.calls.append({"stream": stream, "temperature": temperature, "tools": tools})
        if stream:
            return self._stream()
        return copy.deepcopy(self.response)

    async def _stream(self) -> AsyncIterator[dict[str, Any]]:
        for i, chunk in enumerate(self.chunks):
            if self.raise_after is not None and i == self.raise_after:
                raise ConnectionError("stream dropped")
            yield copy.deepcopy(chunk)

    async def check_health(self) -> bool:
        self.health_checks += 1
        return True


def _store(tmp_path: Path) -> SqliteCacheStore:
    return SqliteCacheStore(tmp_path / "c.db", max_bytes=_CAP)


def _wrap(inner: _FakeChat, store: SqliteCacheStore, **kwargs: Any) -> CachedChatProvider:
    return CachedChatProvider(inner, store, **kwargs)


async def _drain(stream: Any) -> list[dict[str, Any]]:
    return [chunk async for chunk in stream]


def _stats(store: SqliteCacheStore) -> tuple[int, int, int, int]:
    s = store.stats(COMPLETIONS_NAMESPACE, _CAP)
    return s.hits, s.misses, s.bypasses, s.entries


# ------------------------------------------------------------------ non-stream


@pytest.mark.asyncio
async def test_a_second_identical_call_is_served_from_the_store(tmp_path: Path) -> None:
    inner = _FakeChat()
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    first = await cached.chat(_MESSAGES)
    first["content"] = "mutated by the caller"
    second = await cached.chat(_MESSAGES)
    third = await cached.chat(_MESSAGES)
    assert len(inner.calls) == 1
    assert second == _answer() and third == _answer()
    assert second is not third
    assert _stats(store) == (2, 1, 0, 1)
    assert store.stats(COMPLETIONS_NAMESPACE, _CAP).latency_saved_ms == 2 * 1234


@pytest.mark.asyncio
async def test_volatile_message_fields_do_not_change_the_key(tmp_path: Path) -> None:
    inner = _FakeChat()
    cached = _wrap(inner, _store(tmp_path))
    await cached.chat([{**_MESSAGES[0], "id": "m1", "timestamp": "2026-10-06T10:00:00Z"}])
    await cached.chat([{**_MESSAGES[0], "id": "m2", "timestamp": "2026-10-06T11:00:00Z"}])
    assert len(inner.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["length", "error", "unknown", "content_filter"])
async def test_a_non_stream_answer_without_a_clean_finish_is_not_stored(
    tmp_path: Path, finish_reason: str
) -> None:
    inner = _FakeChat(response=_answer(finish_reason))
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    await cached.chat(_MESSAGES)
    await cached.chat(_MESSAGES)
    assert len(inner.calls) == 2
    assert _stats(store) == (0, 2, 0, 0)


@pytest.mark.asyncio
async def test_a_tool_call_answer_is_stored(tmp_path: Path) -> None:
    tools = [{"type": "function", "function": {"name": "Extract", "parameters": {}}}]
    answer = {**_answer("tool_calls"), "tool_calls": [{"name": "Extract", "args": {"a": 1}}]}
    inner = _FakeChat(response=answer)
    cached = _wrap(inner, _store(tmp_path))
    await cached.chat(_MESSAGES, tools=tools)
    assert await cached.chat(_MESSAGES, tools=tools) == answer
    assert len(inner.calls) == 1
    # other tools are another question
    await cached.chat(_MESSAGES, tools=[{"type": "function", "function": {"name": "Other"}}])
    assert len(inner.calls) == 2


@pytest.mark.asyncio
async def test_the_wall_clock_is_the_latency_when_the_provider_reports_none(
    tmp_path: Path,
) -> None:
    inner = _FakeChat(response={**_answer(), "provider_timings": {}})
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    await cached.chat(_MESSAGES)
    [(_, _, entry)] = list(read_entries(tmp_path / "c.db"))
    assert 0 <= entry.latency_ms < 1000


# ---------------------------------------------------------------------- streams


@pytest.mark.asyncio
async def test_a_stream_is_replayed_chunk_for_chunk(tmp_path: Path) -> None:
    inner = _FakeChat()
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    first = await _drain(await cached.chat(_MESSAGES, stream=True))
    second = await _drain(await cached.chat(_MESSAGES, stream=True))
    assert first == second == _chunks()
    assert len(inner.calls) == 1
    assert _stats(store) == (1, 1, 0, 1)


@pytest.mark.asyncio
async def test_a_consumer_that_stops_at_the_done_chunk_still_stores_the_stream(
    tmp_path: Path,
) -> None:
    """The chat loop breaks on the done chunk and closes the stream (``streaming/chat/loop.py``)."""
    inner = _FakeChat()
    cached = _wrap(inner, _store(tmp_path))
    stream = await cached.chat(_MESSAGES, stream=True)
    async for chunk in stream:
        if chunk["type"] == "done":
            break
    await stream.aclose()
    assert await _drain(await cached.chat(_MESSAGES, stream=True)) == _chunks()
    assert len(inner.calls) == 1


@pytest.mark.asyncio
async def test_stream_and_non_stream_calls_never_serve_each_other(tmp_path: Path) -> None:
    inner = _FakeChat()
    cached = _wrap(inner, _store(tmp_path))
    await _drain(await cached.chat(_MESSAGES, stream=True))
    assert await cached.chat(_MESSAGES) == _answer()
    assert [c["stream"] for c in inner.calls] == [True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["loop", "length", "error"])
async def test_a_stream_without_a_clean_finish_is_not_stored(
    tmp_path: Path, finish_reason: str
) -> None:
    """Review Focus 4: a stream cut by the loop guard is never served later."""
    inner = _FakeChat(chunks=_chunks(finish_reason))
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    assert await _drain(await cached.chat(_MESSAGES, stream=True)) == _chunks(finish_reason)
    await _drain(await cached.chat(_MESSAGES, stream=True))
    assert len(inner.calls) == 2
    assert _stats(store)[3] == 0


@pytest.mark.asyncio
async def test_a_stream_with_an_error_chunk_is_not_stored(tmp_path: Path) -> None:
    error = {"type": "error", "error": "model crashed", "error_code": "LLM_ERROR"}
    inner = _FakeChat(chunks=[_chunks()[0], error, _chunks()[2]])
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    await _drain(await cached.chat(_MESSAGES, stream=True))
    await _drain(await cached.chat(_MESSAGES, stream=True))
    assert len(inner.calls) == 2
    assert _stats(store)[3] == 0


@pytest.mark.asyncio
async def test_a_stream_that_raises_is_not_stored_and_the_error_propagates(
    tmp_path: Path,
) -> None:
    inner = _FakeChat(raise_after=2)
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    received: list[dict[str, Any]] = []

    async def consume() -> None:
        async for chunk in await cached.chat(_MESSAGES, stream=True):
            received.append(chunk)

    with pytest.raises(ConnectionError):
        await consume()
    assert received == _chunks()[:2]
    assert _stats(store)[3] == 0


@pytest.mark.asyncio
async def test_a_stream_abandoned_before_its_done_chunk_is_not_stored(tmp_path: Path) -> None:
    """The loop guard trips mid-stream and closes it: nothing reaches the store."""
    inner = _FakeChat()
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    stream = await cached.chat(_MESSAGES, stream=True)
    async for _chunk in stream:
        break
    await stream.aclose()
    assert _stats(store)[3] == 0


# ----------------------------------------------------------------------- policy


@pytest.mark.asyncio
async def test_sampled_and_unseeded_calls_bypass(tmp_path: Path) -> None:
    inner = _FakeChat()
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    await cached.chat(_MESSAGES, temperature=0.3)
    await cached.chat(_MESSAGES, temperature=0.3)
    with seed_override(None):  # Ollama with no seed picks one per request
        await cached.chat(_MESSAGES)
        await cached.chat(_MESSAGES)
    assert len(inner.calls) == 4
    assert _stats(store) == (0, 0, 4, 0)


@pytest.mark.asyncio
async def test_a_provider_with_no_configured_temperature_bypasses(tmp_path: Path) -> None:
    """None means the provider's own default, which is not pinned."""
    inner = _FakeChat(temperature=None)
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    await cached.chat(_MESSAGES)
    await cached.chat(_MESSAGES)
    assert len(inner.calls) == 2
    assert _stats(store) == (0, 0, 2, 0)


@pytest.mark.asyncio
async def test_a_regenerate_at_a_raised_temperature_bypasses(tmp_path: Path) -> None:
    inner = _FakeChat()
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    with decoding_override(TurnDecoding(seed=43, temperature=0.7, attempt=1)):
        await cached.chat(_MESSAGES)
        await cached.chat(_MESSAGES)
    assert len(inner.calls) == 2
    assert _stats(store) == (0, 0, 2, 0)


@pytest.mark.asyncio
async def test_a_retry_rung_at_another_seed_has_its_own_key_never_the_base_key(
    tmp_path: Path,
) -> None:
    inner = _FakeChat()
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    with decoding_override(seed=43):
        await cached.chat(_MESSAGES)  # miss: stored under the seed-43 key
    await cached.chat(_MESSAGES)  # the base key (seed 42): a miss, not the rung's answer
    with decoding_override(seed=43):
        await cached.chat(_MESSAGES)  # the rung's own key: a hit
    with decoding_override(repeat_penalty=1.1):
        await cached.chat(_MESSAGES)  # the second rung's repeat penalty: another key
    assert len(inner.calls) == 3
    assert _stats(store) == (1, 3, 0, 3)


@pytest.mark.asyncio
async def test_a_hosted_provider_at_temperature_zero_caches_as_unseeded(
    tmp_path: Path,
) -> None:
    inner = _FakeChat(provider="anthropic", seed=None)
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    await cached.chat(_MESSAGES)
    await cached.chat(_MESSAGES)
    assert len(inner.calls) == 1
    [(_, _, entry)] = list(read_entries(tmp_path / "c.db"))
    assert entry.seeded is False
    assert entry.model_build == "anthropic:qwen3:4b"


@pytest.mark.asyncio
async def test_an_ollama_answer_at_a_seed_is_flagged_seeded(tmp_path: Path) -> None:
    inner = _FakeChat()
    cached = _wrap(inner, _store(tmp_path))
    await cached.chat(_MESSAGES)
    [(_, _, entry)] = list(read_entries(tmp_path / "c.db"))
    assert entry.seeded is True


@pytest.mark.asyncio
async def test_options_the_model_sees_are_in_the_key(tmp_path: Path) -> None:
    inner = _FakeChat()
    cached = _wrap(inner, _store(tmp_path))
    await cached.chat(_MESSAGES)
    await cached.chat(_MESSAGES, max_tokens=64)
    await cached.chat(_MESSAGES, enable_thinking=True)
    inner.config["ollama_num_ctx"] = 4096
    await cached.chat(_MESSAGES)
    assert len(inner.calls) == 4


# ------------------------------------------------------- switches, build, sites


@pytest.mark.asyncio
async def test_disabled_passes_every_call_through_as_a_bypass(tmp_path: Path) -> None:
    inner = _FakeChat()
    store = _store(tmp_path)
    cached = _wrap(inner, store, enabled=False)
    await cached.chat(_MESSAGES)
    await _drain(await cached.chat(_MESSAGES, stream=True))
    await cached.chat(_MESSAGES)
    assert len(inner.calls) == 3
    assert _stats(store) == (0, 0, 3, 0)


@pytest.mark.asyncio
async def test_require_hit_refuses_a_miss_without_calling_the_model(tmp_path: Path) -> None:
    store = _store(tmp_path)
    recorder = _FakeChat()
    await _wrap(recorder, store).chat(_MESSAGES)
    inner = _FakeChat()
    replay = _wrap(inner, store, require_hit=True)
    assert await replay.chat(_MESSAGES) == _answer()
    with pytest.raises(ValidationError):
        await replay.chat([{"role": "user", "content": "Another question"}])
    with pytest.raises(ValidationError):
        await replay.chat(_MESSAGES, stream=True)
    assert inner.calls == []


@pytest.mark.asyncio
async def test_the_build_comes_from_the_resolver_once_per_model(tmp_path: Path) -> None:
    asked: list[str] = []

    async def resolve(model: str) -> str | None:
        asked.append(model)
        return f"ollama:{model}:sha256:abc@0.12.1"

    inner = _FakeChat()
    cached = _wrap(inner, _store(tmp_path), build_resolver=resolve)
    await cached.chat(_MESSAGES)
    await cached.chat(_MESSAGES)
    inner.chat_model = "qwen3:8b"  # another model: another build, never the first one's answers
    await cached.chat(_MESSAGES)
    assert asked == ["qwen3:4b", "qwen3:8b"]
    assert len(inner.calls) == 2
    builds = sorted(e.model_build for _, _, e in read_entries(tmp_path / "c.db"))
    assert builds == ["ollama:qwen3:4b:sha256:abc@0.12.1", "ollama:qwen3:8b:sha256:abc@0.12.1"]


@pytest.mark.asyncio
async def test_an_unknown_build_bypasses_rather_than_keying_on_the_name(tmp_path: Path) -> None:
    async def resolve(model: str) -> str | None:
        return None  # the server could not say which weights answer

    inner = _FakeChat()
    store = _store(tmp_path)
    cached = _wrap(inner, store, build_resolver=resolve)
    await cached.chat(_MESSAGES)
    await cached.chat(_MESSAGES)
    assert len(inner.calls) == 2
    assert _stats(store) == (0, 0, 2, 0)


@pytest.mark.asyncio
async def test_messages_that_are_not_dicts_bypass(tmp_path: Path) -> None:
    from langchain_core.messages import HumanMessage

    inner = _FakeChat()
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    await cached.chat([HumanMessage(content="hi")])  # type: ignore[list-item]
    assert _stats(store) == (0, 0, 1, 0)


@pytest.mark.asyncio
async def test_stats_are_kept_per_call_site(tmp_path: Path) -> None:
    inner = _FakeChat()
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    with call_site("extraction"):
        await cached.chat(_MESSAGES)
        await cached.chat(_MESSAGES)
    await cached.chat(_MESSAGES, temperature=0.5)
    sites = store.stats(COMPLETIONS_NAMESPACE, _CAP).by_call_site
    assert sites["extraction"]["hits"] == 1 and sites["extraction"]["misses"] == 1
    assert sites["other"]["bypasses"] == 1
    [(_, _, entry)] = list(read_entries(tmp_path / "c.db"))
    assert entry.call_site == "extraction"


@pytest.mark.asyncio
async def test_a_store_that_fails_never_breaks_the_call(tmp_path: Path) -> None:
    inner = _FakeChat()
    store = _store(tmp_path)
    cached = _wrap(inner, store)
    store.close()  # every store call now raises sqlite3.ProgrammingError
    assert await cached.chat(_MESSAGES) == _answer()
    assert await _drain(await cached.chat(_MESSAGES, stream=True)) == _chunks()
    assert len(inner.calls) == 2


@pytest.mark.asyncio
async def test_everything_else_is_the_inner_providers(tmp_path: Path) -> None:
    inner = _FakeChat()
    cached = _wrap(inner, _store(tmp_path))
    assert cached.inner is inner
    assert cached.config is inner.config
    assert cached.chat_model == "qwen3:4b"
    cached.chat_model = "qwen3:8b"
    assert inner.chat_model == "qwen3:8b"
    assert cached.provider_type == "ollama"
    assert await cached.check_health() is True and inner.health_checks == 1
    assert cached.raise_after is None  # an attribute only the inner provider has
    with pytest.raises(AttributeError):
        _ = cached.no_such_attribute


# ---------------------------------------------------------------------- wiring


class TestFactoryWiring:
    """The provider factory hands out its chat providers behind the completion cache."""

    def test_the_factory_wraps_when_enabled_and_not_when_disabled(self, tmp_path: Path) -> None:
        """Review Focus 5: the switch decides for every provider built after it."""
        from chaoscypher_core.adapters.llm.factory import ProviderFactory
        from chaoscypher_core.adapters.llm.providers.ollama_provider import OllamaProvider
        from chaoscypher_core.settings import CacheSettings, CacheStoreSettings, EngineSettings

        enabled = EngineSettings(paths={"data_dir": str(tmp_path)})
        disabled = enabled.model_copy(
            update={"cache": CacheSettings(completions=CacheStoreSettings(enabled=False))}
        )
        factory = ProviderFactory(enabled)
        chat = factory.get_chat_provider()
        extraction = factory.get_extraction_provider()
        assert isinstance(chat, CachedChatProvider) and isinstance(chat.inner, OllamaProvider)
        assert isinstance(extraction, CachedChatProvider)
        assert factory.get_chat_provider() is chat  # still one provider per session
        off = ProviderFactory(disabled)
        assert isinstance(off.get_chat_provider(), OllamaProvider)
        assert isinstance(off.get_extraction_provider(), OllamaProvider)
        assert not (tmp_path / "cache").exists()  # building a provider opens nothing

    def test_an_embeddings_switch_does_not_turn_the_completion_cache_off(
        self, tmp_path: Path
    ) -> None:
        from chaoscypher_core.adapters.llm.factory import ProviderFactory
        from chaoscypher_core.settings import CacheSettings, CacheStoreSettings, EngineSettings

        settings = EngineSettings(
            paths={"data_dir": str(tmp_path)},
            cache=CacheSettings(embeddings=CacheStoreSettings(enabled=False)),
        )
        assert isinstance(ProviderFactory(settings).get_chat_provider(), CachedChatProvider)

    def test_a_hosted_provider_gets_no_digest_resolver(self) -> None:
        from chaoscypher_core.adapters.llm.factory import chat_build_resolver

        assert chat_build_resolver(_FakeChat(provider="anthropic")) is None
        assert chat_build_resolver(_FakeChat(provider="ollama")) is None  # no base_url
        ollama = _FakeChat(provider="ollama")
        ollama.config["base_url"] = "http://gpu:11434"
        assert chat_build_resolver(ollama) is not None

    @pytest.mark.asyncio
    async def test_the_health_check_reaches_the_model_not_the_cache(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from chaoscypher_core.adapters.llm.factory import ProviderFactory
        from chaoscypher_core.settings import EngineSettings

        factory = ProviderFactory(EngineSettings(paths={"data_dir": str(tmp_path)}))
        inner = _FakeChat()
        with patch("chaoscypher_core.adapters.llm.factory.get_provider", return_value=inner):
            for _ in range(2):
                result = await factory.check_provider_health()
                assert result["status"] == "healthy"
        assert len(inner.calls) == 2

    @pytest.mark.asyncio
    async def test_a_second_identical_chat_through_the_real_provider_makes_no_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The real factory and the real Ollama provider over a fake LangChain layer."""
        from unittest.mock import AsyncMock, MagicMock

        from chaoscypher_core.adapters.llm.factory import ProviderFactory
        from chaoscypher_core.adapters.llm.providers import ollama_provider
        from chaoscypher_core.cache.wiring import close_stores, open_store
        from chaoscypher_core.settings import EngineSettings

        builds: list[tuple[str, str]] = []

        async def fake_build(base_url: str, model: str, **_: Any) -> dict[str, Any]:
            builds.append((base_url, model))
            return {"digest": "sha256:abc", "server_version": "0.12.1"}

        monkeypatch.setattr(ollama_provider, "ollama_model_build", fake_build)
        requests: list[Any] = []

        def response() -> MagicMock:
            message = MagicMock()
            message.content = "Pierre kept the accounts."
            message.tool_calls = []
            message.additional_kwargs = {}
            message.response_metadata = {
                "prompt_eval_count": 9,
                "eval_count": 6,
                "done_reason": "stop",
            }
            return message

        class _FakeChatOllama(MagicMock):
            """ChatOllama with ``ainvoke`` recorded and answered."""

            def __init__(self, **kwargs: Any) -> None:
                super().__init__()
                self.kwargs = kwargs

                async def ainvoke(messages: Any, **_: Any) -> MagicMock:
                    requests.append((self.kwargs.get("seed"), messages))
                    return response()

                self.ainvoke = AsyncMock(side_effect=ainvoke)

            def bind_tools(self, *_a: Any, **_kw: Any) -> _FakeChatOllama:
                return self

        monkeypatch.setattr(ollama_provider, "ChatOllama", _FakeChatOllama)
        settings = EngineSettings(paths={"data_dir": str(tmp_path)})
        try:
            provider = ProviderFactory(settings).get_chat_provider()
            assert isinstance(provider, CachedChatProvider)
            first = await provider.chat(_MESSAGES)
            second = await provider.chat(_MESSAGES)
            assert first == second and first["content"] == "Pierre kept the accounts."
            assert len(requests) == 1
            model = settings.llm.ollama_chat_model
            assert builds == [(settings.llm.primary_ollama_url, model)]
            stats = open_store(settings, "completions").stats(COMPLETIONS_NAMESPACE, _CAP)
            assert (stats.hits, stats.misses, stats.entries) == (1, 1, 1)
            with decoding_override(seed=43):  # a retry rung: its own request
                await provider.chat(_MESSAGES)
            assert [seed for seed, _ in requests] == [42, 43]
        finally:
            close_stores()
