# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``link_memory``, the one step that links a memory into the graph.

A memory is stored first (its chunk is searchable at once), then linked: claimed, chunked
again from its text if its store failed, extracted with its seed and committed, or, when
the commit stood but its follow-ups did not, finished. Every outcome is written on the row
so a later link (or a later start) can tell what is left to do.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from sqlalchemy import update
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, col, select

from chaoscypher_core.adapters.sqlite.models import GraphNodeSource, SourceEntity, SourceRow
from chaoscypher_core.adapters.sqlite.repos.memory_link import MemoryLinkRepository
from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.mcp import memory_link
from chaoscypher_core.mcp.memory import memory_handle
from chaoscypher_core.mcp.memory_link import (
    FAILURE_REASONS,
    LinkOutcome,
    MemoryLinker,
    link_db,
    link_memory,
    link_repository,
    new_claim_token,
)
from chaoscypher_core.mcp.memory_seed import memory_rows
from chaoscypher_core.models import MEMORY_SOURCE_TYPE, MemoryLinkState
from chaoscypher_core.services.graph.memory import supersession
from tests.unit.mcp.memory_world import WORLD_SCRIPT, build_world, call, store_pending, until


if TYPE_CHECKING:
    from tests.fakes.scripted_llm import Script


LYON = "Pierre Bezukhov moved to Lyon."
ANNA_PARIS = "Anna Pavlovna moved to Paris."
ANNA_LYON = "Anna Pavlovna moved to Lyon."
SCRIPT: dict[str, Script] = {
    **WORLD_SCRIPT,
    ANNA_LYON: (
        "E|Anna Pavlovna|Person|Anna|0.9|S1|A hostess\nE|Lyon|Location||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|Anna Pavlovna moved to Lyon\n",
    ),
}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch, script=SCRIPT)
    yield w
    w.engine.close()


def _row(world: SimpleNamespace, source_id: str) -> SourceRow | None:
    with Session(link_db(world.engine)) as session:
        return session.get(SourceRow, source_id)


def _labels(world: SimpleNamespace, source_id: str) -> set[str]:
    graph = world.graph
    ids = graph.member_node_ids(source_id)
    return {n.label for n in graph.get_nodes_batch(ids, include_embedding=False)}


async def test_link_memory_links_a_pending_memory(world: SimpleNamespace) -> None:
    source_id = await store_pending(world.engine, LYON)
    assert await link_memory(world.engine, source_id) is LinkOutcome.LINKED
    row = _row(world, source_id)
    assert row is not None
    assert row.memory_link_state == MemoryLinkState.LINKED.value
    assert row.memory_linked_at is not None
    assert row.memory_link_claim is None
    assert row.commit_complete
    assert {"Pierre Bezukhov", "Lyon"} <= _labels(world, source_id)


async def test_link_memory_with_no_model_is_no_model_and_not_an_attempt(
    world: SimpleNamespace,
) -> None:
    source_id = await store_pending(world.engine, LYON)
    world.engine.settings.llm.chat_provider = ""
    assert await link_memory(world.engine, source_id) is LinkOutcome.NO_MODEL
    row = _row(world, source_id)
    assert row is not None
    assert row.memory_link_state == MemoryLinkState.NO_MODEL.value
    assert row.recovery_attempts == 0
    assert row.memory_link_claim is None
    assert world.llm.call_count == 0
    hits = world.search.keyword_search("Lyon", item_type="chunk")
    assert hits


async def test_a_model_that_raises_is_failed_with_a_fixed_reason(world: SimpleNamespace) -> None:
    world.llm.raise_on[LYON] = LLMError("the model is not answering")
    source_id = await store_pending(world.engine, LYON)
    assert await link_memory(world.engine, source_id) is LinkOutcome.FAILED
    row = _row(world, source_id)
    assert row is not None
    assert row.memory_link_state == MemoryLinkState.FAILED.value
    assert row.recovery_attempts == 1
    assert row.error_stage == "extraction"
    assert row.error_message == FAILURE_REASONS["extraction"]
    assert row.memory_link_claim is None
    assert not row.commit_complete


async def test_the_error_fields_never_hold_the_text(world: SimpleNamespace) -> None:
    world.llm.raise_on[LYON] = LLMError(f"cannot parse the prompt: {LYON}")
    source_id = await store_pending(world.engine, LYON)
    assert await link_memory(world.engine, source_id) is LinkOutcome.FAILED
    row = _row(world, source_id)
    assert row is not None
    assert row.error_message == FAILURE_REASONS["extraction"]
    assert LYON not in (row.error_message or "")
    assert LYON not in (row.error_stage or "")


async def test_a_committed_memory_whose_supersession_never_ran_is_finished(
    world: SimpleNamespace,
) -> None:
    paris = await store_pending(world.engine, ANNA_PARIS, "2026-10-18")
    assert await link_memory(world.engine, paris) is LinkOutcome.LINKED
    lyon = await store_pending(world.engine, ANNA_LYON, "2026-10-20")
    real = supersession.supersede_memory_edges
    calls = {"n": 0}

    def fails_once(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("supersession failed")
        return real(*args, **kwargs)

    with patch.object(supersession, "supersede_memory_edges", fails_once):
        assert await link_memory(world.engine, lyon) is LinkOutcome.FAILED
        row = _row(world, lyon)
        assert row is not None
        assert row.error_stage == "after_commit"
        assert row.error_message == FAILURE_REASONS["after_commit"]
        assert row.commit_complete
        assert row.memory_linked_at is None
        assert await link_memory(world.engine, lyon) is LinkOutcome.LINKED

    closed = [e for e in world.graph.list_edges() if e.closed_by_source_id == lyon]
    paris_node = next(
        n for n in world.graph.list_nodes(include_embedding=False) if n.label == "Paris"
    )
    assert [e.target_node_id for e in closed] == [paris_node.id]


async def test_a_store_blocked_after_the_row_keeps_the_text_and_links_later(
    world: SimpleNamespace,
) -> None:
    world.storage.create_source(
        {
            "id": "blocked",
            "database_name": world.engine.database_name,
            "filename": "memory-blocked.txt",
            "filepath": "memory-blocked.txt",
            "status": "pending",
            "source_type": MEMORY_SOURCE_TYPE,
            "memory_by": "alice",
            "valid_from": "2026-10-20",
            "full_text": LYON,
            "memory_link_state": MemoryLinkState.PENDING.value,
        }
    )
    _, total = world.storage.get_chunks_by_source("blocked", page_size=1)
    assert total == 0
    assert await link_memory(world.engine, "blocked") is LinkOutcome.LINKED
    _, total = world.storage.get_chunks_by_source("blocked", page_size=1)
    assert total == 1
    assert {"Pierre Bezukhov", "Lyon"} <= _labels(world, "blocked")


async def test_a_stale_claim_is_taken_over_and_a_live_one_is_not(world: SimpleNamespace) -> None:
    source_id = await store_pending(world.engine, LYON)
    repo = link_repository(world.engine)
    now = datetime.now(UTC)
    assert repo.claim(source_id, "other", now=now, stale_before=now - timedelta(minutes=5))
    assert await link_memory(world.engine, source_id) is LinkOutcome.ELSEWHERE
    row = _row(world, source_id)
    assert row is not None
    assert (row.memory_link_state, row.memory_link_claim) == ("linking", "other")
    assert world.llm.call_count == 0

    with Session(link_db(world.engine)) as session:
        session.execute(
            update(SourceRow)
            .where(col(SourceRow.id) == source_id)
            .values(last_activity_at=now - timedelta(minutes=6))
        )
        session.commit()
    assert await link_memory(world.engine, source_id) is LinkOutcome.LINKED


async def test_the_heartbeat_keeps_a_claim_live(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory_link, "CLAIM_HEARTBEAT_SECONDS", 0.05)
    release = asyncio.Event()
    world.llm.hold_on[LYON] = release
    source_id = await store_pending(world.engine, LYON)
    task = asyncio.create_task(link_memory(world.engine, source_id))
    await until(lambda: bool(world.llm.prompts))
    first = _row(world, source_id)
    assert first is not None and first.last_activity_at is not None
    before = first.last_activity_at
    await until(lambda: (r := _row(world, source_id)) is not None and r.last_activity_at > before)
    release.set()
    assert await task is LinkOutcome.LINKED


async def test_forget_during_a_link_leaves_nothing(world: SimpleNamespace) -> None:
    release = asyncio.Event()
    world.llm.hold_on[LYON] = release
    source_id = await store_pending(world.engine, LYON)
    task = asyncio.create_task(link_memory(world.engine, source_id))
    await until(lambda: bool(world.llm.prompts))
    handle = memory_handle(source_id, [r["id"] for r in memory_rows(world.engine)])
    await call(world.server, "forget", {"handle": handle, "confirm": True})
    release.set()
    assert await task is LinkOutcome.GONE

    assert world.storage.get_source(source_id, world.engine.database_name) is None
    _, chunks = world.storage.get_chunks_by_source(source_id, page_size=1)
    assert chunks == 0
    with Session(link_db(world.engine)) as session:
        members = session.exec(
            select(GraphNodeSource).where(col(GraphNodeSource.source_id) == source_id)
        ).all()
        entities = session.exec(
            select(SourceEntity).where(col(SourceEntity.source_id) == source_id)
        ).all()
    assert members == []
    assert entities == []


async def test_shutdown_releases_claims(world: SimpleNamespace) -> None:
    world.llm.hold_on[LYON] = asyncio.Event()
    source_id = await store_pending(world.engine, LYON)
    linker = MemoryLinker(world.engine)
    task = linker.spawn(source_id, token=new_claim_token())
    await until(lambda: bool(world.llm.prompts))
    await linker.close()
    assert task.done()
    row = _row(world, source_id)
    assert row is not None
    assert row.memory_link_state == MemoryLinkState.PENDING.value
    assert row.memory_link_claim is None
    assert row.recovery_attempts == 0


async def test_include_empty_runs_the_model_again_on_a_linked_empty_memory(
    world: SimpleNamespace,
) -> None:
    grey = "The weather was grey."
    source_id = await store_pending(world.engine, grey)
    assert await link_memory(world.engine, source_id) is LinkOutcome.LINKED_EMPTY
    calls = world.llm.call_count
    assert await link_memory(world.engine, source_id) is LinkOutcome.ALREADY_LINKED
    assert world.llm.call_count == calls  # without include_empty it is left alone

    # the script alternates pass 1 and pass 2 per key; this key's next call is its second
    pierre = "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\n"
    world.llm.script[grey] = (pierre, pierre)
    outcome = await link_memory(world.engine, source_id, include_empty=True)
    assert world.llm.call_count > calls
    assert outcome is LinkOutcome.LINKED
    assert "Pierre Bezukhov" in _labels(world, source_id)


async def test_a_memory_linked_before_reads_already_linked(world: SimpleNamespace) -> None:
    source_id = await store_pending(world.engine, LYON)
    assert await link_memory(world.engine, source_id) is LinkOutcome.LINKED
    calls = world.llm.call_count
    for include_empty in (False, True):
        outcome = await link_memory(world.engine, source_id, include_empty=include_empty)
        assert outcome is LinkOutcome.ALREADY_LINKED
    assert world.llm.call_count == calls


async def test_a_reopened_memory_is_committed_even_when_the_storage_session_holds_its_row(
    world: SimpleNamespace,
) -> None:
    """The claim reopens on its own session; the commit must not read a stale copy of the row."""
    grey = "The weather was grey."
    source_id = await store_pending(world.engine, grey)
    assert await link_memory(world.engine, source_id) is LinkOutcome.LINKED_EMPTY
    pierre = "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\n"
    world.llm.script[grey] = (pierre, pierre)
    # the storage adapter's session holds the row (its identity map), as any earlier
    # read or write of the row through the adapter leaves it
    await call(world.server, "list_memories", {})
    cached = world.storage.session.get(SourceRow, source_id)
    assert cached is not None and cached.commit_complete

    assert await link_memory(world.engine, source_id, include_empty=True) is LinkOutcome.LINKED
    assert "Pierre Bezukhov" in _labels(world, source_id)


async def test_a_heartbeat_that_raises_does_not_fail_a_link_that_linked(
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
    source_id = await store_pending(world.engine, LYON)
    task = asyncio.create_task(link_memory(world.engine, source_id))
    await until(lambda: beats["n"] >= 2)  # it keeps beating after a failed beat
    release.set()
    assert await task is LinkOutcome.LINKED
    row = _row(world, source_id)
    assert row is not None and row.memory_link_state == MemoryLinkState.LINKED.value


async def test_a_mark_that_finds_the_claim_taken_over_is_elsewhere_not_gone(
    world: SimpleNamespace,
) -> None:
    release = asyncio.Event()
    world.llm.hold_on[LYON] = release
    source_id = await store_pending(world.engine, LYON)
    task = asyncio.create_task(link_memory(world.engine, source_id))
    await until(lambda: bool(world.llm.prompts))
    with Session(link_db(world.engine)) as session:  # another process took the claim over
        session.execute(
            update(SourceRow).where(col(SourceRow.id) == source_id).values(memory_link_claim="b")
        )
        session.commit()
    release.set()
    assert await task is LinkOutcome.ELSEWHERE
    row = _row(world, source_id)
    assert row is not None and row.memory_link_claim == "b"


async def test_a_cancel_during_the_claim_gives_the_claim_back(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = await store_pending(world.engine, LYON)
    real = MemoryLinkRepository.claim
    started = threading.Event()
    finished = threading.Event()

    def slow_claim(self: Any, *args: Any, **kwargs: Any) -> bool:
        started.set()
        time.sleep(0.3)
        try:
            return real(self, *args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(MemoryLinkRepository, "claim", slow_claim)
    task = asyncio.create_task(link_memory(world.engine, source_id))
    await until(started.is_set)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await until(finished.is_set)  # the claim's thread ran to its end
    await asyncio.sleep(0.05)
    row = _row(world, source_id)
    assert row is not None
    assert row.memory_link_claim is None
    assert row.memory_link_state == MemoryLinkState.PENDING.value


async def test_a_claim_that_raises_writes_no_failure_reason(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = await store_pending(world.engine, LYON)

    def claim(self: Any, *args: Any, **kwargs: Any) -> bool:
        raise OperationalError("UPDATE sources", {}, Exception("database is locked"))

    monkeypatch.setattr(MemoryLinkRepository, "claim", claim)
    assert await link_memory(world.engine, source_id) is LinkOutcome.FAILED
    row = _row(world, source_id)
    assert row is not None
    assert row.memory_link_state == MemoryLinkState.PENDING.value
    assert row.error_stage is None and row.recovery_attempts == 0


async def test_a_cancel_inside_the_commit_rolls_it_back_and_releases_at_once(
    world: SimpleNamespace,
) -> None:
    """A cancel during one of the commit's awaits ends its write transaction, so release is quick."""
    from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService

    source_id = await store_pending(world.engine, LYON)
    inside = asyncio.Event()
    never = asyncio.Event()
    real = SourceCommitService._update_chunk_status

    async def held(self: Any, file_id: str, status: str) -> None:
        inside.set()  # the nodes and edges are written, the transaction is open
        await never.wait()
        await real(self, file_id, status)

    with patch.object(SourceCommitService, "_update_chunk_status", held):
        task = asyncio.create_task(link_memory(world.engine, source_id))
        await asyncio.wait_for(inside.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)

    row = _row(world, source_id)
    assert row is not None
    assert row.memory_link_state == MemoryLinkState.PENDING.value
    assert row.memory_link_claim is None
    assert not row.commit_complete
    with Session(link_db(world.engine)) as session:
        members = session.exec(
            select(GraphNodeSource).where(col(GraphNodeSource.source_id) == source_id)
        ).all()
    assert members == []


async def test_an_sdk_memory_reads_pending_and_links(world: SimpleNamespace) -> None:
    """process_document(source_type="memory") with no link fields still gives the row a state."""
    stored = await world.engine.process_document(
        LYON, filename="note.txt", source_type=MEMORY_SOURCE_TYPE, extract=False
    )
    row = _row(world, stored.source_id)
    assert row is not None and row.memory_link_state == MemoryLinkState.PENDING.value
    assert await link_memory(world.engine, stored.source_id) is LinkOutcome.LINKED


async def test_a_store_killed_before_indexing_completed_is_indexed_again(
    world: SimpleNamespace,
) -> None:
    """Chunks written, complete_indexing never ran (a kill between them): the link indexes it."""
    source_id = await store_pending(world.engine, LYON)
    with Session(link_db(world.engine)) as session:
        session.execute(
            update(SourceRow)
            .where(col(SourceRow.id) == source_id)
            .values(indexing_complete=False, status="indexing")
        )
        session.commit()
    assert await link_memory(world.engine, source_id) is LinkOutcome.LINKED
    row = _row(world, source_id)
    assert row is not None and row.indexing_complete
    _, chunks = world.storage.get_chunks_by_source(source_id, page_size=10)
    assert chunks == 1
