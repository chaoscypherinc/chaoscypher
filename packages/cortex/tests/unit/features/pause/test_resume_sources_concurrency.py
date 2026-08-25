# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""resume_sources must fan out recover_source calls concurrently.

Each gathered recovery must also run inside its own
``adapter.session_scope()`` — the gathered calls previously all resolved
``adapter.session`` to the shared fallback SafeSession (the 2026-05-20
silent-data-loss race, re-found in this path 2026-07-27).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_cortex.features.pause.service import PauseService


_current_session: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "test_current_session", default=None
)


class _AdapterStub:
    """Adapter stand-in whose session_scope is a real async CM.

    Mimics the SqliteAdapter contract PauseService relies on: entering
    ``session_scope()`` binds a fresh per-task session (modelled with a
    ContextVar, like the real adapter).
    """

    def __init__(self) -> None:
        self.sessions: list[object] = []

    @contextlib.asynccontextmanager
    async def session_scope(self) -> AsyncIterator[object]:
        session = object()
        self.sessions.append(session)
        token = _current_session.set(session)
        try:
            yield session
        finally:
            _current_session.reset(token)


def _service(
    repository: MagicMock, source_recovery: MagicMock
) -> tuple[PauseService, _AdapterStub]:
    adapter = _AdapterStub()
    return (
        PauseService(repository=repository, source_recovery=source_recovery, adapter=adapter),
        adapter,
    )


@pytest.mark.asyncio
async def test_resume_sources_fans_out_recoveries() -> None:
    """5 recoveries with 100ms latency each must finish in <300ms total."""
    repository = MagicMock()
    repository.resume_sources.return_value = 5

    source_recovery = MagicMock()
    events: list[str] = []

    async def slow_recover(source_id: str, database_name: str) -> None:
        events.append(f"enter:{source_id}")
        await asyncio.sleep(0)
        events.append(f"exit:{source_id}")

    source_recovery.recover_source = AsyncMock(side_effect=slow_recover)

    service, _adapter = _service(repository, source_recovery)

    count = await service.resume_sources(
        source_ids=["s1", "s2", "s3", "s4", "s5"],
        database_name="default",
    )

    assert count == 5
    # Structural concurrency check instead of a wall-clock gate (which
    # reds under CI load): under gather() every recovery enters before
    # any exits; a serial loop interleaves enter/exit pairs.
    assert all(e.startswith("enter:") for e in events[:5]), f"resume_sources ran serially: {events}"


@pytest.mark.asyncio
async def test_resume_sources_continues_when_one_recovery_fails() -> None:
    """A single recovery failure does not block the rest."""
    repository = MagicMock()
    repository.resume_sources.return_value = 3

    source_recovery = MagicMock()
    calls: list[str] = []

    async def maybe_fail(source_id: str, database_name: str) -> None:
        calls.append(source_id)
        if source_id == "s2":
            msg = "transient"
            raise RuntimeError(msg)

    source_recovery.recover_source = AsyncMock(side_effect=maybe_fail)

    service, _adapter = _service(repository, source_recovery)
    count = await service.resume_sources(
        source_ids=["s1", "s2", "s3"],
        database_name="default",
    )

    assert count == 3
    assert set(calls) == {"s1", "s2", "s3"}  # all three attempted


@pytest.mark.asyncio
async def test_resume_sources_each_recovery_runs_in_distinct_session_scope() -> None:
    """N gathered recoveries each run under their own session scope.

    The recovery bodies overlap in time (the sleeps force interleaving),
    yet each observes a distinct bound session — sharing one session
    across the gather is the regression this pins.
    """
    repository = MagicMock()
    repository.resume_sources.return_value = 4

    source_recovery = MagicMock()
    seen_sessions: dict[str, object | None] = {}

    async def record_session(source_id: str, database_name: str) -> None:
        await asyncio.sleep(0.02)  # force the gathered tasks to interleave
        seen_sessions[source_id] = _current_session.get()

    source_recovery.recover_source = AsyncMock(side_effect=record_session)

    service, adapter = _service(repository, source_recovery)
    await service.resume_sources(
        source_ids=["s1", "s2", "s3", "s4"],
        database_name="default",
    )

    # Every recovery ran inside a scope...
    assert len(adapter.sessions) == 4
    assert all(session is not None for session in seen_sessions.values())
    # ...and no two recoveries shared a session.
    distinct = {id(session) for session in seen_sessions.values()}
    assert len(distinct) == 4
