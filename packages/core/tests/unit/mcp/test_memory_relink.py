# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The relink: a write-mode server links the memories not linked yet, after the handshake.

``relink_pending`` sweeps the memories oldest first, one at a time, through ``link_memory``:
the start-up sweep honours each failed memory's backoff and does nothing with no extraction
model configured; ``chaoscypher memory relink`` runs the same sweep ignoring the backoff.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import mcp.types
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy import update
from sqlmodel import Session, col

from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.mcp.memory_link import (
    BACKOFF_CAP_SECONDS,
    LinkOutcome,
    LinkView,
    link_db,
    link_memory,
    link_repository,
)
from chaoscypher_core.mcp.memory_relink import (
    RelinkReport,
    backoff_seconds,
    relink_pending,
)
from chaoscypher_core.models import MemoryLinkState
from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
    KNOWN_GRAPH_HEADER,
)
from tests.unit.mcp.memory_world import (
    build_world,
    call,
    memory_sources,
    settings,
    store_pending,
    until,
)


LYON = "Pierre Bezukhov moved to Lyon."
FRIEND = "Pierre Bezukhov is a friend of Anna Pavlovna."
GREY = "The weather was grey."
INITIALIZED = mcp.types.InitializedNotification(method="notifications/initialized")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch)
    yield w
    w.engine.close()


def _row(world: SimpleNamespace, source_id: str) -> Any:
    return link_repository(world.engine).read_link_row(source_id)


def _set_last_activity(world: SimpleNamespace, source_id: str, ago: timedelta) -> None:
    with Session(link_db(world.engine)) as session:
        session.execute(
            update(SourceRow)
            .where(col(SourceRow.id) == source_id)
            .values(last_activity_at=datetime.now(UTC) - ago)
        )
        session.commit()


def _source_of(world: SimpleNamespace, text: str) -> str:
    for row in memory_sources(world.storage):
        if _row(world, row["id"]).full_text == text:
            return str(row["id"])
    raise AssertionError(text)


async def _start_sweep(world: SimpleNamespace) -> RelinkReport:
    """Fire the handshake's end, as a client's ``initialized`` notification does, and await the sweep."""
    await world.server.notification_handlers[mcp.types.InitializedNotification](INITIALIZED)
    task = world.linker.sweep_task
    assert task is not None
    return await task


async def _failed(world: SimpleNamespace, text: str) -> str:
    """A memory whose link failed once just now (the model raised)."""
    world.llm.raise_on[text] = LLMError("the model is not answering")
    await call(world.server, "remember", {"text": text})
    del world.llm.raise_on[text]
    source_id = _source_of(world, text)
    row = _row(world, source_id)
    assert (row.state, row.recovery_attempts) == (MemoryLinkState.FAILED, 1)
    return source_id


@pytest.mark.parametrize(
    ("attempts", "seconds"),
    [(0, 60), (1, 120), (2, 240), (5, 1920), (10, 61440), (11, 86400), (40, 86400)],
)
def test_the_backoff_doubles_from_two_minutes_up_to_a_day(attempts: int, seconds: float) -> None:
    assert backoff_seconds(attempts) == seconds
    assert BACKOFF_CAP_SECONDS == 86400


async def test_no_model_memories_link_once_a_model_is_set_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_model = settings()
    no_model.llm.chat_provider = ""
    first = build_world(tmp_path, monkeypatch, s=no_model)
    await call(first.server, "remember", {"text": LYON, "valid_from": "2026-10-19"})
    await call(first.server, "remember", {"text": FRIEND, "valid_from": "2026-10-20"})
    ids = [_source_of(first, LYON), _source_of(first, FRIEND)]
    rows = [_row(first, i) for i in ids]
    assert [(r.state, r.recovery_attempts) for r in rows] == [(MemoryLinkState.NO_MODEL, 0)] * 2
    assert first.llm.call_count == 0
    first.engine.close()

    world = build_world(tmp_path, monkeypatch)
    try:
        report = await _start_sweep(world)
        assert (report.linked, report.left) == (2, 0)
        assert [_row(world, i).state for i in ids] == [MemoryLinkState.LINKED] * 2
        # the first memory, linked first (oldest first), made the Pierre node the second is offered
        [friend_prompt, *_] = [p for p in world.llm.prompts if FRIEND in p]
        assert f"{KNOWN_GRAPH_HEADER}\n- Pierre Bezukhov (Person)" in friend_prompt
    finally:
        world.engine.close()


async def test_backoff_skips_a_memory_that_just_failed_and_the_command_does_not(
    world: SimpleNamespace,
) -> None:
    source_id = await _failed(world, LYON)
    _set_last_activity(world, source_id, timedelta(seconds=30))
    calls = world.llm.call_count

    swept = await relink_pending(world.engine, honour_backoff=True)
    assert swept == RelinkReport(left=1)
    assert world.llm.call_count == calls
    assert (_row(world, source_id).state, _row(world, source_id).recovery_attempts) == (
        MemoryLinkState.FAILED,
        1,
    )

    commanded = await relink_pending(world.engine, honour_backoff=False)
    assert commanded == RelinkReport(linked=1, left=0)
    assert _row(world, source_id).state is MemoryLinkState.LINKED


async def test_the_sweep_takes_a_failed_memory_once_its_backoff_has_passed(
    world: SimpleNamespace,
) -> None:
    source_id = await _failed(world, LYON)
    _set_last_activity(world, source_id, timedelta(seconds=121))  # 1 attempt: 120 s
    assert await relink_pending(world.engine, honour_backoff=True) == RelinkReport(linked=1)


async def test_the_sweep_without_a_model_touches_nothing(world: SimpleNamespace) -> None:
    pending = await store_pending(world.engine, LYON)
    failed = await _failed(world, FRIEND)
    _set_last_activity(world, failed, timedelta(days=2))
    before = {i: _row(world, i) for i in (pending, failed)}
    world.engine.settings.llm.chat_provider = ""

    report = await relink_pending(world.engine, honour_backoff=True)
    assert report == RelinkReport(left=2)
    assert {i: _row(world, i) for i in (pending, failed)} == before


async def test_relink_include_empty_retries_linked_empty_memories(world: SimpleNamespace) -> None:
    source_id = await store_pending(world.engine, GREY)
    assert await relink_pending(world.engine, honour_backoff=True) == RelinkReport(linked_empty=1)
    # the model now finds a node in it (the script alternates passes; its next call is pass 2)
    pierre = "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\n"
    world.llm.script[GREY] = (pierre, pierre)
    calls = world.llm.call_count

    assert await relink_pending(world.engine, honour_backoff=True) == RelinkReport()
    assert await relink_pending(world.engine, honour_backoff=False) == RelinkReport()
    assert world.llm.call_count == calls

    report = await relink_pending(world.engine, honour_backoff=False, include_empty=True)
    assert report == RelinkReport(linked=1)
    assert _row(world, source_id).state is MemoryLinkState.LINKED
    graph = world.graph
    nodes = graph.get_nodes_batch(graph.member_node_ids(source_id), include_embedding=False)
    assert "Pierre Bezukhov" in {n.label for n in nodes}


async def test_the_sweep_takes_over_a_stale_claim_and_leaves_a_live_one(
    world: SimpleNamespace,
) -> None:
    stale = await store_pending(world.engine, LYON, "2026-10-19")
    live = await store_pending(world.engine, FRIEND, "2026-10-20")
    repo = link_repository(world.engine)
    now = datetime.now(UTC)
    for source_id in (stale, live):
        assert repo.claim(source_id, "other", now=now, stale_before=now - timedelta(minutes=5))
    _set_last_activity(world, stale, timedelta(minutes=6))

    report = await relink_pending(world.engine, honour_backoff=True)
    assert report == RelinkReport(linked=1, elsewhere=1, left=1)
    assert _row(world, stale).state is MemoryLinkState.LINKED
    assert (_row(world, live).state, _row(world, live).claim) == (MemoryLinkState.LINKING, "other")


async def test_a_memory_another_link_finished_first_counts_as_elsewhere(
    world: SimpleNamespace,
) -> None:
    release = asyncio.Event()
    world.llm.hold_on[LYON] = release
    first = await store_pending(world.engine, LYON, "2026-10-19")
    second = await store_pending(world.engine, FRIEND, "2026-10-20")
    sweep = asyncio.create_task(relink_pending(world.engine, honour_backoff=True))
    await until(lambda: any(LYON in p for p in world.llm.prompts))
    # while the sweep holds the first, another link takes and finishes the second
    assert await link_memory(world.engine, second) is LinkOutcome.LINKED
    release.set()
    assert await sweep == RelinkReport(linked=1, elsewhere=1)
    assert _row(world, first).state is MemoryLinkState.LINKED


async def test_closing_the_linker_stops_the_sweep_and_gives_its_claim_back(
    world: SimpleNamespace,
) -> None:
    world.llm.hold_on[LYON] = asyncio.Event()
    held = await store_pending(world.engine, LYON, "2026-10-19")
    later = await store_pending(world.engine, FRIEND, "2026-10-20")
    await world.server.notification_handlers[mcp.types.InitializedNotification](INITIALIZED)
    await until(lambda: any(LYON in p for p in world.llm.prompts))

    await world.linker.close()
    task = world.linker.sweep_task
    assert task is not None and task.cancelled()
    for source_id in (held, later):
        row = _row(world, source_id)
        assert (row.state, row.claim, row.recovery_attempts) == (MemoryLinkState.PENDING, None, 0)


async def test_a_read_mode_server_starts_no_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path, monkeypatch, s=settings(mode="read"))
    try:
        assert mcp.types.InitializedNotification not in world.server.notification_handlers
    finally:
        world.engine.close()


async def test_relink_on_start_off_starts_no_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = settings()
    s.mcp.memory.relink_on_start = False
    world = build_world(tmp_path, monkeypatch, s=s)
    try:
        assert mcp.types.InitializedNotification not in world.server.notification_handlers
    finally:
        world.engine.close()


async def test_the_sweep_starts_once_per_linker(world: SimpleNamespace) -> None:
    await store_pending(world.engine, LYON)
    handler = world.server.notification_handlers[mcp.types.InitializedNotification]
    await handler(INITIALIZED)
    task = world.linker.sweep_task
    await handler(INITIALIZED)
    assert world.linker.sweep_task is task
    assert world.linker.start_sweep() is None
    assert task is not None
    assert await task == RelinkReport(linked=1)


async def test_remember_during_a_sweep_answers_within_its_wait(world: SimpleNamespace) -> None:
    texts = [f"Boris Drubetskoy walked through the garden on day {day}." for day in range(10)]
    for day, text in enumerate(texts):
        await store_pending(world.engine, text, f"2026-10-{day + 10:02d}")
    world.engine.settings.mcp.memory.wait_seconds = 5
    sweep = world.linker.start_sweep()
    assert sweep is not None
    await until(lambda: world.llm.call_count > 0)

    started = time.monotonic()
    answer = await call(world.server, "remember", {"text": LYON})
    assert time.monotonic() - started < 7
    assert answer.splitlines()[0].endswith(f'"{LYON}"')

    report = await sweep
    assert report.linked + report.linked_empty == 10
    assert report.failed == 0
    views = [r.state for r in link_repository(world.engine).link_rows(world.engine.database_name)]
    assert LinkView.FAILED.value not in [v.value for v in views if v is not None]
    assert len(views) == 11


async def test_with_a_limit_every_live_claim_still_counts_as_elsewhere(
    world: SimpleNamespace,
) -> None:
    first = await store_pending(world.engine, LYON, "2026-10-18")
    await store_pending(world.engine, GREY, "2026-10-19")
    held = await store_pending(world.engine, FRIEND, "2026-10-20")
    now = datetime.now(UTC)
    assert link_repository(world.engine).claim(
        held, "other", now=now, stale_before=now - timedelta(minutes=5)
    )
    report = await relink_pending(world.engine, honour_backoff=False, limit=1)
    assert report == RelinkReport(linked=1, elsewhere=1, left=2)
    assert _row(world, first).state is MemoryLinkState.LINKED


async def test_include_empty_with_no_model_leaves_linked_empty_memories_alone(
    world: SimpleNamespace,
) -> None:
    source_id = await store_pending(world.engine, GREY)
    assert await relink_pending(world.engine, honour_backoff=False) == RelinkReport(linked_empty=1)
    world.engine.settings.llm.chat_provider = ""
    report = await relink_pending(world.engine, honour_backoff=False, include_empty=True)
    assert report == RelinkReport()
    assert _row(world, source_id).state is MemoryLinkState.LINKED_EMPTY


async def test_a_real_handshake_starts_the_sweep(world: SimpleNamespace) -> None:
    """A client's initialize, over the MCP protocol, ends with the notification that starts it."""
    source_id = await store_pending(world.engine, LYON)
    async with create_connected_server_and_client_session(world.server) as client:
        await until(lambda: world.linker.sweep_task is not None)
        task = world.linker.sweep_task
        assert task is not None
        assert await task == RelinkReport(linked=1)
        listed = await client.call_tool("list_memories", {})
    assert _row(world, source_id).state is MemoryLinkState.LINKED
    assert not listed.isError
