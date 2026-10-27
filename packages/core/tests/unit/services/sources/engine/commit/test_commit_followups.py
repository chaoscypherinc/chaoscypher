# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The commit's follow-ups: a memory whose supersession failed after its commit is finished later.

The identity pass and a memory's supersession run after the commit's transaction and are
best-effort: the commit stands when they fail. The result says so
(``followups_complete``), and ``SourceCommitService.finish_memory`` runs them again.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from chaoscypher_core.models import MEMORY_SOURCE_TYPE
from chaoscypher_core.services.graph.memory import supersession
from chaoscypher_core.services.graph.resolution import apply
from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService
from tests.unit.mcp.memory_world import WORLD_SCRIPT, build_world


if TYPE_CHECKING:
    from tests.fakes.scripted_llm import Script


PARIS = "Anna Pavlovna moved to Paris."
LYON = "Anna Pavlovna moved to Lyon."
SCRIPT: dict[str, Script] = {
    **WORLD_SCRIPT,
    LYON: (
        "E|Anna Pavlovna|Person|Anna|0.9|S1|A hostess\nE|Lyon|Location||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|Anna Pavlovna moved to Lyon\n",
    ),
}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch, script=SCRIPT)
    yield w
    w.engine.close()


async def _memory(world: SimpleNamespace, text: str, valid_from: str) -> Any:
    return await world.engine.process_document(
        text,
        filename=f"memory-{valid_from}.txt",
        forced_domain="generic",
        source_type=MEMORY_SOURCE_TYPE,
        source_fields={"memory_by": "alice", "valid_from": valid_from},
    )


def _failing_supersession(*args: Any, **kwargs: Any) -> Any:
    raise RuntimeError("supersession failed")


def _paris_edge(world: SimpleNamespace) -> Any:
    edges = [e for e in world.graph.list_edges() if e.label == "located_in"]
    paris = next(n for n in world.graph.list_nodes(include_embedding=False) if n.label == "Paris")
    return next(e for e in edges if e.target_node_id == paris.id)


async def test_followups_complete_is_false_when_supersession_raises(
    world: SimpleNamespace,
) -> None:
    with patch.object(supersession, "supersede_memory_edges", _failing_supersession):
        result = await _memory(world, PARIS, "2026-10-18")
    assert result.followups_complete is False
    source = world.storage.get_source(result.source_id, world.engine.database_name)
    assert source is not None and source["commit_complete"]
    assert result.nodes


async def test_followups_complete_is_true_for_a_document(world: SimpleNamespace) -> None:
    result = await world.engine.process_document(PARIS, filename="book.txt")
    assert result.followups_complete is True


async def test_finish_memory_closes_paris_with_lyon(world: SimpleNamespace) -> None:
    await _memory(world, PARIS, "2026-10-18")
    # every supersession step fails: the memory's own, and the one its merge runs
    with (
        patch.object(supersession, "supersede_memory_edges", _failing_supersession),
        patch.object(apply, "recompute_single_valued", _failing_supersession),
    ):
        second = await _memory(world, LYON, "2026-10-20")
    assert second.followups_complete is False
    assert _paris_edge(world).valid_to is None

    service = SourceCommitService.from_engine(world.engine)
    assert await service.finish_memory(second.source_id) is True

    closed = _paris_edge(world)
    assert closed.closed_by_source_id == second.source_id
    assert closed.valid_to == date(2026, 10, 20)
