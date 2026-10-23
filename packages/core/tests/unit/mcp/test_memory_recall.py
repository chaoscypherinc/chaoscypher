# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``recall``: the graph's answer in its shape, as plain text with provenance (spec decision 4)."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
)
from tests.unit.mcp.memory_world import build_world, call, remember_three_and_a_document


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch)
    await remember_three_and_a_document(w)
    yield w
    w.engine.close()


@pytest.fixture
def empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch)
    yield w
    w.engine.close()


async def test_where_does_pierre_live_is_a_card_with_tagged_facts(world: SimpleNamespace) -> None:
    text = await call(world.server, "recall", {"query": "Where does Pierre live?"})
    lines = text.splitlines()
    assert lines[0] == "Pierre Bezukhov (Person)"
    assert (
        '  Pierre Bezukhov located_in Lyon [alice 2026-10-20] "Pierre Bezukhov moved to Lyon."'
        in lines
    )
    assert any(line.startswith("  Pierre Bezukhov born_in Moscow [doc book.txt]") for line in lines)


async def test_name_everyone_pierre_knows_is_a_grouped_list(world: SimpleNamespace) -> None:
    text = await call(world.server, "recall", {"query": "Name everyone Pierre knows."})
    lines = text.splitlines()
    assert "Pierre Bezukhov: friend_of (1)" in lines
    # friend_of is symmetric: the stored direction is the pipeline's choice
    tail = ' [alice 2026-10-19] "Pierre Bezukhov is a friend of Anna Pavlovna."'
    assert (
        "  Pierre Bezukhov friend_of Anna Pavlovna" + tail in lines
        or "  Anna Pavlovna friend_of Pierre Bezukhov" + tail in lines
    )


async def test_how_is_pierre_connected_to_paris_is_a_chain(world: SimpleNamespace) -> None:
    text = await call(world.server, "recall", {"query": "How is Pierre connected to Paris?"})
    lines = text.splitlines()
    assert lines[0] in ("Pierre Bezukhov to Paris:", "Paris to Pierre Bezukhov:")
    hops = lines[1:3]
    assert all(h.startswith("  ") for h in hops)
    assert any(" friend_of " in h and "[alice 2026-10-19]" in h for h in hops)
    assert any(h.startswith("  Anna Pavlovna located_in Paris [bob 2026-10-18]") for h in hops)


async def test_recall_makes_no_chat_call(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = AsyncMock(side_effect=RuntimeError("the chat model must not be called"))
    world.engine.settings.graphrag.router = "llm"
    monkeypatch.setattr(GraphRAGToolHandlers, "_router_chat", lambda self: broken)
    text = await call(world.server, "recall", {"query": "Where does Pierre live?"})
    assert text.splitlines()[0] == "Pierre Bezukhov (Person)"
    broken.assert_not_called()


async def test_recall_on_an_empty_database_is_one_line(empty: SimpleNamespace) -> None:
    assert await call(empty.server, "recall", {"query": "anything"}) == (
        "Nothing remembered about that yet."
    )


async def test_a_graph_with_documents_but_no_memories_answers_doc_facts(
    empty: SimpleNamespace,
) -> None:
    """Review Focus 4: documents are always in scope, tagged [doc TITLE]."""
    await empty.engine.process_document("Pierre Bezukhov was born in Moscow.", filename="book.txt")
    text = await call(empty.server, "recall", {"query": "Where was Pierre born?"})
    assert text.splitlines()[0] == "Pierre Bezukhov (Person)"
    assert "[doc book.txt]" in text


async def test_a_manual_with_no_facts_answers_with_its_passage(empty: SimpleNamespace) -> None:
    """A procedure becomes no fact; recall still gives the agent the manual's own words."""
    await empty.engine.process_document(
        "To reset the router, hold the reset button on the back panel for ten seconds. "
        "Wait for the light to blink amber, then release the reset button.",
        filename="router-manual.txt",
    )
    text = await call(empty.server, "recall", {"query": "How do I reset the router?"})
    assert text.startswith("Passages:\n  [doc router-manual.txt] ")
    assert "release the reset button." in text


async def test_a_scope_narrows_the_memories_but_not_the_documents(world: SimpleNamespace) -> None:
    await call(
        world.server,
        "remember",
        {"text": "The weather was grey.", "scope": "diary", "by": "alice"},
    )
    text = await call(
        world.server, "recall", {"query": "What was the weather like?", "scope": "diary"}
    )
    assert "Notes:\n  [alice" in text and '"The weather was grey."' in text
    text = await call(
        world.server, "recall", {"query": "Where does Pierre live?", "scope": "diary"}
    )
    assert "located_in Lyon" not in text  # the Lyon memory is unscoped
    assert "born_in Moscow [doc book.txt]" in text  # the document stays in scope


async def test_a_note_is_listed_under_notes(world: SimpleNamespace) -> None:
    """Review Focus 1: a memory no fact covers comes back under Notes."""
    await call(world.server, "remember", {"text": "The weather was grey.", "by": "alice"})
    text = await call(world.server, "recall", {"query": "What was the weather like?"})
    assert "Notes:" in text.splitlines()
    assert any(line.endswith('"The weather was grey."') for line in text.splitlines())
