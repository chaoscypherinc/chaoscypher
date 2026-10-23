# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""How the product opens its stores and wraps the providers its factories build.

One store file per cache (``<cache dir>/completions.db``,
``<cache dir>/embeddings.db``, the dir from
:meth:`~chaoscypher_core.settings.CacheSettings.resolved_dir`), memoised per
process so every provider of a cache shares one connection. Wrapping opens
nothing: the store is opened on the first call through the wrapper
(:class:`LazyCacheStore`), so building a provider never touches the data
directory or the network. The factories (``chaoscypher_core.adapters``)
call these functions; this package never imports the adapters, so a
server-specific build resolver (the Ollama digest) is passed in.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal, cast

from chaoscypher_core.cache.chat import COMPLETIONS_NAMESPACE, CachedChatProvider
from chaoscypher_core.cache.embedding import EMBEDDINGS_NAMESPACE, CachedEmbeddingProvider
from chaoscypher_core.cache.keys import model_build_id
from chaoscypher_core.cache.sqlite_store import SqliteCacheStore


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.cache.build import BuildResolver
    from chaoscypher_core.cache.chat import ChatBuildResolver, ChatProvider, DegenerateCheck
    from chaoscypher_core.cache.store import CacheEntry, CacheStats, CacheStore
    from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol
    from chaoscypher_core.settings import EngineSettings


CacheName = Literal["completions", "embeddings"]
"""The two product caches; each is one store file and one settings sub-section."""

CACHE_NAMES: tuple[CacheName, ...] = ("completions", "embeddings")
"""Every product cache, in display order."""

_STORES: dict[tuple[Path, int], SqliteCacheStore] = {}
_STORES_LOCK = threading.Lock()


def store_path(settings: EngineSettings, name: CacheName) -> Path:
    """The store file of cache ``name``: ``<resolved cache dir>/<name>.db``."""
    return settings.cache.resolved_dir(settings.paths.data_dir) / f"{name}.db"


def open_store(settings: EngineSettings, name: CacheName) -> SqliteCacheStore:
    """The process's store for cache ``name`` (opened once per file and cap)."""
    path = store_path(settings, name)
    max_bytes = getattr(settings.cache, name).max_bytes
    with _STORES_LOCK:
        store = _STORES.get((path, max_bytes))
        if store is None:
            store = SqliteCacheStore(
                path,
                max_bytes=max_bytes,
                busy_timeout_s=float(settings.database.connection_timeout_secs),
            )
            _STORES[(path, max_bytes)] = store
        return store


def close_stores() -> None:
    """Close every store this process opened and forget them (the next use reopens)."""
    with _STORES_LOCK:
        for store in _STORES.values():
            store.close()
        _STORES.clear()


class LazyCacheStore:
    """A :class:`~chaoscypher_core.cache.store.CacheStore` that opens its real store on first use."""

    def __init__(self, opener: Callable[[], CacheStore]) -> None:
        """Defer ``opener`` until the first store call."""
        self._opener = opener
        self._store: CacheStore | None = None
        self._lock = threading.Lock()

    def _real(self) -> CacheStore:
        """The opened store (opening it now when not yet open)."""
        if self._store is None:
            with self._lock:
                if self._store is None:
                    self._store = self._opener()
        return self._store

    def get(self, namespace: str, key: str) -> CacheEntry | None:
        """Delegate to the opened store."""
        return self._real().get(namespace, key)

    def put(self, namespace: str, key: str, entry: CacheEntry) -> None:
        """Delegate to the opened store."""
        self._real().put(namespace, key, entry)

    def record(
        self,
        namespace: str,
        call_site: str,
        *,
        hit: bool | None,
        latency_saved_ms: int = 0,
        count: int = 1,
    ) -> None:
        """Delegate to the opened store."""
        self._real().record(
            namespace, call_site, hit=hit, latency_saved_ms=latency_saved_ms, count=count
        )

    def clear(self, namespace: str | None = None) -> int:
        """Delegate to the opened store."""
        return self._real().clear(namespace)

    def stats(self, namespace: str, max_bytes: int) -> CacheStats:
        """Delegate to the opened store."""
        return self._real().stats(namespace, max_bytes)

    def namespaces(self) -> list[str]:
        """Delegate to the opened store."""
        return self._real().namespaces()

    def close(self) -> None:
        """Nothing to release here: the opened store belongs to the process memo."""


def lazy_store(settings: EngineSettings, name: CacheName) -> LazyCacheStore:
    """Cache ``name``'s store for these settings, opened on first use."""
    return LazyCacheStore(lambda: open_store(settings, name))


def wrap_embedding(
    provider: EmbeddingProviderProtocol,
    settings: EngineSettings,
    *,
    resolve_build: BuildResolver | None = None,
) -> EmbeddingProviderProtocol:
    """``provider`` behind the embedding cache, or unchanged when ``cache.embeddings`` is off.

    The model build is what ``resolve_build`` answers on the first call (the
    factory passes one that reads the Ollama digest), else
    ``provider_type:model_name``.
    """
    section = settings.cache.embeddings
    if not section.enabled:
        return provider
    static = (
        None
        if resolve_build is not None
        else model_build_id(
            provider.provider_type, provider.model_name, digest=None, server_version=None
        )
    )
    return CachedEmbeddingProvider(
        provider,
        lazy_store(settings, "embeddings"),
        model_build=static,
        resolve_build=resolve_build,
        require_hit=section.require_hit,
        namespace=EMBEDDINGS_NAMESPACE,
    )


def wrap_chat[P: ChatProvider](
    provider: P,
    settings: EngineSettings,
    *,
    build_resolver: ChatBuildResolver | None = None,
    degenerate: DegenerateCheck | None = None,
) -> P:
    """``provider`` behind the completion cache, or unchanged when ``cache.completions`` is off.

    The wrapper stands in for the provider (it delegates everything but
    ``chat``), so the factories keep their return types. The model build is
    what ``build_resolver`` answers for the provider's model on the first call
    (the factory passes :func:`digest_build_resolver` over the Ollama
    digest), else ``provider_type:model``. ``degenerate`` says which clean
    answers are not stored (:func:`~chaoscypher_core.cache.chat.is_degenerate_answer`
    when None).
    """
    section = settings.cache.completions
    if not section.enabled:
        return provider
    wrapped = CachedChatProvider(
        provider,
        lazy_store(settings, "completions"),
        build_resolver=build_resolver,
        require_hit=section.require_hit,
        namespace=COMPLETIONS_NAMESPACE,
        degenerate=degenerate,
    )
    return cast("P", wrapped)


def unwrap_chat[P](provider: P) -> P:
    """The provider behind the completion cache (a health check must reach the model)."""
    if isinstance(provider, CachedChatProvider):
        return cast("P", provider.inner)
    return provider


ModelBuildReport = Callable[[str, str], Awaitable[Mapping[str, Any]]]
"""``(base_url, model) -> facts``: what a server says about a model (``digest``, ``server_version``)."""


def digest_build_resolver(
    provider_type: str, base_url: str, report: ModelBuildReport
) -> ChatBuildResolver:
    """A resolver of a model's build from the digest the server at ``base_url`` reports.

    Answers None when the server cannot report the digest, so the cache
    bypasses rather than keying on the model name alone (an ``ollama pull``
    would otherwise serve the old weights' answers as fresh).
    """

    async def resolve(model: str) -> str | None:
        """Ask the server for ``model``'s digest and version."""
        facts = await report(base_url, model)
        digest = facts.get("digest")
        if not digest:
            return None
        version = facts.get("server_version")
        return model_build_id(
            provider_type,
            model,
            digest=str(digest),
            server_version=str(version) if version else None,
        )

    return resolve


__all__ = [
    "CACHE_NAMES",
    "CacheName",
    "LazyCacheStore",
    "ModelBuildReport",
    "close_stores",
    "digest_build_resolver",
    "lazy_store",
    "open_store",
    "store_path",
    "unwrap_chat",
    "wrap_chat",
    "wrap_embedding",
]
