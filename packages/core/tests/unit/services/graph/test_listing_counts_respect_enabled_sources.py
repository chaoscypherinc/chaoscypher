# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Listing pagination totals must exclude disabled-source rows, matching rows shown.

The entities / relationships / templates pages render rows filtered to enabled
sources (``list_*`` defaults ``include_disabled_sources=False``) but historically
derived their pagination total from ``count_*`` queries that ignored source
enabled-state. Disabling a source then left an inflated total and phantom
trailing pages -- and toggling a source never moved the displayed count.

These tests pin the invariant: a single-page listing's ``pagination.total``
equals the number of rows it actually returns once a source is disabled.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.models import EdgeCreate, NodeCreate, TemplateCreate
from chaoscypher_core.services.graph.management.edge import EdgeService
from chaoscypher_core.services.graph.management.node import NodeService
from chaoscypher_core.services.graph.management.template import TemplateService
from chaoscypher_core.settings import EngineSettings


DB_NAME = "default"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Isolated file-backed SqliteAdapter with all tables created."""
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    a = SqliteAdapter(str(db_path), database_name=DB_NAME)
    a.connect()
    yield a
    a.disconnect()


@pytest.fixture
def repo(adapter: SqliteAdapter) -> GraphRepository:
    """GraphRepository sharing the adapter's session."""
    return GraphRepository(session=adapter.session, database_name=DB_NAME)


def _make_source(adapter: SqliteAdapter, source_id: str, *, enabled: bool) -> None:
    """Seed a source row in the given enabled state."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": DB_NAME,
            "filename": f"{source_id}.txt",
            "filepath": f"/tmp/{source_id}.txt",
            "file_type": "text",
            "file_size": 1,
            "content_hash": source_id,
            "status": "indexed",
            "enabled": enabled,
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_node_listing_total_excludes_disabled_source_rows(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Entities page: pagination total counts only enabled-source nodes."""
    _make_source(adapter, "src-on", enabled=True)
    _make_source(adapter, "src-off", enabled=False)

    tpl = repo.create_template(
        TemplateCreate(name="Person", template_type="node", properties=[]),
        is_system=True,
    )
    for i in range(3):
        repo.create_node(NodeCreate(template_id=tpl.id, label=f"on-{i}", source_id="src-on"))
    for i in range(2):
        repo.create_node(NodeCreate(template_id=tpl.id, label=f"off-{i}", source_id="src-off"))

    service = NodeService.from_adapter(adapter, EngineSettings(current_database=DB_NAME))
    result = service.list_nodes(page=1, page_size=50)

    assert len(result["data"]) == 3, "only enabled-source nodes are rendered"
    assert result["pagination"]["total"] == 3, "total must exclude disabled-source nodes"
    assert result["pagination"]["total"] == len(result["data"])


def test_edge_listing_total_excludes_disabled_source_rows(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Relationships page: pagination total counts only enabled-source edges."""
    _make_source(adapter, "src-on", enabled=True)
    _make_source(adapter, "src-off", enabled=False)

    node_tpl = repo.create_template(
        TemplateCreate(name="Person", template_type="node", properties=[]),
        is_system=True,
    )
    edge_tpl = repo.create_template(
        TemplateCreate(name="knows", template_type="edge", properties=[]),
        is_system=True,
    )
    nodes = [
        repo.create_node(NodeCreate(template_id=node_tpl.id, label=f"n{i}", source_id="src-on"))
        for i in range(4)
    ]
    # Distinct endpoint pairs so edge-dedup (same endpoints + template) does not
    # collapse these into a single row.
    repo.create_edge(
        EdgeCreate(
            template_id=edge_tpl.id,
            source_node_id=nodes[0].id,
            target_node_id=nodes[1].id,
            label="e1",
            source_id="src-on",
        )
    )
    repo.create_edge(
        EdgeCreate(
            template_id=edge_tpl.id,
            source_node_id=nodes[1].id,
            target_node_id=nodes[2].id,
            label="e2",
            source_id="src-on",
        )
    )
    repo.create_edge(
        EdgeCreate(
            template_id=edge_tpl.id,
            source_node_id=nodes[2].id,
            target_node_id=nodes[3].id,
            label="e3",
            source_id="src-off",
        )
    )

    service = EdgeService(repo)
    result = service.list_edges(page=1, page_size=50)

    assert len(result["data"]) == 2, "only enabled-source edges are rendered"
    assert result["pagination"]["total"] == 2, "total must exclude disabled-source edges"
    assert result["pagination"]["total"] == len(result["data"])


def test_template_listing_total_excludes_disabled_source_rows(
    adapter: SqliteAdapter, repo: GraphRepository
) -> None:
    """Templates page: total counts enabled-source + NULL-source templates only.

    Templates with a NULL source_id (system / manually created) are always
    visible; a template tied to a disabled source must be excluded from both
    the rendered rows and the total.
    """
    _make_source(adapter, "src-on", enabled=True)
    _make_source(adapter, "src-off", enabled=False)

    repo.create_template(
        TemplateCreate(name="A", template_type="node", properties=[], source_id="src-on")
    )
    repo.create_template(
        TemplateCreate(name="B", template_type="node", properties=[], source_id="src-off")
    )
    repo.create_template(
        TemplateCreate(name="Sys", template_type="node", properties=[]),
        is_system=True,
    )

    service = TemplateService(repo)
    result = service.list_templates(page=1, page_size=50)

    assert len(result["data"]) == 2, "enabled-source + NULL-source templates are rendered"
    assert result["pagination"]["total"] == 2, "total must exclude disabled-source templates"
    assert result["pagination"]["total"] == len(result["data"])
