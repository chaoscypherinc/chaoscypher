# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Operations queue handler registration.

Registers all handlers that run on the Operations queue: bulk
operations, export, import, workflow execution, and quality-score
recalculation.  Uses shared factories from ``chaoscypher_cortex``
for service creation.
"""

from typing import TYPE_CHECKING

from chaoscypher_core.constants import (
    OP_BUILD_GRAPH_SNAPSHOT,
    OP_EXTRACT_CHUNK,
    OP_FINALIZE_EXTRACTION,
    OP_IMPORT_ANALYSIS,
    OP_IMPORT_COMMIT,
    OP_INDEX_DOCUMENT,
    OP_REBUILD_SEARCH_INDEXES,
)
from chaoscypher_core.queue.handler_spec import HandlerSpec
from chaoscypher_core.utils.logging.app_config import get_logger


if TYPE_CHECKING:
    from chaoscypher_neuron.types import WorkerContext


logger = get_logger(__name__)

__all__ = [
    "build_import_handler_specs",
    "build_llm_handler_specs",
    "setup_operations_handlers",
]


def _register_fetch_url_handler(source_processing_service: object) -> None:
    """Register the OP_FETCH_URL handler bound to the worker's source service.

    Extracted from ``setup_operations_handlers`` to keep the parent under
    ruff's PLR0915 statement budget. The closure here captures
    ``source_processing_service`` so the handler can call ``upload_file``
    without a module-level singleton.
    """
    from chaoscypher_core.constants import OP_FETCH_URL, QUEUE_OPERATIONS
    from chaoscypher_core.operations.sources.url_fetch_handler import handle_fetch_url
    from chaoscypher_core.queue import queue_client

    async def _fetch_url_handler(
        data: dict,
        metadata: dict | None = None,
        task_id: str | None = None,
    ) -> dict:
        """Queue handler bridging OP_FETCH_URL to the source processing service."""
        return await handle_fetch_url(
            data=data,
            source_processing_service=source_processing_service,  # type: ignore[arg-type]
            metadata=metadata,
            task_id=task_id,
        )

    queue_client.register_handlers(QUEUE_OPERATIONS, {OP_FETCH_URL: _fetch_url_handler})


def build_import_handler_specs(
    import_service: object = None,
) -> dict[str, HandlerSpec]:
    """Return the operations-queue handler specs for resumable import handlers.

    This is a thin, test-friendly wrapper around the HandlerSpec
    construction already done inside ``ImportOperationsService.__init__``.
    When called with a real service instance it returns bound
    HandlerSpecs; when called without one (from the retry-policy test
    suite) it returns unbound placeholders that still pin the
    retry_on_crash flag. The latter form is all the test cares about.

    OP_INDEX_DOCUMENT, OP_IMPORT_ANALYSIS, and OP_IMPORT_COMMIT all
    have retry_on_crash=True because they are idempotent.
    """
    if import_service is not None:
        # Extract the already-built HandlerSpec instances from the
        # service's operation_handlers dict so the flag propagation
        # is single-sourced from the service constructor.
        handlers = getattr(import_service, "operation_handlers", {}) or {}
        out: dict[str, HandlerSpec] = {}
        for op in (OP_INDEX_DOCUMENT, OP_IMPORT_ANALYSIS, OP_IMPORT_COMMIT):
            spec = handlers.get(op)
            if isinstance(spec, HandlerSpec):
                out[op] = spec
            elif spec is not None:
                out[op] = HandlerSpec(handler=spec, retry_on_crash=False)
        return out

    # Unbound form for tests — None handler, flag only
    return {
        OP_INDEX_DOCUMENT: HandlerSpec(handler=None, retry_on_crash=True),
        OP_IMPORT_ANALYSIS: HandlerSpec(handler=None, retry_on_crash=True),
        OP_IMPORT_COMMIT: HandlerSpec(handler=None, retry_on_crash=True),
    }


def build_llm_handler_specs(
    chunk_extraction_service: object = None,
) -> dict[str, HandlerSpec]:
    """Return the LLM-queue handler specs for resumable extraction handlers.

    Mirror of ``build_import_handler_specs`` for the LLM queue.
    Both extract_chunk (DB short-circuit) and finalize_extraction
    (status short-circuit) are idempotent and therefore opt into
    retry_on_crash=True. extract_chunk opts out of queue-level transient
    retries because its handler owns the chunk-level retry counter;
    finalize_extraction keeps queue-level transient retries on because it
    only owns a cancellation retry counter, not a generic transient one.
    chat_background is deliberately excluded because it is not idempotent
    and stays at retry_on_crash=False.
    """
    if chunk_extraction_service is not None:
        handlers = getattr(chunk_extraction_service, "operation_handlers", {}) or {}
        out: dict[str, HandlerSpec] = {}
        for op in (OP_EXTRACT_CHUNK, OP_FINALIZE_EXTRACTION):
            spec = handlers.get(op)
            if isinstance(spec, HandlerSpec):
                out[op] = spec
            elif spec is not None:
                out[op] = HandlerSpec(handler=spec, retry_on_crash=False)
        return out

    return {
        OP_EXTRACT_CHUNK: HandlerSpec(
            handler=None,
            retry_on_crash=True,
            retry_on_transient=False,
        ),
        OP_FINALIZE_EXTRACTION: HandlerSpec(
            handler=None,
            retry_on_crash=True,
        ),
    }


async def setup_operations_handlers(ctx: WorkerContext) -> None:  # noqa: PLR0915 - bootstrap wiring with documented handler closures
    """Register Operations queue handlers.

    Args:
        ctx: Typed worker context with shared services.

    """
    from chaoscypher_core.adapters.embedding import create_embedding_provider
    from chaoscypher_core.factories import (
        get_tool_service,
    )
    from chaoscypher_core.factories import (
        get_trigger_service as make_trigger_service,
    )
    from chaoscypher_core.factories import (
        get_workflow_service as make_workflow_service,
    )
    from chaoscypher_core.operations.bulk import (
        BulkOperationsService,
    )
    from chaoscypher_core.operations.export_operations_service import (
        ExportOperationsService,
    )
    from chaoscypher_core.operations.importing import (
        ImportOperationsService,
    )
    from chaoscypher_core.operations.sources.processing import (
        SourceFileValidators,
    )
    from chaoscypher_core.operations.workflow_operations_service import (
        WorkflowOperationsService,
    )
    from chaoscypher_core.operations.workflows.orchestrator import execute_workflow_task
    from chaoscypher_core.services.search.engine.index import IndexingService
    from chaoscypher_core.services.sources import SourceProcessingService
    from chaoscypher_core.services.workflows.triggers.engine.executor import TriggerExecutor
    from chaoscypher_core.templates.default_templates import get_all_default_templates
    from chaoscypher_core.utils.chunk import ChunkingService

    from ..handlers.quality_scores import register_quality_score_handler

    settings = ctx["settings"]
    engine_settings = ctx["engine_settings"]
    current_database = ctx["current_database"]
    graph_repository = ctx["graph_repository"]
    search_repository = ctx["search_repository"]
    config_manager = ctx["config_manager"]
    llm_provider = ctx["llm_provider"]
    llm_service = ctx["llm_service"]

    # Ensure default templates exist
    graph_repository.ensure_default_templates_exist(
        default_templates_provider=get_all_default_templates
    )

    # Use shared SqliteAdapter from setup_shared()
    storage_adapter = ctx["storage_adapter"]

    # Source processing dependencies
    source_storage = storage_adapter
    validators = SourceFileValidators(source_manager=source_storage, llm_provider=llm_provider)

    source_processing_service = SourceProcessingService(
        source_manager=source_storage,
        operations_manager=None,  # Set after ImportOperationsService
        config_manager=config_manager,
        validators=validators,
    )

    chunking_service = ChunkingService(engine_settings, repository=storage_adapter)

    embedding_provider = create_embedding_provider(engine_settings)
    indexing_service = IndexingService(
        repository=storage_adapter,
        settings=engine_settings,
        embedding_service=embedding_provider,
    )

    # Workflow system (using shared factories)
    tool_service = get_tool_service(current_database)
    workflow_service = make_workflow_service(current_database)
    trigger_service = make_trigger_service(current_database)
    trigger_dispatcher = TriggerExecutor(
        trigger_service=trigger_service,
        workflow_service=workflow_service,
        tool_service=tool_service,
        llm_service=llm_service,
        graph_repository=graph_repository,
        search_repository=search_repository,
        database_name=current_database,
        execute_workflow_fn=execute_workflow_task,
        trigger_history_limit=settings.pagination.trigger_history_limit,
        graph_manager=graph_repository,
    )
    graph_repository.trigger_service = trigger_dispatcher

    # Settings hot-reload re-runs this setup; stop and discard any prior
    # dispatcher first so we don't leak its event-loop task + bounded queue.
    previous_dispatcher = ctx.get("trigger_dispatcher")
    if previous_dispatcher is not None:
        await previous_dispatcher.stop()

    # Start the event-processing loop so published node/edge events are actually
    # consumed and matched against enabled triggers. Without this the dispatcher
    # is wired but inert: every event trigger silently never fires and the event
    # queue grows unbounded. Stored in ctx so run_worker() can stop it on shutdown.
    await trigger_dispatcher.start()
    ctx["trigger_dispatcher"] = trigger_dispatcher

    # Operations services
    bulk_operations_service = BulkOperationsService(
        graph_repository=graph_repository,
        settings=settings,
    )

    export_operations_service = ExportOperationsService(
        graph_repository=graph_repository,
        workflow_db=None,
    )

    import_operations_service = ImportOperationsService(
        graph_repository=graph_repository,
        config_manager=config_manager,
        source_manager=source_processing_service,
        trigger_service=trigger_dispatcher,
        llm_service=llm_service,
        source_repository=source_storage,
        chunking_service=chunking_service,
        indexing_service=indexing_service,
        search_repository=search_repository,
        engine_settings=engine_settings,
    )

    source_processing_service.operations_manager = import_operations_service

    workflow_operations_service = WorkflowOperationsService(
        workflow_service=workflow_service,
        tool_service=tool_service,
        llm_service=llm_service,
        graph_repository=graph_repository,
        search_repository=search_repository,
        database_name=current_database,
    )

    # Register all operation handlers
    bulk_operations_service.register_handlers()
    export_operations_service.register_handlers()
    import_operations_service.register_handlers()
    workflow_operations_service.register_handlers()

    # Register search rebuild handler
    from chaoscypher_core.constants import QUEUE_OPERATIONS
    from chaoscypher_core.operations.rebuild_handler import (
        handle_rebuild_search_indexes,
    )
    from chaoscypher_core.queue import queue_client

    async def _rebuild_handler(
        data: dict,
        metadata: dict | None = None,
        task_id: str | None = None,
    ) -> dict:
        """Queue handler bridging OP_REBUILD_SEARCH_INDEXES to the rebuild handler."""
        return await handle_rebuild_search_indexes(
            data=data,
            search_repository=search_repository,
            graph_repository=graph_repository,
            indexing_service=indexing_service,
            storage_adapter=storage_adapter,
            engine_settings=engine_settings,
            metadata=metadata,
            task_id=task_id,
        )

    queue_client.register_handlers(QUEUE_OPERATIONS, {OP_REBUILD_SEARCH_INDEXES: _rebuild_handler})

    # /sources/url enqueues OP_FETCH_URL so the route can return 202
    # instead of holding the connection open through the WebScraper fetch.
    _register_fetch_url_handler(source_processing_service)

    # Register reset + cleanup handlers (2026-04-18 decision 3: heavy
    # resets moved from sync-blocking API to queue + 202).
    from chaoscypher_core.constants import (
        OP_CLEANUP_ORPHANS,
        OP_GRAPH_CLEANUP,
        OP_RESET_ALL,
        OP_RESET_KNOWLEDGE_BASE,
    )
    from chaoscypher_core.operations.reset_handler import (
        handle_cleanup_orphans,
        handle_graph_cleanup,
        handle_reset_all,
        handle_reset_knowledge_base,
    )

    queue_client.register_handlers(
        QUEUE_OPERATIONS,
        {
            OP_RESET_KNOWLEDGE_BASE: handle_reset_knowledge_base,
            OP_RESET_ALL: handle_reset_all,
            OP_GRAPH_CLEANUP: handle_graph_cleanup,
            OP_CLEANUP_ORPHANS: handle_cleanup_orphans,
        },
    )

    # Register graph snapshot handler
    from chaoscypher_core.operations.graph_snapshot_handler import (
        handle_build_graph_snapshot,
    )

    async def _graph_snapshot_handler(
        data: dict,
        metadata: dict | None = None,
        task_id: str | None = None,
    ) -> dict:
        """Queue handler bridging OP_BUILD_GRAPH_SNAPSHOT to the snapshot builder."""
        return await handle_build_graph_snapshot(
            data=data,
            adapter=storage_adapter,
            metadata=metadata,
            task_id=task_id,
        )

    queue_client.register_handlers(
        QUEUE_OPERATIONS,
        {OP_BUILD_GRAPH_SNAPSHOT: _graph_snapshot_handler},
    )

    # Register quality score handler
    register_quality_score_handler(storage_adapter, current_database, settings)
