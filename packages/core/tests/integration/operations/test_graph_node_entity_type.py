# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for graph_nodes.entity_type column wiring.

End-to-end coverage for the new column added by migration 0035:
- create_node persists entity_type from NodeCreate
- create_nodes_batch persists entity_type
- upsert_nodes_batch persists entity_type on insert
- list_nodes returns entity_type in both minimal and full projections
- Nullable: NodeCreate without entity_type writes NULL
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.models import NodeCreate


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _make_template(repo: GraphRepository, *, template_id: str = "tpl-1") -> None:
    """Insert the parent template the GraphNode FK requires."""
    from datetime import UTC, datetime

    from chaoscypher_core.adapters.sqlite.models import GraphTemplate

    repo.session.add(
        GraphTemplate(
            id=template_id,
            database_name="default",
            name="Person",
            template_type="node",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    repo.session.maybe_commit()


@pytest.fixture
def graph_repo(integration_adapter: SqliteAdapter) -> GraphRepository:
    """A GraphRepository bound to the integration_adapter's session."""
    return GraphRepository(integration_adapter.session, database_name="default")


def test_create_node_persists_entity_type(graph_repo: GraphRepository) -> None:
    _make_template(graph_repo)
    repo = graph_repo

    created = repo.create_node(
        NodeCreate(
            template_id="tpl-1",
            label="Alice",
            entity_type="Person",
        )
    )

    assert created.entity_type == "Person"

    fetched = repo.get_node(created.id)
    assert fetched is not None
    assert fetched.entity_type == "Person"


def test_create_node_accepts_null_entity_type(graph_repo: GraphRepository) -> None:
    """NodeCreate without entity_type writes NULL — must not error."""
    _make_template(graph_repo)

    created = graph_repo.create_node(
        NodeCreate(template_id="tpl-1", label="Untyped"),
    )

    assert created.entity_type is None
    fetched = graph_repo.get_node(created.id)
    assert fetched is not None
    assert fetched.entity_type is None


def test_list_nodes_returns_entity_type_in_minimal_projection(
    graph_repo: GraphRepository,
) -> None:
    """The list_nodes minimal projection must include entity_type so Schema Insights aggregates work."""
    _make_template(graph_repo)

    graph_repo.create_node(NodeCreate(template_id="tpl-1", label="Alice", entity_type="Person"))
    graph_repo.create_node(
        NodeCreate(template_id="tpl-1", label="Acme", entity_type="Organization")
    )
    graph_repo.create_node(NodeCreate(template_id="tpl-1", label="Untyped"))

    minimal_nodes = graph_repo.list_nodes(minimal=True)
    types_by_label = {n.label: n.entity_type for n in minimal_nodes}
    assert types_by_label["Alice"] == "Person"
    assert types_by_label["Acme"] == "Organization"
    assert types_by_label["Untyped"] is None

    full_nodes = graph_repo.list_nodes(minimal=False)
    types_by_label = {n.label: n.entity_type for n in full_nodes}
    assert types_by_label["Alice"] == "Person"
    assert types_by_label["Acme"] == "Organization"
    assert types_by_label["Untyped"] is None


@pytest.mark.asyncio
async def test_create_nodes_batch_persists_entity_type(graph_repo: GraphRepository) -> None:
    _make_template(graph_repo)

    created = await graph_repo.create_nodes_batch(
        [
            NodeCreate(template_id="tpl-1", label="Alice", entity_type="Person"),
            NodeCreate(template_id="tpl-1", label="Acme", entity_type="Organization"),
        ]
    )

    assert {n.label: n.entity_type for n in created} == {
        "Alice": "Person",
        "Acme": "Organization",
    }


@pytest.mark.asyncio
async def test_upsert_nodes_batch_persists_entity_type(graph_repo: GraphRepository) -> None:
    _make_template(graph_repo)

    nodes, inserted = await graph_repo.upsert_nodes_batch(
        [
            NodeCreate(template_id="tpl-1", label="Alice", entity_type="Person", source_id=None),
            NodeCreate(
                template_id="tpl-1", label="Acme", entity_type="Organization", source_id=None
            ),
        ]
    )

    assert inserted == 2
    assert {n.label: n.entity_type for n in nodes} == {
        "Alice": "Person",
        "Acme": "Organization",
    }
