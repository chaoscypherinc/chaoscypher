# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``forget``: a preview with handles, a confirmed release with lineage, an audit row without text."""

from __future__ import annotations

import re
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from chaoscypher_core.adapters.sqlite.models import MemoryAuditRow, SourceRow
from chaoscypher_core.mcp.memory import memory_handle, resolve_handle
from tests.unit.mcp.memory_world import build_world, call, call_raw, memory_sources
from tests.unit.mcp.test_memory_supersession import LYON, PARIS, SCRIPT


MOSCOW = {
    "text": "Pierre Bezukhov was born in Moscow.",
    "by": "alice",
    "valid_from": "2026-10-19",
}


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch, script=SCRIPT)
    await call(w.server, "remember", PARIS)
    await call(w.server, "remember", LYON)
    await call(w.server, "remember", MOSCOW)
    await w.engine.process_document("Pierre Bezukhov was born in Moscow.", filename="book.txt")
    yield w
    w.engine.close()


def _handle_of(world: SimpleNamespace, text: str) -> str:
    rows = memory_sources(world.storage)
    ids = [r["id"] for r in rows]
    for r in rows:
        chunks, _ = world.storage.get_chunks_by_source(r["id"], page_size=1)
        if chunks and chunks[0]["content"] == text:
            return memory_handle(r["id"], ids)
    raise AssertionError(text)


def test_handles_are_the_shortest_unique_prefix_of_at_least_six() -> None:
    ids = ["abcdef0123", "abcdef9999", "0123456789"]
    assert memory_handle("0123456789", ids) == "012345"
    assert memory_handle("abcdef0123", ids) == "abcdef0"
    assert resolve_handle("012345", ids) == "0123456789"
    assert resolve_handle("abcdef", ids) == "Handle abcdef is ambiguous; use 7 characters."
    assert resolve_handle("zzzzzz", ids) == "No memory has handle zzzzzz."


async def test_the_preview_lists_matches_with_counts_and_how_to_confirm(
    world: SimpleNamespace,
) -> None:
    text = await call(world.server, "forget", {"query": "Moscow"})
    lines = text.splitlines()
    handle = _handle_of(world, MOSCOW["text"])
    assert lines[0] == (
        f'1. {handle} [alice 2026-10-19] "Pierre Bezukhov was born in Moscow." '
        "- 0 facts go, 1 stays (also stated elsewhere)"
    )
    assert lines[-1] == f'To forget one: forget(handle="{handle}", confirm=true)'


async def test_forgetting_removes_what_only_the_memory_attested_and_keeps_the_rest(
    world: SimpleNamespace,
) -> None:
    handle = _handle_of(world, MOSCOW["text"])
    text = await call(world.server, "forget", {"handle": handle, "confirm": True})
    assert text == f"Forgotten {handle}: 0 facts removed, 1 kept."
    assert len(memory_sources(world.storage)) == 2
    edges = [e.label for e in world.graph.list_edges()]
    assert "born_in" in edges  # the document still states it


async def test_the_previews_counts_equal_what_the_confirm_removes(world: SimpleNamespace) -> None:
    handle = _handle_of(world, LYON["text"])
    preview = await call(world.server, "forget", {"query": "Lyon"})
    assert f"{handle} [alice 2026-10-20]" in preview and "- 1 facts go, 0 stays" in preview
    before = len(world.graph.list_edges())
    text = await call(world.server, "forget", {"handle": handle, "confirm": True})
    assert text == f"Forgotten {handle}: 1 facts removed, 0 kept."
    assert len(world.graph.list_edges()) == before - 1


async def test_forgetting_the_lyon_memory_reopens_paris(world: SimpleNamespace) -> None:
    handle = _handle_of(world, LYON["text"])
    await call(world.server, "forget", {"handle": handle, "confirm": True})
    text = await call(world.server, "recall", {"query": "Where does Pierre live?"})
    assert "located_in Paris [alice 2026-03-02]" in text and "replaced" not in text
    assert "Lyon" not in text


async def test_confirm_without_a_handle_previews_and_deletes_nothing(
    world: SimpleNamespace,
) -> None:
    text, is_error = await call_raw(world.server, "forget", {"query": "Lyon", "confirm": True})
    assert not is_error
    assert text.splitlines()[0] == "Say which: confirm needs a handle."
    assert len(memory_sources(world.storage)) == 3


async def test_an_ambiguous_handle_is_refused(world: SimpleNamespace) -> None:
    """Review Focus 3: two sources sharing a prefix after new memories arrived."""
    handle = _handle_of(world, LYON["text"])
    source_id = resolve_handle(handle, [r["id"] for r in memory_sources(world.storage)])
    session = world.storage.session
    twin = session.get(SourceRow, source_id)
    assert twin is not None
    session.add(
        SourceRow(
            id=source_id[:6] + "f" * (len(source_id) - 6),
            database_name=twin.database_name,
            filename="twin.txt",
            filepath="/twin.txt",
            file_type="txt",
            status="committed",
            source_type="memory",
            memory_by="alice",
            valid_from="2026-10-20",
        )
    )
    session.commit()
    text, is_error = await call_raw(world.server, "forget", {"handle": handle, "confirm": True})
    assert is_error and text.startswith(f"Handle {handle} is ambiguous; use ")
    assert len(memory_sources(world.storage)) == 4


async def test_the_audit_row_has_no_text(world: SimpleNamespace) -> None:
    handle = _handle_of(world, LYON["text"])
    await call(world.server, "forget", {"handle": handle, "confirm": True})
    rows = world.storage.session.exec(select(MemoryAuditRow)).all()
    assert [(r.action, r.handle, r.by, r.scope, r.facts_removed, r.facts_kept) for r in rows] == [
        ("forget", handle, "alice", None, 1, 0)
    ]
    assert "Lyon" not in repr(rows[0]) and "Pierre" not in repr(rows[0])
    digest = await call(world.server, "digest", {})
    assert digest.splitlines()[-1] == "Forgotten: 1"


async def test_a_forgotten_fact_never_comes_back(world: SimpleNamespace) -> None:
    handle = _handle_of(world, LYON["text"])
    await call(world.server, "forget", {"handle": handle, "confirm": True})
    assert "Lyon" not in await call(world.server, "recall", {"query": "Where does Pierre live?"})
    assert not world.search.keyword_search("Lyon", item_type="chunk")
    assert "Lyon" not in await call(world.server, "digest", {})
    assert "Lyon" not in await call(world.server, "list_memories", {})


async def test_a_result_has_no_uuid_and_no_brace(world: SimpleNamespace) -> None:
    text = await call(world.server, "forget", {"query": "Pierre"})
    assert not re.search(r"[0-9a-f]{32}", text) and "{" not in text


async def test_a_short_handle_is_refused_and_deletes_nothing(world: SimpleNamespace) -> None:
    """A preview's line number is no handle: anything under six characters is refused."""
    text, is_error = await call_raw(world.server, "forget", {"handle": "1", "confirm": True})
    assert is_error and text == "Handle 1 is too short; a handle has at least 6 characters."
    assert len(memory_sources(world.storage)) == 3


async def test_a_handle_without_confirm_previews_that_memory(world: SimpleNamespace) -> None:
    handle = _handle_of(world, LYON["text"])
    text = await call(world.server, "forget", {"handle": handle})
    assert text.splitlines()[0].startswith(f"1. {handle} [alice 2026-10-20]")


async def test_every_memory_is_reachable_past_the_source_page(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review: the tools paged the newest 1000 sources; older memories and documents vanished."""
    from chaoscypher_core.mcp import memory_seed

    monkeypatch.setattr(memory_seed, "SOURCE_PAGE", 2)
    listed = await call(world.server, "list_memories", {"limit": 50})
    assert len(listed.splitlines()) == 3
    recalled = await call(world.server, "recall", {"query": "Where was Pierre born?"})
    assert "born_in Moscow" in recalled  # the document's fact (a memory also states it)
    preview = await call(world.server, "forget", {"query": "Paris"})
    assert "moved to Paris" in preview
