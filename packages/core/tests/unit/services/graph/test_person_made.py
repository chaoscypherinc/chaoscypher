# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Every path a person creates a node or an edge by marks it ``person_made``; no other does.

A person-made row (migration 0025) is the person's: a source that attests it is released
without deleting or rewriting it. The people's paths are the MCP ``create_node`` and
``create_edge`` tools, the graph services the API, the SDK and the CLI call
(``NodeService.create_node``, ``EdgeService.create_edge``) and the chat research tool;
the extraction commit, the package importer and the resolver's edge moves are a
source's. The guard below holds every direct graph-repository create to that list.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from chaoscypher_core.models import EdgeCreate, NodeCreate
from tests.unit.mcp.memory_world import build_world, call


SRC = Path(__file__).resolve().parents[4] / "src" / "chaoscypher_core"

PERSON_PATHS = {
    "services/workflows/tools/engine/handlers/node_handlers.py",
    "services/workflows/tools/engine/handlers/edge_handlers.py",
    "services/chat/engine/research.py",
    "services/graph/management/node.py",
    "services/graph/management/edge.py",
}
"""The modules that create a row for a person: each create names ``person_made=``."""
SOURCE_PATHS = {
    "services/graph/resolution/apply.py",
    "services/package/importer/service.py",
    "services/sources/engine/commit/entity.py",
    "services/sources/engine/commit/relation.py",
}
"""The modules that write rows for a source: none names ``person_made``."""
_CREATES = re.compile(
    r"^(create_(node|edge)s?(_batch)?|upsert_(node|edge)s?(_batch|_by_ccx_iri)?)$"
)
"""Every graph-repository method that writes a node or an edge row."""


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch, script={})
    yield w
    w.engine.close()


async def test_the_mcp_tools_mark_what_they_create(world: SimpleNamespace) -> None:
    await call(world.server, "create_template", {"name": "Journal", "template_type": "node"})
    await call(world.server, "create_template", {"name": "kept_at", "template_type": "edge"})
    for label in ("Trading journal", "Trading desk"):
        await call(
            world.server,
            "create_node",
            {"template_id": "Journal", "label": label, "properties": {}},
        )
    nodes = {n.label: n for n in world.graph.list_nodes(limit=100, include_embedding=False)}
    edge_template = next(
        t.id for t in world.graph.list_templates(template_type="edge") if t.name == "kept_at"
    )
    await call(
        world.server,
        "create_edge",
        {
            "source_node_id": nodes["Trading journal"].id,
            "target_node_id": nodes["Trading desk"].id,
            "template_id": edge_template,
            "label": "kept_at",
            "properties": {},
        },
    )

    assert all(n.person_made for n in nodes.values())
    assert [e.person_made for e in world.graph.list_edges()] == [True]


async def test_the_sdk_marks_what_a_person_creates_and_not_what_a_source_does(
    world: SimpleNamespace,
) -> None:
    engine = world.engine
    alice = engine.add_node("Person", "Alice")
    bob = engine.create_node(NodeCreate(template_id=alice.template_id, label="Bob"))
    knows = engine.add_edge("knows", alice, bob)
    document = (await engine.process_document("A note.", filename="note.txt")).source_id
    carol = engine.add_node("Person", "Carol", source_id=document)
    # the SDK's returned models read as the rows do
    assert (alice.person_made, bob.person_made, knows.person_made, carol.person_made) == (
        True,
        True,
        True,
        False,
    )

    graph = world.graph
    assert [graph.get_node(i, include_embedding=False).person_made for i in (alice.id, bob.id)] == [
        True,
        True,
    ]
    edge = graph.get_edge(knows.id)
    assert edge is not None and edge.person_made
    node = graph.get_node(carol.id, include_embedding=False)
    assert node is not None and not node.person_made  # attributed to a source


async def test_a_commit_marks_nothing(world: SimpleNamespace) -> None:
    text = "Pierre Bezukhov moved to Lyon."
    world.llm.script[text] = (
        "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\nE|Lyon|Location||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|Pierre Bezukhov moved to Lyon\n",
    )
    await world.engine.process_document(text, filename="book.txt")

    nodes = world.graph.list_nodes(limit=100, include_embedding=False)
    assert nodes and not any(n.person_made for n in nodes)
    assert not any(e.person_made for e in world.graph.list_edges())


def _creates(source: str) -> list[ast.Call]:
    """The calls in ``source`` that write a graph row on anything but a service.

    A service (``node_service``, ``self.engine_service``, ...) marks what it writes
    itself; any other owner (``self.graph``, ``self._graph``, ``repo``) is a repository.
    """
    calls = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = node.func.value
        name = owner.attr if isinstance(owner, ast.Attribute) else getattr(owner, "id", "")
        if _CREATES.match(node.func.attr) and not name.endswith("service"):
            calls.append(node)
    return calls


def _names_person_made(call: ast.Call) -> bool:
    return any(k.arg == "person_made" for k in call.keywords)


def test_the_guard_finds_a_create_on_any_repository_owner() -> None:
    """Self-test: a private owner, a batch and an upsert are all writes; a service's is not."""
    source = (
        "class Writer:\n"
        "    def f(self):\n"
        "        self._graph.create_node(NodeCreate(template_id='t', label='x'))\n"
        "        self._graph.upsert_edges_batch([])\n"
        "        self.node_service.create_node(NodeCreate(template_id='t', label='x'))\n"
        "        self._graph.upsert_templates_batch([])\n"
    )
    found = [c.func.attr for c in _creates(source)]  # type: ignore[attr-defined]
    assert found == ["create_node", "upsert_edges_batch"]


def test_every_graph_write_is_a_reviewed_path_and_each_person_path_marks_its_row() -> None:
    by_path = {
        str(path.relative_to(SRC)): calls
        for path in SRC.rglob("*.py")
        if (calls := _creates(path.read_text(encoding="utf-8")))
    }
    assert set(by_path) == PERSON_PATHS | SOURCE_PATHS
    for relative in PERSON_PATHS:
        assert all(_names_person_made(c) for c in by_path[relative]), relative
    for relative in SOURCE_PATHS:
        assert not any(_names_person_made(c) for c in by_path[relative]), relative


def test_person_made_is_no_request_input() -> None:
    """A create model never carries the mark: an API body cannot claim a row is a person's."""
    with pytest.raises(ValidationError):
        NodeCreate(template_id="t", label="x", person_made=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        EdgeCreate(  # type: ignore[call-arg]
            template_id="t", source_node_id="a", target_node_id="b", label="r", person_made=True
        )
