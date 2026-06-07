# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for handler registration (setup module).

Verifies that build_*_handler_specs return correct specs, and
that the setup functions call register_handlers on the expected services.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.constants import (
    OP_EXTRACT_CHUNK,
    OP_FINALIZE_EXTRACTION,
    OP_IMPORT_ANALYSIS,
    OP_IMPORT_COMMIT,
    OP_INDEX_DOCUMENT,
    QUEUE_OPERATIONS,
)
from chaoscypher_core.queue.handler_spec import HandlerSpec
from chaoscypher_neuron.setup.ops_handlers import (
    build_import_handler_specs,
    build_llm_handler_specs,
)


# ============================================================================
# Handler Spec Builders (pure functions, no side effects)
# ============================================================================


class TestBuildImportHandlerSpecs:
    """Tests for build_import_handler_specs."""

    def test_unbound_returns_all_expected_ops(self) -> None:
        """Without a service, returns placeholder specs for all import operations."""
        specs = build_import_handler_specs()

        assert OP_INDEX_DOCUMENT in specs
        assert OP_IMPORT_ANALYSIS in specs
        assert OP_IMPORT_COMMIT in specs
        assert len(specs) == 3

    def test_unbound_all_retry_on_crash_true(self) -> None:
        """All unbound import handler specs have retry_on_crash=True."""
        specs = build_import_handler_specs()

        for op_name, spec in specs.items():
            assert spec.retry_on_crash is True, f"{op_name} should be retry_on_crash=True"

    def test_unbound_handlers_are_none(self) -> None:
        """Unbound specs have None handlers (placeholders)."""
        specs = build_import_handler_specs()

        for spec in specs.values():
            assert spec.handler is None

    def test_with_service_extracts_handler_specs(self) -> None:
        """With a service, extracts HandlerSpec instances from operation_handlers."""
        mock_handler = AsyncMock()
        mock_service = MagicMock()
        mock_service.operation_handlers = {
            OP_INDEX_DOCUMENT: HandlerSpec(handler=mock_handler, retry_on_crash=True),
            OP_IMPORT_ANALYSIS: HandlerSpec(handler=mock_handler, retry_on_crash=True),
            OP_IMPORT_COMMIT: HandlerSpec(handler=mock_handler, retry_on_crash=True),
        }

        specs = build_import_handler_specs(mock_service)

        assert len(specs) == 3
        for spec in specs.values():
            assert spec.handler is mock_handler
            assert spec.retry_on_crash is True

    def test_with_service_wraps_bare_callable(self) -> None:
        """Bare callables from a service are wrapped with retry_on_crash=False."""
        mock_handler = AsyncMock()
        mock_service = MagicMock()
        mock_service.operation_handlers = {
            OP_INDEX_DOCUMENT: mock_handler,  # Bare callable, not HandlerSpec
        }

        specs = build_import_handler_specs(mock_service)

        assert OP_INDEX_DOCUMENT in specs
        assert specs[OP_INDEX_DOCUMENT].retry_on_crash is False

    def test_with_service_ignores_unknown_ops(self) -> None:
        """Only known import operations are extracted from the service."""
        mock_service = MagicMock()
        mock_service.operation_handlers = {
            "unknown_op": HandlerSpec(handler=AsyncMock(), retry_on_crash=True),
        }

        specs = build_import_handler_specs(mock_service)

        assert "unknown_op" not in specs
        assert len(specs) == 0


class TestBuildLlmHandlerSpecs:
    """Tests for build_llm_handler_specs."""

    def test_unbound_returns_all_expected_ops(self) -> None:
        """Without a service, returns placeholder specs for LLM extraction operations."""
        specs = build_llm_handler_specs()

        assert OP_EXTRACT_CHUNK in specs
        assert OP_FINALIZE_EXTRACTION in specs
        assert len(specs) == 2

    def test_unbound_all_retry_on_crash_true(self) -> None:
        """All unbound LLM handler specs have retry_on_crash=True."""
        specs = build_llm_handler_specs()

        for op_name, spec in specs.items():
            assert spec.retry_on_crash is True, f"{op_name} should be retry_on_crash=True"

    def test_unbound_extract_chunk_disables_queue_transient_retries(self) -> None:
        """extract_chunk owns its retry budget in the handler layer."""
        specs = build_llm_handler_specs()

        assert specs[OP_EXTRACT_CHUNK].retry_on_transient is False, (
            "extract_chunk owns its retry counter and must not also be retried by the queue"
        )

    def test_unbound_finalize_keeps_queue_transient_retries(self) -> None:
        """finalize_extraction has no domain transient-retry counter, so the queue must retry."""
        specs = build_llm_handler_specs()

        assert specs[OP_FINALIZE_EXTRACTION].retry_on_transient is True, (
            "finalize_extraction only owns a cancellation counter; "
            "queue-level transient retries are the only safety net for inline transient errors"
        )

    def test_with_service_extracts_handler_specs(self) -> None:
        """With a service, extracts HandlerSpec instances from operation_handlers."""
        mock_handler = AsyncMock()
        mock_service = MagicMock()
        mock_service.operation_handlers = {
            OP_EXTRACT_CHUNK: HandlerSpec(handler=mock_handler, retry_on_crash=True),
            OP_FINALIZE_EXTRACTION: HandlerSpec(handler=mock_handler, retry_on_crash=True),
        }

        specs = build_llm_handler_specs(mock_service)

        assert len(specs) == 2
        for spec in specs.values():
            assert spec.handler is mock_handler


# ============================================================================
# Setup Functions (integration with mocked dependencies)
# ============================================================================


class TestSetupLlmHandlers:
    """Tests for setup_llm_handlers function."""

    @pytest.mark.asyncio
    async def test_registers_llm_service_handlers(self) -> None:
        """setup_llm_handlers calls register_handlers on the LLM service."""
        mock_llm_service = MagicMock()
        ctx = _make_llm_ctx(llm_service=mock_llm_service)

        with _patch_llm_handler_deps():
            from chaoscypher_neuron.setup.llm_handlers import setup_llm_handlers

            await setup_llm_handlers(ctx)

        mock_llm_service.register_handlers.assert_called_once()

    @pytest.mark.asyncio
    async def test_registers_chunk_extraction_handlers(self) -> None:
        """setup_llm_handlers creates and registers ChunkExtractionOperationsService."""
        ctx = _make_llm_ctx()

        mock_chunk_service = MagicMock()
        with _patch_llm_handler_deps() as patches:
            patches["chunk_cls"].return_value = mock_chunk_service
            from chaoscypher_neuron.setup.llm_handlers import setup_llm_handlers

            await setup_llm_handlers(ctx)

        mock_chunk_service.register_handlers.assert_called_once()

    @pytest.mark.asyncio
    async def test_initializes_load_balancer_for_ollama(self) -> None:
        """When chat_provider is ollama with instances, load balancer is initialized."""
        mock_instance = MagicMock()
        mock_settings = MagicMock()
        mock_settings.llm.chat_provider = "ollama"
        mock_settings.llm.ollama_instances = [mock_instance]
        mock_settings.timeouts.instance_drain_max_wait = 30.0
        mock_settings.timeouts.instance_drain_check_interval = 1.0
        mock_settings.llm.ollama_load_balancing = "round_robin"

        ctx = _make_llm_ctx(settings=mock_settings)

        with _patch_llm_handler_deps() as patches:
            mock_lb = MagicMock()
            mock_lb.get_total_capacity.return_value = 4
            patches["get_lb"].return_value = mock_lb

            from chaoscypher_neuron.setup.llm_handlers import setup_llm_handlers

            await setup_llm_handlers(ctx)

        patches["reload_lb"].assert_called_once()


class TestSetupOperationsHandlers:
    """Tests for setup_operations_handlers function."""

    @pytest.mark.asyncio
    async def test_registers_all_operation_services(self) -> None:
        """setup_operations_handlers registers handlers for all operation services."""
        ctx = _make_ops_ctx()

        mock_bulk = MagicMock()
        mock_export = MagicMock()
        mock_import = MagicMock()
        mock_workflow = MagicMock()

        with _patch_ops_handler_deps() as patches:
            patches["bulk_cls"].return_value = mock_bulk
            patches["export_cls"].return_value = mock_export
            patches["import_cls"].return_value = mock_import
            patches["workflow_cls"].return_value = mock_workflow

            from chaoscypher_neuron.setup.ops_handlers import setup_operations_handlers

            await setup_operations_handlers(ctx)

        mock_bulk.register_handlers.assert_called_once()
        mock_export.register_handlers.assert_called_once()
        mock_import.register_handlers.assert_called_once()
        mock_workflow.register_handlers.assert_called_once()

    @pytest.mark.asyncio
    async def test_registers_search_rebuild_handler(self) -> None:
        """setup_operations_handlers registers the search rebuild handler on operations queue."""
        ctx = _make_ops_ctx()

        with _patch_ops_handler_deps() as patches:
            from chaoscypher_neuron.setup.ops_handlers import setup_operations_handlers

            await setup_operations_handlers(ctx)

        # ops_handlers makes several register_handlers calls (rebuild_search_indexes,
        # reset/cleanup batch, graph snapshot). Find the rebuild_search_indexes one.
        register_calls = patches["queue_client"].register_handlers.call_args_list
        rebuild_calls = [c for c in register_calls if "rebuild_search_indexes" in c.args[1]]
        assert len(rebuild_calls) == 1, "rebuild_search_indexes should be registered once"
        assert rebuild_calls[0].args[0] == QUEUE_OPERATIONS

    @pytest.mark.asyncio
    async def test_registers_quality_score_handler(self) -> None:
        """setup_operations_handlers registers the quality score handler."""
        ctx = _make_ops_ctx()

        with _patch_ops_handler_deps() as patches:
            from chaoscypher_neuron.setup.ops_handlers import setup_operations_handlers

            await setup_operations_handlers(ctx)

        patches["quality_handler"].assert_called_once()

    @pytest.mark.asyncio
    async def test_starts_and_stores_trigger_dispatcher(self) -> None:
        """The event trigger dispatcher is started and stored in the context.

        Regression: the dispatcher was wired onto the graph repository but its
        event-processing loop was never started, so every event-driven trigger
        silently never fired. It must also be stored in ctx so the worker can
        stop it during shutdown.
        """
        ctx = _make_ops_ctx()

        with _patch_ops_handler_deps() as patches:
            from chaoscypher_neuron.setup.ops_handlers import setup_operations_handlers

            await setup_operations_handlers(ctx)

        dispatcher = patches["trigger_exec_cls"].return_value
        dispatcher.start.assert_awaited_once()
        assert ctx.get("trigger_dispatcher") is dispatcher


# ============================================================================
# Helpers
# ============================================================================


def _make_llm_ctx(**overrides):
    """Build a minimal context for setup_llm_handlers."""
    mock_settings = MagicMock()
    mock_settings.llm.chat_provider = "openai"
    mock_settings.llm.ollama_instances = []

    return {
        "settings": overrides.get("settings", mock_settings),
        "llm_service": overrides.get("llm_service", MagicMock()),
        "llm_provider": MagicMock(),
        "graph_repository": MagicMock(),
        "search_repository": MagicMock(),
        "config_manager": MagicMock(),
        "current_database": "test_db",
        "engine_settings": MagicMock(),
        "storage_adapter": MagicMock(),
    }


def _make_ops_ctx():
    """Build a minimal context for setup_operations_handlers."""
    mock_settings = MagicMock()
    mock_settings.pagination.trigger_history_limit = 100

    return {
        "settings": mock_settings,
        "engine_settings": MagicMock(),
        "current_database": "test_db",
        "graph_repository": MagicMock(),
        "search_repository": MagicMock(),
        "config_manager": MagicMock(),
        "llm_provider": MagicMock(),
        "llm_service": MagicMock(),
        "storage_adapter": MagicMock(),
    }


class _PatchContext(dict):
    """Dict subclass that also acts as a context manager."""

    def __init__(self, patches_dict, exit_stack):
        super().__init__(patches_dict)
        self._stack = exit_stack

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self._stack.__exit__(*args)


def _patch_llm_handler_deps():
    """Return a context manager that patches llm_handlers function-local imports."""
    import contextlib

    stack = contextlib.ExitStack()
    patches = {}

    def _enter():
        stack.__enter__()
        patches["chunk_cls"] = stack.enter_context(
            patch("chaoscypher_core.operations.extraction.ChunkExtractionOperationsService")
        )
        patches["template_handler"] = stack.enter_context(
            patch(
                "chaoscypher_neuron.handlers.template_embedding.register_template_embedding_handler"
            )
        )
        patches["chat_handler"] = stack.enter_context(
            patch("chaoscypher_neuron.handlers.chat_completion.register_chat_completion_handler")
        )
        patches["reload_lb"] = stack.enter_context(
            patch(
                "chaoscypher_core.adapters.llm.load_balancer.reload_load_balancer_config",
                new_callable=AsyncMock,
            )
        )
        patches["get_lb"] = stack.enter_context(
            patch("chaoscypher_core.adapters.llm.load_balancer.get_ollama_load_balancer")
        )
        return _PatchContext(patches, stack)

    return _enter()


def _patch_ops_handler_deps():
    """Return a context manager that patches ops_handlers function-local imports."""
    import contextlib

    stack = contextlib.ExitStack()
    patches = {}

    def _enter():
        stack.__enter__()
        patches["bulk_cls"] = stack.enter_context(
            patch("chaoscypher_core.operations.bulk.BulkOperationsService")
        )
        patches["export_cls"] = stack.enter_context(
            patch("chaoscypher_core.operations.export_operations_service.ExportOperationsService")
        )
        patches["import_cls"] = stack.enter_context(
            patch("chaoscypher_core.operations.importing.ImportOperationsService")
        )
        patches["workflow_cls"] = stack.enter_context(
            patch(
                "chaoscypher_core.operations.workflow_operations_service.WorkflowOperationsService"
            )
        )
        patches["quality_handler"] = stack.enter_context(
            patch("chaoscypher_neuron.handlers.quality_scores.register_quality_score_handler")
        )
        patches["queue_client"] = stack.enter_context(patch("chaoscypher_core.queue.queue_client"))
        # Patch remaining deps that are imported inside the function
        stack.enter_context(patch("chaoscypher_core.adapters.embedding.create_embedding_provider"))
        stack.enter_context(patch("chaoscypher_core.services.search.engine.index.IndexingService"))
        stack.enter_context(patch("chaoscypher_core.services.sources.SourceProcessingService"))
        stack.enter_context(
            patch("chaoscypher_core.operations.sources.processing.SourceFileValidators")
        )
        stack.enter_context(patch("chaoscypher_core.utils.chunk.ChunkingService"))
        patches["trigger_exec_cls"] = stack.enter_context(
            patch("chaoscypher_core.services.workflows.triggers.engine.executor.TriggerExecutor")
        )
        # The dispatcher's lifecycle methods are awaited by setup/shutdown.
        patches["trigger_exec_cls"].return_value.start = AsyncMock()
        patches["trigger_exec_cls"].return_value.stop = AsyncMock()
        stack.enter_context(patch("chaoscypher_core.factories.get_tool_service"))
        stack.enter_context(patch("chaoscypher_core.factories.get_trigger_service"))
        stack.enter_context(patch("chaoscypher_core.factories.get_workflow_service"))
        stack.enter_context(
            patch("chaoscypher_core.templates.default_templates.get_all_default_templates")
        )
        stack.enter_context(
            patch("chaoscypher_core.operations.workflows.orchestrator.execute_workflow_task")
        )
        stack.enter_context(
            patch("chaoscypher_core.operations.rebuild_handler.handle_rebuild_search_indexes")
        )
        return _PatchContext(patches, stack)

    return _enter()
