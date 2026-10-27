# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""What a memory that is not linked yet says: ``remember``'s status line, the list and digest marks.

A memory whose link cannot finish now is kept and searchable, marked with why on its row,
and says so: ``remember`` answers with one fixed line per reason, ``list_memories`` ends
the memory's line with its state, ``digest`` counts what is not linked yet.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from sqlalchemy.exc import OperationalError

from chaoscypher_core import Engine
from chaoscypher_core.adapters.sqlite.repos.memory_link import MemoryLinkRepository
from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.mcp import memory_link
from chaoscypher_core.mcp.memory import (
    FAILED_LINE,
    NO_FACTS,
    NO_MODEL_LINE,
    NOT_STORED,
    TIMED_OUT_LINE,
    link_status_lines,
    link_suffix,
    remembered_line,
)
from chaoscypher_core.mcp.memory_link import (
    LinkOutcome,
    LinkView,
    link_memory,
    link_repository,
)
from chaoscypher_core.mcp.memory_relink import counts_line, memory_views
from chaoscypher_core.models import MEMORY_SOURCE_TYPE, MemoryLinkState
from chaoscypher_core.services.workflows.tools.engine.memory_render import SENTENCE_CHARS
from chaoscypher_core.settings import EngineSettings
from tests.unit.mcp.memory_world import build_world, call, call_raw, memory_sources, until


if TYPE_CHECKING:
    from datetime import date

    from tests.fakes.scripted_llm import Script


LYON = "Pierre Bezukhov moved to Lyon."
PARIS = "Anna Pavlovna moved to Paris."
MOSCOW = "Pierre Bezukhov was born in Moscow."
GREY = "The weather was grey."
SCRIPT: dict[str, Script] = {
    LYON: (
        "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\nE|Lyon|Location||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|Pierre Bezukhov moved to Lyon\n",
    ),
    PARIS: (
        "E|Anna Pavlovna|Person|Anna|0.9|S1|A hostess\nE|Paris|Location||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|Anna Pavlovna moved to Paris\n",
    ),
    MOSCOW: (
        "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\nE|Moscow|Location||0.9|S1|A city\n",
        "R|0|1|born_in|0.9|S1|Pierre Bezukhov was born in Moscow\n",
    ),
    GREY: ("", ""),
}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch, script=SCRIPT)
    yield w
    w.engine.close()


def _state(world: SimpleNamespace, source_id: str) -> Any:
    return link_repository(world.engine).read_link_row(source_id)


def _source_of(world: SimpleNamespace, text: str) -> str:
    for row in memory_sources(world.storage):
        if _state(world, row["id"]).full_text == text:
            return str(row["id"])
    raise AssertionError(text)


async def _no_model(world: SimpleNamespace, text: str) -> str:
    provider = world.engine.settings.llm.chat_provider
    world.engine.settings.llm.chat_provider = ""
    try:
        return await call(world.server, "remember", {"text": text})
    finally:
        world.engine.settings.llm.chat_provider = provider


def test_the_status_lines_are_pinned() -> None:
    assert TIMED_OUT_LINE == (
        "Not linked yet: the model did not answer in time. Kept as a note; it links later."
    )
    assert FAILED_LINE == "Not linked yet: the model failed. Kept as a note; it links later."
    assert NO_MODEL_LINE == (
        "Not linked yet: no extraction model is set up. Kept as a note; it links once one is."
    )
    assert NOT_STORED == "Not stored: the database is busy. Try again."
    assert NO_FACTS == "Linked: none (no facts found; the text is kept as a note)"
    budget = EngineSettings().mcp.memory.result_chars
    first = remembered_line("alice", _today(), "x" * (SENTENCE_CHARS + 40))
    for line in (TIMED_OUT_LINE, FAILED_LINE, NO_MODEL_LINE, NO_FACTS):
        assert len(first + "\n" + line) <= budget


def _today() -> date:
    from datetime import UTC, datetime

    return datetime.now(UTC).date()


def test_the_list_suffixes_are_pinned() -> None:
    assert link_suffix(LinkView.LINKING, 0) == " (linking)"
    assert link_suffix(LinkView.INTERRUPTED, 0) == " (not linked yet: interrupted)"
    assert link_suffix(LinkView.PENDING, 0) == " (not linked yet)"
    assert link_suffix(LinkView.NO_MODEL, 0) == " (not linked yet: no extraction model)"
    assert link_suffix(LinkView.FAILED, 1) == " (not linked yet: the model failed, 1 try)"
    assert link_suffix(LinkView.FAILED, 3) == " (not linked yet: the model failed, 3 tries)"
    assert link_suffix(LinkView.LINKED_EMPTY, 0) == " (no facts found)"
    assert link_suffix(LinkView.LINKED, 0) == ""


async def test_a_timed_out_remember_is_pending_and_says_so(world: SimpleNamespace) -> None:
    release = asyncio.Event()
    world.llm.hold_on[LYON] = release
    world.engine.settings.mcp.memory.wait_seconds = 1
    text = await call(world.server, "remember", {"text": LYON, "by": "alice"})
    lines = text.splitlines()
    assert lines[0].startswith("Remembered [alice ")
    assert lines[1:] == [TIMED_OUT_LINE]
    source_id = _source_of(world, LYON)
    row = _state(world, source_id)
    assert row.state is MemoryLinkState.LINKING

    release.set()
    await until(lambda: _state(world, source_id).state is MemoryLinkState.LINKED)
    recalled = await call(world.server, "recall", {"query": "Where does Pierre live?"})
    assert "Lyon" in recalled


async def test_each_unlinked_outcome_persists_and_reports_its_own_state(
    world: SimpleNamespace,
) -> None:
    no_model = await _no_model(world, MOSCOW)
    assert no_model.splitlines()[1:] == [NO_MODEL_LINE]

    world.llm.raise_on[PARIS] = LLMError("the model is not answering")
    failed = await call(world.server, "remember", {"text": PARIS})
    assert failed.splitlines()[1:] == [FAILED_LINE]

    release = asyncio.Event()
    world.llm.hold_on[LYON] = release
    world.engine.settings.mcp.memory.wait_seconds = 1
    timed_out = await call(world.server, "remember", {"text": LYON})
    assert timed_out.splitlines()[1:] == [TIMED_OUT_LINE]
    world.engine.settings.mcp.memory.wait_seconds = 20

    empty = await call(world.server, "remember", {"text": GREY})
    assert empty.splitlines()[1:] == [NO_FACTS]

    states = {text: _state(world, _source_of(world, text)).state for text in SCRIPT}
    assert states == {
        MOSCOW: MemoryLinkState.NO_MODEL,
        PARIS: MemoryLinkState.FAILED,
        LYON: MemoryLinkState.LINKING,
        GREY: MemoryLinkState.LINKED_EMPTY,
    }
    listed = await call(world.server, "list_memories", {})
    suffixes = {
        MOSCOW: "(not linked yet: no extraction model)",
        PARIS: "(not linked yet: the model failed, 1 try)",
        LYON: "(linking)",
        GREY: "(no facts found)",
    }
    for text, suffix in suffixes.items():
        [line] = [line for line in listed.splitlines() if text in line]
        assert line.endswith(f'"{text}" {suffix}'), line
    # relink --dry-run's line: one count per state
    assert counts_line(view for _, view in memory_views(world.engine)) == (
        "Not linked yet: 3 (0 pending, 1 linking now, 0 interrupted, 1 no model, 1 failed). "
        "Linked empty: 1."
    )

    release.set()
    await until(lambda: _state(world, _source_of(world, LYON)).state is MemoryLinkState.LINKED)


async def test_list_memories_and_digest_mark_pending_memories(world: SimpleNamespace) -> None:
    await call(world.server, "remember", {"text": LYON})
    digest = await call(world.server, "digest", {})
    assert not any(line.startswith("Not linked yet:") for line in digest.splitlines())
    listed = await call(world.server, "list_memories", {})
    assert listed.endswith(f'"{LYON}"')

    await _no_model(world, MOSCOW)
    world.llm.raise_on[PARIS] = LLMError("the model is not answering")
    await call(world.server, "remember", {"text": PARIS})

    digest = await call(world.server, "digest", {})
    lines = digest.splitlines()
    assert lines[-2] == "Forgotten: 0"
    assert lines[-1] == "Not linked yet: 2 (1 no model, 1 failed, 0 interrupted)"
    listed = (await call(world.server, "list_memories", {})).splitlines()
    assert next(line for line in listed if LYON in line).endswith(f'"{LYON}"')
    assert next(line for line in listed if MOSCOW in line).endswith(
        "(not linked yet: no extraction model)"
    )


async def test_list_memories_sees_the_state_the_link_just_wrote(world: SimpleNamespace) -> None:
    stored = await world.engine.process_document(
        LYON,
        filename="memory-lyon.txt",
        forced_domain="generic",
        source_type=MEMORY_SOURCE_TYPE,
        source_fields={
            "memory_by": "alice",
            "valid_from": "2026-10-20",
            "full_text": LYON,
            "memory_link_state": MemoryLinkState.PENDING.value,
        },
        extract=False,
    )
    before = await call(world.server, "list_memories", {})
    assert before.endswith("(not linked yet)")
    await link_memory(world.engine, stored.source_id)
    after = await call(world.server, "list_memories", {})
    assert after.endswith(f'"{LYON}"')


async def test_a_store_blocked_after_the_row_keeps_the_text_and_links_later(
    world: SimpleNamespace,
) -> None:
    async def blocked(self: Engine, source_id: str, text: str) -> Any:
        raise OSError("disk I/O error")

    with patch.object(Engine, "chunk_and_index", blocked):
        text, is_error = await call_raw(world.server, "remember", {"text": LYON})
    assert not is_error
    assert text.splitlines()[-1] == FAILED_LINE
    source_id = _source_of(world, LYON)
    _, chunks = world.storage.get_chunks_by_source(source_id, page_size=1)
    assert chunks == 0
    row = _state(world, source_id)
    assert row.state is MemoryLinkState.FAILED
    assert row.full_text == LYON
    source = world.storage.get_source(source_id, world.engine.database_name)
    assert source is not None and source["error_stage"] == "indexing"
    listed = await call(world.server, "list_memories", {})
    assert listed.endswith(f'"{LYON}" (not linked yet: the model failed, 1 try)')

    assert await link_memory(world.engine, source_id) == "linked"
    _, chunks = world.storage.get_chunks_by_source(source_id, page_size=1)
    assert chunks == 1


async def test_a_store_that_writes_no_row_is_not_stored(world: SimpleNamespace) -> None:
    def no_row(self: Engine, **kwargs: Any) -> None:
        raise OSError("database is locked")

    with patch.object(Engine, "_ensure_source_row", no_row):
        text, is_error = await call_raw(world.server, "remember", {"text": LYON})
    assert is_error
    assert text == NOT_STORED
    assert memory_sources(world.storage) == []


def test_wait_seconds_defaults_to_20() -> None:
    assert EngineSettings().mcp.memory.wait_seconds == 20


async def test_a_heartbeat_that_raises_still_answers_with_the_linked_lines(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory_link, "CLAIM_HEARTBEAT_SECONDS", 0.01)
    beats = {"n": 0}

    def touch(self: Any, *args: Any, **kwargs: Any) -> bool:
        beats["n"] += 1
        raise OperationalError("UPDATE sources", {}, Exception("database is locked"))

    monkeypatch.setattr(MemoryLinkRepository, "touch", touch)
    release = asyncio.Event()
    world.llm.hold_on[LYON] = release
    answer = asyncio.create_task(call_raw(world.server, "remember", {"text": LYON}))
    await until(lambda: beats["n"] >= 1)
    release.set()
    text, is_error = await answer
    assert not is_error, text
    assert text.splitlines()[1].startswith("Linked: ")
    assert _state(world, _source_of(world, LYON)).state is MemoryLinkState.LINKED


async def test_a_mark_linked_that_raises_answers_the_failed_line(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def mark_linked(self: Any, *args: Any, **kwargs: Any) -> bool:
        raise OperationalError("UPDATE sources", {}, Exception("disk I/O error"))

    monkeypatch.setattr(MemoryLinkRepository, "mark_linked", mark_linked)
    text, is_error = await call_raw(world.server, "remember", {"text": LYON})
    assert not is_error, text
    assert text.splitlines()[1:] == [FAILED_LINE]


async def test_remember_answers_the_failed_line_when_its_link_raises(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise OperationalError("UPDATE sources", {}, Exception("disk I/O error"))

    monkeypatch.setattr("chaoscypher_core.mcp.memory_link.link_memory", broken)
    text, is_error = await call_raw(world.server, "remember", {"text": LYON})
    assert not is_error, text
    assert text.splitlines()[1:] == [FAILED_LINE]


async def test_a_memory_forgotten_between_the_two_reads_is_left_out(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await call(world.server, "remember", {"text": LYON})
    await call(world.server, "remember", {"text": GREY})
    gone = _source_of(world, GREY)
    real = MemoryLinkRepository.link_rows

    def without_gone(self: Any, *args: Any, **kwargs: Any) -> Any:
        return [r for r in real(self, *args, **kwargs) if r.id != gone]

    monkeypatch.setattr(MemoryLinkRepository, "link_rows", without_gone)
    listed = await call(world.server, "list_memories", {})
    assert LYON in listed
    assert GREY not in listed


async def test_digest_reads_the_scopes_link_rows_by_scope_not_by_ids(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One query by scope: an id list of a large scope's memories would pass SQLite's limit."""
    await call(world.server, "remember", {"text": LYON, "scope": "travel"})
    await _no_model_scoped(world, MOSCOW, "travel")
    await _no_model_scoped(world, PARIS, "salon")
    calls: list[dict[str, Any]] = []
    real = MemoryLinkRepository.link_rows

    def spy(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(MemoryLinkRepository, "link_rows", spy)
    digest = await call(world.server, "digest", {"scope": "travel"})
    assert digest.splitlines()[-1] == "Not linked yet: 1 (1 no model, 0 failed, 0 interrupted)"
    assert calls and all(c.get("source_ids") is None for c in calls)
    assert any(c.get("scope") == "travel" for c in calls)


async def _no_model_scoped(world: SimpleNamespace, text: str, scope: str) -> str:
    provider = world.engine.settings.llm.chat_provider
    world.engine.settings.llm.chat_provider = ""
    try:
        return await call(world.server, "remember", {"text": text, "scope": scope})
    finally:
        world.engine.settings.llm.chat_provider = provider


async def test_an_already_linked_memory_answers_with_its_linked_lines(
    world: SimpleNamespace,
) -> None:
    linked = await call(world.server, "remember", {"text": LYON})
    empty = await call(world.server, "remember", {"text": GREY})
    for text, answer in ((LYON, linked), (GREY, empty)):
        lines = link_status_lines(world.engine, _source_of(world, text), LinkOutcome.ALREADY_LINKED)
        assert lines == answer.splitlines()[1:]
