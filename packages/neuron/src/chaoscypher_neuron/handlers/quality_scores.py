# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Quality score recalculation handler (Operations queue).

Registers a handler on the Operations queue that recalculates cached
quality scores for a batch of source files.  Triggered after extraction
results change or the scoring algorithm is updated.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue import queue_client
from chaoscypher_core.utils.logging.app_config import get_logger
from chaoscypher_neuron.config import get_neuron_settings


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.app_config import Settings

logger = get_logger(__name__)

__all__ = ["register_quality_score_handler"]


def register_quality_score_handler(
    storage_adapter: SqliteAdapter,
    current_database: str,
    settings: Settings,
) -> None:
    """Register quality score recalculation handler.

    Args:
        storage_adapter: SqliteAdapter for database operations.
        current_database: Current database name.
        settings: Application settings (drives the batch yield cadence).

    """
    batch_size = settings.batching.quality_score_batch_size
    max_tracked_errors = settings.batching.quality_score_max_tracked_errors
    max_returned_errors = settings.batching.quality_score_max_returned_errors
    max_quality_score_batch = get_neuron_settings().max_quality_score_batch

    async def recalculate_quality_scores_handler(
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Handle quality score recalculation for multiple sources.

        Args:
            data: Task data containing source_ids and database_name.
            metadata: Task metadata (unused).
            task_id: Task ID from queue (unused).

        Returns:
            Result dictionary with recalculated count and any errors.

        """
        from chaoscypher_neuron.handlers import validate_database_name

        source_ids = data.get("source_ids", [])
        if len(source_ids) > max_quality_score_batch:
            logger.warning(
                "quality_score_batch_too_large",
                requested=len(source_ids),
                max_allowed=max_quality_score_batch,
            )
            source_ids = source_ids[:max_quality_score_batch]
        database_name = validate_database_name(data.get("database_name"), current_database)

        logger.info(
            "recalculate_quality_scores_started",
            source_count=len(source_ids),
            database_name=database_name,
        )

        from chaoscypher_core.services.quality import SCORING_VERSION, QualityScorer

        success_count = 0
        errors: list[dict[str, Any]] = []

        for i, source_id in enumerate(source_ids):
            # Yield to event loop between batches so other tasks aren't starved
            if i > 0 and i % batch_size == 0:
                logger.debug(
                    "recalculate_quality_scores_progress",
                    processed=i,
                    total=len(source_ids),
                )
                await asyncio.sleep(0)
            try:
                source = storage_adapter.get_source_extraction_metadata(source_id, database_name)
                if not source:
                    errors.append({"source_id": source_id, "error": "Source not found"})
                    continue

                # Migration 0042: per-source entity / relationship rows
                # live in dedicated tables.
                entities = storage_adapter.list_source_entities(source_id, database_name)
                relationships = storage_adapter.list_source_relationships(source_id, database_name)

                if not entities:
                    continue

                domain = source.get("extraction_domain")
                quality_config = {}
                if domain:
                    try:
                        from chaoscypher_core.services.sources.engine.extraction.domains import (
                            get_domain_registry,
                        )

                        registry = get_domain_registry(database_name=database_name)
                        analyzer = registry.get_domain(domain)
                        if analyzer and hasattr(analyzer, "get_quality_scoring"):
                            quality_config = analyzer.get_quality_scoring()
                    except Exception:
                        logger.debug("domain_quality_scoring_lookup_failed", domain=domain)

                entity_chunk_mentions: dict[int, int] = {}
                for idx, entity in enumerate(entities):
                    chunks = entity.get("source_chunks", []) or entity.get("chunks", [])
                    entity_chunk_mentions[idx] = len(chunks) if chunks else 1

                chunk_count = source.get("chunk_count", 0) or 0

                scorer = QualityScorer(quality_config)
                cached_scores = scorer.get_cacheable_scores(
                    source_id=source_id,
                    entities=entities,
                    relationships=relationships,
                    entity_chunk_mentions=entity_chunk_mentions,
                    chunk_count=chunk_count,
                )
                storage_adapter.update_file(
                    source_id, database_name=database_name, updates=cached_scores
                )
                success_count += 1

                logger.debug(
                    "source_quality_scores_recalculated",
                    source_id=source_id,
                    quality_grade=cached_scores["cached_quality_grade"],
                )

            except Exception as e:
                if len(errors) < max_tracked_errors:
                    errors.append({"source_id": source_id, "error": "Score calculation failed"})
                logger.warning(
                    "source_quality_recalculation_failed",
                    source_id=source_id,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )

        logger.info(
            "recalculate_quality_scores_completed",
            success_count=success_count,
            error_count=len(errors),
            total_sources=len(source_ids),
            scoring_version=SCORING_VERSION,
        )

        return {
            "recalculated_count": success_count,
            "error_count": len(errors),
            "errors": errors[:max_returned_errors],
        }

    queue_client.register_handlers(
        QUEUE_OPERATIONS, {"recalculate_quality_scores": recalculate_quality_scores_handler}
    )
