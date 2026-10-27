# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A merge settles the single-valued facts it brings together on the kept node.

Two nodes of one person, each with an open ``located_in``, merged into one: the earlier
fact must close at the later one's date, whatever brought the merge on (the identity pass
after a forget's release, or after a memory's commit). The second node is made by
remembering with the identity pass off (each memory then makes its own node), the way two
processes' first mentions arrive before either pass runs.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from tests.unit.mcp.memory_world import WORLD_SCRIPT, build_world, call, memory_sources


if TYPE_CHECKING:
    from chaoscypher_core.models import Edge


_PIERRE = "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\n"


def _moved(city: str) -> tuple[str, str]:
    return (
        _PIERRE + f"E|{city}|Location||0.9|S1|A city\n",
        f"R|0|1|located_in|0.9|S1|Pierre Bezukhov moved to {city}\n",
    )


SCRIPT = {
    **WORLD_SCRIPT,
    "Pierre Bezukhov moved to Kyiv.": _moved("Kyiv"),
    "Pierre Bezukhov moved to Paris.": _moved("Paris"),
}


def _moved_to(city: str, valid_from: str) -> dict[str, Any]:
    return {"text": f"Pierre Bezukhov moved to {city}.", "by": "alice", "valid_from": valid_from}


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch, script=SCRIPT)
    try:
        yield w
    finally:
        await w.linker.close()
        w.engine.close()


def _facts(world: SimpleNamespace) -> dict[str, Edge]:
    """The one Pierre node's located_in facts, by city (one node: the merge happened)."""
    graph = world.graph
    [pierre] = [
        n
        for n in graph.list_nodes(limit=1000, include_embedding=False)
        if n.label == "Pierre Bezukhov"
    ]
    facts = {}
    for edge in graph.edges_from(pierre.id, "located_in"):
        target = graph.get_node(edge.target_node_id, include_embedding=False)
        assert target is not None
        facts[target.label] = edge
    return facts


def _memory_id(world: SimpleNamespace, valid_from: str) -> str:
    return next(
        s["id"]
        for s in memory_sources(world.storage)
        if str(s["valid_from"]).startswith(valid_from)
    )


async def _a_second_pierre_in_paris(world: SimpleNamespace) -> None:
    """Paris (2026-10-22) remembered with the identity pass off: a Pierre node of its own."""
    world.engine.settings.graph.resolve_identities = False
    await call(world.server, "remember", _moved_to("Paris", "2026-10-22"))
    world.engine.settings.graph.resolve_identities = True


async def test_the_merge_after_a_forget_closes_the_fact_the_forget_reopened(
    world: SimpleNamespace,
) -> None:
    """Lyon closed by Kyiv; Kyiv forgotten reopens Lyon; the merge brings Paris: Lyon closes."""
    await call(world.server, "remember", _moved_to("Lyon", "2026-10-20"))
    await call(world.server, "remember", _moved_to("Kyiv", "2026-10-21"))
    await _a_second_pierre_in_paris(world)
    preview = await call(world.server, "forget", {"query": "Kyiv"})
    found = re.search(r'forget\(handle="([^"]+)"', preview)
    assert found, preview
    await call(world.server, "forget", {"handle": found.group(1), "confirm": True})
    facts = _facts(world)
    assert sorted(facts) == ["Lyon", "Paris"]
    assert facts["Paris"].valid_to is None
    assert (facts["Lyon"].valid_to, facts["Lyon"].closed_by_source_id) == (
        date(2026, 10, 22),
        _memory_id(world, "2026-10-22"),
    )


async def test_the_merge_after_a_memorys_commit_settles_facts_that_memory_does_not_state(
    world: SimpleNamespace,
) -> None:
    """A friend-of memory brings the merge; the located_in facts of both Pierres settle."""
    await call(world.server, "remember", _moved_to("Lyon", "2026-10-20"))
    await _a_second_pierre_in_paris(world)
    await call(
        world.server,
        "remember",
        {"text": "Pierre Bezukhov is a friend of Anna Pavlovna.", "by": "bob"},
    )
    facts = _facts(world)
    assert facts["Paris"].valid_to is None
    assert (facts["Lyon"].valid_to, facts["Lyon"].closed_by_source_id) == (
        date(2026, 10, 22),
        _memory_id(world, "2026-10-22"),
    )
