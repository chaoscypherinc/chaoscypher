# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``remember``: a memory is a one-chunk extracted source with by, scope and valid_from.

A real ``Engine`` on a temporary directory, the extraction model scripted per memory text
(``tests/fakes/scripted_llm.py``), the embedder faked, driven through the in-process MCP
server's ``CallToolRequest`` handler the way the MCP host would call it.
"""

from __future__ import annotations

import re
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from chaoscypher_core.mcp.memory import NO_MODEL_LINE
from chaoscypher_core.mcp.server import create_mcp_server
from tests.unit.mcp.memory_world import (
    build_world,
    call,
    call_raw,
    engine_at,
    memory_sources,
    settings,
)


if TYPE_CHECKING:
    from tests.fakes.scripted_llm import Script


SCRIPT: dict[str, Script] = {
    "Pierre Bezukhov inherited a fortune.": (
        "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count who inherited a fortune\n",
        "",
    ),
    "Pierre moved to Lyon.": (
        "E|Pierre|Person||0.9|S1|A man\nE|Lyon|Place||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|He moved to Lyon\n",
    ),
    "I moved to Lyon.": (
        "E|alice|Person||0.9|S1|The speaker\nE|Lyon|Place||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|She moved to Lyon\n",
    ),
    "Anna keeps bees in Lyon.": (
        "E|Anna|Person||0.9|S1|A beekeeper\nE|Lyon|Place||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|She keeps bees in Lyon\n",
    ),
    "Boris serves in the Guards.": ("E|Boris|Person||0.9|S1|An officer\n", ""),
    "The weather was grey.": ("", ""),
    "Lise sings.": ("E|Lise|Person||0.9|S1|A singer\n", ""),
}

LONG_TEXT = "\n\n".join(
    f"Paragraph {i}. Pierre walked through the garden and thought about the house in Lyon, "
    "the bees, the long winter and the letters he had not answered; the day went on."
    for i in range(60)
)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch, script=SCRIPT)
    yield w
    w.engine.close()


@pytest.fixture
def server_without_llm(tmp_path: Path) -> Generator[SimpleNamespace]:
    s = settings()
    s.llm.chat_provider = ""  # what an install with no provider at all reads as
    engine = engine_at(tmp_path, s)
    yield SimpleNamespace(
        engine=engine,
        server=create_mcp_server(engine),
        search=engine.search_repository,
        storage=engine.storage_adapter,
    )
    engine.close()


async def test_two_memories_naming_pierre_attest_one_pierre_node(world: SimpleNamespace) -> None:
    await call(
        world.server, "remember", {"text": "Pierre Bezukhov inherited a fortune.", "by": "alice"}
    )
    await call(world.server, "remember", {"text": "Pierre moved to Lyon.", "by": "alice"})
    pierres = [n for n in world.graph.list_nodes(include_embedding=False) if "Pierre" in n.label]
    assert len(pierres) == 1
    assert len(world.graph.node_members(pierres[0].id)) == 2


async def test_a_first_person_memory_lands_on_the_speaker_node(world: SimpleNamespace) -> None:
    await call(world.server, "remember", {"text": "I moved to Lyon.", "by": "alice"})
    assert "- alice (Person) — the speaker (I, me)" in world.llm.prompts[0]
    assert [n for n in world.graph.list_nodes(include_embedding=False) if n.label == "alice"]


async def test_the_text_is_the_sources_one_chunk_and_is_keyword_searchable(
    world: SimpleNamespace,
) -> None:
    await call(world.server, "remember", {"text": "Anna keeps bees in Lyon."})
    [source] = memory_sources(world.storage)
    chunks, _ = world.storage.get_chunks_by_source(source["id"])
    assert [c["content"] for c in chunks] == ["Anna keeps bees in Lyon."]
    assert world.search.keyword_search("bees", item_type="chunk")


async def test_by_scope_and_valid_from_read_back_through_memberships(
    world: SimpleNamespace,
) -> None:
    await call(
        world.server,
        "remember",
        {
            "text": "Boris serves in the Guards.",
            "by": "alice",
            "scope": "work",
            "valid_from": "2026-03-02",
        },
    )
    node = next(
        n for n in world.graph.list_nodes(include_embedding=False) if n.label.startswith("Boris")
    )
    src = world.storage.get_source(
        next(iter(world.graph.node_members(node.id))), world.engine.database_name
    )
    assert src is not None
    assert (src["source_type"], src["memory_by"], src["memory_scope"], src["valid_from"]) == (
        "memory",
        "alice",
        "work",
        "2026-03-02",
    )


async def test_the_answer_lists_what_was_linked(world: SimpleNamespace) -> None:
    text = await call(
        world.server,
        "remember",
        {"text": "Pierre moved to Lyon.", "by": "alice", "valid_from": "2026-10-20"},
    )
    lines = text.splitlines()
    assert lines[0] == 'Remembered [alice 2026-10-20] "Pierre moved to Lyon."'
    assert lines[1].startswith("Linked: ")
    assert "Pierre (Person)" in lines[1]
    assert "Lyon (Location)" in lines[1]  # Place is the Location core type
    assert lines[2] == "Facts: Pierre located_in Lyon"


async def test_an_extraction_that_finds_nothing_still_stores_the_note(
    world: SimpleNamespace,
) -> None:
    text = await call(world.server, "remember", {"text": "The weather was grey."})
    assert "Linked: none (no facts found; the text is kept as a note)" in text
    assert world.search.keyword_search("grey", item_type="chunk")


async def test_a_secret_is_refused_and_nothing_is_stored(world: SimpleNamespace) -> None:
    text, is_error = await call_raw(
        world.server, "remember", {"text": "my api_key = sk-abcdefghijklmnopqrstuvwxyz123456"}
    )
    assert is_error
    assert text.startswith("Not stored: this looks like a")
    assert text.endswith("Memories never hold keys or passwords.")
    assert world.storage.list_sources()[1] == 0


async def test_a_bad_date_is_refused(world: SimpleNamespace) -> None:
    text, is_error = await call_raw(
        world.server, "remember", {"text": "Lise sings.", "valid_from": "yesterday"}
    )
    assert is_error and text == "valid_from must be YYYY-MM-DD"


async def test_no_extraction_model_stores_an_unlinked_note(
    server_without_llm: SimpleNamespace,
) -> None:
    text = await call(server_without_llm.server, "remember", {"text": "Lise sings."})
    assert text.splitlines()[1:] == [NO_MODEL_LINE]
    assert server_without_llm.search.keyword_search("Lise", item_type="chunk")
    [source] = memory_sources(server_without_llm.storage)
    assert source["memory_by"] == "user"


async def test_a_long_text_is_chunked_but_is_one_memory(world: SimpleNamespace) -> None:
    await call(world.server, "remember", {"text": LONG_TEXT})
    [source] = memory_sources(world.storage)
    chunks, total = world.storage.get_chunks_by_source(source["id"], page_size=200)
    assert total > 1 and len(chunks) == total


async def test_the_result_has_no_uuid_and_no_brace(world: SimpleNamespace) -> None:
    text = await call(world.server, "remember", {"text": "Pierre moved to Lyon."})
    assert not re.search(r"[0-9a-f]{32}", text)
    assert "{" not in text and "}" not in text
    assert len(text) <= 1600
