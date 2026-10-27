# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Releasing a source reopens the facts it closed (spec 2026-10-20 decision 7: forget with lineage)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.models import EdgeCreate, NodeCreate, TemplateCreate
from chaoscypher_core.services.graph.memory.supersession import (
    MEMORY_SINGLE_VALUED,
    supersede_memory_edges,
)


DB = "test"


@pytest.fixture
def repo(tmp_path: Path) -> GraphRepository:
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(db_path), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name=DB)
    adapter.connect()
    session = adapter.session
    assert session is not None
    for sid, valid_from in (("m_paris", "2026-03-02"), ("m_lyon", "2026-10-20")):
        session.add(
            SourceRow(
                id=sid,
                database_name=DB,
                filename=f"{sid}.txt",
                filepath=f"/{sid}.txt",
                file_type="txt",
                status="committed",
                source_type="memory",
                memory_by="alice",
                valid_from=valid_from,
                created_at=datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
            )
        )
    session.commit()
    graph = GraphRepository(session, database_name=DB)
    graph.create_template(TemplateCreate(name="Person", template_type="node"), custom_id="person")
    graph.create_template(TemplateCreate(name="rel", template_type="edge"), custom_id="rel")
    for nid in ("pierre", "paris", "lyon"):
        graph.create_node(NodeCreate(template_id="person", label=nid.title()), custom_id=nid)
    for eid, tgt, src in (("e_paris", "paris", "m_paris"), ("e_lyon", "lyon", "m_lyon")):
        graph.create_edge(
            EdgeCreate(
                template_id="rel",
                source_node_id="pierre",
                target_node_id=tgt,
                label="located_in",
                source_id=src,
            ),
            custom_id=eid,
        )
    supersede_memory_edges(graph, "m_lyon", single_valued=MEMORY_SINGLE_VALUED)
    return graph


def test_releasing_the_closing_source_reopens_the_edge_it_closed(repo: GraphRepository) -> None:
    closed = repo.get_edge("e_paris")
    assert closed is not None and closed.valid_to == date(2026, 10, 20)
    assert closed.closed_by_source_id == "m_lyon"
    released = repo.release_source("m_lyon")
    assert "e_lyon" in released.deleted_edge_ids
    reopened = repo.get_edge("e_paris")
    assert reopened is not None
    assert (reopened.valid_from, reopened.valid_to, reopened.closed_by_source_id) == (
        date(2026, 3, 2),
        None,
        None,
    )


def test_releasing_the_closed_source_leaves_the_open_fact_alone(repo: GraphRepository) -> None:
    repo.release_source("m_paris")
    lyon = repo.get_edge("e_lyon")
    assert lyon is not None and lyon.valid_to is None and lyon.valid_from == date(2026, 10, 20)
    assert repo.get_edge("e_paris") is None
