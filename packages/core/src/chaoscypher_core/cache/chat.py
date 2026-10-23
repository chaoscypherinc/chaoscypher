# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The completion cache: a chat provider whose pinned answers are served again.

A call is looked up under :func:`~chaoscypher_core.cache.keys.completion_key`
(the model build, the normalised messages, the EFFECTIVE decoding and the
tools) when the policy says its decoding is pinned
(:func:`~chaoscypher_core.cache.policy.cacheable`); anything else (a sampled
temperature, an unseeded Ollama call, an unknown model build, input that is
not plain message dicts) bypasses and goes to the inner provider as it came.

A non-stream answer is stored as its response dict and served as a fresh
copy; a stream is stored as its ordered chunk list and replayed as an async
generator, so the consumer's loop guard and sinks see the same shapes. Only
a clean finish (:data:`CLEAN_FINISH_REASONS`) is stored: never a loop abort,
a length cut, an error chunk or an exception; nor a degenerate answer
(:func:`is_degenerate_answer`, or the stricter check a caller passes): a
clean but empty answer stored once would be served on every rerun, so the
caller's retry would start past the first, pinned attempt every time. A
stream and a non-stream call of the same input are keyed apart (their stored
shapes differ).

Time saved is the sum of what each hit's first call cost: the provider's
``provider_timings.total_ms`` when it reports one, else the wall clock.
"""

from __future__ import annotations

import copy
import hashlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from chaoscypher_core.cache.build import LazyModelBuild, pinned_build
from chaoscypher_core.cache.call_site import current_call_site
from chaoscypher_core.cache.guard import store_call
from chaoscypher_core.cache.keys import Decoding, completion_key, model_build_id
from chaoscypher_core.cache.policy import cacheable, seeded
from chaoscypher_core.cache.store import CacheEntry, CacheStore, encode_value, stored_value
from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.utils.decoding import (
    effective_repeat_penalty,
    effective_seed,
    effective_temperature,
)


logger = structlog.get_logger(__name__)

COMPLETIONS_NAMESPACE = "completions"
"""The namespace the product's completion cache stores under."""

CLEAN_FINISH_REASONS: frozenset[str] = frozenset({"stop", "tool_calls"})
"""The finish reasons of an answer worth serving again (never ``loop``, ``length``, ``error``)."""

NUM_CTX_CONFIG_KEYS: Mapping[str, str] = {"ollama": "ollama_num_ctx"}
"""The config key of the context window each provider that takes one is built with."""

ChatBuildResolver = Callable[[str], Awaitable[str | None]]
"""An async callable answering the build id of a model (by name), or None when unknown."""

DegenerateCheck = Callable[[Mapping[str, Any]], bool]
"""``answer -> True`` when a clean answer must not be stored (the wrapper's ``degenerate=``)."""


def is_degenerate_answer(answer: Mapping[str, Any]) -> bool:
    """Whether a clean answer is not worth serving again: blank content and no tool calls.

    Covers the empty answer and the one whose output tokens all went to
    thinking (output tokens spent, content empty). A tool-call answer has
    no content by design and is kept. ``answer`` is a response dict, or a
    stream's done chunk (which carries the accumulated ``content`` and the
    ``tool_calls``).
    """
    content = answer.get("content")
    blank = not content.strip() if isinstance(content, str) else not content
    return blank and not answer.get("tool_calls")


class ChatProvider(Protocol):
    """What the wrapper needs of the provider it wraps: ``BaseLLMProvider.chat``."""

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
        """A response dict, or an async generator of chunk dicts when ``stream``."""
        ...


@dataclass(frozen=True)
class _Lookup:
    """A cacheable call: where its answer lives and how the entry is labelled."""

    key: str
    """The entry key (stream calls keyed apart from non-stream ones)."""
    build: str
    """The model build the key names."""
    seeded: bool
    """Whether a hit is the deterministic answer (:func:`~chaoscypher_core.cache.policy.seeded`)."""


def _stream_key(key: str) -> str:
    """The key of the streamed form of the call keyed ``key``."""
    return hashlib.sha256(f"stream:{key}".encode()).hexdigest()


def _latency_ms(answer: Mapping[str, Any], started: float) -> int:
    """The model time an answer cost: its ``provider_timings.total_ms``, else the wall clock."""
    timings = answer.get("provider_timings")
    if isinstance(timings, Mapping):
        total = timings.get("total_ms")
        if isinstance(total, int | float) and not isinstance(total, bool) and total >= 0:
            return int(total)
    return int((time.perf_counter() - started) * 1000)


def _positive_int(value: Any) -> int | None:
    """``value`` when it is a positive int (a set option), else None (unset)."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _keyable(messages: Any, tools: Any) -> bool:
    """Whether the call's input is plain JSON-shaped dicts, the only input a key is built from."""
    if not isinstance(messages, list) or not all(isinstance(m, Mapping) for m in messages):
        return False
    return tools is None or (isinstance(tools, list) and all(isinstance(t, Mapping) for t in tools))


def _response_shaped(value: Any) -> bool:
    """Whether a stored value is a non-stream answer: a response dict."""
    return isinstance(value, dict)


def _stream_shaped(value: Any) -> bool:
    """Whether a stored value is a stream: a list of chunk dicts."""
    return isinstance(value, list) and all(isinstance(c, dict) for c in value)


async def _replay(chunks: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    """Yield a stored stream's chunks, in order, with no delay."""
    for chunk in chunks:
        yield chunk


class CachedChatProvider:
    """A ``BaseLLMProvider`` stand-in: pinned completions served from the store.

    Everything but :meth:`chat` is the inner provider's (``metadata``,
    ``check_health``, ``close``, ``chat_model``, ``config``...).

    Example:
        store = SqliteCacheStore(Path("cache/completions.db"), max_bytes=2 * 1024**3)
        provider = CachedChatProvider(my_provider, store, build_resolver=resolve_digest)
        answer = await provider.chat([{"role": "user", "content": "hi"}])
    """

    def __init__(
        self,
        inner: ChatProvider,
        store: CacheStore,
        *,
        build_resolver: ChatBuildResolver | None = None,
        enabled: bool = True,
        require_hit: bool = False,
        namespace: str = COMPLETIONS_NAMESPACE,
        degenerate: DegenerateCheck | None = None,
    ) -> None:
        """Wrap ``inner``; key on what ``build_resolver`` answers for its model, on first use.

        ``degenerate`` says which clean answers are not stored
        (:func:`is_degenerate_answer` when None; the extraction path passes a
        stricter one, an answer its parser rejects).

        Without a resolver the build is ``provider:model`` (a hosted model id,
        which carries its own date); a build pinned by
        :func:`~chaoscypher_core.cache.build.pinned_builds` wins over both.
        ``enabled=False`` passes every call through and records it as a
        bypass; ``require_hit`` (replay) refuses, with a
        :class:`~chaoscypher_core.exceptions.ValidationError`, every call that
        would reach the model (a miss, an unknown build, an uncacheable call).
        """
        self._inner = inner
        self._store = store
        self._resolver = build_resolver
        self._builds: dict[str, LazyModelBuild] = {}
        self._enabled = enabled
        self._require_hit = require_hit
        self._namespace = namespace
        self._degenerate = degenerate or is_degenerate_answer

    # ------------------------------------------------------------ delegation

    @property
    def inner(self) -> ChatProvider:
        """The wrapped provider."""
        return self._inner

    @property
    def config(self) -> Mapping[str, Any]:
        """The wrapped provider's config (empty when it has none)."""
        config = getattr(self._inner, "config", None)
        return config if isinstance(config, Mapping) else {}

    @property
    def provider_type(self) -> str:
        """The wrapped provider's type (``config["chat_provider"]``), ``unknown`` when unset."""
        name = self.config.get("chat_provider")
        return name if isinstance(name, str) and name else "unknown"

    @property
    def chat_model(self) -> Any:
        """The wrapped provider's model (read on every call: a change keys anew)."""
        return getattr(self._inner, "chat_model", None)

    @chat_model.setter
    def chat_model(self, value: str) -> None:
        """Set the wrapped provider's model."""
        self._inner.chat_model = value  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> Any:
        """Everything this wrapper does not define is the inner provider's."""
        inner = self.__dict__.get("_inner")
        if inner is None or name.startswith("__"):
            raise AttributeError(name)
        return getattr(inner, name)

    async def check_health(self) -> Any:
        """The wrapped provider's health (never cached)."""
        return await self._inner.check_health()  # type: ignore[attr-defined]

    # -------------------------------------------------------------- caching

    def _decoding(
        self, temperature: float | None, max_tokens: int | None, enable_thinking: bool
    ) -> Decoding | None:
        """The decoding the call is sent with, or None when it is not pinned to a number.

        Read the way the providers read it, before any await: the call's
        temperature, else the active override's, else the configured one; the
        active seed and repeat penalty overrides, else the configured seed.
        """
        config = self.config
        temp = effective_temperature(temperature)
        if temp is None:
            temp = config.get("ai_temperature")
        if not isinstance(temp, int | float) or isinstance(temp, bool):
            return None  # the provider's own default temperature: not pinned
        seed = effective_seed(config.get("seed"))
        penalty = effective_repeat_penalty()
        num_ctx_key = NUM_CTX_CONFIG_KEYS.get(self.provider_type)
        return Decoding(
            seed=seed if isinstance(seed, int) and not isinstance(seed, bool) else None,
            temperature=float(temp),
            num_ctx=_positive_int(config.get(num_ctx_key)) if num_ctx_key else None,
            max_tokens=_positive_int(
                max_tokens if max_tokens is not None else config.get("ai_max_tokens")
            ),
            thinking=bool(enable_thinking),
            repeat_penalty=float(penalty) if isinstance(penalty, int | float) else None,
        )

    def _model_build(self, model: str) -> LazyModelBuild:
        """The build of ``model``: resolved once per model name, so a model switch keys anew."""
        lazy = self._builds.get(model)
        if lazy is None:
            resolver = self._resolver
            if resolver is None:
                static = model_build_id(self.provider_type, model, digest=None, server_version=None)
                lazy = LazyModelBuild(static)
            else:
                lazy = LazyModelBuild(resolve=lambda: resolver(model))
            self._builds[model] = lazy
        return lazy

    def _bypass_reason(self, messages: Any, tools: Any, decoding: Decoding | None) -> str | None:
        """Why this call cannot be keyed, before its build is known; None when it can."""
        if not self._enabled:
            return "the completion cache is off"
        if decoding is None:
            return "the decoding is not pinned (the provider's own temperature)"
        ok, reason = cacheable(decoding, self.provider_type)
        if not ok:
            return f"the decoding is {reason}"
        if not _keyable(messages, tools):
            # LangChain message objects, a bare prompt or tool objects: not keyed
            return "the input is not plain message dicts"
        model = self.chat_model
        if not isinstance(model, str) or not model:
            return "the provider names no model"
        return None

    async def _lookup_for(
        self,
        messages: Any,
        tools: Any,
        decoding: Decoding | None,
        *,
        stream: bool,
    ) -> _Lookup | str:
        """Where this call's answer lives, or why the call bypasses the store.

        A pinned build (:func:`~chaoscypher_core.cache.build.pinned_build`,
        a replay's) wins over the resolver, so it is never asked.

        Raises:
            ValidationError: Under ``require_hit`` when the model build is unknown.
        """
        reason = self._bypass_reason(messages, tools, decoding)
        if reason is not None:
            return reason
        assert decoding is not None  # _bypass_reason
        provider, model = self.provider_type, str(self.chat_model)
        build = pinned_build(provider, model) or await self._model_build(model).get()
        if build is None:
            # an unknown build: keying on the name would serve a stale answer
            if self._require_hit:
                msg = (
                    f"replay needs the model build of {provider}:{model} (the server did not "
                    "report its digest; a replay from a reference pack pins the build its "
                    "manifest records)"
                )
                raise ValidationError(msg, field="completions")
            return "the model build is unknown"
        key = completion_key(build, messages, decoding, tools)
        return _Lookup(
            key=_stream_key(key) if stream else key,
            build=build,
            seeded=seeded(decoding, provider),
        )

    def _get(self, key: str, *, stream: bool) -> tuple[Any, int] | None:
        """``(stored answer, latency_ms)`` under ``key``, or None (a bad or misshapen value is a miss)."""
        entry = self._store.get(self._namespace, key)
        if entry is None:
            return None
        value = stored_value(
            entry,
            namespace=self._namespace,
            key=key,
            shaped=_stream_shaped if stream else _response_shaped,
        )
        if value is None:
            return None
        return value, entry.latency_ms

    def _save(self, lookup: _Lookup, site: str, answer: Any, latency_ms: int) -> None:
        """Store ``answer`` under the lookup's key (an answer that is not JSON is left out)."""
        try:
            value = encode_value(answer)
        except TypeError, ValueError:
            logger.warning("llm_cache_unstorable", namespace=self._namespace, key=lookup.key)
            return
        self._store.put(
            self._namespace,
            lookup.key,
            CacheEntry(
                value=value,
                model_build=lookup.build,
                call_site=site,
                seeded=lookup.seeded,
                latency_ms=latency_ms,
                bytes=len(value),
            ),
        )

    def _storable(self, answer: Mapping[str, Any]) -> bool:
        """Whether a finished answer (a response dict, a done chunk) is stored: clean, not degenerate."""
        if answer.get("finish_reason") not in CLEAN_FINISH_REASONS:
            return False
        try:
            return not self._degenerate(answer)
        except Exception:  # a caller's check must never lose the answer; it is just not stored
            logger.warning("llm_cache_degenerate_check_failed", exc_info=True)
            return False

    async def _record(self, site: str, *, hit: bool | None, saved_ms: int = 0) -> None:
        """Count one call for ``site``: a hit (saving ``saved_ms``), a miss or a bypass."""
        await store_call(
            lambda: self._store.record(self._namespace, site, hit=hit, latency_saved_ms=saved_ms),
            default=None,
            op="record",
        )

    async def _tee(
        self, stream: Any, lookup: _Lookup, site: str, started: float
    ) -> AsyncIterator[Any]:
        """Yield every chunk as it arrives; store the list when the done chunk finishes clean.

        The list is stored before the done chunk is yielded (the chat loop
        stops reading at the done chunk). An error chunk, a chunk that is not
        a dict, an exception, or a consumer that stops early stores nothing.
        """
        chunks: list[dict[str, Any]] = []
        storable = True
        try:
            async for chunk in stream:
                if not isinstance(chunk, dict) or chunk.get("type") == "error":
                    storable = False
                elif storable:
                    chunks.append(copy.deepcopy(chunk))
                    if chunk.get("type") == "done" and self._storable(chunk):
                        latency = _latency_ms(chunk, started)
                        await store_call(
                            self._save, lookup, site, chunks, latency, default=None, op="put"
                        )
                        storable = False  # stored once; anything after the done chunk is not
                yield chunk
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()

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
        """The answer: from the store when pinned and seen, else from the inner provider (then stored).

        Returns a response dict, or an async generator of chunk dicts when
        ``stream`` (a hit replays the stored chunks).

        Raises:
            ValidationError: Under ``require_hit`` (a replay) when the call would
                reach the model: a miss (``replay miss: <key>``), an unknown
                model build, or a call the store cannot serve (sampled
                decoding, input that is not plain message dicts).
        """
        site = current_call_site()
        decoding = self._decoding(temperature, max_tokens, enable_thinking)  # before any await
        lookup = await self._lookup_for(messages, tools, decoding, stream=stream)

        async def forward() -> Any:
            """The call, as it came, to the inner provider."""
            return await self._inner.chat(
                messages,
                tools=tools,
                stream=stream,
                enable_thinking=enable_thinking,
                high_priority=high_priority,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        if isinstance(lookup, str):
            if self._require_hit and self._enabled:
                msg = f"replay cannot serve this call: {lookup}"
                raise ValidationError(msg, field="completions")
            await self._record(site, hit=None)
            return await forward()
        found = await store_call(
            lambda: self._get(lookup.key, stream=stream), default=None, op="get"
        )
        if found is not None:
            answer, latency_ms = found
            await self._record(site, hit=True, saved_ms=latency_ms)
            return _replay(answer) if stream else answer
        if self._require_hit:
            msg = f"replay miss: {lookup.key}"
            raise ValidationError(msg, field="completions")
        await self._record(site, hit=False)
        started = time.perf_counter()
        if stream:
            return self._tee(await forward(), lookup, site, started)
        response = await forward()
        if isinstance(response, dict) and self._storable(response):
            latency = _latency_ms(response, started)
            await store_call(self._save, lookup, site, response, latency, default=None, op="put")
        return response


__all__ = [
    "CLEAN_FINISH_REASONS",
    "COMPLETIONS_NAMESPACE",
    "NUM_CTX_CONFIG_KEYS",
    "CachedChatProvider",
    "ChatBuildResolver",
    "ChatProvider",
    "DegenerateCheck",
    "is_degenerate_answer",
]
