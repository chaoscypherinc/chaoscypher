# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The commit binds a stamped entity to its offered template, and nothing else (D26).

A memory's extraction stamps an entity written with an offered type
(``offered_types.stamp_offered_types``); ``prepare_entity_nodes`` binds it to that template
while the template exists, and pops the stamp. Every other entity, a document's always,
goes through the per-source matcher as before (``commit/template.py``: no reuse across
sources), and ``drop_bound_suggestions`` keeps only the suggested templates an entity
that does not bind still needs.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.models import SourceStatus, TemplateCreate
from chaoscypher_core.services.sources.engine.commit.entity import EntityCommitHandler
from chaoscypher_core.services.sources.engine.commit.matcher import EntityTemplateMatcher
from chaoscypher_core.services.sources.engine.commit.template import (
    TemplateCommitHandler,
    drop_bound_suggestions,
    live_offered_templates,
)
from chaoscypher_core.services.sources.engine.extraction.utils.offered_types import (
    OFFERED_TEMPLATE_KEY,
)
from chaoscypher_core.services.sources.engine.extraction.utils.template_extractor import (
    TemplateExtractor,
)


SOURCE_ID = "src_offered"


class _NoEmbeddings:
    def get_entity_embeddings(self, source_id: str) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def graph(tmp_path: Path) -> Any:
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    assert adapter.session is not None
    adapter.session.add(
        SourceRow(
            id=SOURCE_ID,
            database_name="default",
            filename="notes.txt",
            filepath="/tmp/notes.txt",
            file_type="text",
            file_size=1,
            content_hash="hash",
            status=SourceStatus.PENDING.value,
        )
    )
    adapter.session.commit()
    repo = GraphRepository(session=adapter.session, database_name="default")
    repo.create_template(TemplateCreate(name="Market", template_type="node"), custom_id="t_market")
    yield repo
    adapter.disconnect()


def _market(*, stamped: bool) -> dict[str, Any]:
    entity: dict[str, Any] = {
        "id": "e1",
        "name": "ETH market",
        "type": "Organization",
        "type_rescued_from": "Market",
        "properties": {"definition": "A market"},
    }
    if stamped:
        entity["properties"][OFFERED_TEMPLATE_KEY] = "Market"
    return entity


async def _prepare(
    graph: Any, entities: list[dict[str, Any]], offered: dict[str, str]
) -> tuple[list[Any], dict[str, str]]:
    suggestions = TemplateExtractor.generate_suggestions_from_entities(entities)
    offered = live_offered_templates(graph, offered)  # as the commit reads them
    commit_data = {
        "create_templates": True,
        "suggested_templates": drop_bound_suggestions(
            {"entities": entities, "suggested_templates": suggestions}, offered
        ),
    }
    _ids, name_to_id, _all, _n = await TemplateCommitHandler(
        graph_repository=graph
    ).create_suggested_templates(commit_data, source_id=SOURCE_ID)
    handler = EntityCommitHandler(
        graph_repository=graph,
        source_repository=_NoEmbeddings(),
        entity_matcher=EntityTemplateMatcher(graph_repository=graph),
        database_name="default",
    )
    nodes, _data, _tids = handler.prepare_entity_nodes(
        entities,
        all_templates=graph.list_templates(template_type="node"),
        suggested_templates=commit_data["suggested_templates"],
        template_name_to_id=name_to_id,
        file_info={},
        file_id=SOURCE_ID,
        source_id=SOURCE_ID,
        offered_templates=offered,
    )
    return nodes, name_to_id


async def test_a_stamped_entity_binds_to_its_offered_template(graph: Any) -> None:
    entities = [_market(stamped=True)]
    before = copy.deepcopy(entities)

    nodes, name_to_id = await _prepare(graph, entities, {"market": "t_market"})

    assert nodes[0].template_id == "t_market"
    assert OFFERED_TEMPLATE_KEY not in nodes[0].properties
    assert name_to_id == {}  # its one suggestion was bound: no template of its own
    assert entities == before  # the commit data is left as it was: a retried commit binds again


async def test_a_document_entity_never_binds_by_name(graph: Any) -> None:
    """The queued-import path: a document typing "Market" with a "Market" template present."""
    nodes, name_to_id = await _prepare(graph, [_market(stamped=False)], {})

    assert nodes[0].template_id == name_to_id["organization"] != "t_market"
    template = graph.get_template(nodes[0].template_id)
    assert template is not None and template.source_id == SOURCE_ID


async def test_a_stamp_whose_template_is_gone_falls_back_to_its_own(graph: Any) -> None:
    entities = [_market(stamped=True)]

    nodes, name_to_id = await _prepare(graph, entities, {"market": "t_gone"})

    assert nodes[0].template_id == name_to_id["organization"]
    assert OFFERED_TEMPLATE_KEY not in nodes[0].properties


async def test_a_suggestion_an_unbound_entity_needs_is_kept(graph: Any) -> None:
    entities = [
        _market(stamped=True),
        {"id": "e2", "name": "Kraken", "type": "Organization", "properties": {}},
        {"id": "e3", "name": "Pierre", "type": "Person", "properties": {}},
    ]

    nodes, name_to_id = await _prepare(graph, entities, {"market": "t_market"})

    assert sorted(name_to_id) == ["organization", "person"]
    by_label = {n.label: n.template_id for n in nodes}
    assert by_label["ETH market"] == "t_market"
    assert by_label["Kraken"] == name_to_id["organization"]


def test_with_nothing_offered_the_suggestions_stand() -> None:
    suggestions = [{"name": "Person"}, {"name": "Location"}]
    assert drop_bound_suggestions({"suggested_templates": suggestions}, {}) == suggestions
