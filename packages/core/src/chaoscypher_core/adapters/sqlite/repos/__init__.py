# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SQLite adapter repositories.

Concrete repository implementations backed by the SQLite adapter. Live under
the adapter layer because they issue raw SQL against the SQLite engine and
depend on adapter-specific schema (FTS5, sqlite-vec virtual tables).

Domain ports that define the abstract contracts live in
``chaoscypher_core.ports``; consumers should type-hint against those
protocols and receive concrete instances via dependency injection.
"""

from chaoscypher_core.adapters.sqlite.repos.extraction import ExtractionRepository
from chaoscypher_core.adapters.sqlite.repos.graph import GraphRepository, remove_corrupt_nodes
from chaoscypher_core.adapters.sqlite.repos.graph_breakdown import GraphBreakdownQueryRepository
from chaoscypher_core.adapters.sqlite.repos.graph_snapshot import GraphSnapshotRepository
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository
from chaoscypher_core.adapters.sqlite.repos.text_indexer import extract_searchable_text


__all__ = [
    "ExtractionRepository",
    "GraphBreakdownQueryRepository",
    "GraphRepository",
    "GraphSnapshotRepository",
    "SearchRepository",
    "extract_searchable_text",
    "remove_corrupt_nodes",
]
