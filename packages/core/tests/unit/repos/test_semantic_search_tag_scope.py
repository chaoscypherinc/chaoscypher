# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``semantic_search`` takes a tag scope, on its vector path and on its rebuild fallback.

A tag set no document carries matches nothing: it never widens to an
unscoped search (``source search --mode semantic --tag T`` relies on it).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository
from chaoscypher_core.models import Node, TagMatch


@pytest.fixture
def repo(tmp_path) -> Iterator[SearchRepository]:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "app.db"
    built = SearchRepository(engine=get_engine(db_path), vector_dim=4, embedding_model="test-m")
    built.index_node(
        Node(
            id="node-a",
            label="Alpha physicist",
            template_id="person",
            properties={},
            embedding=[1.0, 0.0, 0.0, 0.0],
        )
    )
    yield built
    evict_engine(db_path)


async def _vector(_query: str) -> list[float]:
    return [1.0, 0.0, 0.0, 0.0]


def _set_rebuilding(repo: SearchRepository) -> None:
    from sqlalchemy import text

    with repo._engine.connect() as conn:
        conn.execute(
            text(
                "INSERT OR REPLACE INTO search_metadata (key, value) VALUES ('is_rebuilding', 'true')"
            )
        )
        conn.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("rebuilding", [False, True])
async def test_semantic_search_tag_scope_matching_nothing_returns_nothing(
    repo: SearchRepository, rebuilding: bool
) -> None:
    if rebuilding:
        _set_rebuilding(repo)

    unscoped = await repo.semantic_search(
        "physicist", k=5, embedding_provider_callback=_vector, item_type="node"
    )
    scoped = await repo.semantic_search(
        "physicist",
        k=5,
        embedding_provider_callback=_vector,
        item_type="node",
        tags=["alpha"],
        tag_match=TagMatch.ALL,
    )

    assert [hit[0] for hit in unscoped] == ["node-a"]
    assert scoped == []
