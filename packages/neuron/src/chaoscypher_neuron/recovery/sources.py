# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Stuck source recovery on worker startup.

Recovers sources that are stuck in ``extracting`` status with no active
extraction job, typically because the worker died mid-extraction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chaoscypher_core.models import SourceStatus, is_memory_source
from chaoscypher_core.operations.extraction.extraction_finalizer import (
    trigger_next_waiting_extraction,
)
from chaoscypher_core.utils.logging.app_config import get_logger


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.app_config import Settings


logger = get_logger(__name__)

__all__ = ["recover_stuck_sources"]


async def recover_stuck_sources(
    adapter: SqliteAdapter,
    database_name: str,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Recover sources stuck in 'extracting' status with no active job.

    Args:
        adapter: SQLite adapter for database operations.
        database_name: Current database context.
        settings: Application settings, needed to dispatch the next waiting
            source after a failure-release. When omitted the dispatch is
            skipped (and logged), preserving the old behaviour.

    Returns:
        Dictionary with counts: {"reset": N, "marked_failed": M}

    """
    stuck_sources = adapter.get_stuck_extracting_sources(database_name)

    if not stuck_sources:
        logger.debug("recovery_no_stuck_sources")
        return {"reset": 0, "marked_failed": 0}

    logger.info("recovery_found_stuck_sources", count=len(stuck_sources))

    reset_count = marked_failed = 0

    for source in stuck_sources:
        source_id = source.get("id")
        job_status = source.get("extraction_job_status")

        if not source_id:
            logger.warning("recovery_source_missing_id", source=source)
            continue
        if is_memory_source(source):
            # the link step owns a memory: reset to indexed it would be extracted as a document
            logger.debug("recovery_source_memory_skipped", source_id=source_id)
            continue

        try:
            if job_status == "failed":
                adapter.fail_extraction(
                    source_id, "Extraction job failed (recovered on worker restart)"
                )
                marked_failed += 1
                logger.info(
                    "recovery_source_marked_failed",
                    source_id=source_id,
                    job_status=job_status,
                )
            else:
                adapter.update_file(
                    source_id,
                    database_name=database_name,
                    updates={
                        "status": SourceStatus.INDEXED,
                        "extraction_started_at": None,
                        "current_extraction_job_id": None,
                        "step_description": None,
                        "current_step": None,
                        "total_steps": None,
                    },
                )
                reset_count += 1
                logger.info(
                    "recovery_source_reset_to_indexed",
                    source_id=source_id,
                    job_status=job_status,
                )
        except Exception as e:
            logger.exception(
                "recovery_source_update_failed",
                source_id=source_id,
                error=str(e),
            )

    # Slot-lifetime invariant (Audit fix F41): ``fail_extraction`` above set
    # status=ERROR on at least one source, which frees the single extraction
    # slot, so the next waiting source must be dispatched. A waiter parked by
    # ``mark_extraction_waiting`` holds no queue task — only a row breadcrumb
    # that ``trigger_next_waiting_extraction`` reads — and the standing
    # reconciler declines to advance it when the row says
    # ``auto_analyze=False`` (every ``.ccx``-imported source), so without this
    # the waiter is stranded at ``indexed`` permanently.
    #
    # Fired once after the loop rather than per source: there is a single
    # extraction slot and ``get_oldest_waiting_extraction`` is LIMIT 1, so one
    # dispatch is all that can start, and one call cannot double-queue the
    # same waiter.
    if marked_failed:
        if settings is None:
            logger.warning(
                "recovery_next_extraction_dispatch_skipped",
                reason="no_settings",
                marked_failed=marked_failed,
            )
        else:
            try:
                await trigger_next_waiting_extraction(adapter, database_name, settings)
            except Exception as dispatch_exc:
                logger.warning(
                    "trigger_next_waiting_extraction_failed",
                    marked_failed=marked_failed,
                    error_type=type(dispatch_exc).__name__,
                    error_message=str(dispatch_exc),
                )

    return {"reset": reset_count, "marked_failed": marked_failed}
