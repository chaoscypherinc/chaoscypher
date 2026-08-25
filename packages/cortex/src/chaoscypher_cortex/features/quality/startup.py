# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Quality score startup check.

Detects sources with outdated quality score caches and queues
background recalculation during application lifespan startup.
"""

from typing import TYPE_CHECKING

from chaoscypher_core.utils.logging.app_config import get_logger


if TYPE_CHECKING:
    from chaoscypher_core.app_config import Settings

logger = get_logger(__name__)

# Source IDs per enqueued recalculation task.
#
# Two ceilings force the fan-out. The neuron handler silently truncates any
# single request above ``NeuronSettings.max_quality_score_batch`` (default
# 500, quality_scores.py:64-72), so a one-shot enqueue of every stale source
# would warm only the first 500 and log a warning nobody reads. And the task
# payload is JSON-serialised into the ``data`` field of the
# ``queue:task:{id}`` hash, so a six-figure ID list would put megabytes into
# a single hash field that six full-keyspace scans then HGETALL.
#
# Cortex must not import neuron config (package boundary), so this tracks
# that ceiling by value; keep the two in step.
_RECALC_CHUNK_SIZE = 500


async def queue_outdated_quality_score_recalculation(settings: Settings) -> None:
    """Check for outdated quality scores and queue recalculation.

    Compares each source's cached_scores_version against the current
    SCORING_VERSION and enqueues a background recalculation task for
    any that are stale.

    Args:
        settings: Application settings instance.

    """
    from chaoscypher_core.constants import QUEUE_OPERATIONS
    from chaoscypher_core.database import get_sqlite_adapter
    from chaoscypher_core.queue import queue_client
    from chaoscypher_core.services.quality import SCORING_VERSION
    from chaoscypher_cortex.features.quality.service import SOURCE_FETCH_LIMIT

    adapter = get_sqlite_adapter(database_name=settings.current_database)
    # Every stale source this pass misses stays stale until some request
    # recalculates it inline, so the warm-up has to see past ``list_files``'
    # 100-row default. One projection query; the enqueue below is a single
    # background task regardless of how many IDs it carries.
    sources = adapter.list_files(settings.current_database, limit=SOURCE_FETCH_LIMIT)

    # Find sources needing score recalculation
    outdated_source_ids = []
    for source in sources:
        if not source.get("extraction_complete"):
            continue
        cached_version = source.get("cached_scores_version")
        if cached_version is None or cached_version != SCORING_VERSION:
            outdated_source_ids.append(source.get("id"))

    if not outdated_source_ids:
        logger.debug("quality_scores_up_to_date", version=SCORING_VERSION)
        return

    logger.info(
        "outdated_quality_scores_detected",
        count=len(outdated_source_ids),
        current_version=SCORING_VERSION,
    )

    # Queue background recalculation if queue is connected
    if queue_client.is_available:
        batches = [
            outdated_source_ids[start : start + _RECALC_CHUNK_SIZE]
            for start in range(0, len(outdated_source_ids), _RECALC_CHUNK_SIZE)
        ]
        for batch in batches:
            await queue_client.enqueue_task(
                queue=QUEUE_OPERATIONS,
                operation="recalculate_quality_scores",
                data={
                    "source_ids": batch,
                    "database_name": settings.current_database,
                },
                priority=settings.priorities.background,
                metadata={
                    "operation_type": "recalculate_quality_scores",
                    "triggered_by": "startup_version_check",
                },
            )
        logger.info(
            "quality_score_recalculation_queued",
            source_count=len(outdated_source_ids),
            task_count=len(batches),
        )
    else:
        logger.warning(
            "quality_score_recalculation_skipped",
            reason="queue_not_connected",
            source_count=len(outdated_source_ids),
        )
