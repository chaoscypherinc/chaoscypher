# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pause/resume orchestration service.

Wraps PauseRepository with three behaviors the repository layer
doesn't own:

1. Resume-triggered immediate recovery — calling recover_source on
   SourceRecovery so the user sees the pipeline kick back into motion
   within a second instead of waiting up to a full reconciler interval.
2. Observability logs — structured structlog events on every
   pause/resume action so the operational timeline is visible in
   production dashboards.
3. Bulk resume's per-source error isolation — one failing
   recover_source call should not abort the bulk operation.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_cortex.features.pause.repository import PauseRepository


logger = structlog.get_logger(__name__)


class PauseService:
    """Pause and resume sources and the whole system.

    On resume, calls SourceRecovery.recover_source for instant
    feedback — users shouldn't have to wait for the next periodic
    pass.
    """

    def __init__(
        self,
        *,
        repository: PauseRepository,
        source_recovery: Any,
    ) -> None:
        """Initialize the service.

        Args:
            repository: PauseRepository for adapter delegation.
            source_recovery: A SourceRecovery-compatible object with
                an async ``recover_source(source_id, database_name)``
                method. Tests typically use AsyncMock here.
        """
        self.repository = repository
        self.source_recovery = source_recovery

    # --- Per-source --------------------------------------------------------

    async def pause_source(
        self,
        *,
        source_id: str,
        database_name: str,
        reason: str | None,
    ) -> None:
        """Pause a single source. No recovery side-effects."""
        self.repository.pause_source(
            source_id=source_id,
            database_name=database_name,
            reason=reason,
        )
        logger.info(
            "source_paused",
            source_id=source_id,
            scope="source",
            reason=reason,
        )

    async def resume_source(
        self,
        *,
        source_id: str,
        database_name: str,
    ) -> None:
        """Resume a single source and kick off immediate recovery."""
        self.repository.resume_source(source_id=source_id, database_name=database_name)
        await self.source_recovery.recover_source(source_id=source_id, database_name=database_name)
        logger.info(
            "source_resumed",
            source_id=source_id,
            scope="source",
            recovery_triggered=True,
        )

    async def pause_sources(
        self,
        *,
        source_ids: list[str],
        database_name: str,
        reason: str | None,
    ) -> int:
        """Bulk-pause. Returns the number of rows updated."""
        count = self.repository.pause_sources(
            source_ids=source_ids,
            database_name=database_name,
            reason=reason,
        )
        logger.info("sources_paused_bulk", count=count, reason=reason)
        return count

    async def resume_sources(
        self,
        *,
        source_ids: list[str],
        database_name: str,
    ) -> int:
        """Bulk-resume. Fires recover_source for each requested source.

        A failure on any one recover_source call is logged and
        swallowed so the bulk resume completes for the remaining
        sources — partial progress is preferable to zero progress.
        """
        count = self.repository.resume_sources(source_ids=source_ids, database_name=database_name)

        async def _recover_one(sid: str) -> None:
            """Recover a single source, logging and swallowing per-source failures."""
            try:
                await self.source_recovery.recover_source(
                    source_id=sid, database_name=database_name
                )
            except Exception as err:
                # Per-source failures are logged but do not block the rest.
                logger.exception(
                    "bulk_resume_recovery_error",
                    source_id=sid,
                    error=str(err),
                )

        await asyncio.gather(*(_recover_one(sid) for sid in source_ids))
        logger.info("sources_resumed_bulk", count=count)
        return count

    # --- System-wide -------------------------------------------------------

    async def pause_system(self, *, reason: str | None) -> None:
        """Set the global processing_paused flag."""
        self.repository.pause_system(reason=reason, paused_by="user")
        logger.info("system_paused", scope="system", reason=reason)

    async def resume_system(self) -> None:
        """Clear the global processing_paused flag.

        Intentionally does NOT walk every non-terminal source and
        call recover_source. The next periodic reconciler pass will
        pick them up. Walking the table from an API handler would
        scale poorly and hold the request open for too long.
        """
        self.repository.resume_system()
        logger.info("system_resumed", scope="system")

    async def get_system_status(self) -> dict[str, Any]:
        """Read the singleton SystemState and return the API shape."""
        state = self.repository.get_system_state()
        return {
            "paused": state.get("processing_paused", False),
            "paused_at": state.get("processing_paused_at"),
            "reason": state.get("processing_paused_reason"),
            "paused_by": state.get("paused_by"),
        }
