# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The memory surface's budgets: a short tool list, short answers, no ids unless asked."""

from __future__ import annotations

import json
import re
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.mcp.memory_world import (
    build_world,
    call,
    list_tools,
    remember_three_and_a_document,
    settings,
)


RESULT_CHARS = 1600
SCHEMA_CHARS = 2000
UUID = re.compile(r"[0-9a-f]{32}")

CALLS: tuple[tuple[str, dict[str, object]], ...] = (
    ("remember", {"text": "The weather was grey.", "by": "alice"}),
    ("recall", {"query": "Where does Pierre live?"}),
    ("recall", {"query": "Name everyone Pierre knows."}),
    ("recall", {"query": "How is Pierre connected to Paris?"}),
    ("recall", {"query": "Summarize what you remember."}),
    ("list_memories", {}),
    ("list_memories", {"about": "Anna Pavlovna"}),
    ("digest", {}),
)


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    s = settings()
    s.mcp.profile = "memory"
    w = build_world(tmp_path, monkeypatch, s=s)
    await remember_three_and_a_document(w)
    yield w
    w.engine.close()


async def test_the_memory_profiles_tool_list_fits_the_schema_budget(world: SimpleNamespace) -> None:
    tools = await list_tools(world.server)
    assert {t.name for t in tools} == {"remember", "recall", "forget", "list_memories", "digest"}
    payload = json.dumps([t.model_dump(exclude_none=True) for t in tools])
    assert len(payload) <= SCHEMA_CHARS, len(payload)


@pytest.mark.parametrize(("name", "args"), CALLS)
async def test_every_answer_is_short_plain_text(
    world: SimpleNamespace, name: str, args: dict
) -> None:
    text = await call(world.server, name, dict(args))
    assert len(text) <= RESULT_CHARS
    assert not UUID.search(text)
    assert "{" not in text and "}" not in text
    assert text.isascii() or all(ord(c) < 0x250 for c in text)  # names may carry accents


def _friend_script(n: int) -> dict[str, tuple[str, str]]:
    return {
        f"Pierre Bezukhov is a friend of Friend{i:02d}.": (
            f"E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\nE|Friend{i:02d}|Person||0.9|S1|A friend\n",
            "R|0|1|friend_of|0.9|S1|They are friends\n",
        )
        for i in range(n)
    }


async def test_a_long_answer_is_cut_on_a_line_and_more_returns_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = settings()
    s.mcp.memory.result_chars = 600  # the block lists at most its row cap; the budget cuts below it
    w = build_world(tmp_path, monkeypatch, script=_friend_script(30), s=s)
    try:
        for i in range(30):
            await call(
                w.server, "remember", {"text": f"Pierre Bezukhov is a friend of Friend{i:02d}."}
            )
        short = await call(w.server, "recall", {"query": "Name everyone Pierre knows."})
        assert len(short) <= 600
        assert short.splitlines()[-1].endswith("more, ask with more=true)")
        more = await call(
            w.server, "recall", {"query": "Name everyone Pierre knows.", "more": True}
        )
        assert len(more.splitlines()) > len(short.splitlines())
        with_ids = await call(w.server, "recall", {"query": "Where does Pierre live?", "ids": True})
        assert " #" in with_ids
    finally:
        w.engine.close()
