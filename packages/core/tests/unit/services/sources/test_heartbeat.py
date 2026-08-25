# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the source-liveness heartbeat context manager."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.heartbeat import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    SourceHeartbeat,
    source_heartbeat,
)


@pytest.mark.asyncio
async def test_heartbeat_emits_on_entry_and_exit() -> None:
    """The CM emits at least two heartbeats: one on enter, one on exit.

    A short workload that finishes before the background loop fires
    still gets both bookend beats so the reconciler sees fresh
    activity at the start and right after completion.
    """
    adapter = MagicMock()

    async with source_heartbeat(
        adapter=adapter,
        source_id="src-1",
        database_name="default",
        interval_seconds=60.0,  # long enough that the loop never fires
    ):
        pass  # workload completes immediately

    # First call: __aenter__ beat. Second call: __aexit__ beat.
    assert adapter.update_source_last_activity.call_count == 2
    for call in adapter.update_source_last_activity.call_args_list:
        assert call.kwargs["source_id"] == "src-1"
        assert call.kwargs["database_name"] == "default"


@pytest.mark.asyncio
async def test_heartbeat_background_loop_fires_during_long_work() -> None:
    """Long-running work gets periodic heartbeats from the background loop.

    Configures a 50ms interval, sleeps 200ms, expects roughly 4 mid-work
    beats plus the entry and exit beats.
    """
    adapter = MagicMock()
    beats_seen = asyncio.Event()

    def _count_beat(**_kwargs: object) -> None:
        # Entry beat + 3 background beats — set the barrier once reached.
        if adapter.update_source_last_activity.call_count >= 4:
            beats_seen.set()

    adapter.update_source_last_activity.side_effect = _count_beat

    async with source_heartbeat(
        adapter=adapter,
        source_id="src-2",
        database_name="default",
        interval_seconds=0.01,
    ):
        # Deterministic barrier: hold the workload open until the loop has
        # demonstrably fired, instead of counting beats in a fixed window
        # (which reds under CI load). Bounded at 5s.
        await asyncio.wait_for(beats_seen.wait(), timeout=5)

    # Entry + at least 3 background beats observed (exit beat follows).
    assert adapter.update_source_last_activity.call_count >= 4


@pytest.mark.asyncio
async def test_heartbeat_failure_in_adapter_does_not_crash_workload() -> None:
    """A heartbeat that raises is logged but never propagates.

    The heartbeat is best-effort liveness — if the adapter momentarily
    fails (DB lock, etc.), the wrapped work must still complete.
    """
    adapter = MagicMock()
    adapter.update_source_last_activity.side_effect = RuntimeError("boom")

    # Should NOT raise.
    async with source_heartbeat(
        adapter=adapter,
        source_id="src-3",
        database_name="default",
        interval_seconds=60.0,
    ):
        pass

    # We tried to beat at least twice (enter + exit).
    assert adapter.update_source_last_activity.call_count >= 2


@pytest.mark.asyncio
async def test_heartbeat_propagates_exception_from_workload() -> None:
    """An exception from the wrapped workload still escapes the CM.

    The heartbeat must not swallow handler errors — those are
    information the queue worker needs to mark the task failed.
    """
    adapter = MagicMock()

    with pytest.raises(ValueError, match="workload failed"):
        async with source_heartbeat(
            adapter=adapter,
            source_id="src-4",
            database_name="default",
            interval_seconds=60.0,
        ):
            raise ValueError("workload failed")

    # Even on failure, both bookend beats fire.
    assert adapter.update_source_last_activity.call_count == 2


def test_default_interval_is_under_default_stall_threshold() -> None:
    """Default heartbeat interval must be well under the recovery stall threshold.

    Recovery's default ``stalled_threshold_seconds`` is 120s. The
    default heartbeat interval must be small enough that several beats
    happen within one threshold window so a single dropped beat never
    trips a false stall.
    """
    # The default stall threshold lives in cortex (SourceRecoverySettings)
    # but the design contract is: heartbeat << stall threshold. 30s ≪ 120s
    # gives 4 beats per window — adequate margin.
    assert DEFAULT_HEARTBEAT_INTERVAL_SECONDS <= 30.0


def test_factory_returns_source_heartbeat_instance() -> None:
    """``source_heartbeat()`` is a thin factory over ``SourceHeartbeat``."""
    adapter = MagicMock()
    cm = source_heartbeat(
        adapter=adapter,
        source_id="src-5",
        database_name="default",
    )
    assert isinstance(cm, SourceHeartbeat)


@pytest.mark.asyncio
async def test_beats_never_run_on_the_handler_scoped_session(tmp_path) -> None:
    """Every beat must use its own session, never the wrapped handler's.

    The finalize handler runs inside ``adapter.session_scope()`` and
    offloads its transaction body via ``asyncio.to_thread``; the beat
    loop is an ``asyncio.create_task``. Both COPY the handler's context,
    so before the isolated-beat fix a background beat resolved
    ``adapter.session`` to the handler's own ``SafeSession`` and could
    execute on it from a second thread mid-flush (``Session is already
    flushing`` / ``This transaction is closed`` — killing the finalize
    after all extraction work was done). This pins the fix: with a real
    ``SqliteAdapter`` scope active, no beat may touch the scoped session.
    """
    from pathlib import Path

    from sqlmodel import SQLModel

    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.engine import get_engine

    db_path = Path(tmp_path) / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    inner = SqliteAdapter(str(db_path), database_name="default")
    inner.connect()

    class _ProbeAdapter:
        """Delegates to the real adapter, recording the session each beat uses."""

        def __init__(self) -> None:
            self.beat_sessions: list[object] = []

        def update_source_last_activity(self, **kwargs: object) -> None:
            self.beat_sessions.append(inner.session)
            inner.update_source_last_activity(**kwargs)  # type: ignore[arg-type]

        def session_scope(self):
            return inner.session_scope()

    probe = _ProbeAdapter()
    try:
        async with inner.session_scope() as handler_session:
            async with source_heartbeat(
                adapter=probe,
                source_id="src-isolated",
                database_name="default",
                interval_seconds=0.02,
            ):
                await asyncio.sleep(0.07)
    finally:
        inner.disconnect()

    # Entry beat + at least one background beat + exit beat.
    assert len(probe.beat_sessions) >= 3
    assert all(s is not handler_session for s in probe.beat_sessions), (
        "a beat executed on the handler's scoped session — the cross-thread "
        "shared-session race the isolated beat exists to prevent"
    )
