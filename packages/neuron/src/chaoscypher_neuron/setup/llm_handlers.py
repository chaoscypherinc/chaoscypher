# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM queue handler registration.

Registers all handlers that run on the LLM queue: the core
LLM service handlers, chunk extraction handlers, template embedding
regeneration, and the Ollama load-balancer initialisation.
"""

from typing import TYPE_CHECKING

from chaoscypher_core.utils.logging.app_config import get_logger


if TYPE_CHECKING:
    from chaoscypher_neuron.types import WorkerContext


logger = get_logger(__name__)

__all__ = ["setup_llm_handlers"]


async def setup_llm_handlers(ctx: WorkerContext) -> None:
    """Register (or re-register) all LLM queue handlers from context.

    Shared by initial setup and hot-reload paths. Registers:
    - Core LLM service handlers
    - Chunk extraction handlers
    - Template embedding handler
    - Ollama load balancer (if applicable)

    Args:
        ctx: Typed worker context with shared services.

    """
    from chaoscypher_core.adapters.llm.load_balancer import (
        get_ollama_load_balancer,
        reload_load_balancer_config,
    )
    from chaoscypher_core.operations.extraction import (
        ChunkExtractionOperationsService,
    )
    from chaoscypher_core.operations.importing.vision_operations_service import (
        VisionOperationsService,
    )

    from ..handlers.chat_completion import register_chat_completion_handler
    from ..handlers.template_embedding import register_template_embedding_handler

    settings = ctx["settings"]
    llm_service = ctx["llm_service"]
    graph_repository = ctx["graph_repository"]
    search_repository = ctx["search_repository"]
    config_manager = ctx["config_manager"]
    current_database = ctx["current_database"]

    # Register LLM handlers
    llm_service.register_handlers()

    # Register chunk extraction handlers (also on LLM queue)
    chunk_extraction_service = ChunkExtractionOperationsService(
        graph_repository=graph_repository,
        config_manager=config_manager,
        llm_service=ctx["llm_provider"],
        source_repository=ctx["storage_adapter"],
    )
    chunk_extraction_service.register_handlers()

    # Register vision per-page handler (OP_VISION_PAGE on LLM queue).
    # OP_VISION_FINALIZE will be added here when Task 11 (vision_finalizer) ships.
    vision_operations_service = VisionOperationsService(
        adapter=ctx["storage_adapter"],
        settings=settings,
        database_name=current_database,
    )
    vision_operations_service.register_handlers()

    # Register template embedding handler (use cached engine_settings)
    register_template_embedding_handler(
        graph_repository,
        search_repository,
        settings,
        current_database,
        engine_settings=ctx.get("engine_settings"),
    )

    # Initialize Ollama load balancer if applicable
    ollama_instances = settings.llm.ollama_instances or []
    if settings.llm.chat_provider == "ollama" and ollama_instances:
        await reload_load_balancer_config(
            settings.llm,
            drain_max_wait=settings.timeouts.instance_drain_max_wait,
            drain_check_interval=settings.timeouts.instance_drain_check_interval,
        )
        load_balancer = get_ollama_load_balancer()
        logger.info(
            "load_balancer_initialized",
            instance_count=len(ollama_instances),
            total_capacity=load_balancer.get_total_capacity(),
            strategy=settings.llm.ollama_load_balancing,
        )

    # Register chat completion handler (background chat processing)
    register_chat_completion_handler(
        storage_adapter=ctx["storage_adapter"],
        settings=settings,
        config_manager=config_manager,
        graph_repository=graph_repository,
        search_repository=search_repository,
        current_database=current_database,
    )
