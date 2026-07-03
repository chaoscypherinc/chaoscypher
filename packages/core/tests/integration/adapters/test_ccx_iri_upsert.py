# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for get/upsert-by-ccx_iri (Task 4.1).

Exercises the idempotent upsert-by-IRI primitives the CCX 3.0 importer relies
on (Task 4.3): a create-with-``ccx_iri`` is found by ``get_*_by_ccx_iri``, and
a second ``upsert_*_by_ccx_iri`` with the SAME IRI updates the existing row
rather than inserting a duplicate. Run against the real file-backed SQLite
adapter (``integration_adapter``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chaoscypher_core.adapters.sqlite.models import GraphTemplate
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.models import EdgeCreate, NodeCreate


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


_DB = "default"
_NODE_IRI = "urn:ccx:chaoscypher:node/imported-1"
_EDGE_IRI = "urn:ccx:chaoscypher:rel/imported-1"
_SOURCE_IRI = "urn:ccx:chaoscypher:source/imported-1"


def _seed_templates(adapter: SqliteAdapter, database_name: str = _DB) -> None:
    """Insert the node/edge templates the graph rows FK-reference.

    ``graph_nodes.template_id`` / ``graph_edges.template_id`` are RESTRICT
    foreign keys onto ``graph_templates.id``; the real importer creates
    templates before nodes/edges, so the tests do the same.
    """
    assert adapter.session is not None
    session = adapter.session
    session.add(
        GraphTemplate(
            id="tpl_person",
            database_name=database_name,
            name="Person",
            template_type="node",
        )
    )
    session.add(
        GraphTemplate(
            id="tpl_rel",
            database_name=database_name,
            name="Knows",
            template_type="edge",
        )
    )
    session.flush()


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def test_upsert_node_creates_then_gets_by_ccx_iri(integration_adapter: SqliteAdapter) -> None:
    """First upsert creates a node carrying the ccx_iri; get-by-iri returns it."""
    assert integration_adapter.session is not None
    _seed_templates(integration_adapter)
    repo = GraphRepository(integration_adapter.session, _DB)

    created = repo.upsert_node_by_ccx_iri(
        _NODE_IRI,
        NodeCreate(template_id="tpl_person", label="Alice", entity_type="Person"),
        database_name=_DB,
    )
    assert created["ccx_iri"] == _NODE_IRI
    assert created["label"] == "Alice"

    fetched = repo.get_node_by_ccx_iri(_NODE_IRI, database_name=_DB)
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert fetched["ccx_iri"] == _NODE_IRI


def test_get_node_by_ccx_iri_missing_returns_none(integration_adapter: SqliteAdapter) -> None:
    """An unknown IRI yields None, not an error."""
    assert integration_adapter.session is not None
    repo = GraphRepository(integration_adapter.session, _DB)
    assert repo.get_node_by_ccx_iri("urn:ccx:chaoscypher:node/nope", database_name=_DB) is None


def test_upsert_node_twice_updates_no_duplicate(integration_adapter: SqliteAdapter) -> None:
    """Second upsert with the same IRI updates the row; exactly one row exists."""
    assert integration_adapter.session is not None
    _seed_templates(integration_adapter)
    repo = GraphRepository(integration_adapter.session, _DB)

    first = repo.upsert_node_by_ccx_iri(
        _NODE_IRI,
        NodeCreate(template_id="tpl_person", label="Alice", entity_type="Person"),
        database_name=_DB,
    )
    second = repo.upsert_node_by_ccx_iri(
        _NODE_IRI,
        NodeCreate(
            template_id="tpl_person",
            label="Alice Updated",
            entity_type="Human",
            properties={"age": 31},
        ),
        database_name=_DB,
    )

    # Same row reused (no duplicate), fields updated.
    assert second["id"] == first["id"]
    assert second["label"] == "Alice Updated"
    assert second["entity_type"] == "Human"
    assert second["properties"] == {"age": 31}
    assert repo.count_nodes() == 1


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


def test_upsert_edge_creates_then_updates_no_duplicate(
    integration_adapter: SqliteAdapter,
) -> None:
    """Edge upsert creates with ccx_iri, then updates the same row on re-upsert."""
    assert integration_adapter.session is not None
    _seed_templates(integration_adapter)
    repo = GraphRepository(integration_adapter.session, _DB)

    # Endpoints first (real node ids the edge references).
    src = repo.upsert_node_by_ccx_iri(
        "urn:ccx:chaoscypher:node/src",
        NodeCreate(template_id="tpl_person", label="Src"),
        database_name=_DB,
    )
    tgt = repo.upsert_node_by_ccx_iri(
        "urn:ccx:chaoscypher:node/tgt",
        NodeCreate(template_id="tpl_person", label="Tgt"),
        database_name=_DB,
    )

    created = repo.upsert_edge_by_ccx_iri(
        _EDGE_IRI,
        EdgeCreate(
            template_id="tpl_rel",
            source_node_id=src["id"],
            target_node_id=tgt["id"],
            label="knows",
        ),
        database_name=_DB,
    )
    assert created["ccx_iri"] == _EDGE_IRI
    assert created["label"] == "knows"

    fetched = repo.get_edge_by_ccx_iri(_EDGE_IRI, database_name=_DB)
    assert fetched is not None
    assert fetched["id"] == created["id"]

    updated = repo.upsert_edge_by_ccx_iri(
        _EDGE_IRI,
        EdgeCreate(
            template_id="tpl_rel",
            source_node_id=src["id"],
            target_node_id=tgt["id"],
            label="worksWith",
            properties={"since": 2020},
        ),
        database_name=_DB,
    )
    assert updated["id"] == created["id"]
    assert updated["label"] == "worksWith"
    assert updated["properties"] == {"since": 2020}
    assert repo.count_edges() == 1


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def test_upsert_source_creates_then_updates_no_duplicate(
    integration_adapter: SqliteAdapter,
) -> None:
    """Source upsert creates with ccx_iri, then updates the same row on re-upsert."""
    created = integration_adapter.upsert_source_by_ccx_iri(
        _SOURCE_IRI,
        {
            "id": "src_imported",
            "database_name": _DB,
            "filename": "doc.txt",
            "filepath": "/data/doc.txt",
            "title": "Doc One",
            "full_text": "Alice works with Bob.",
        },
        database_name=_DB,
    )
    assert created["ccx_iri"] == _SOURCE_IRI
    assert created["title"] == "Doc One"

    fetched = integration_adapter.get_source_by_ccx_iri(_SOURCE_IRI, database_name=_DB)
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert fetched["full_text"] == "Alice works with Bob."

    updated = integration_adapter.upsert_source_by_ccx_iri(
        _SOURCE_IRI,
        {
            "id": "src_imported",
            "database_name": _DB,
            "filename": "doc.txt",
            "filepath": "/data/doc.txt",
            "title": "Doc One (rev 2)",
            "full_text": "Alice works with Carol.",
        },
        database_name=_DB,
    )
    assert updated["id"] == created["id"]
    assert updated["title"] == "Doc One (rev 2)"
    assert updated["full_text"] == "Alice works with Carol."
    assert integration_adapter.count_sources(database_name=_DB) == 1


def test_get_source_by_ccx_iri_missing_returns_none(
    integration_adapter: SqliteAdapter,
) -> None:
    """An unknown source IRI yields None."""
    assert (
        integration_adapter.get_source_by_ccx_iri(
            "urn:ccx:chaoscypher:source/nope", database_name=_DB
        )
        is None
    )
