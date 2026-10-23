# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The commit creates one template per core type; a node keeps its pack subtype (spec 2026-10-07 § 6).

A Person/character and a Person/author share the source's Person template;
``graph_nodes.entity_type`` is the core type and ``properties.entity_subtype``
the subtype (a merge's ``entity_subtypes`` too). The template's icon and
colour are the floor's; a pack subtype may offer its own icon to the UI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel, select

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import GraphTemplate, SourceRow
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.services.sources.engine.commit.entity import EntityCommitHandler
from chaoscypher_core.services.sources.engine.commit.matcher import EntityTemplateMatcher
from chaoscypher_core.services.sources.engine.commit.template import TemplateCommitHandler
from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
from chaoscypher_core.services.sources.engine.extraction.utils.template_extractor import (
    TemplateExtractor,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import committed_type


SOURCE_ID = "src_core_types"

ENTITIES: list[dict[str, Any]] = [
    {"id": "e1", "name": "Pierre", "type": "Person", "properties": {"entity_subtype": "character"}},
    {"id": "e2", "name": "Tolstoy", "type": "Person", "properties": {"entity_subtype": "author"}},
    {"id": "e3", "name": "Moscow", "type": "Location"},
]


def _floor_templates() -> list[dict[str, Any]]:
    return DomainStack.resolve(get_domain_registry(), []).get_templates()["node_templates"]


class _NoEmbeddings:
    def get_entity_embeddings(self, source_id: str) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def repo(tmp_path: Path):
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    assert adapter.session is not None
    adapter.session.add(
        SourceRow(
            id=SOURCE_ID,
            database_name="default",
            filename="book.txt",
            filepath="/tmp/book.txt",
            file_type="text",
            file_size=1,
            content_hash="hash",
            status=SourceStatus.PENDING.value,
        )
    )
    adapter.session.commit()
    yield GraphRepository(session=adapter.session, database_name="default"), adapter.session
    adapter.disconnect()


def test_one_suggestion_per_core_type_with_the_floors_visuals() -> None:
    suggestions = TemplateExtractor.generate_suggestions_from_entities(
        ENTITIES, domain_templates=_floor_templates()
    )
    assert [s["name"] for s in suggestions] == ["Person", "Location"]
    person = suggestions[0]
    assert person["entity_count"] == 2
    assert person["subtypes"] == {"author": 1, "character": 1}
    floor_person = next(t for t in _floor_templates() if t["name"] == "Person")
    assert (person["icon"], person["color"]) == (floor_person["icon"], floor_person["color"])
    assert suggestions[1]["subtypes"] == {}


def test_an_old_type_name_counts_as_its_core_type_and_no_core_type_gets_no_template() -> None:
    suggestions = TemplateExtractor.generate_suggestions_from_entities(
        [
            {"name": "Natásha", "type": "Character"},
            {"name": "Mavra", "type": "person"},
            {"name": "the glow", "type": "Xyzzy"},
            {"name": "a box", "type": "Object"},
        ],
        domain_templates=_floor_templates(),
    )
    assert [(s["name"], s["entity_count"]) for s in suggestions] == [("Person", 2), ("Object", 1)]
    assert suggestions[0]["subtypes"] == {"character": 1}
    assert committed_type({"type": "Character"}) == ("Person", "character")
    assert committed_type({"type": "Xyzzy"}) is None


def test_a_pack_subtype_offers_its_own_icon() -> None:
    stack = DomainStack.resolve(get_domain_registry(), ["literary"])
    visuals = stack.subtype_visuals()
    assert visuals["character"]["of"] == "Person"
    suggestions = TemplateExtractor.generate_suggestions_from_entities(
        ENTITIES,
        domain_templates=stack.get_templates()["node_templates"],
        subtype_visuals=visuals,
    )
    icons = suggestions[0].get("subtype_icons", {})
    for key in ("character", "author"):
        if visuals.get(key, {}).get("icon"):
            assert icons[key] == visuals[key]["icon"]


@pytest.mark.asyncio
async def test_a_commit_creates_two_templates_and_nodes_keep_their_subtype(repo) -> None:
    graph_repo, session = repo
    suggestions = TemplateExtractor.generate_suggestions_from_entities(
        ENTITIES, domain_templates=_floor_templates()
    )
    _ids, name_to_id, _all, inserted = await TemplateCommitHandler(
        graph_repository=graph_repo
    ).create_suggested_templates(
        {"create_templates": True, "suggested_templates": suggestions}, source_id=SOURCE_ID
    )

    rows = session.exec(
        select(GraphTemplate).where(
            GraphTemplate.source_id == SOURCE_ID, GraphTemplate.template_type == "node"
        )
    ).all()
    assert sorted(r.name for r in rows) == ["Location", "Person"]
    assert inserted == 2

    handler = EntityCommitHandler(
        graph_repository=graph_repo,
        source_repository=_NoEmbeddings(),
        entity_matcher=EntityTemplateMatcher(graph_repository=graph_repo),
        database_name="default",
    )
    nodes, _data, template_ids = handler.prepare_entity_nodes(
        [*ENTITIES, {"id": "e4", "name": "Natásha", "type": "Character"}],
        all_templates=[],
        suggested_templates=suggestions,
        template_name_to_id=name_to_id,
        file_info={},
        file_id=SOURCE_ID,
        source_id=SOURCE_ID,
    )
    by_name = {n.label: n for n in nodes}
    person_template = name_to_id["person"]
    assert {by_name[n].template_id for n in ("Pierre", "Tolstoy", "Natásha")} == {person_template}
    assert by_name["Moscow"].template_id == name_to_id["location"]
    assert sorted(template_ids) == sorted({person_template, name_to_id["location"]})
    assert [by_name[n].entity_type for n in ("Pierre", "Tolstoy", "Natásha", "Moscow")] == [
        "Person",
        "Person",
        "Person",
        "Location",
    ]
    assert by_name["Pierre"].properties["entity_subtype"] == "character"
    assert by_name["Tolstoy"].properties["entity_subtype"] == "author"
    # an old per-type name is stored as its core type with the table's subtype
    assert by_name["Natásha"].properties["entity_subtype"] == "character"
    assert "entity_subtype" not in by_name["Moscow"].properties


def test_a_merges_subtypes_stay_a_list() -> None:
    handler = EntityCommitHandler(
        graph_repository=None,  # type: ignore[arg-type]
        source_repository=_NoEmbeddings(),
        entity_matcher=EntityTemplateMatcher(graph_repository=None),  # type: ignore[arg-type]
        database_name="default",
    )
    merged = {
        "id": "e9",
        "name": "Pierre",
        "type": "Person",
        "properties": {"entity_subtype": "character", "entity_subtypes": ["character", "author"]},
    }
    nodes, _data, _ids = handler.prepare_entity_nodes(
        [merged],
        all_templates=[],
        suggested_templates=None,
        template_name_to_id={"person": "tpl_person"},
        file_info={},
        file_id=SOURCE_ID,
        source_id=SOURCE_ID,
    )
    assert nodes[0].template_id == "tpl_person"
    assert nodes[0].properties["entity_subtypes"] == ["character", "author"]


@pytest.mark.asyncio
async def test_old_per_type_suggestions_create_their_core_types_once(repo) -> None:
    """A payload cached before the restructure (Character, Author, Location) commits as Person, Location."""
    graph_repo, session = repo
    _ids, name_to_id, _all, inserted = await TemplateCommitHandler(
        graph_repository=graph_repo
    ).create_suggested_templates(
        {
            "create_templates": True,
            "suggested_templates": [
                {"name": "Character"},
                {"name": "Author"},
                {"name": "Location"},
            ],
        },
        source_id=SOURCE_ID,
    )
    assert sorted(name_to_id) == ["location", "person"]
    assert inserted == 2


def test_a_type_no_core_type_answers_is_committed_as_an_unplaced_concept() -> None:
    """The commit places a node's type as the type rescue does (``core_typed``): keep, flag, never delete."""
    handler = EntityCommitHandler(
        graph_repository=None,  # type: ignore[arg-type]
        source_repository=_NoEmbeddings(),
        entity_matcher=EntityTemplateMatcher(graph_repository=None),  # type: ignore[arg-type]
        database_name="default",
    )
    nodes, _data, _ids = handler.prepare_entity_nodes(
        [{"id": "e10", "name": "the glow", "type": "Xyzzy"}],
        all_templates=[],
        suggested_templates=None,
        template_name_to_id={"concept": "tpl_concept"},
        file_info={},
        file_id=SOURCE_ID,
        source_id=SOURCE_ID,
    )
    assert nodes[0].entity_type == "Concept"
    assert nodes[0].template_id == "tpl_concept"
    assert nodes[0].properties["unplaced_type"] is True
    assert nodes[0].properties["written_type"] == "Xyzzy"
