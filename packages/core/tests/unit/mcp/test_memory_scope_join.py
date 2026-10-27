# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A scoped memory attaches to a node a person made, and never takes it with it.

A node made with ``create_node`` has no type and no home source, so the identity pass
never compares it with what a memory writes. A scoped memory offered such a node by its
scope (the scope's attested nodes, or ``mcp.memory.scope_nodes``) and writing its exact
name is attached to it: the memory attests the person's node, its edges move there, and
the node keeps its own text. Its own twin goes only when the memory alone made it.
Forgetting the memory takes its membership and its own edges, never the person's node.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from chaoscypher_core.mcp.memory import memory_handle
from chaoscypher_core.mcp.memory_link import link_repository
from chaoscypher_core.mcp.memory_seed import memory_rows, node_by_label
from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
    KNOWN_GRAPH_HEADER,
)
from tests.unit.mcp.memory_world import build_world, call, settings


if TYPE_CHECKING:
    from chaoscypher_core.models import Node


JOURNAL = "Trading journal"
FIRST = "Wrote the morning entry."
SECOND = "Wrote the evening entry."


def _offers_journal(prompt: str) -> bool:
    if KNOWN_GRAPH_HEADER not in prompt:
        return False
    return f"- {JOURNAL} (" in prompt.split(KNOWN_GRAPH_HEADER, 1)[1].split("\n\n", 1)[0]


def _writes_the_journal(prompt: str) -> tuple[str, str]:
    """A model that writes the journal by its name once the seed offers it."""
    if not _offers_journal(prompt):
        return ("", "")
    return (f"E|{JOURNAL}|Entity||0.9|S1|The day's journal\n", "")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    s = settings()
    s.mcp.memory.scope_nodes = {"trader": [JOURNAL]}
    script = {FIRST: _writes_the_journal, SECOND: _writes_the_journal}
    w = build_world(tmp_path, monkeypatch, script=script, s=s)
    yield w
    w.engine.close()


async def _person_graph(world: SimpleNamespace) -> tuple[str, str]:
    """A journal and a desk a person made, and their edge; the journal's and the edge's ids."""
    server = world.server
    await call(server, "create_template", {"name": "Journal", "template_type": "node"})
    await call(server, "create_template", {"name": "kept_at", "template_type": "edge"})
    for label, properties in ((JOURNAL, {"kept_by": "the desk"}), ("Trading desk", {})):
        await call(
            server,
            "create_node",
            {"template_id": "Journal", "label": label, "properties": properties},
        )
    journal, desk = _node(world, JOURNAL), _node(world, "Trading desk")
    kept_at = next(
        t.id for t in world.graph.list_templates(template_type="edge") if t.name == "kept_at"
    )
    await call(
        server,
        "create_edge",
        {
            "source_node_id": journal.id,
            "target_node_id": desk.id,
            "template_id": kept_at,
            "label": "kept_at",
            "properties": {},
        },
    )
    edges = world.graph.list_edges(source_node_id=journal.id)
    return journal.id, edges[0].id


def _nodes(world: SimpleNamespace, label: str) -> list[Node]:
    return [
        n for n in world.graph.list_nodes(limit=1000, include_embedding=False) if n.label == label
    ]


def _node(world: SimpleNamespace, label: str) -> Node:
    [node] = _nodes(world, label)
    return node


def _reading(world: SimpleNamespace, node_id: str) -> tuple[Any, ...]:
    node = world.graph.get_node(node_id, include_embedding=False)
    assert node is not None
    return (node.label, node.entity_type, node.source_id, node.template_id, node.properties)


async def _remember(world: SimpleNamespace, text: str, scope: str | None = "trader") -> str:
    await call(world.server, "remember", {"text": text} | ({"scope": scope} if scope else {}))
    return link_repository(world.engine).link_rows(world.engine.database_name)[-1].id


async def _forget(world: SimpleNamespace, source_id: str) -> None:
    handle = memory_handle(source_id, [r["id"] for r in memory_rows(world.engine)])
    await call(world.server, "forget", {"handle": handle, "confirm": True})


async def test_forgetting_a_joined_memory_keeps_the_person_made_node_and_its_edges(
    world: SimpleNamespace,
) -> None:
    journal, edge = await _person_graph(world)
    before = _reading(world, journal)

    memory = await _remember(world, FIRST)

    assert [n.id for n in _nodes(world, JOURNAL)] == [journal]  # attached, no twin
    assert journal in world.graph.member_node_ids(memory)
    assert _reading(world, journal) == before  # the person's text is the person's
    await _forget(world, memory)
    assert _reading(world, journal) == before
    assert world.graph.get_edge(edge) is not None
    assert world.graph.node_members(journal) == {}


async def test_two_memories_join_one_node_and_forgetting_one_changes_nothing(
    world: SimpleNamespace,
) -> None:
    journal, edge = await _person_graph(world)
    before = _reading(world, journal)
    first = await _remember(world, FIRST)
    second = await _remember(world, SECOND)
    assert set(world.graph.node_members(journal)) == {first, second}

    await _forget(world, first)

    assert _reading(world, journal) == before
    assert set(world.graph.node_members(journal)) == {second}
    assert world.graph.get_edge(edge) is not None


async def test_a_documents_typed_node_of_the_same_name_is_never_folded(
    world: SimpleNamespace,
) -> None:
    journal, _edge = await _person_graph(world)
    before = _reading(world, journal)
    book = "The Trading journal lists every trade of the year."
    world.llm.script[book] = (f"E|{JOURNAL}|Work||0.9|S1|A ledger\n", "")
    document = (await world.engine.process_document(book, filename="ledger.txt")).source_id
    assert len(_nodes(world, JOURNAL)) == 2  # the document's own, typed Work
    world.llm.script[FIRST] = lambda prompt: (
        (f"E|{JOURNAL}|Work||0.9|S1|The ledger\n", "") if _offers_journal(prompt) else ("", "")
    )

    await _remember(world, FIRST)

    survivors = {n.id for n in _nodes(world, JOURNAL)}
    assert journal in survivors and len(survivors) == 2
    documents_node = next(i for i in survivors if i != journal)
    assert document in world.graph.node_members(documents_node)
    assert document not in world.graph.node_members(journal)
    assert _reading(world, journal) == before


async def test_an_unscoped_memory_naming_notes_does_not_join_a_person_made_notes_node(
    world: SimpleNamespace,
) -> None:
    await call(world.server, "create_template", {"name": "Journal", "template_type": "node"})
    await call(
        world.server, "create_node", {"template_id": "Journal", "label": "Notes", "properties": {}}
    )
    person = _node(world, "Notes")
    text = "Notes on the call with the broker."
    world.llm.script[text] = ("E|Notes|Entity||0.9|S1|The notes\n", "")

    memory = await _remember(world, text, scope=None)

    notes = _nodes(world, "Notes")
    assert len(notes) == 2  # the memory keeps its own node, as before
    assert world.graph.node_members(person.id) == {}
    assert any(memory in world.graph.node_members(n.id) for n in notes if n.id != person.id)


async def test_a_label_names_the_person_made_node_before_a_typed_twin(
    world: SimpleNamespace,
) -> None:
    """A memory's typed twin never hides the person's node from ``scope_nodes`` or ``about``."""
    journal, _edge = await _person_graph(world)
    world.llm.script["The Trading journal is full."] = (
        f"E|{JOURNAL}|Work||0.9|S1|A ledger\n",
        "",
    )
    await world.engine.process_document("The Trading journal is full.", filename="full.txt")
    assert len(_nodes(world, JOURNAL)) == 2

    for _ in range(3):  # whatever order the index returns them in
        node = node_by_label(world.engine, JOURNAL)
        assert node is not None and node.id == journal
