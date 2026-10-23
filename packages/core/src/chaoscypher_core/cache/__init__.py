# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM response and embedding caches: the same input to the same model build costs nothing twice.

A store (:class:`SqliteCacheStore`, one SQLite file) holds pinned
completions (:class:`CachedChatProvider`) and embeddings
(:class:`CachedEmbeddingProvider`) keyed on the model build and the normalised
input (:mod:`~chaoscypher_core.cache.keys`), so the same input to the same
model costs nothing the second time. Only pinned decoding is cached
(:mod:`~chaoscypher_core.cache.policy`); stats are kept per call site
(:mod:`~chaoscypher_core.cache.call_site`).

This package imports only ``chaoscypher_core.models``, ``ports``, ``utils``,
``exceptions`` and ``settings``; the provider factories import it
(:mod:`~chaoscypher_core.cache.wiring`), never the reverse.

Wrap any chat provider (an object with ``BaseLLMProvider.chat`` and a
``config`` naming its ``chat_provider`` and ``ai_temperature``) and the
second identical pinned call is served from the file. Without a build
resolver the key names ``provider:model``; an Ollama call is cached only
when it carries a seed. :class:`CachedEmbeddingProvider` wraps an embedding
provider the same way, keyed on the ``model_build`` you pass.

Example:
    from pathlib import Path

    from chaoscypher_core.cache import CachedChatProvider, SqliteCacheStore

    store = SqliteCacheStore(Path("cache/completions.db"), max_bytes=2 * 1024**3)
    provider = CachedChatProvider(my_provider, store)
    answer = await provider.chat([{"role": "user", "content": "Who kept the accounts?"}])
"""

from __future__ import annotations

from chaoscypher_core.cache.build import pinned_build, pinned_builds
from chaoscypher_core.cache.call_site import CALL_SITES, call_site, current_call_site
from chaoscypher_core.cache.chat import (
    CLEAN_FINISH_REASONS,
    COMPLETIONS_NAMESPACE,
    CachedChatProvider,
    ChatBuildResolver,
    DegenerateCheck,
    is_degenerate_answer,
)
from chaoscypher_core.cache.embedding import EMBEDDINGS_NAMESPACE, CachedEmbeddingProvider
from chaoscypher_core.cache.export import export_store, file_sha256, import_store
from chaoscypher_core.cache.keys import (
    Decoding,
    build_names_model,
    completion_key,
    embedding_key,
    model_build_id,
    normalise_messages,
)
from chaoscypher_core.cache.policy import cacheable, seeded
from chaoscypher_core.cache.sqlite_store import SqliteCacheStore, read_entries
from chaoscypher_core.cache.store import (
    CacheEntry,
    CacheStats,
    CacheStore,
    decode_value,
    encode_value,
    stored_value,
)


__all__ = [
    "CALL_SITES",
    "CLEAN_FINISH_REASONS",
    "COMPLETIONS_NAMESPACE",
    "EMBEDDINGS_NAMESPACE",
    "CacheEntry",
    "CacheStats",
    "CacheStore",
    "CachedChatProvider",
    "CachedEmbeddingProvider",
    "ChatBuildResolver",
    "Decoding",
    "DegenerateCheck",
    "SqliteCacheStore",
    "build_names_model",
    "cacheable",
    "call_site",
    "completion_key",
    "current_call_site",
    "decode_value",
    "embedding_key",
    "encode_value",
    "export_store",
    "file_sha256",
    "import_store",
    "is_degenerate_answer",
    "model_build_id",
    "normalise_messages",
    "pinned_build",
    "pinned_builds",
    "read_entries",
    "seeded",
    "stored_value",
]
