# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``list_nodes_without_embeddings``: every node with no vector, by id, from a keyset cursor.

A node created without a vector stores the JSON ``null`` (the column is ``JSON``, which
writes Python None as the text ``null``), not SQL NULL; a failed re-embed can store ``[]``.
Both read as "no vector".
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.models import NodeCreate, TemplateCreate


@pytest.fixture
def repo(tmp_path: Path) -> Generator[GraphRepository]:
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="test")
    adapter.connect()
    repo = GraphRepository(adapter.session, database_name="test")
    repo.create_template(TemplateCreate(name="person", template_type="node"), custom_id="person")
    for nid, label in (("n-a", "Anna"), ("n-b", "Lise"), ("n-c", "Pierre"), ("n-d", "Boris")):
        repo.create_node(
            NodeCreate(template_id="person", label=label, entity_type="person"), custom_id=nid
        )
    repo.update_node_embeddings_batch({"n-c": [0.1, 0.2]})
    repo.session.execute(text("UPDATE graph_nodes SET embedding = '[]' WHERE id = 'n-d'"))
    repo.session.commit()
    yield repo
    adapter.disconnect()


def test_a_node_created_without_a_vector_is_listed(repo: GraphRepository) -> None:
    assert [n.id for n in repo.list_nodes_without_embeddings(limit=10)] == ["n-a", "n-b", "n-d"]


def test_the_cursor_reads_past_the_given_id(repo: GraphRepository) -> None:
    assert [n.id for n in repo.list_nodes_without_embeddings(after_id="n-a", limit=1)] == ["n-b"]
    assert [n.id for n in repo.list_nodes_without_embeddings(after_id="n-d", limit=10)] == []


def test_a_malformed_vector_does_not_fail_the_listing(repo: GraphRepository) -> None:
    """A row holding text that is not JSON is skipped, not an error for every caller."""
    repo.session.execute(text("UPDATE graph_nodes SET embedding = 'not json' WHERE id = 'n-b'"))
    repo.session.commit()
    assert [n.id for n in repo.list_nodes_without_embeddings(limit=10)] == ["n-a", "n-d"]
