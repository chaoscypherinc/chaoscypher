# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A keyword-only engine on a database another process gave vectors: its rebuilds leave them.

A vector process (8 dimensions) writes nodes and a chunk with their vectors; a
keyword-only engine (vector search off, the default dimensions) then rebuilds the
indexes and refreshes nodes after a merge. The vector tables keep every row, and
nothing is queued for a re-embed nobody would run.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chaoscypher_core import Engine
from chaoscypher_core.services.graph.management.release import (
    reembed_nodes,
    refresh_node_search,
)
from chaoscypher_core.services.search.engine.index import index_source_chunk_vectors
from chaoscypher_core.settings import EngineSettings
from chaoscypher_core.testing.embedding import FakeEmbeddingProvider
from chaoscypher_core.utils.id import generate_hex_id


DIMS = 8


def _engine(path: Path, *, vectors: bool) -> Engine:
    settings = EngineSettings()
    settings.search.enable_vector_search = vectors
    if vectors:
        settings.search.vector_dimensions = DIMS
    with patch(
        "chaoscypher_core.adapters.embedding.factory.create_embedding_provider",
        return_value=FakeEmbeddingProvider(dimensions=DIMS),
    ):
        return Engine(str(path / "databases" / "test"), settings=settings)


@pytest.fixture
async def vectors_written(tmp_path: Path) -> Generator[SimpleNamespace]:
    on = _engine(tmp_path, vectors=True)
    source_id = generate_hex_id()
    await on.process_document(
        "Pierre Bezukhov moved to Lyon.",
        source_id=source_id,
        filename="memory.txt",
        auto_confirm=True,
        source_type="memory",
        extract=False,
    )
    on.storage_adapter.update_file(source_id, on.database_name, {"status": "committed"})
    index_source_chunk_vectors(on.storage_adapter, on.search_repository, source_id, page_size=100)
    ids = [on.add_node("Person", label).id for label in ("Pierre", "Pierre Bezukhov")]
    await reembed_nodes(on.graph_repository, ids, on.embedding_service, search=on.search_repository)
    assert on.search_repository.count_vectors("node") == 2
    assert on.search_repository.count_vectors("chunk") == 1
    on.close()

    off = _engine(tmp_path, vectors=False)
    yield SimpleNamespace(engine=off, node_ids=ids)
    off.close()


def test_a_keyword_only_rebuild_leaves_the_vector_tables(vectors_written: SimpleNamespace) -> None:
    off = vectors_written.engine

    result = off.rebuild_indexes()

    assert result.total_nodes == 2
    search = off.search_repository
    assert search.count_vectors("node") == 2
    assert search.count_vectors("chunk") == 1
    assert search.has_pending_reindex is False
    assert [i for i, _ in search.keyword_search("Bezukhov", item_type="node")] == [
        vectors_written.node_ids[1]
    ]


async def test_a_keyword_only_refresh_after_a_merge_queues_nothing(
    vectors_written: SimpleNamespace,
) -> None:
    off = vectors_written.engine

    await refresh_node_search(
        off.graph_repository, vectors_written.node_ids, None, search=off.search_repository
    )

    assert off.search_repository.has_pending_reindex is False
    assert off.search_repository.count_vectors("node") == 2
