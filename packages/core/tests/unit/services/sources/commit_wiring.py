# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A ``SourceCommitService`` on a test adapter, wired the way production wires one.

Five test files each built their own copy of this (2026-10-26), with no embedding provider: the
service then resolved the default one from ``EngineSettings()``, the local model, and three of
them downloaded Qwen3-Embedding-0.6B (1.2 GB) into the test's data directory and loaded it,
~19 s a test. The provider here is the fake, sized to the search repository's vectors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.embedding import FakeEmbeddingProvider


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


VECTOR_DIM = 4


def commit_service_on(adapter: SqliteAdapter) -> SourceCommitService:
    """A commit service on ``adapter``'s session, as the import service builds one.

    The adapter implements the source, sources and indexing storage protocols through its
    mixins, so it is passed for all three.
    """
    return SourceCommitService(
        graph_repository=GraphRepository(
            session=adapter.session,
            database_name=adapter.database_name,
        ),
        source_repository=adapter,
        sources_repository=adapter,
        indexing_repository=adapter,
        search_repository=SearchRepository(
            engine=get_engine(adapter.db_path),
            vector_dim=VECTOR_DIM,
            embedding_model=FakeEmbeddingProvider.model_name,
        ),
        settings=EngineSettings(),
        embedding_provider=FakeEmbeddingProvider(dimensions=VECTOR_DIM),
    )
