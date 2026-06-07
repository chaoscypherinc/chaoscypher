# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pin every OPERATION_QUEUE_ROUTING entry to its registered handler.

Each entry in chaoscypher_core.constants.OPERATION_QUEUE_ROUTING must be
registered by either setup_llm_handlers or setup_operations_handlers
on its declared queue, and the value must be an awaitable callable (or a
HandlerSpec wrapping one).
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

# Import for side-effect (configure_logging at import time)
import chaoscypher_neuron.worker  # noqa: F401
from chaoscypher_core.constants import OPERATION_QUEUE_ROUTING
from chaoscypher_core.queue.handler_spec import HandlerSpec


# worker_harness.py lives in packages/neuron/tests/fixtures/ which is added to
# sys.path by conftest.py at runtime.  The same import-not-found suppression
# is used in conftest.py itself for the same reason.
sys.path.insert(0, str(Path(__file__).parent.parent / "fixtures"))
from worker_harness import WorkerHarness  # type: ignore[import-not-found]


def _unwrap_handler(registered_value: object) -> object:
    """Return the bare callable from a HandlerSpec or a plain callable.

    The RecordingQueueClient stores whatever is passed to register_handlers
    as-is (mirroring the real queue client pre-normalization).  Some
    services pass HandlerSpec instances; others pass bare callables.  Either
    form is valid — the real queue client normalises both.  The test must
    accept both and check the underlying callable.
    """
    if isinstance(registered_value, HandlerSpec):
        return registered_value.handler
    return registered_value


@pytest.fixture
def setup_done(worker_harness: WorkerHarness) -> WorkerHarness:
    """Yield the harness after running both setup helpers.

    Configures the stub llm_service so that its register_handlers() side
    effect calls queue_client.register_handlers(QUEUE_LLM, {...}) with
    async-callable placeholders for the two ops it owns (chat_completion,
    tool_execution).  Without this, the MagicMock's no-op register_handlers
    would silently skip those two routing-table entries.

    Also patches create_embedding_provider (called inside
    setup_operations_handlers) to avoid needing a real embedding backend.
    """
    import asyncio
    from unittest.mock import MagicMock, patch

    from chaoscypher_core.constants import QUEUE_LLM
    from chaoscypher_neuron.setup import setup_llm_handlers, setup_operations_handlers

    # Wire the stub llm_service so its register_handlers() actually records
    # the LLM queue ops that LLMQueueService.register_handlers() would register.
    async def _chat_completion_stub(
        data: dict,
        metadata: dict | None = None,
        task_id: str | None = None,
    ) -> dict:
        return {}

    async def _tool_execution_stub(
        data: dict,
        metadata: dict | None = None,
        task_id: str | None = None,
    ) -> dict:
        return {}

    def _llm_service_register_handlers_side_effect() -> None:
        worker_harness.queue.register_handlers(
            QUEUE_LLM,
            {
                "chat_completion": _chat_completion_stub,
                "tool_execution": _tool_execution_stub,
            },
        )

    worker_harness.ctx[
        "llm_service"
    ].register_handlers.side_effect = _llm_service_register_handlers_side_effect

    # create_embedding_provider inspects engine_settings.embedding.provider for a
    # real string value and raises ValueError on MagicMock — patch it out.
    with patch(
        "chaoscypher_core.adapters.embedding.create_embedding_provider",
        return_value=MagicMock(name="embedding_provider"),
    ):
        asyncio.run(setup_llm_handlers(worker_harness.ctx))
        asyncio.run(setup_operations_handlers(worker_harness.ctx))

    return worker_harness


@pytest.mark.parametrize(
    ("op_name", "expected_queue"),
    sorted(OPERATION_QUEUE_ROUTING.items()),
    ids=sorted(OPERATION_QUEUE_ROUTING.keys()),
)
def test_routing_entry_is_registered(
    op_name: str,
    expected_queue: str,
    setup_done: WorkerHarness,
) -> None:
    """Each OPERATION_QUEUE_ROUTING entry has a handler registered on the right queue."""
    registered = setup_done.queue.registered_on(expected_queue)
    assert op_name in registered, (
        f"{op_name!r} should be registered on queue={expected_queue!r}, "
        f"but the {expected_queue!r} queue has only: {sorted(registered.keys())}"
    )
    raw = registered[op_name]
    handler = _unwrap_handler(raw)
    assert callable(handler), f"{op_name!r}'s registered handler {handler!r} is not callable"
    assert inspect.iscoroutinefunction(handler), (
        f"{op_name!r}'s handler {handler!r} must be an async coroutine function"
    )


def test_total_registered_ops_matches_routing_table(setup_done: WorkerHarness) -> None:
    """No op is registered that's not in OPERATION_QUEUE_ROUTING; no entry is unregistered."""
    registered = setup_done.queue.all_registered_ops()
    declared = set(OPERATION_QUEUE_ROUTING.keys())
    missing = declared - registered
    extra = registered - declared
    assert not missing, (
        f"OPERATION_QUEUE_ROUTING entries with NO registered handler: {sorted(missing)}"
    )
    assert not extra, f"Registered handlers NOT in OPERATION_QUEUE_ROUTING: {sorted(extra)}"


def test_setup_llm_handlers_idempotent(worker_harness: WorkerHarness) -> None:
    """Calling setup_llm_handlers twice produces the same registered op set.

    Pinned because chaoscypher_neuron.settings_sync.listen_for_settings_changes
    re-runs the setup helpers on every config change at runtime. A second call
    must not duplicate, drop, or otherwise mutate the registered-op set.
    """
    import asyncio

    from chaoscypher_core.constants import QUEUE_LLM
    from chaoscypher_neuron.setup import setup_llm_handlers

    # Wire the stub llm_service so its register_handlers() actually records
    # the LLM queue ops — same side-effect used by the setup_done fixture.
    async def _chat_completion_stub(
        data: dict,
        metadata: dict | None = None,
        task_id: str | None = None,
    ) -> dict:
        return {}

    async def _tool_execution_stub(
        data: dict,
        metadata: dict | None = None,
        task_id: str | None = None,
    ) -> dict:
        return {}

    def _llm_service_register_handlers_side_effect() -> None:
        worker_harness.queue.register_handlers(
            QUEUE_LLM,
            {
                "chat_completion": _chat_completion_stub,
                "tool_execution": _tool_execution_stub,
            },
        )

    worker_harness.ctx[
        "llm_service"
    ].register_handlers.side_effect = _llm_service_register_handlers_side_effect

    asyncio.run(setup_llm_handlers(worker_harness.ctx))
    after_first = set(worker_harness.queue.registered_on("llm").keys())

    asyncio.run(setup_llm_handlers(worker_harness.ctx))
    after_second = set(worker_harness.queue.registered_on("llm").keys())

    assert after_first == after_second, (
        f"LLM-queue op set changed between two setup_llm_handlers calls. "
        f"First: {sorted(after_first)}; second: {sorted(after_second)}"
    )


def test_setup_operations_handlers_idempotent(worker_harness: WorkerHarness) -> None:
    """Calling setup_operations_handlers twice produces the same registered op set.

    Same hot-reload rationale as test_setup_llm_handlers_idempotent.
    """
    import asyncio
    from unittest.mock import MagicMock, patch

    from chaoscypher_neuron.setup import setup_operations_handlers

    # create_embedding_provider inspects engine_settings.embedding.provider for a
    # real string value and raises ValueError on MagicMock — patch it out.
    with patch(
        "chaoscypher_core.adapters.embedding.create_embedding_provider",
        return_value=MagicMock(name="embedding_provider"),
    ):
        asyncio.run(setup_operations_handlers(worker_harness.ctx))
        after_first = set(worker_harness.queue.registered_on("operations").keys())

        asyncio.run(setup_operations_handlers(worker_harness.ctx))
        after_second = set(worker_harness.queue.registered_on("operations").keys())

    assert after_first == after_second, (
        f"operations-queue op set changed between two setup_operations_handlers calls. "
        f"First: {sorted(after_first)}; second: {sorted(after_second)}"
    )
