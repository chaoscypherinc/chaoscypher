# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``list_memories`` and ``digest``: what was remembered, newest first; what a window added."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.mcp.memory_world import build_world, call, remember_three_and_a_document


LONG_TEXT = "\n\n".join(
    f"Paragraph {i}. Boris Drubetskoy walked through the garden and thought about the house, "
    "the bees, the long winter and the letters he had not answered; the day went on."
    for i in range(60)
)


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch)
    await remember_three_and_a_document(w)
    yield w
    w.engine.close()


async def test_list_memories_is_newest_first(world: SimpleNamespace) -> None:
    text = await call(world.server, "list_memories", {})
    assert text.splitlines() == [
        '[alice 2026-10-20] "Pierre Bezukhov moved to Lyon."',
        '[alice 2026-10-19] "Pierre Bezukhov is a friend of Anna Pavlovna."',
        '[bob 2026-10-18] "Anna Pavlovna moved to Paris."',
    ]


async def test_list_memories_about_a_name_and_a_limit(world: SimpleNamespace) -> None:
    text = await call(world.server, "list_memories", {"about": "anna pavlovna"})
    assert text.splitlines() == [
        '[alice 2026-10-19] "Pierre Bezukhov is a friend of Anna Pavlovna."',
        '[bob 2026-10-18] "Anna Pavlovna moved to Paris."',
    ]
    assert await call(world.server, "list_memories", {"limit": 1}) == (
        '[alice 2026-10-20] "Pierre Bezukhov moved to Lyon."'
    )
    assert await call(world.server, "list_memories", {"about": "Zoe"}) == (
        "Nothing remembered about Zoe."
    )


async def test_list_memories_filters_by_scope_and_says_when_empty(world: SimpleNamespace) -> None:
    await call(world.server, "remember", {"text": "The weather was grey.", "scope": "diary"})
    text = await call(world.server, "list_memories", {"scope": "diary"})
    assert text.splitlines() == [
        f'[user {datetime.now(UTC).date().isoformat()}] "The weather was grey." (no facts found)'
    ]
    assert await call(world.server, "list_memories", {"scope": "nothing-here"}) == (
        "Nothing remembered yet."
    )


async def test_a_multi_chunk_memory_is_listed_once(world: SimpleNamespace) -> None:
    """Review Focus 5: a pasted chapter is one memory, its first 160 characters once."""
    await call(world.server, "remember", {"text": LONG_TEXT, "by": "alice"})
    lines = await call(world.server, "list_memories", {"about": "Boris Drubetskoy"})
    listed = [line for line in lines.splitlines() if "Paragraph 0." in line]
    assert len(listed) == 1
    assert listed[0].endswith('..."') and len(listed[0]) <= 200


async def test_digest_counts_the_week(world: SimpleNamespace) -> None:
    text = await call(world.server, "digest", {})
    since = (datetime.now(UTC).date() - timedelta(days=7)).isoformat()
    lines = text.splitlines()
    assert lines[0] == f"Since {since}: 3 memories, 3 facts added."
    assert lines[1] == "Added:"
    assert "Pierre Bezukhov:" in lines and "Anna Pavlovna:" in lines
    assert (
        '  Pierre Bezukhov located_in Lyon [alice 2026-10-20] "Pierre Bezukhov moved to Lyon."'
        in lines
    )
    assert "born_in Moscow" not in text  # the document is no memory
    assert lines[-2] == "Replaced: none"
    assert lines[-1] == "Forgotten: 0"


async def test_digest_since_a_date_and_a_scope(world: SimpleNamespace) -> None:
    tomorrow = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    text = await call(world.server, "digest", {"since": tomorrow})
    assert text.splitlines()[0] == f"Since {tomorrow}: 0 memories, 0 facts added."
    await call(world.server, "remember", {"text": "The weather was grey.", "scope": "diary"})
    text = await call(world.server, "digest", {"scope": "diary"})
    assert text.splitlines()[0].endswith(": 1 memories, 0 facts added.")


async def test_a_global_question_recalls_the_digest(world: SimpleNamespace) -> None:
    text = await call(world.server, "recall", {"query": "Summarize what you remember."})
    assert text.splitlines()[0].startswith("Since ")
    assert "Added:" in text.splitlines()
