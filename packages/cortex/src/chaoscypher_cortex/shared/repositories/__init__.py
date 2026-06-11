# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cortex repositories — FastAPI-coupled aggregator only.

The per-call repository factories (``get_graph_repository``,
``get_search_repository``, ``get_embedding_service``) live in
``chaoscypher_core.repo_factories``. Cortex retains only the
``RepositoryBundle`` aggregator, which uses ``fastapi.Depends`` to
compose per-request session + adapter + repositories for FastAPI
handlers.
"""

from chaoscypher_cortex.shared.repositories.bundle import (
    RepositoryBundle,
    get_repositories,
)


__all__ = ["RepositoryBundle", "get_repositories"]
