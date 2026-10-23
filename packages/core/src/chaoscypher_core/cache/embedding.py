# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The embedding cache: an ``EmbeddingProviderProtocol`` over any provider.

Each text is looked up under :func:`~chaoscypher_core.cache.keys.embedding_key`
(the model build, the text, the provider's output dimensions and query
prefix); only the misses go to the inner provider, in one ``batch_embed``
call, in first-appearance order, and the answer comes back in input order.
Embeddings are deterministic, so no decoding policy applies. A miss records
the model time it cost (the inner call's wall clock shared over its texts);
a hit adds that time to the time saved.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.cache.build import BuildResolver, LazyModelBuild, pinned_build
from chaoscypher_core.cache.call_site import current_call_site
from chaoscypher_core.cache.guard import store_call
from chaoscypher_core.cache.keys import embedding_key
from chaoscypher_core.cache.store import CacheEntry, CacheStore, encode_value, stored_value
from chaoscypher_core.exceptions import LLMError, ValidationError
from chaoscypher_core.models import BatchEmbedResult, EmbedResult


if TYPE_CHECKING:
    from chaoscypher_core.ports.embedding import EmbeddingHealthStatus, EmbeddingProviderProtocol


logger = structlog.get_logger(__name__)

EMBEDDINGS_NAMESPACE = "embeddings"
"""The namespace the product's embedding cache stores under."""


def _is_vector(value: Any) -> bool:
    """Whether a stored value is a vector: a list of numbers (never a bool)."""
    return isinstance(value, list) and all(
        isinstance(x, int | float) and not isinstance(x, bool) for x in value
    )


class CachedEmbeddingProvider:
    """``EmbeddingProviderProtocol`` over any provider: misses go to inner in one batch, in input order.

    Example:
        store = SqliteCacheStore(Path("cache/embeddings.db"), max_bytes=2 * 1024**3)
        provider = CachedEmbeddingProvider(my_provider, store, model_build="ollama:m:sha256:abc")
    """

    def __init__(
        self,
        inner: EmbeddingProviderProtocol,
        store: CacheStore,
        *,
        model_build: str | None = None,
        resolve_build: BuildResolver | None = None,
        enabled: bool = True,
        require_hit: bool = False,
        namespace: str = EMBEDDINGS_NAMESPACE,
    ) -> None:
        """Wrap ``inner``; key on ``model_build``, or on what ``resolve_build`` answers on first use.

        A build pinned by :func:`~chaoscypher_core.cache.build.pinned_builds`
        wins over both. ``enabled=False`` passes every call through and
        records it as a bypass; ``require_hit`` (replay) refuses a miss or an
        unknown build with a
        :class:`~chaoscypher_core.exceptions.ValidationError` instead of
        calling the model.
        """
        self._inner = inner
        self._store = store
        self._build = LazyModelBuild(model_build, resolve_build)
        self._enabled = enabled
        self._require_hit = require_hit
        self._namespace = namespace

    # ------------------------------------------------------------ delegation

    @property
    def inner(self) -> EmbeddingProviderProtocol:
        """The wrapped provider."""
        return self._inner

    @property
    def provider_type(self) -> str:
        """The wrapped provider's type."""
        return self._inner.provider_type

    @property
    def model_name(self) -> str:
        """The wrapped provider's model (a protocol member, so defined, not delegated)."""
        return self._inner.model_name

    @model_name.setter
    def model_name(self, value: str) -> None:
        """Set the wrapped provider's model."""
        self._inner.model_name = value

    @property
    def query_prefix(self) -> str:
        """The wrapped provider's query prefix (a protocol member, so defined, not delegated)."""
        return self._inner.query_prefix

    @query_prefix.setter
    def query_prefix(self, value: str) -> None:
        """Set the wrapped provider's query prefix."""
        self._inner.query_prefix = value

    def __getattr__(self, name: str) -> Any:
        """Everything this wrapper does not define is the inner provider's."""
        inner = self.__dict__.get("_inner")
        if inner is None or name.startswith("__"):
            raise AttributeError(name)
        return getattr(inner, name)

    async def check_health(self) -> EmbeddingHealthStatus:
        """The wrapped provider's health (never cached)."""
        return await self._inner.check_health()

    # -------------------------------------------------------------- caching

    def _key(self, build: str, text: str) -> str:
        """The entry key of ``text`` for this provider's build, dimensions and prefix."""
        dims = getattr(self._inner, "vector_dimensions", None)
        prefix = getattr(self._inner, "query_prefix", "")
        return embedding_key(
            build,
            text,
            dimensions=dims if isinstance(dims, int) and not isinstance(dims, bool) else None,
            query_prefix=prefix if isinstance(prefix, str) else "",
        )

    def _lookup(self, keys: list[str]) -> dict[str, tuple[list[float], int]]:
        """``{key: (vector, latency_ms)}`` for the keys the store holds (a bad value is a miss)."""
        found: dict[str, tuple[list[float], int]] = {}
        for key in dict.fromkeys(keys):
            entry = self._store.get(self._namespace, key)
            if entry is None:
                continue
            vector = stored_value(entry, namespace=self._namespace, key=key, shaped=_is_vector)
            if vector is not None:
                found[key] = (vector, entry.latency_ms)
        return found

    def _save(
        self, build: str, site: str, items: list[tuple[str, list[float]]], latency_ms: int
    ) -> None:
        """Store each ``(key, vector)`` at ``latency_ms`` apiece."""
        for key, vector in items:
            value = encode_value(vector)
            self._store.put(
                self._namespace,
                key,
                CacheEntry(
                    value=value,
                    model_build=build,
                    call_site=site,
                    seeded=True,
                    latency_ms=latency_ms,
                    bytes=len(value),
                ),
            )

    def _account(self, site: str, hits: int, saved_ms: int, misses: int) -> None:
        """Record ``hits`` (saving ``saved_ms``) and ``misses`` for ``site``."""
        if hits:
            self._store.record(
                self._namespace, site, hit=True, latency_saved_ms=saved_ms, count=hits
            )
        if misses:
            self._store.record(self._namespace, site, hit=False, count=misses)

    async def _bypass(self, site: str, count: int) -> None:
        """Record ``count`` bypassed texts."""
        await store_call(
            lambda: self._store.record(self._namespace, site, hit=None, count=count),
            default=None,
            op="record",
        )

    async def _model_build(self) -> str | None:
        """The build to key on: a pinned one (a replay's) first, else the given or resolved one."""
        pinned = pinned_build(self.provider_type, self.model_name)
        return pinned if pinned is not None else await self._build.get()

    async def _vectors(
        self, texts: list[str], *, single: bool, batch_size: int
    ) -> list[list[float]] | None:
        """Every text's vector through the store, or None when the cache does not apply."""
        site = current_call_site()
        build = await self._model_build() if self._enabled else None
        if build is None:
            if self._require_hit and self._enabled:
                msg = (
                    f"replay needs the model build of {self.provider_type}:{self.model_name} "
                    "(the server did not report its digest; a replay pins the build it was "
                    "recorded under)"
                )
                raise ValidationError(msg, field="embeddings")
            await self._bypass(site, len(texts))
            return None
        keys = [self._key(build, t) for t in texts]
        nothing: dict[str, tuple[list[float], int]] = {}
        found = await store_call(self._lookup, keys, default=nothing, op="get")
        missing = {k: t for k, t in zip(keys, texts, strict=True) if k not in found}
        if missing and self._require_hit:
            msg = f"replay miss: {next(iter(missing))}"
            raise ValidationError(msg, field="embeddings")
        if missing:
            miss_texts = list(missing.values())
            started = time.perf_counter()
            if single:
                fresh = [(await self._inner.embed(miss_texts[0])).embedding]
            else:
                fresh = (
                    await self._inner.batch_embed(miss_texts, batch_size=batch_size)
                ).embeddings
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if len(fresh) != len(miss_texts):  # the port is all-or-nothing; never store a mismatch
                msg = (
                    f"embedding provider returned {len(fresh)} vectors for {len(miss_texts)} texts"
                )
                raise LLMError(msg, provider=self.provider_type, model=self.model_name)
            per_text_ms = elapsed_ms // len(miss_texts)
            items = list(zip(missing, fresh, strict=True))
            await store_call(self._save, build, site, items, per_text_ms, default=None, op="put")
            found.update({k: (v, per_text_ms) for k, v in items})
        hit_keys = [k for k in keys if k not in missing]
        saved_ms = sum(found[k][1] for k in hit_keys)
        await store_call(
            self._account,
            site,
            len(hit_keys),
            saved_ms,
            len(keys) - len(hit_keys),
            default=None,
            op="record",
        )
        return [found[k][0] for k in keys]

    async def embed(self, text: str) -> EmbedResult:
        """The vector of ``text``: from the store, else from the inner provider (then stored)."""
        vectors = await self._vectors([text], single=True, batch_size=1)
        if vectors is None:
            return await self._inner.embed(text)
        return EmbedResult(embedding=vectors[0], provider=self.provider_type)

    async def batch_embed(self, texts: list[str], batch_size: int = 64) -> BatchEmbedResult:
        """Every text's vector in input order; only the misses reach the inner provider."""
        if not texts:
            return await self._inner.batch_embed(texts, batch_size=batch_size)
        vectors = await self._vectors(texts, single=False, batch_size=batch_size)
        if vectors is None:
            return await self._inner.batch_embed(texts, batch_size=batch_size)
        return BatchEmbedResult(embeddings=vectors, total=len(vectors), provider=self.provider_type)


__all__ = ["EMBEDDINGS_NAMESPACE", "CachedEmbeddingProvider"]
