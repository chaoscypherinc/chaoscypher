# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template embedding regeneration handler (LLM queue).

Registers a handler on the LLM queue that regenerates vector embeddings
for all graph templates.  Triggered manually via
``POST /api/v1/templates/embeddings`` (the only enqueue site) — typically
after importing templates or changing the embedding model.
"""

import asyncio
from typing import TYPE_CHECKING, Any

from chaoscypher_core.constants import QUEUE_LLM
from chaoscypher_core.queue import queue_client
from chaoscypher_core.utils.logging.app_config import get_logger


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.ports.transactional import TransactionalAdapterProtocol
    from chaoscypher_core.settings import EngineSettings

logger = get_logger(__name__)

__all__ = ["register_template_embedding_handler"]


def register_template_embedding_handler(
    graph_repository: GraphRepository,
    search_repository: SearchRepository,
    settings: Settings,
    current_database: str,
    engine_settings: EngineSettings | None = None,
    *,
    storage_adapter: TransactionalAdapterProtocol,
) -> None:
    """Register the template embedding regeneration handler.

    Args:
        graph_repository: Graph repository for template operations.
        search_repository: Search repository for indexing.
        settings: Application settings.
        current_database: Current database name.
        engine_settings: Cached EngineSettings from worker startup (optional).
        storage_adapter: Transaction owner — groups each template's
            graph-row update and search-index upsert into one commit.

    """
    batch_size = settings.batching.template_embedding_batch_size

    async def regenerate_template_embeddings_handler(
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Regenerate embeddings for all templates.

        Args:
            data: Task data containing database_name.
            metadata: Task metadata (unused).
            task_id: Task ID from queue.

        Returns:
            Result dictionary with count and status.

        """
        from chaoscypher_core.adapters.embedding import create_embedding_provider
        from chaoscypher_core.services.graph.management.embedding import TemplateEmbeddingService
        from chaoscypher_neuron.handlers import validate_database_name

        db_name = validate_database_name(data.get("database_name"), current_database)
        logger.info("regenerate_template_embeddings_started", database_name=db_name)

        try:
            # ALL templates, including those of currently-disabled sources:
            # a regeneration pass that skips them leaves old-model vectors
            # behind, and re-enabling the source would then mix embedding
            # models within one search space.
            templates = graph_repository.list_templates(include_disabled_sources=True)

            if not templates:
                return {
                    "success": True,
                    "count": 0,
                    "message": "No templates found",
                }

            # Use cached engine_settings from worker context, fallback to converting
            _engine_settings = engine_settings
            if _engine_settings is None:
                from chaoscypher_core.app_config.engine_factory import (
                    build_engine_settings,
                )

                _engine_settings = build_engine_settings(settings)
            embedding_provider = create_embedding_provider(_engine_settings)
            template_service = TemplateEmbeddingService(embedding_provider)

            updated = 0
            failed = 0
            last_error: Exception | None = None
            for i, template in enumerate(templates):
                # Yield between batches to avoid blocking LLM queue
                if i > 0 and i % batch_size == 0:
                    logger.debug(
                        "template_embedding_progress",
                        processed=i,
                        total=len(templates),
                    )
                    await asyncio.sleep(0)
                try:
                    embedding = await template_service.generate_embedding(
                        template.name, template.description
                    )
                    if not embedding:
                        continue
                    # One transaction per template: the graph-row update and
                    # the search-index upsert commit or roll back TOGETHER.
                    # Sharing the session also makes an index failure
                    # propagate — sessionless index_template downgrades it
                    # to a warning, leaving a permanent graph/search
                    # mismatch on the first failure (templates have no
                    # pending-search-index reconciliation path to repair it).
                    with storage_adapter.transaction():
                        graph_repository.update_template(
                            template.id,
                            {
                                "embedding": embedding,
                                "embedding_model": template_service.get_embedding_model(),
                                "embedding_dimensions": len(embedding),
                            },
                        )
                        search_repository.index_template(
                            template.id, embedding, session=graph_repository.session
                        )
                    updated += 1
                    logger.debug(
                        "template_embedding_generated",
                        template_id=template.id,
                        template_name=template.name,
                    )
                except Exception as exc:
                    # Per-template isolation: one bad template must not force
                    # a full-batch retry that re-embeds every template that
                    # already succeeded — or, if it keeps failing, dead-letter
                    # the batch with the tail never processed.
                    failed += 1
                    last_error = exc
                    logger.exception(
                        "template_embedding_failed",
                        template_id=template.id,
                        template_name=template.name,
                    )

            if failed and not updated and last_error is not None:
                # Nothing succeeded — systemic failure (provider down, bad
                # credentials). Re-raise the original error so
                # _execute_handler classifies transient vs permanent and the
                # queue retry applies.
                raise last_error

            logger.info(
                "regenerate_template_embeddings_completed",
                database_name=db_name,
                templates_updated=updated,
                templates_failed=failed,
                total_templates=len(templates),
            )

            return {
                "success": True,
                "count": updated,
                "failed": failed,
                "total": len(templates),
                "message": f"Generated embeddings for {updated} templates",
            }

        except Exception as e:
            logger.exception(
                "regenerate_template_embeddings_failed",
                error=str(e),
            )
            # Re-raise so _execute_handler can classify the error and
            # retry transient failures (LLM timeouts, network errors).
            raise

    queue_client.register_handlers(
        QUEUE_LLM, {"regenerate_template_embeddings": regenerate_template_embeddings_handler}
    )
