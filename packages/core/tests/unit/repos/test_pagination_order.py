# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Task 7.1: list_* pagination must be deterministic.

Regression tests that verify every list_* method with skip/limit pagination
returns a stable, non-overlapping result set across pages. Without an
explicit ORDER BY, SQLite is free to return rows in any order between pages,
causing duplicates or gaps under concurrent writes.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.models import NodeCreate, TemplateCreate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Isolated file-backed SqliteAdapter with all tables created."""
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


@pytest.fixture
def graph_repo(adapter: SqliteAdapter) -> GraphRepository:
    """GraphRepository sharing the adapter's session.

    Seeds one source row so graph_nodes.source_id FK is satisfied.
    """
    adapter.create_source(
        {
            "id": "src-pagination",
            "database_name": adapter.database_name,
            "filename": "pagination.txt",
            "filepath": "/tmp/pagination.txt",
            "file_type": "text",
            "file_size": 1,
            "content_hash": "abc123",
            "status": "indexed",
        }
    )
    return GraphRepository(
        session=adapter.session,
        database_name=adapter.database_name,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_template(graph_repo: GraphRepository, name: str) -> str:
    """Create a minimal node template and return its ID."""
    t = graph_repo.create_template(
        TemplateCreate(name=name, template_type="node", properties=[]),
        is_system=False,
    )
    return t.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_list_nodes_pagination_is_deterministic(graph_repo: GraphRepository) -> None:
    """Paging through list_nodes returns each node exactly once.

    Inserts 30 nodes, pages through them in batches of 10.
    The concatenated result must contain all 30 node IDs with no
    duplicates and no gaps — proving ORDER BY keeps pagination stable.
    """
    template_id = _create_template(graph_repo, "Person")

    inserted_ids: set[str] = set()
    for i in range(30):
        node = graph_repo.create_node(
            NodeCreate(
                template_id=template_id,
                label=f"Node {i:03d}",
                source_id="src-pagination",
            )
        )
        inserted_ids.add(node.id)

    assert len(inserted_ids) == 30, "prerequisite: 30 distinct nodes created"

    pages: list[list[str]] = []
    for page in range(3):
        result = graph_repo.list_nodes(
            template_id=template_id,
            skip=page * 10,
            limit=10,
            include_disabled_sources=True,
        )
        pages.append([n.id for n in result])

    assert len(pages[0]) == 10, f"page 0 expected 10 items, got {len(pages[0])}"
    assert len(pages[1]) == 10, f"page 1 expected 10 items, got {len(pages[1])}"
    assert len(pages[2]) == 10, f"page 2 expected 10 items, got {len(pages[2])}"

    all_ids = pages[0] + pages[1] + pages[2]
    assert len(all_ids) == 30, "concatenated pages must have 30 entries"
    assert len(set(all_ids)) == 30, "no duplicates across pages"
    assert set(all_ids) == inserted_ids, "no rows dropped or invented across pages"

    # Verify each page has no internal overlap with others
    assert set(pages[0]).isdisjoint(set(pages[1])), "page 0 and 1 must not overlap"
    assert set(pages[1]).isdisjoint(set(pages[2])), "page 1 and 2 must not overlap"
    assert set(pages[0]).isdisjoint(set(pages[2])), "page 0 and 2 must not overlap"
