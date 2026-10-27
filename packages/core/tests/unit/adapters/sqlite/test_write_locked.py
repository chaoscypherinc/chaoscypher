# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``write_locked()`` reads the database as of the lock, not the session's cached rows.

SQLAlchemy hands back an object already in a session's identity map without reloading it,
so a row a long-lived session loaded before the lock would keep the values it had then:
``BEGIN IMMEDIATE`` fixes the database snapshot, not the session's cache. Two sessions on
one file-backed database stand for two processes (spec 2026-10-24 § 3.3 (a)).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.models import EdgeCreate, NodeCreate, TemplateCreate


DB = "test"


def _repository(db_path: Path) -> tuple[SqliteAdapter, GraphRepository]:
    adapter = SqliteAdapter(str(db_path), database_name=DB)
    adapter.connect()
    assert adapter.session is not None
    return adapter, GraphRepository(adapter.session, database_name=DB)


@pytest.fixture
def pair(tmp_path: Path) -> Iterator[tuple[GraphRepository, GraphRepository]]:
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(db_path), checkfirst=True)
    first_adapter, first = _repository(db_path)
    second_adapter, second = _repository(db_path)
    first.create_template(TemplateCreate(name="Person", template_type="node"), custom_id="person")
    first.create_template(TemplateCreate(name="rel", template_type="edge"), custom_id="rel")
    first.create_node(NodeCreate(template_id="person", label="Pierre"), custom_id="pierre")
    first.create_node(NodeCreate(template_id="person", label="Lyon"), custom_id="lyon")
    first.create_edge(
        EdgeCreate(
            template_id="rel", source_node_id="pierre", target_node_id="lyon", label="located_in"
        ),
        custom_id="e1",
    )
    try:
        yield first, second
    finally:
        first_adapter.disconnect()
        second_adapter.disconnect()


def test_a_read_inside_write_locked_sees_a_row_another_session_changed(
    pair: tuple[GraphRepository, GraphRepository],
) -> None:
    first, second = pair
    # the row now sits in first's session; the identity map is weak, so something must hold
    # it (an object a caller kept, or one with changes of its own) for the cache to matter
    cached = first.session.get(GraphEdge, "e1")
    assert cached is not None and cached.valid_to is None
    second.set_edge_validity(
        "e1", valid_from=date(2026, 10, 20), valid_to=date(2026, 10, 21), closed_by_source_id=None
    )
    with first.write_locked():
        [edge] = first.edges_from("pierre", "located_in")
    assert edge.valid_to == date(2026, 10, 21)


def test_a_change_made_before_write_locked_is_kept(
    pair: tuple[GraphRepository, GraphRepository],
) -> None:
    first, second = pair
    node = first.session.get(GraphNode, "pierre")
    assert node is not None
    node.label = "Pierre Bezukhov"  # not flushed yet
    with first.write_locked():
        pass
    reread = second.get_node("pierre", include_embedding=False)
    assert reread is not None and reread.label == "Pierre Bezukhov"


def test_embeddings_written_while_another_writer_deletes_a_node_never_fail(
    pair: tuple[GraphRepository, GraphRepository], tmp_path: Path
) -> None:
    """The re-embed after an identity pass reads its nodes and writes their vectors in one unit.

    Another process merging a node away between that read and the write made the UPDATE
    match no row (``StaleDataError``), and the memory's link failed. The other writer here
    tries its delete at the worst moment, as the batch flushes; it waits for the lock.
    """
    first, _second = pair
    attempts: list[str] = []

    def delete_from_another_process(*_args: object) -> None:
        other = sqlite3.connect(tmp_path / "app.db", timeout=0.1)
        try:
            other.execute("DELETE FROM graph_edges WHERE id = 'e1'")
            other.execute("DELETE FROM graph_nodes WHERE id = 'lyon'")
            other.commit()
            attempts.append("deleted")
        except sqlite3.OperationalError as error:
            attempts.append(str(error))
        finally:
            other.close()

    event.listen(first.session, "before_flush", delete_from_another_process, once=True)
    changed = first.update_node_embeddings_batch({"pierre": [0.1] * 8, "lyon": [0.2] * 8})
    assert attempts == ["database is locked"]  # the read and the write held the lock
    assert changed == 2
