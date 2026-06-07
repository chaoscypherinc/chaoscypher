# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Storage Adapters for Chaos Cypher Knowledge Engine.

Barrel exports from sub-packages for shorter imports. Each sub-package
(``sqlite``, ``llm``, ``embedding``) also has its own ``__init__.py``
with full exports.

Example:
    from chaoscypher_core.adapters import SqliteAdapter, create_embedding_provider
"""

# Embedding providers
from chaoscypher_core.adapters.embedding import create_embedding_provider

# LLM providers
from chaoscypher_core.adapters.llm import (
    LLMProvider,
    ProviderFactory,
    get_llm_semaphore,
    get_model_registry,
    get_ollama_load_balancer,
)

# SQLite storage
from chaoscypher_core.adapters.sqlite import (
    SqliteAdapter,
    evict_engine,
    initialize_database,
)


# Session helpers (``get_session`` / ``get_db_session``) and the raw
# ``Engine`` factory (``get_engine``) used to be re-exported here for
# historical convenience. Phase 3 removed those re-exports: they are
# adapter-internal plumbing. Callers that still need them should import
# from ``chaoscypher_core.adapters.sqlite.session`` /
# ``chaoscypher_core.adapters.sqlite.engine`` directly, or (preferred)
# route through ``SqliteAdapter.transaction()``.
__all__ = [  # noqa: RUF022 — intentionally grouped by subsystem
    # Embedding
    "create_embedding_provider",
    # LLM
    "LLMProvider",
    "ProviderFactory",
    "get_llm_semaphore",
    "get_model_registry",
    "get_ollama_load_balancer",
    # SQLite
    "SqliteAdapter",
    "evict_engine",
    "initialize_database",
]
