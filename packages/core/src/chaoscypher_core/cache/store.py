# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""What a cache store holds and the protocol every store implements.

A store maps ``(namespace, key)`` to a :class:`CacheEntry`: the stored
object as zlib-compressed canonical JSON (:func:`encode_value`) plus the
facts the stats page needs (which model build answered, from which call
site, whether the answer is the seeded deterministic one, and what the
first call cost). Namespaces partition one store file: ``completions`` and
``embeddings`` for the product caches, ``bench:<run_id>`` for a benchmark
run whose entries a reference pack exports.

Stats are kept per ``(namespace, call_site)`` apart from the entries, so a
:meth:`CacheStore.clear` empties the entries and keeps the history.
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import structlog


logger = structlog.get_logger(__name__)


def encode_value(obj: Any) -> bytes:
    """``obj`` as zlib-compressed canonical JSON (sorted keys, no spaces, UTF-8)."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return zlib.compress(text.encode("utf-8"))


def decode_value(value: bytes) -> Any:
    """The object :func:`encode_value` stored."""
    return json.loads(zlib.decompress(value).decode("utf-8"))


def stored_value(
    entry: CacheEntry, *, namespace: str, key: str, shaped: Callable[[Any], bool]
) -> Any | None:
    """The object an entry holds, or None (a soft miss) when it does not decode or is misshapen.

    The one read of a stored value every wrapper uses: a value that is not
    zlib (``zlib.error``), not UTF-8 or not JSON (``ValueError``), or that
    ``shaped`` rejects (a vector that is not a list of numbers, a response
    that is not a dict), is logged as ``llm_cache_bad_value`` and treated
    as a miss, so a damaged store file costs a model call, never a crash.
    """
    try:
        value = decode_value(entry.value)
    except ValueError, TypeError, zlib.error:
        value = None
    else:
        if shaped(value):
            return value
    logger.warning("llm_cache_bad_value", namespace=namespace, key=key)
    return None


@dataclass(frozen=True)
class CacheEntry:
    """One stored answer and the facts about how it was made."""

    value: bytes
    """zlib-compressed canonical JSON of the stored object (:func:`encode_value`)."""
    model_build: str
    """The model build that produced it (:func:`chaoscypher_core.cache.keys.model_build_id`)."""
    call_site: str
    """The stage that first asked (:mod:`chaoscypher_core.cache.call_site`)."""
    seeded: bool
    """True when the provider honours seeds, so a hit is the deterministic answer."""
    latency_ms: int
    """What the first call cost in model time; a hit adds it to the time saved."""
    bytes: int
    """``len(value)``: what the entry weighs against the size cap."""


@dataclass(frozen=True)
class CacheStats:
    """One namespace's size and history, as data (the Settings page renders it)."""

    namespace: str
    """The namespace these numbers describe."""
    entries: int
    """How many entries the namespace holds now."""
    bytes: int
    """Their total stored size."""
    max_bytes: int
    """The cap the caller enforces."""
    hits: int
    """Answers served from the store."""
    misses: int
    """Calls that went to the model and were offered to the store."""
    bypasses: int
    """Calls the policy kept away from the store (sampled or unseeded decoding)."""
    latency_saved_ms: int
    """The sum of the first-call model time of every hit."""
    by_call_site: dict[str, dict[str, int]] = field(default_factory=dict)
    """``{site: {hits, misses, bypasses, latency_saved_ms}}``."""


@runtime_checkable
class CacheStore(Protocol):
    """A key-value store of :class:`CacheEntry` with per-call-site stats."""

    def get(self, namespace: str, key: str) -> CacheEntry | None:
        """The entry under ``key`` (bumping its recency), or None on a miss."""
        ...

    def put(self, namespace: str, key: str, entry: CacheEntry) -> None:
        """Store ``entry`` under ``key`` (last write wins), evicting past the cap."""
        ...

    def record(
        self,
        namespace: str,
        call_site: str,
        *,
        hit: bool | None,
        latency_saved_ms: int = 0,
        count: int = 1,
    ) -> None:
        """Count ``count`` calls: hits (``True``), misses (``False``) or bypasses (``None``).

        ``latency_saved_ms`` is the total the hits saved.
        """
        ...

    def clear(self, namespace: str | None = None) -> int:
        """Remove the entries of ``namespace`` (every namespace when None); return how many."""
        ...

    def stats(self, namespace: str, max_bytes: int) -> CacheStats:
        """The size and history of ``namespace``."""
        ...

    def namespaces(self) -> list[str]:
        """The namespaces that hold entries, sorted."""
        ...

    def close(self) -> None:
        """Release the store's resources."""
        ...


__all__ = [
    "CacheEntry",
    "CacheStats",
    "CacheStore",
    "decode_value",
    "encode_value",
    "stored_value",
]
