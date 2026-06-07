# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source liveness heartbeat — keeps ``last_activity_at`` fresh during long handlers.

The source-recovery reconciler (`SourceRecovery._is_recently_active`)
debounces re-dispatch by checking whether a source's
``last_activity_at`` column was bumped within the configured stall
threshold. Handlers that sit on a source for longer than that threshold
without heartbeating look "stalled" and trigger a duplicate dispatch —
which races with the still-running handler and causes data corruption
(stale chunk ID warnings, duplicate graph writes, etc.).

This module provides a single asyncio context manager
(``source_heartbeat``) that handlers wrap around their work to opt
into automatic heartbeating. Future handlers should use this rather
than scattering ``adapter.update_source_last_activity`` calls inline:

    async with source_heartbeat(
        adapter=adapter,
        source_id=file_id,
        database_name=database_name,
    ):
        await long_running_work(...)

Default interval is 30s — comfortably under the default 600s stall
threshold so even a handler that runs for hours stays live. The first
heartbeat fires immediately on entry; the last fires on exit. A
background asyncio task fires the rest. Failures inside the heartbeat
are logged at WARNING but never raised — heartbeat is best-effort and
must not crash the wrapped work.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import structlog


if TYPE_CHECKING:
    from types import TracebackType


logger = structlog.get_logger(__name__)

#: Default seconds between automatic heartbeats. Sized well below the
#: configured ``SourceRecoverySettings.stalled_threshold_seconds``
#: (default 600s) so that a handler whose only async yield is a long-
#: running provider call still beats many times before the reconciler
#: would treat it as stalled.
DEFAULT_HEARTBEAT_INTERVAL_SECONDS: float = 30.0


class _AdapterWithHeartbeat(Protocol):
    """Minimal adapter surface required by ``source_heartbeat``.

    Any storage adapter (SQLite, in-memory test fakes, future backends)
    that exposes ``update_source_last_activity`` satisfies this
    protocol. Kept structural so the heartbeat does not pull in the
    full storage protocol surface as a dependency.
    """

    def update_source_last_activity(
        self,
        *,
        source_id: str,
        database_name: str,
        at_time: datetime,
    ) -> None:
        """Bump the source's last_activity_at timestamp."""


class SourceHeartbeat:
    """Async context manager that periodically bumps ``last_activity_at``.

    Use via the ``source_heartbeat`` factory function rather than
    constructing directly — the factory keeps the call site readable
    and gives us room to swap implementations later (e.g., for tests).

    Lifecycle:
        - ``__aenter__``: emit one immediate heartbeat, spawn the
          background poll task.
        - background loop: sleep ``interval_seconds``, emit heartbeat,
          repeat until cancelled.
        - ``__aexit__``: cancel the background task, emit one final
          heartbeat so the next reconciler tick definitively sees a
          fresh timestamp.

    Heartbeat failures (DB errors, missing source row, etc.) are
    logged but suppressed — the heartbeat is a liveness signal, not a
    transactional operation. A dropped heartbeat just risks one
    spurious recovery, which is preferable to crashing the handler.
    """

    def __init__(
        self,
        *,
        adapter: _AdapterWithHeartbeat,
        source_id: str,
        database_name: str,
        interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        """Initialize the heartbeat.

        Args:
            adapter: Storage adapter implementing
                ``update_source_last_activity``.
            source_id: Source whose ``last_activity_at`` to bump.
            database_name: Database scope (multi-DB isolation).
            interval_seconds: Seconds between automatic heartbeats.
                Defaults to ``DEFAULT_HEARTBEAT_INTERVAL_SECONDS``.
                Override only for tests that need faster cadence.
        """
        self._adapter = adapter
        self._source_id = source_id
        self._database_name = database_name
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> SourceHeartbeat:
        """Emit the first heartbeat and start the background loop."""
        self._beat()
        self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Cancel the background loop and emit a final heartbeat."""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        # Final beat — covers the case where the wrapped work just
        # completed and the reconciler scans the database in the next
        # millisecond. Without this, exit-time race could still trigger
        # a duplicate dispatch before the status transition commits.
        self._beat()

    def _beat(self) -> None:
        """Emit one heartbeat, swallowing any error."""
        try:
            self._adapter.update_source_last_activity(
                source_id=self._source_id,
                database_name=self._database_name,
                at_time=datetime.now(UTC),
            )
        except Exception as exc:
            logger.warning(
                "source_heartbeat_failed",
                source_id=self._source_id,
                database_name=self._database_name,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    async def _loop(self) -> None:
        """Background loop: sleep, beat, repeat.

        Uses a deadline-based schedule so beats fire at the nominal
        interval from the START of each beat, not from its end. A slow
        ``_beat()`` (e.g. a sluggish DB write) shortens the subsequent
        sleep rather than delaying it by a full extra interval. If a beat
        takes longer than ``_interval_seconds``, the next beat fires
        immediately so no accumulated delay builds up over time.
        """
        try:
            next_beat_at = time.monotonic() + self._interval_seconds
            while True:
                now = time.monotonic()
                sleep_for = max(0.0, next_beat_at - now)
                await asyncio.sleep(sleep_for)
                self._beat()
                # Advance deadline by one interval from when this beat
                # was SCHEDULED (not when it finished), so overruns don't
                # accumulate into drift.
                next_beat_at += self._interval_seconds
                # If we're already behind the next deadline (e.g. beat
                # took > interval), advance to the next future slot.
                next_beat_at = max(next_beat_at, time.monotonic())
        except asyncio.CancelledError:
            return


def source_heartbeat(
    *,
    adapter: _AdapterWithHeartbeat,
    source_id: str,
    database_name: str,
    interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
) -> SourceHeartbeat:
    """Construct a ``SourceHeartbeat`` async context manager.

    See module docstring for usage. This factory exists so handler call
    sites read as a verb (``async with source_heartbeat(...)``) rather
    than a class instantiation.
    """
    return SourceHeartbeat(
        adapter=adapter,
        source_id=source_id,
        database_name=database_name,
        interval_seconds=interval_seconds,
    )


__all__ = [
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    "SourceHeartbeat",
    "source_heartbeat",
]
