# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``create_node`` and ``update_node`` index what they write (spec § 4 gap: index on write).

A node an agent creates through the tools was found by ``get_node`` alone until 2026-10-20:
neither the keyword index nor the vector index saw it, so ``search_nodes`` could not. The
handlers now index the node the way the commit path does (``index_node``, and
``reembed_nodes`` when an embedding provider is wired), and a template may be named by its
name as well as its id.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.adapters.sqlite.repos.search import SearchRepository
from chaoscypher_core.models import Template, TemplateCreate
from chaoscypher_core.services.workflows.tools.engine.handlers.node_handlers import (
    NodeToolHandlers,
)
from chaoscypher_core.testing.embedding import FakeEmbeddingProvider


VECTOR_DIM = 384


@pytest.fixture
def repos(tmp_path: Path) -> Generator[tuple[GraphRepository, SearchRepository, Template]]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="test")
    adapter.connect()
    graph = GraphRepository(adapter.session, database_name="test")
    search = SearchRepository(engine, vector_dim=VECTOR_DIM)
    person = graph.create_template(TemplateCreate(name="Person", template_type="node"))
    yield graph, search, person
    adapter.disconnect()


@pytest.fixture
def handlers(repos) -> NodeToolHandlers:
    graph, search, _ = repos
    return NodeToolHandlers(graph, search)


@pytest.fixture
def handlers_with_embedder(repos) -> NodeToolHandlers:
    graph, search, _ = repos
    return NodeToolHandlers(
        graph, search, embedding_provider=FakeEmbeddingProvider(dimensions=VECTOR_DIM)
    )


async def test_a_created_node_is_found_by_keyword_at_once(handlers, repos) -> None:
    _, search, person = repos
    await handlers.create_node(template_id=person.id, label="Pierre Bezukhov", properties={})
    assert [h for h, _ in search.keyword_search("Bezukhov", item_type="node")]


async def test_a_created_node_gets_a_vector_when_a_provider_is_wired(
    handlers_with_embedder, repos
) -> None:
    graph, _, person = repos
    r = await handlers_with_embedder.create_node(template_id=person.id, label="Anna", properties={})
    assert r["success"], r
    assert graph.get_node(r["node_id"], include_embedding=True).embedding is not None


async def test_a_created_node_gets_no_vector_without_a_provider(handlers, repos) -> None:
    graph, _, person = repos
    r = await handlers.create_node(template_id=person.id, label="Anna", properties={})
    assert graph.get_node(r["node_id"], include_embedding=True).embedding is None


async def test_an_updated_label_replaces_the_keyword_row(handlers, repos) -> None:
    _, search, person = repos
    r = await handlers.create_node(template_id=person.id, label="Boris", properties={})
    await handlers.update_node(node_id=r["node_id"], label="Boris Drubetskoy")
    hits = search.keyword_search("Drubetskoy", item_type="node")
    assert [h for h, _ in hits] == [r["node_id"]]


async def test_a_template_name_resolves_and_an_unknown_one_is_refused(handlers, repos) -> None:
    _, _, person = repos
    ok = await handlers.create_node(template_id="Person", label="Lise", properties={})
    assert ok["success"] and ok["node"]["template_id"] == person.id
    bad = await handlers.create_node(template_id="Rocket", label="x", properties={})
    assert bad == {
        "success": False,
        "error": "Unknown template: Rocket",
        "code": "UNKNOWN_TEMPLATE",
    }


async def test_list_nodes_pages_a_template_by_name_and_lists_all_without_one(
    handlers, repos
) -> None:
    graph, _, person = repos
    place = graph.create_template(TemplateCreate(name="Place", template_type="node"))
    a = await handlers.create_node(template_id=person.id, label="Pierre", properties={})
    b = await handlers.create_node(template_id=person.id, label="Anna", properties={})
    await handlers.create_node(template_id=place.id, label="Lyon", properties={})

    first = await handlers.list_nodes(template_id="Person", limit=1)
    second = await handlers.list_nodes(template_id="Person", skip=1, limit=1)
    assert first["success"] and first["count"] == 1 and first["skip"] == 0 and first["limit"] == 1
    assert second["count"] == 1 and second["skip"] == 1
    assert {first["nodes"][0]["id"], second["nodes"][0]["id"]} == {a["node_id"], b["node_id"]}
    assert set(first["nodes"][0]) == {"id", "label", "template_id", "properties"}

    everything = await handlers.list_nodes()
    assert everything["count"] == 3

    bad = await handlers.list_nodes(template_id="Rocket")
    assert bad == {
        "success": False,
        "error": "Unknown template: Rocket",
        "code": "UNKNOWN_TEMPLATE",
    }
