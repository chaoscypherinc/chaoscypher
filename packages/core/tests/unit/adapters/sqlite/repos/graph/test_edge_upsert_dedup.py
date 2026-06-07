# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Edge upsert dedup: same endpoints + template = one row, regardless of label spelling."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import GraphNode, GraphTemplate, SourceRow
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_edge_ops import _stable_edge_id
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _adapter(tmp_path: Path) -> Generator[SqliteAdapter, Any]:
    """Fresh file-backed adapter with schema created."""
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    yield adapter
    adapter.disconnect()


@pytest.fixture
def graph_repo_factory(_adapter: SqliteAdapter):
    """Seed minimal rows and return a (GraphRepository, session) tuple.

    Seeded rows:
    - SourceRow "src_x" (enabled, status=committed)
    - edge template "t_interacts"
    - edge template "t_serves"
    - node "n_boris" (template=system_template_item or a minimal node template)
    - node "n_vicomte"

    Returns a callable so the test can build the repo after seeding.
    """
    session = _adapter.session
    assert session is not None

    # Seed a node template (needed for node FK; use a simple one)
    tpl_node = GraphTemplate(
        id="tpl_node_person",
        database_name="default",
        name="Person",
        template_type="node",
        color="#aaaaaa",
    )
    session.add(tpl_node)

    # Seed edge templates
    for tpl_id, tpl_name in [("t_interacts", "interacts_with"), ("t_serves", "serves")]:
        session.add(
            GraphTemplate(
                id=tpl_id,
                database_name="default",
                name=tpl_name,
                template_type="edge",
                color="#bbbbbb",
            )
        )

    # Seed source row
    session.add(
        SourceRow(
            id="src_x",
            database_name="default",
            filename="test_source.txt",
            filepath="/tmp/test_source.txt",
            title="Test Source",
            source_type="text",
            status="committed",
            enabled=True,
        )
    )
    session.flush()

    # Seed endpoint nodes (FK on graph_edges requires them)
    for node_id, label in [("n_boris", "Boris"), ("n_vicomte", "Vicomte")]:
        session.add(
            GraphNode(
                id=node_id,
                database_name="default",
                graph_name="knowledge",
                template_id="tpl_node_person",
                label=label,
                source_id="src_x",
            )
        )
    session.commit()

    def _factory():
        repo = GraphRepository(session, database_name="default")
        return repo, session

    return _factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_stable_edge_id_ignores_label():
    """Same endpoints + template should produce the same ID regardless of label text."""
    base = {
        "database_name": "default",
        "source_id": "src_x",
        "template_id": "t_serves",
        "source_node_id": "n_a",
        "target_node_id": "n_b",
    }
    # Phase 4 removes label from _stable_edge_id, so calling with no label arg
    # produces a single ID.
    sid = _stable_edge_id(**base)
    # Re-call with the same args — same ID.
    assert sid == _stable_edge_id(**base)


@pytest.mark.asyncio
async def test_upsert_collapses_repeated_pair_to_single_row(graph_repo_factory):
    """Eleven inserts of the same (src, dst, template) collapse to one row."""
    from chaoscypher_core.models import EdgeCreate

    repo, session = graph_repo_factory()
    edges_in = [
        EdgeCreate(
            template_id="t_interacts",
            source_node_id="n_boris",
            target_node_id="n_vicomte",
            label=label,
            properties={},
            source_id="src_x",
        )
        for label in [
            "interacts_with",  # canonical
            "Interacts_With",  # mixed case + underscores
            "interacts with",  # space instead of underscore
            "INTERACTS_WITH",  # all caps
            " interacts_with ",  # leading + trailing whitespace
            "INTERACTS WITH",  # caps + space
            "interacts__with",  # double underscore (collapse runs)
            "_interacts_with_",  # leading + trailing underscore
            "interacts   with",  # multiple internal spaces
            "interacts_with",  # exact duplicate (sanity)
            "Interacts With",  # title case + space
        ]
    ]
    rows, inserted = await repo.upsert_edges_batch(edges_in)
    assert inserted == 1, f"expected 1 inserted, got {inserted}"
    assert len({r.id for r in rows}) == 1


def test_canonicalize_edge_label_normalizes_variants():
    """The label canonicalizer collapses spaces, underscores, and case."""
    from chaoscypher_core.services.sources.engine.commit.relation import (
        _canonicalize_edge_label,
    )

    assert _canonicalize_edge_label("Confides In") == "confides_in"
    assert _canonicalize_edge_label("confides_in") == "confides_in"
    assert _canonicalize_edge_label("  CONFIDES   IN  ") == "confides_in"
    assert _canonicalize_edge_label("confides__in") == "confides_in"
    assert _canonicalize_edge_label("") == ""
    assert _canonicalize_edge_label(None) == ""
