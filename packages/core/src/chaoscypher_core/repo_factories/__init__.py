# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Runtime repository factories.

Per-call constructors for the graph, search, and embedding repositories.
These take an explicit session / adapter / database name (no FastAPI
``Depends``), so both the API process (Cortex) and the worker process
(Neuron) can call them directly.

The FastAPI-coupled ``RepositoryBundle`` + ``get_repositories`` aggregator
stays in ``chaoscypher_cortex.shared.repositories.bundle`` because it
uses ``fastapi.Depends`` for HTTP-layer DI.
"""

from chaoscypher_core.repo_factories.embedding_factory import get_embedding_service
from chaoscypher_core.repo_factories.graph_factory import get_graph_repository
from chaoscypher_core.repo_factories.search_factory import get_search_repository


__all__ = [
    "get_embedding_service",
    "get_graph_repository",
    "get_search_repository",
]
