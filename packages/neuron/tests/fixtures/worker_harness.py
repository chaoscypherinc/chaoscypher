# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Worker harness — stub WorkerContext + queue-client double for neuron tests.

The neuron worker's startup, handler registration, dispatch, and recovery
paths all consume a ``WorkerContext`` TypedDict and the module-level
``queue_client`` singleton. This harness builds a realistic ``WorkerContext``
populated with ``MagicMock``/``AsyncMock`` doubles for every key, and a
``RecordingQueueClient`` that captures ``register_handlers`` calls so tests
can assert what was registered on which queue.

Usage::

    def test_handler_registration_pin(worker_harness):
        # worker_harness.ctx is the stub WorkerContext
        # worker_harness.queue is the recording queue client
        # ... import and call setup_*_handlers(worker_harness.ctx) ...
        assert worker_harness.queue.registered_on("operations").keys() == {...}
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest


if TYPE_CHECKING:
    from chaoscypher_neuron.types import WorkerContext


HandlerFn = Callable[..., Awaitable[Any]]


@dataclass
class RecordingQueueClient:
    """Captures register_handlers calls for assertion in tests.

    Mirrors the relevant public surface of ``chaoscypher_core.queue.queue_client``
    used by setup_llm_handlers / setup_operations_handlers.
    """

    _registered: dict[str, dict[str, HandlerFn]] = field(default_factory=dict)

    def register_handlers(
        self,
        queue_name: str,
        handlers: dict[str, HandlerFn],
    ) -> None:
        """Record handlers registered on a given queue.

        Multiple calls for the same queue merge — later calls override earlier
        registrations for the same op (mirrors the real queue client).
        """
        self._registered.setdefault(queue_name, {}).update(handlers)

    def registered_on(self, queue_name: str) -> dict[str, HandlerFn]:
        """Return the handlers registered on ``queue_name``."""
        return dict(self._registered.get(queue_name, {}))

    def all_registered_ops(self) -> set[str]:
        """Every op registered across all queues."""
        ops: set[str] = set()
        for queue_handlers in self._registered.values():
            ops.update(queue_handlers.keys())
        return ops

    async def dispatch(
        self,
        queue_name: str,
        op: str,
        data: dict,
        metadata: dict | None = None,
        task_id: str | None = None,
    ) -> Any:
        """Invoke the registered handler for (queue_name, op) with a simulated message.

        Mirrors how QueueWorker._execute_handler invokes the handler — passes
        data, metadata, task_id positionally so HandlerSpec-style handlers
        receive them correctly.

        Raises:
            KeyError: if no handler is registered for (queue_name, op).
        """
        registered = self._registered.get(queue_name, {})
        if op not in registered:
            raise KeyError(
                f"op={op!r} not registered on queue={queue_name!r}; "
                f"registered ops: {sorted(registered.keys())}"
            )
        handler = registered[op]
        # Unwrap HandlerSpec if needed (mirrors test_handler_registration.py's _unwrap_handler).
        callable_handler = getattr(handler, "handler", handler)
        return await callable_handler(data, metadata=metadata, task_id=task_id)


@dataclass
class WorkerHarness:
    """Bundled stub WorkerContext + RecordingQueueClient for neuron tests."""

    ctx: WorkerContext
    queue: RecordingQueueClient


def _build_stub_ctx() -> WorkerContext:
    """Build a WorkerContext populated entirely with mocks.

    All keys are typed via TypedDict but the values are MagicMock/AsyncMock
    instances — the registration paths only call attributes that the mocks
    auto-spec, never reaching real adapters or LLM providers.
    """
    settings = MagicMock(name="settings")
    settings.current_database = "test_db"
    settings.source_recovery.reconcile_timeout_seconds = 30
    settings.queue_recovery.worker_reconcile_interval_seconds = 60
    settings.queue_recovery.heartbeat_ttl_seconds = 30
    settings.queue_recovery.heartbeat_refresh_interval_seconds = 10

    llm_service = MagicMock(name="llm_service")
    llm_service.register_handlers = MagicMock()  # called inside setup_llm_handlers

    ctx: WorkerContext = cast(
        "WorkerContext",
        {
            "settings": settings,
            "current_database": "test_db",
            "config_manager": MagicMock(name="config_manager"),
            "engine_settings": MagicMock(name="engine_settings"),
            "database_repository": MagicMock(name="database_repository"),
            "search_repository": MagicMock(name="search_repository"),
            "graph_repository": MagicMock(name="graph_repository"),
            "worker_session": MagicMock(name="worker_session"),
            "llm_provider": AsyncMock(name="llm_provider"),
            "llm_service": llm_service,
            "storage_adapter": MagicMock(name="storage_adapter"),
        },
    )
    return ctx


@pytest.fixture
def worker_harness(monkeypatch: pytest.MonkeyPatch) -> WorkerHarness:
    """Yield a fresh WorkerHarness with the module-level queue_client patched.

    The patch covers ``chaoscypher_core.queue.queue_client`` — every site
    in setup_llm_handlers / setup_operations_handlers that calls
    ``queue_client.register_handlers(...)`` lands on the harness's
    RecordingQueueClient instead of the real Valkey client.
    """
    import chaoscypher_neuron.worker  # noqa: F401 — import-for-side-effect: settle worker.py's import-time configure_logging() before fixture activates  # noqa: E501

    queue = RecordingQueueClient()

    # Patch the queue_client singleton at every observed import path.
    # ops_handlers.py uses deferred `from chaoscypher_core.queue import queue_client`
    # inside functions, so patching the attribute on the module object is the
    # correct target for those sites.
    import chaoscypher_core.queue as core_queue_module

    monkeypatch.setattr(core_queue_module, "queue_client", queue)

    # Also patch the re-exported name in places that re-imported the singleton
    # at module top-level. Add more here if registration tests fail with the
    # real client being touched.
    #
    # Patch settings_sync and setup.shared — module-level queue_client bindings
    # that Phase 3 tests reach via _run_startup_recovery's context wiring.
    try:
        import chaoscypher_neuron.settings_sync as settings_sync_mod

        monkeypatch.setattr(settings_sync_mod, "queue_client", queue, raising=False)
    except ImportError, AttributeError:
        pass
    try:
        import chaoscypher_neuron.setup.shared as shared_setup_mod

        monkeypatch.setattr(shared_setup_mod, "queue_client", queue, raising=False)
    except ImportError, AttributeError:
        pass
    try:
        import chaoscypher_neuron.setup.ops_handlers as ops_setup

        monkeypatch.setattr(ops_setup, "queue_client", queue, raising=False)
    except ImportError, AttributeError:
        pass
    try:
        import chaoscypher_neuron.setup.llm_handlers as llm_setup

        monkeypatch.setattr(llm_setup, "queue_client", queue, raising=False)
    except ImportError, AttributeError:
        pass

    # chat_completion, template_embedding, and quality_scores import queue_client
    # at module top-level, so patch those references directly.
    try:
        import chaoscypher_neuron.handlers.chat_completion as chat_mod

        monkeypatch.setattr(chat_mod, "queue_client", queue, raising=False)
    except ImportError, AttributeError:
        pass
    try:
        import chaoscypher_neuron.handlers.template_embedding as tmpl_mod

        monkeypatch.setattr(tmpl_mod, "queue_client", queue, raising=False)
    except ImportError, AttributeError:
        pass
    try:
        import chaoscypher_neuron.handlers.quality_scores as qs_mod

        monkeypatch.setattr(qs_mod, "queue_client", queue, raising=False)
    except ImportError, AttributeError:
        pass

    # Core service modules that bind queue_client at module top-level.
    # These are reached by setup_llm_handlers / setup_operations_handlers when
    # they instantiate and call register_handlers() on the real service classes.
    # The patch at chaoscypher_core.queue.queue_client covers deferred-import
    # sites; these explicit patches cover the already-bound module attributes.
    _core_queue_modules = [
        "chaoscypher_core.operations.bulk.bulk_service",
        "chaoscypher_core.operations.export_operations_service",
        "chaoscypher_core.operations.importing.import_service",
        "chaoscypher_core.operations.importing.vision_operations_service",
        "chaoscypher_core.operations.workflow_operations_service",
        "chaoscypher_core.operations.extraction.chunk_extraction_service",
        "chaoscypher_core.llm_queue.queue_service",
    ]
    for _mod_name in _core_queue_modules:
        try:
            import importlib

            _mod = importlib.import_module(_mod_name)
            monkeypatch.setattr(_mod, "queue_client", queue, raising=False)
        except ImportError, AttributeError:
            pass

    return WorkerHarness(ctx=_build_stub_ctx(), queue=queue)
