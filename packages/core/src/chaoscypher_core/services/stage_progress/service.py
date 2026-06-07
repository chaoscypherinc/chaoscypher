# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""StageProgress async context manager + StageName enum.

Tracks per-stage LLM progress against a parent (source in v1) using a
``StageProgressStorageProtocol``.  Best-effort: storage write failures
are logged and swallowed so the underlying work never blocks on
progress reporting.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog


if TYPE_CHECKING:
    from types import TracebackType

    from chaoscypher_core.ports.stage_progress import StageProgressStorageProtocol


logger = structlog.get_logger(__name__)


# EMA weight on each new observation. alpha=0.3 reaches ~95% of steady-state
# after ~9 ticks - right responsiveness for our 100-1000-tick stages.
EMA_ALPHA = 0.3


class StageName(StrEnum):
    """Known stage names for typed call-sites.

    ``stage_name`` columns/parameters are free-form ``str`` so future
    in-tree stages or third-party plugins can register their own
    without touching this enum.  Membership here just gives mypy a
    typo catch on the three stages we currently ship.
    """

    VISION = "vision"
    EMBEDDING = "embedding"
    MCP_EXTRACTION = "mcp_extraction"


class StageProgress:
    """Async context manager: tracks one stage's progress against a parent.

    Usage:
        async with StageProgress(
            storage=adapter, parent_id=source_id,
            stage=StageName.VISION, total=len(image_pages),
        ) as progress:
            for page in image_pages:
                await vision_service.describe_image(page)
                await progress.tick()    # auto-measures wall-clock since last tick

    Best-effort: storage write failures log a warning and return.
    The underlying work never blocks on progress reporting.
    """

    def __init__(
        self,
        *,
        storage: StageProgressStorageProtocol,
        parent_id: str,
        stage: str | StageName,
        total: int,
    ) -> None:
        """Bind storage and stage identity; counters initialise to zero."""
        self._storage = storage
        self._parent_id = parent_id
        self._stage = stage.value if isinstance(stage, StageName) else stage
        self._total = total
        self._processed = 0
        self._avg_ms: int | None = None
        self._last_tick_monotonic: float | None = None

    async def __aenter__(self) -> StageProgress:  # noqa: D105 - obvious dunder
        self._last_tick_monotonic = time.monotonic()
        await self._safe(
            self._storage.start_stage(
                parent_id=self._parent_id,
                stage_name=self._stage,
                total=self._total,
                started_at=datetime.now(UTC),
            )
        )
        return self

    async def __aexit__(  # noqa: D105 - obvious dunder
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._safe(
            self._storage.complete_stage(
                parent_id=self._parent_id,
                stage_name=self._stage,
                completed_at=datetime.now(UTC),
            )
        )

    async def tick(self, *, duration_ms: int | None = None) -> None:
        """Record one unit of work complete and update the EMA.

        ``duration_ms`` is optional — when omitted, computed from monotonic
        clock since the previous tick (or stage start). Pass explicitly
        when you already have a precise measurement.
        """
        now = time.monotonic()
        if duration_ms is None and self._last_tick_monotonic is not None:
            duration_ms = int((now - self._last_tick_monotonic) * 1000)
        self._last_tick_monotonic = now
        self._processed += 1

        if duration_ms is not None and duration_ms > 0:
            if self._avg_ms is None:
                self._avg_ms = duration_ms  # first observation IS the EMA
            else:
                self._avg_ms = int(EMA_ALPHA * duration_ms + (1 - EMA_ALPHA) * self._avg_ms)

        await self._safe(
            self._storage.tick_stage(
                parent_id=self._parent_id,
                stage_name=self._stage,
                processed=self._processed,
                avg_ms=self._avg_ms,
                last_activity=datetime.now(UTC),
            )
        )

    async def _safe(self, coro: Awaitable[None]) -> None:
        """Best-effort wrapper: log + swallow storage failures."""
        try:
            await coro
        except Exception:
            logger.warning(
                "stage_progress_storage_failed",
                parent_id=self._parent_id,
                stage_name=self._stage,
                exc_info=True,
            )
