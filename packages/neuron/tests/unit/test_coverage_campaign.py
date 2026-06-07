# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage-campaign tests for three neuron modules.

Raises coverage on:

* ``setup/shared.py`` — the ``setup_shared`` orchestration body.
* ``config.py`` — the ``_get_default_config`` Ollama-aware branch and its
  fallback ladder.
* ``setup/ops_handlers.py`` — the real handler closures
  (``_fetch_url_handler``, ``_rebuild_handler``, ``_graph_snapshot_handler``)
  and the ``build_llm_handler_specs`` bare-callable wrap.

All deferred (function-local) imports are patched at their SOURCE module
path so the lazy import inside each function resolves to the mock.
"""

import contextlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.queue.handler_spec import HandlerSpec


# ============================================================================
# TARGET 1 — setup/shared.py :: setup_shared
# ============================================================================


def _make_shared_settings() -> MagicMock:
    """Build a settings mock with every attribute setup_shared reads."""
    settings = MagicMock()
    settings.current_database = "test_db"
    settings.search.vector_dimensions = 768
    settings.embedding.model = "nomic-embed-text"
    settings.data_dir = "/data"
    settings.paths.app_db_filename = "app.db"
    settings.settings_filename = "settings.json"
    return settings


@contextlib.contextmanager
def _patch_shared_deps(*, db_info):
    """Patch every deferred import inside ``setup_shared``.

    ``db_info`` is the value returned by ``DatabaseRepository.get_database``;
    when falsy, ``setup_shared`` is expected to call ``create_database``.
    Yields a dict of the key mocks so tests can assert on them.
    """
    stack = contextlib.ExitStack()
    p = {}

    settings = _make_shared_settings()

    # ConfigManager().get_settings() -> settings
    config_manager_instance = MagicMock()
    config_manager_instance.get_settings.return_value = settings

    # PathSettings() instance — settings_filename + default_settings_path read
    path_settings_instance = MagicMock()
    path_settings_instance.settings_filename = "settings.json"
    path_settings_instance.default_settings_path = "/defaults/settings.json"

    # DatabaseRepository() instance
    db_repo_instance = MagicMock()
    db_repo_instance.get_database.return_value = db_info

    # SqliteAdapter() instance — connect() then .session must be non-None
    adapter_instance = MagicMock()
    adapter_instance.session = MagicMock()  # non-None so the guard passes

    with stack:
        p["DatabaseRepository"] = stack.enter_context(
            patch(
                "chaoscypher_core.database.repository.DatabaseRepository",
                return_value=db_repo_instance,
            )
        )
        p["PathSettings"] = stack.enter_context(
            patch(
                "chaoscypher_core.app_config.PathSettings",
                return_value=path_settings_instance,
            )
        )
        p["ConfigManager"] = stack.enter_context(
            patch(
                "chaoscypher_core.app_config.manager.ConfigManager",
                return_value=config_manager_instance,
            )
        )
        p["get_engine"] = stack.enter_context(patch("chaoscypher_core.database.engine.get_engine"))
        p["SearchRepository"] = stack.enter_context(
            patch("chaoscypher_core.adapters.sqlite.repos.SearchRepository")
        )
        p["SqliteAdapter"] = stack.enter_context(
            patch(
                "chaoscypher_core.adapters.sqlite.SqliteAdapter",
                return_value=adapter_instance,
            )
        )
        p["register_worker_adapter"] = stack.enter_context(
            patch("chaoscypher_core.queue.service.register_worker_adapter")
        )
        p["GraphRepository"] = stack.enter_context(
            patch("chaoscypher_core.adapters.sqlite.repos.GraphRepository")
        )
        p["LLMProvider"] = stack.enter_context(patch("chaoscypher_core.llm_queue.LLMProvider"))
        p["LLMQueueService"] = stack.enter_context(
            patch("chaoscypher_core.llm_queue.queue_service.LLMQueueService")
        )
        p["build_engine_settings"] = stack.enter_context(
            patch("chaoscypher_core.app_config.engine_factory.build_engine_settings")
        )
        p["queue_connect"] = stack.enter_context(
            patch(
                "chaoscypher_neuron.setup.shared.queue_client.connect",
                new_callable=AsyncMock,
            )
        )
        p["db_repo_instance"] = db_repo_instance
        p["adapter_instance"] = adapter_instance
        p["config_manager_instance"] = config_manager_instance
        p["settings"] = settings
        yield p


class TestSetupShared:
    """Tests for the ``setup_shared`` orchestration body."""

    @pytest.mark.asyncio
    async def test_populates_all_context_keys(self, monkeypatch) -> None:
        """A fresh ctx is fully populated with every shared service key."""
        monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", "/data")

        from chaoscypher_neuron.setup.shared import setup_shared

        ctx: dict = {}
        with _patch_shared_deps(db_info=MagicMock()):
            await setup_shared(ctx)

        expected_keys = {
            "database_repository",
            "config_manager",
            "settings",
            "engine_settings",
            "current_database",
            "search_repository",
            "graph_repository",
            "worker_session",
            "llm_provider",
            "llm_service",
            "storage_adapter",
        }
        assert expected_keys <= set(ctx)
        assert ctx["current_database"] == "test_db"

    @pytest.mark.asyncio
    async def test_connects_adapter_and_queue(self, monkeypatch) -> None:
        """The adapter is connected and the Valkey queue connect is awaited."""
        monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", "/data")

        from chaoscypher_neuron.setup.shared import setup_shared

        ctx: dict = {}
        with _patch_shared_deps(db_info=MagicMock()) as p:
            await setup_shared(ctx)

        p["adapter_instance"].connect.assert_called_once()
        p["register_worker_adapter"].assert_called_once_with(p["adapter_instance"])
        p["queue_connect"].assert_awaited_once_with(p["settings"])

    @pytest.mark.asyncio
    async def test_creates_database_when_missing(self, monkeypatch) -> None:
        """When get_database returns None, create_database is called."""
        monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", "/data")

        from chaoscypher_neuron.setup.shared import setup_shared

        ctx: dict = {}
        with _patch_shared_deps(db_info=None) as p:
            await setup_shared(ctx)

        p["db_repo_instance"].create_database.assert_called_once_with("test_db")

    @pytest.mark.asyncio
    async def test_skips_create_when_database_present(self, monkeypatch) -> None:
        """When get_database returns a record, create_database is NOT called."""
        monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", "/data")

        from chaoscypher_neuron.setup.shared import setup_shared

        ctx: dict = {}
        with _patch_shared_deps(db_info=MagicMock()) as p:
            await setup_shared(ctx)

        p["db_repo_instance"].create_database.assert_not_called()


# ============================================================================
# TARGET 2 — config.py :: _get_default_config
# ============================================================================


def _ollama_settings(*, enabled_flags):
    """Build a settings mock whose ollama_instances honour ``enabled_flags``."""
    settings = MagicMock()
    settings.llm.chat_provider = "ollama"
    instances = []
    for flag in enabled_flags:
        inst = MagicMock()
        inst.enabled = flag
        instances.append(inst)
    settings.llm.ollama_instances = instances
    settings.workers.operations_max_concurrent = 8
    return settings


class TestGetDefaultConfig:
    """Tests for ``_get_default_config`` provider-aware concurrency."""

    def test_ollama_uses_enabled_instance_count(self) -> None:
        """Ollama provider sets llm max_concurrent to the enabled instance count."""
        from chaoscypher_neuron import config

        settings = _ollama_settings(enabled_flags=[True, False])
        with patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=settings,
        ):
            result = config._get_default_config()

        # Only one instance is enabled.
        assert result["llm_worker"]["max_concurrent"] == 1
        assert result["operations_worker"]["max_concurrent"] == 8

    def test_ollama_counts_all_enabled(self) -> None:
        """All-enabled Ollama instances raise concurrency to the full count."""
        from chaoscypher_neuron import config

        settings = _ollama_settings(enabled_flags=[True, True, True])
        with patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=settings,
        ):
            result = config._get_default_config()

        assert result["llm_worker"]["max_concurrent"] == 3

    def test_non_ollama_provider_defaults_to_one(self) -> None:
        """A non-Ollama provider keeps llm max_concurrent at 1."""
        from chaoscypher_neuron import config

        settings = MagicMock()
        settings.llm.chat_provider = "openai"
        settings.llm.ollama_instances = []
        settings.workers.operations_max_concurrent = 8

        with patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=settings,
        ):
            result = config._get_default_config()

        assert result["llm_worker"]["max_concurrent"] == 1

    def test_inner_except_falls_back_to_one(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        """An error reading settings falls back to max_concurrent=1 + debug log."""
        from chaoscypher_neuron import config

        with (
            patch(
                "chaoscypher_core.app_config.get_settings",
                side_effect=ValueError("boom"),
            ),
            caplog.at_level(logging.DEBUG),
        ):
            result = config._get_default_config()

        # Inner except swallows the error and keeps the single-instance default,
        # but the rest of the config still comes from TimeoutSettings/etc.
        assert result["llm_worker"]["max_concurrent"] == 1
        assert any("could_not_determine_instance_count" in r.message for r in caplog.records)

    def test_outer_except_uses_hardcoded_fallback(
        self,
        caplog: pytest.LogCaptureFixture,
        structlog_for_caplog: None,
    ) -> None:
        """When TimeoutSettings construction fails, the hardcoded dict is returned."""
        from chaoscypher_neuron import config

        with (
            patch(
                "chaoscypher_core.app_config.TimeoutSettings",
                side_effect=ValueError("cannot build timeouts"),
            ),
            caplog.at_level(logging.WARNING),
        ):
            result = config._get_default_config()

        assert result["llm_worker"]["max_concurrent"] == 1
        assert result["llm_worker"]["timeout"] == 3600
        assert result["operations_worker"]["max_concurrent"] == 8
        assert result["operations_worker"]["timeout"] == 3600
        assert any("settings_load_failed_using_fallbacks" in r.message for r in caplog.records)

    def test_get_defaults_builds_on_first_call(self) -> None:
        """``_get_defaults`` (cached) delegates to ``_get_default_config`` once cleared."""
        from chaoscypher_neuron import config

        sentinel = {"llm_worker": {"sentinel": True}}
        config._get_defaults.cache_clear()
        try:
            with patch.object(config, "_get_default_config", return_value=sentinel) as mock_build:
                first = config._get_defaults()
                second = config._get_defaults()

            assert first is sentinel
            assert second is sentinel
            # Cached: the builder runs exactly once despite two calls.
            mock_build.assert_called_once()
        finally:
            config._get_defaults.cache_clear()


# ============================================================================
# TARGET 3 — setup/ops_handlers.py :: real handler closures + bare-callable wrap
# ============================================================================


def _ops_ctx() -> dict:
    """Minimal worker context for setup_operations_handlers."""
    settings = MagicMock()
    settings.pagination.trigger_history_limit = 100
    return {
        "settings": settings,
        "engine_settings": MagicMock(),
        "current_database": "test_db",
        "graph_repository": MagicMock(),
        "search_repository": MagicMock(),
        "config_manager": MagicMock(),
        "llm_provider": MagicMock(),
        "llm_service": MagicMock(),
        "storage_adapter": MagicMock(),
    }


@contextlib.contextmanager
def _patch_ops_deps():
    """Patch ops_handlers function-local imports; expose the queue_client + core fns."""
    stack = contextlib.ExitStack()
    p = {}
    with stack:
        stack.enter_context(patch("chaoscypher_core.operations.bulk.BulkOperationsService"))
        stack.enter_context(
            patch("chaoscypher_core.operations.export_operations_service.ExportOperationsService")
        )
        stack.enter_context(patch("chaoscypher_core.operations.importing.ImportOperationsService"))
        stack.enter_context(
            patch(
                "chaoscypher_core.operations.workflow_operations_service.WorkflowOperationsService"
            )
        )
        stack.enter_context(
            patch("chaoscypher_neuron.handlers.quality_scores.register_quality_score_handler")
        )
        p["queue_client"] = stack.enter_context(patch("chaoscypher_core.queue.queue_client"))
        stack.enter_context(patch("chaoscypher_core.adapters.embedding.create_embedding_provider"))
        stack.enter_context(patch("chaoscypher_core.services.search.engine.index.IndexingService"))
        stack.enter_context(patch("chaoscypher_core.services.sources.SourceProcessingService"))
        stack.enter_context(
            patch("chaoscypher_core.operations.sources.processing.SourceFileValidators")
        )
        stack.enter_context(patch("chaoscypher_core.utils.chunk.ChunkingService"))
        trigger_exec = stack.enter_context(
            patch("chaoscypher_core.services.workflows.triggers.engine.executor.TriggerExecutor")
        )
        trigger_exec.return_value.start = AsyncMock()
        trigger_exec.return_value.stop = AsyncMock()
        stack.enter_context(patch("chaoscypher_core.factories.get_tool_service"))
        stack.enter_context(patch("chaoscypher_core.factories.get_trigger_service"))
        stack.enter_context(patch("chaoscypher_core.factories.get_workflow_service"))
        stack.enter_context(
            patch("chaoscypher_core.templates.default_templates.get_all_default_templates")
        )
        stack.enter_context(
            patch("chaoscypher_core.operations.workflows.orchestrator.execute_workflow_task")
        )

        # Core handler functions invoked by the real closures — AsyncMocks so
        # dispatch flows through the closure body and into them.
        p["handle_fetch_url"] = stack.enter_context(
            patch(
                "chaoscypher_core.operations.sources.url_fetch_handler.handle_fetch_url",
                new_callable=AsyncMock,
            )
        )
        p["handle_rebuild"] = stack.enter_context(
            patch(
                "chaoscypher_core.operations.rebuild_handler.handle_rebuild_search_indexes",
                new_callable=AsyncMock,
            )
        )
        p["handle_snapshot"] = stack.enter_context(
            patch(
                "chaoscypher_core.operations.graph_snapshot_handler.handle_build_graph_snapshot",
                new_callable=AsyncMock,
            )
        )
        yield p


def _registered_handler(queue_client_mock, op_name):
    """Pull the closure registered for ``op_name`` from queue_client.register_handlers."""
    for call in queue_client_mock.register_handlers.call_args_list:
        handler_map = call.args[1]
        if op_name in handler_map:
            return handler_map[op_name]
    raise AssertionError(f"{op_name} was never registered")


class TestOpsHandlerClosures:
    """Dispatch through the REAL handler closures registered by setup_operations_handlers."""

    @pytest.mark.asyncio
    async def test_fetch_url_closure_bridges_to_core(self) -> None:
        """_fetch_url_handler forwards to handle_fetch_url with the bound service."""
        from chaoscypher_core.constants import OP_FETCH_URL
        from chaoscypher_neuron.setup.ops_handlers import setup_operations_handlers

        ctx = _ops_ctx()
        with _patch_ops_deps() as p:
            await setup_operations_handlers(ctx)
            handler = _registered_handler(p["queue_client"], OP_FETCH_URL)

            p["handle_fetch_url"].return_value = {"ok": True}
            result = await handler({"url": "x"}, {"m": 1}, "task-1")

        assert result == {"ok": True}
        p["handle_fetch_url"].assert_awaited_once()
        kwargs = p["handle_fetch_url"].await_args.kwargs
        assert kwargs["data"] == {"url": "x"}
        assert kwargs["metadata"] == {"m": 1}
        assert kwargs["task_id"] == "task-1"
        assert "source_processing_service" in kwargs

    @pytest.mark.asyncio
    async def test_rebuild_closure_bridges_to_core(self) -> None:
        """_rebuild_handler forwards to handle_rebuild_search_indexes with shared deps."""
        from chaoscypher_core.constants import OP_REBUILD_SEARCH_INDEXES
        from chaoscypher_neuron.setup.ops_handlers import setup_operations_handlers

        ctx = _ops_ctx()
        with _patch_ops_deps() as p:
            await setup_operations_handlers(ctx)
            handler = _registered_handler(p["queue_client"], OP_REBUILD_SEARCH_INDEXES)

            p["handle_rebuild"].return_value = {"rebuilt": 3}
            result = await handler({"scope": "all"}, None, "task-2")

        assert result == {"rebuilt": 3}
        p["handle_rebuild"].assert_awaited_once()
        kwargs = p["handle_rebuild"].await_args.kwargs
        assert kwargs["data"] == {"scope": "all"}
        assert kwargs["task_id"] == "task-2"
        for dep in (
            "search_repository",
            "graph_repository",
            "indexing_service",
            "storage_adapter",
            "engine_settings",
        ):
            assert dep in kwargs

    @pytest.mark.asyncio
    async def test_graph_snapshot_closure_bridges_to_core(self) -> None:
        """_graph_snapshot_handler forwards to handle_build_graph_snapshot."""
        from chaoscypher_core.constants import OP_BUILD_GRAPH_SNAPSHOT
        from chaoscypher_neuron.setup.ops_handlers import setup_operations_handlers

        ctx = _ops_ctx()
        with _patch_ops_deps() as p:
            await setup_operations_handlers(ctx)
            handler = _registered_handler(p["queue_client"], OP_BUILD_GRAPH_SNAPSHOT)

            p["handle_snapshot"].return_value = {"snapshot": "ok"}
            result = await handler({"graph": "g"}, {"meta": 2}, "task-3")

        assert result == {"snapshot": "ok"}
        p["handle_snapshot"].assert_awaited_once()
        kwargs = p["handle_snapshot"].await_args.kwargs
        assert kwargs["data"] == {"graph": "g"}
        assert kwargs["adapter"] is ctx["storage_adapter"]
        assert kwargs["metadata"] == {"meta": 2}
        assert kwargs["task_id"] == "task-3"


class TestBuildLlmHandlerSpecsBareCallable:
    """build_llm_handler_specs wraps bare callables from a service (line 127-128)."""

    def test_bare_callable_wrapped_with_retry_on_crash_false(self) -> None:
        """A bare callable in operation_handlers becomes HandlerSpec(retry_on_crash=False)."""
        from chaoscypher_core.constants import OP_EXTRACT_CHUNK
        from chaoscypher_neuron.setup.ops_handlers import build_llm_handler_specs

        bare = AsyncMock()
        service = MagicMock()
        service.operation_handlers = {OP_EXTRACT_CHUNK: bare}  # bare callable, not HandlerSpec

        specs = build_llm_handler_specs(service)

        assert OP_EXTRACT_CHUNK in specs
        assert isinstance(specs[OP_EXTRACT_CHUNK], HandlerSpec)
        assert specs[OP_EXTRACT_CHUNK].handler is bare
        assert specs[OP_EXTRACT_CHUNK].retry_on_crash is False
