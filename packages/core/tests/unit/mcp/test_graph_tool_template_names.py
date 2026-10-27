# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A template named by the graph tools is the database's own before a shipped one of that name.

Every open now gives a database the shipped templates (``Person``, ``Note``, ``related_to``,
...). A client that built its database before made templates with some of those names, and
its nodes and edges live under them. A tool naming the template by name must keep finding
the client's, never the shipped one that sorts first by id.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chaoscypher_core.models import NodeCreate
from tests.unit.mcp.memory_world import build_world, call, call_raw


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    w = build_world(tmp_path, monkeypatch)
    yield w
    w.engine.close()


async def _client_template(world: SimpleNamespace, name: str, template_type: str) -> str:
    created = json.loads(
        await call(world.server, "create_template", {"name": name, "template_type": template_type})
    )
    return str(created["template_id"])


async def test_a_node_named_by_a_shipped_name_lands_on_the_clients_template(
    world: SimpleNamespace,
) -> None:
    person = await _client_template(world, "Person", "node")
    world.graph.create_node(NodeCreate(template_id=person, label="Ada"))  # an older client's node
    created = json.loads(
        await call(
            world.server,
            "create_node",
            {"template_id": "Person", "label": "Bea", "properties": {}},
        )
    )
    assert created["node"]["template_id"] == person
    listed = json.loads(await call(world.server, "list_nodes", {"template_id": "Person"}))
    assert {n["label"] for n in listed["nodes"]} == {"Ada", "Bea"}


async def test_an_edge_named_by_a_shipped_name_lands_on_the_clients_template(
    world: SimpleNamespace,
) -> None:
    note = await _client_template(world, "Note", "node")
    related = await _client_template(world, "related_to", "edge")
    a = world.graph.create_node(NodeCreate(template_id=note, label="a"))
    b = world.graph.create_node(NodeCreate(template_id=note, label="b"))
    created = json.loads(
        await call(
            world.server,
            "create_edge",
            {"source_node_id": a.id, "target_node_id": b.id, "template_id": "related_to"},
        )
    )
    edge = world.graph.get_edge(created["edge_id"])
    assert edge is not None and edge.template_id == related


async def test_two_own_templates_of_one_name_answer_ambiguous_with_their_ids(
    world: SimpleNamespace,
) -> None:
    first = await _client_template(world, "Note", "node")
    second = await _client_template(world, "Note", "node")
    text, is_error = await call_raw(
        world.server, "create_node", {"template_id": "Note", "label": "a", "properties": {}}
    )
    assert is_error
    answer = json.loads(text)
    assert answer["code"] == "AMBIGUOUS_TEMPLATE"
    assert first in answer["error"] and second in answer["error"]
    assert sorted(answer["template_ids"]) == sorted([first, second])


async def test_a_shipped_name_alone_still_finds_the_shipped_template(
    world: SimpleNamespace,
) -> None:
    created = json.loads(
        await call(
            world.server,
            "create_node",
            {"template_id": "Person", "label": "Bea", "properties": {}},
        )
    )
    assert created["node"]["template_id"] == "system_template_person"
