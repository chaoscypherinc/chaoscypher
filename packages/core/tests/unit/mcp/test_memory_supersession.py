# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Supersession through the tools: a later memory on a single-valued relation replaces the earlier."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from tests.unit.mcp.memory_world import WORLD_SCRIPT, build_world, call


if TYPE_CHECKING:
    from tests.fakes.scripted_llm import Script


SCRIPT: dict[str, Script] = {
    **WORLD_SCRIPT,
    "Pierre Bezukhov moved to Paris.": (
        "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\nE|Paris|Location||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|Pierre Bezukhov moved to Paris\n",
    ),
    # a model that reads the KNOWN ENTITIES block writes the name the block gives
    "Pierre moved to Paris.": lambda prompt: (
        f"E|{'Pierre Bezukhov' if '- Pierre Bezukhov (Person)' in prompt else 'Pierre'}"
        "|Person||0.9|S1|A man\nE|Paris|Location||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|Pierre moved to Paris\n",
    ),
}
PARIS = {"text": "Pierre Bezukhov moved to Paris.", "by": "alice", "valid_from": "2026-03-02"}
LYON = {"text": "Pierre Bezukhov moved to Lyon.", "by": "alice", "valid_from": "2026-10-20"}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch, script=SCRIPT)
    yield w
    w.engine.close()


async def test_paris_then_lyon_answers_with_the_changed_line(world: SimpleNamespace) -> None:
    first = await call(world.server, "remember", PARIS)
    assert "Changed:" not in first
    text = await call(world.server, "remember", LYON)
    assert (
        "Changed: Pierre Bezukhov located_in Paris [alice 2026-03-02] -> Lyon [alice 2026-10-20]. "
        "Both kept." in text.splitlines()
    )


async def test_recall_shows_the_open_fact_and_as_of_the_closed_one(world: SimpleNamespace) -> None:
    await call(world.server, "remember", PARIS)
    await call(world.server, "remember", LYON)
    now = await call(world.server, "recall", {"query": "Where does Pierre live?"})
    assert "located_in Lyon [alice 2026-10-20]" in now
    assert "located_in Paris" not in now
    then = await call(
        world.server, "recall", {"query": "Where does Pierre live?", "as_of": "2026-03-05"}
    )
    assert "located_in Paris [alice 2026-03-02..2026-10-20, replaced]" in then
    assert "located_in Lyon" not in then


async def test_digest_lists_the_replacement(world: SimpleNamespace) -> None:
    await call(world.server, "remember", PARIS)
    await call(world.server, "remember", LYON)
    text = await call(world.server, "digest", {})
    lines = text.splitlines()
    assert "Replaced:" in lines
    i = lines.index("Replaced:")
    assert lines[i + 1].startswith(
        "  Pierre Bezukhov located_in Paris [alice 2026-03-02..2026-10-20, replaced]"
    )
    assert "Replaced: none" not in lines


async def test_an_older_memory_is_noted_as_history(world: SimpleNamespace) -> None:
    await call(world.server, "remember", LYON)
    text = await call(world.server, "remember", PARIS)
    assert (
        "Noted: Pierre Bezukhov located_in Paris [alice 2026-03-02] is older than Lyon "
        "[alice 2026-10-20]; kept as history." in text.splitlines()
    )


async def test_a_merged_name_still_closes_the_earlier_fact(world: SimpleNamespace) -> None:
    """The step runs after the identity pass: "Pierre" and "Pierre Bezukhov" are one node."""
    await call(
        world.server,
        "remember",
        {"text": "Pierre moved to Paris.", "by": "alice", "valid_from": "2026-03-02"},
    )
    text = await call(world.server, "remember", LYON)
    assert "Changed: Pierre Bezukhov located_in Paris [alice 2026-03-02] -> Lyon" in text
    pierres = [n for n in world.graph.list_nodes(include_embedding=False) if "Pierre" in n.label]
    assert len(pierres) == 1


MOSCOW_HOME = {
    "text": "Pierre Bezukhov moved to Moscow.",
    "by": "alice",
    "valid_from": "2025-01-01",
}
SCRIPT["Pierre Bezukhov moved to Moscow."] = (
    "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\nE|Moscow|Location||0.9|S1|A city\n",
    "R|0|1|located_in|0.9|S1|Pierre Bezukhov moved to Moscow\n",
)


async def test_a_third_home_closes_only_the_open_one(world: SimpleNamespace) -> None:
    """A closed fact is never closed again: Moscow's window ends when Paris began."""
    await call(world.server, "remember", MOSCOW_HOME)
    await call(world.server, "remember", {**PARIS, "valid_from": "2025-06-01"})
    text = await call(world.server, "remember", LYON)
    changed = [line for line in text.splitlines() if line.startswith("Changed:")]
    assert changed == [
        "Changed: Pierre Bezukhov located_in Paris [alice 2025-06-01] -> Lyon [alice 2026-10-20]. "
        "Both kept."
    ]
    moscow = next(
        e for e in world.graph.list_edges() if e.target_node_id.endswith(_node_id(world, "Moscow"))
    )
    assert (moscow.valid_from.isoformat(), moscow.valid_to.isoformat()) == (
        "2025-01-01",
        "2025-06-01",
    )
    then = await call(
        world.server, "recall", {"query": "Where does Pierre live?", "as_of": "2025-08-01"}
    )
    assert "located_in Paris [alice 2025-06-01..2026-10-20, replaced]" in then
    assert "Moscow" not in then


def _node_id(world: SimpleNamespace, label: str) -> str:
    return next(n.id for n in world.graph.list_nodes(include_embedding=False) if n.label == label)


async def test_a_replaced_or_future_memory_is_no_note(world: SimpleNamespace) -> None:
    """Notes list the memories no fact covered; a hidden fact's memory is covered too."""
    await call(world.server, "remember", PARIS)
    await call(world.server, "remember", LYON)
    now = await call(world.server, "recall", {"query": "Where does Pierre live?"})
    assert "Notes:" not in now and "moved to Paris" not in now
    then = await call(
        world.server, "recall", {"query": "Where does Pierre live?", "as_of": "2026-03-05"}
    )
    assert "Notes:" not in then and "moved to Lyon" not in then


async def test_moving_back_makes_the_earlier_home_current_again(world: SimpleNamespace) -> None:
    await call(world.server, "remember", PARIS)
    await call(world.server, "remember", LYON)
    text = await call(world.server, "remember", {**PARIS, "valid_from": "2026-11-05"})
    assert (
        "Changed: Pierre Bezukhov located_in Lyon [alice 2026-10-20] -> Paris [alice 2026-11-05]. Both kept."
        in text.splitlines()
    )
    now = await call(world.server, "recall", {"query": "Where does Pierre live?"})
    assert "located_in Paris [alice" in now and "located_in Lyon" not in now


async def test_forgetting_the_middle_home_keeps_the_latest_current(world: SimpleNamespace) -> None:
    await call(world.server, "remember", PARIS)
    await call(world.server, "remember", LYON)
    await call(world.server, "remember", {**MOSCOW_HOME, "valid_from": "2026-11-05"})
    preview = await call(world.server, "forget", {"query": "Lyon"})
    handle = preview.splitlines()[0].split()[1]
    await call(world.server, "forget", {"handle": handle, "confirm": True})
    now = await call(world.server, "recall", {"query": "Where does Pierre live?"})
    assert "located_in Moscow [alice 2026-11-05]" in now
    assert "located_in Paris [alice" not in now


async def test_the_receipt_fits_the_budget(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.engine.settings.mcp.memory.result_chars = 300
    text = await call(world.server, "remember", PARIS)
    assert len(text) <= 300
