# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-op dispatch tests — handler invocation contract for representative ops.

For each op, the test:
1. Runs setup_llm_handlers / setup_operations_handlers against the harness.
2. Confirms the op is registered on the correct queue.
3. Either dispatches directly (for stub handlers) or substitutes the registered
   handler with an AsyncMock before dispatching (for complex real handlers).
4. Asserts the mock was invoked correctly.

Substitution strategy
---------------------
Tests 2-6 replace the registered handler in the harness's ``_registered`` dict
with a ``HandlerSpec``-wrapped ``AsyncMock`` before dispatch.  This approach is
necessary because:

- ``patch.object(ServiceClass, "_method", ...)`` applied *after* setup cannot
  intercept bound methods already captured in ``HandlerSpec.handler`` or in
  ``functools.partial`` closures — the references were resolved at service
  instantiation time.
- The fetch_url and recalculate_quality_scores handlers are inner closures that
  reference captured locals at registration time; post-registration module
  patches cannot intercept them.

Wrapping the mock in ``HandlerSpec`` ensures the harness's
``dispatch`` helper (which does ``callable_handler = getattr(handler, 'handler',
handler)``) unwraps the spec and calls the mock directly rather than calling
the mock's auto-generated ``.handler`` child attribute.

Test 1 (chat_completion) uses the stub wired by the setup_done fixture and
dispatches without substitution.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.queue.handler_spec import HandlerSpec


# worker_harness.py lives in packages/neuron/tests/fixtures/ which is added to
# sys.path by conftest.py at runtime.  The same import-not-found suppression
# is used in conftest.py itself for the same reason.
sys.path.insert(0, str(Path(__file__).parent.parent / "fixtures"))
from worker_harness import WorkerHarness  # type: ignore[import-not-found]

# Import for side-effect (configure_logging at import time)
import chaoscypher_neuron.worker  # noqa: F401


@pytest.fixture
def setup_done(worker_harness: WorkerHarness) -> WorkerHarness:
    """Yield the harness after running both setup helpers.

    Mirrors the fixture from test_handler_registration.py: wires the stub
    llm_service and patches create_embedding_provider so setup can complete
    without real adapters.
    """
    from chaoscypher_core.constants import QUEUE_LLM
    from chaoscypher_neuron.setup import setup_llm_handlers, setup_operations_handlers

    async def _chat_completion_stub(
        data: dict,
        metadata: dict | None = None,
        task_id: str | None = None,
    ) -> dict:
        return {"echo": data}

    async def _tool_execution_stub(
        data: dict,
        metadata: dict | None = None,
        task_id: str | None = None,
    ) -> dict:
        return {"echo": data}

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

    with patch(
        "chaoscypher_core.adapters.embedding.create_embedding_provider",
        return_value=MagicMock(name="embedding_provider"),
    ):
        asyncio.run(setup_llm_handlers(worker_harness.ctx))
        asyncio.run(setup_operations_handlers(worker_harness.ctx))

    return worker_harness


def _substitute(harness: WorkerHarness, queue: str, op: str, mock: AsyncMock) -> None:
    """Replace the registered handler for (queue, op) with a HandlerSpec-wrapped mock.

    Wrapping in HandlerSpec ensures the harness's dispatch helper unwraps the
    spec (``getattr(handler, 'handler', handler)``) and calls the mock directly,
    rather than calling the mock's auto-generated ``.handler`` child attribute.
    """
    harness.queue._registered[queue][op] = HandlerSpec(handler=mock)


# ---------------------------------------------------------------------------
# Test 1: chat_completion — LLM queue, llm-service-mediated registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_chat_completion(setup_done: WorkerHarness) -> None:
    """chat_completion dispatched on the LLM queue invokes the stub handler."""
    result = await setup_done.queue.dispatch(
        "llm",
        "chat_completion",
        {"prompt": "hello"},
    )
    assert result == {"echo": {"prompt": "hello"}}


# ---------------------------------------------------------------------------
# Test 2: OP_EXTRACT_CHUNK — LLM queue, ChunkExtractionOperationsService
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_extract_chunk(setup_done: WorkerHarness) -> None:
    """OP_EXTRACT_CHUNK dispatched on the LLM queue invokes the chunk-extraction handler.

    Registration: HandlerSpec(handler=self._extract_chunk_handler) — bound method
    captured at service instantiation time.  Substitution is required because
    patching the class method after setup cannot intercept the already-captured
    bound method reference in the HandlerSpec.
    """
    from chaoscypher_core.constants import OP_EXTRACT_CHUNK

    assert OP_EXTRACT_CHUNK in setup_done.queue.registered_on("llm"), (
        f"{OP_EXTRACT_CHUNK!r} must be registered on the llm queue"
    )

    mock_body = AsyncMock(return_value={"chunk_id": "c1", "extracted": True})
    _substitute(setup_done, "llm", OP_EXTRACT_CHUNK, mock_body)

    result = await setup_done.queue.dispatch(
        "llm",
        OP_EXTRACT_CHUNK,
        {"chunk_id": "c1", "source_id": "s1", "database_name": "test_db"},
    )

    mock_body.assert_called_once_with(
        {"chunk_id": "c1", "source_id": "s1", "database_name": "test_db"},
        metadata=None,
        task_id=None,
    )
    assert result == {"chunk_id": "c1", "extracted": True}


# ---------------------------------------------------------------------------
# Test 3: OP_INDEX_DOCUMENT — Operations queue, ImportOperationsService
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_index_document(setup_done: WorkerHarness) -> None:
    """OP_INDEX_DOCUMENT dispatched on the Operations queue invokes the indexing handler.

    Registration: HandlerSpec(handler=self._index_document_handler).  Same
    substitution strategy as OP_EXTRACT_CHUNK.
    """
    from chaoscypher_core.constants import OP_INDEX_DOCUMENT

    assert OP_INDEX_DOCUMENT in setup_done.queue.registered_on("operations"), (
        f"{OP_INDEX_DOCUMENT!r} must be registered on the operations queue"
    )

    mock_body = AsyncMock(return_value={"indexed": True})
    _substitute(setup_done, "operations", OP_INDEX_DOCUMENT, mock_body)

    result = await setup_done.queue.dispatch(
        "operations",
        OP_INDEX_DOCUMENT,
        {"source_id": "s1", "database_name": "test_db"},
    )

    mock_body.assert_called_once_with(
        {"source_id": "s1", "database_name": "test_db"},
        metadata=None,
        task_id=None,
    )
    assert result == {"indexed": True}


# ---------------------------------------------------------------------------
# Test 4: OP_FETCH_URL — Operations queue, closure-style registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_fetch_url(setup_done: WorkerHarness) -> None:
    """OP_FETCH_URL dispatched on the Operations queue reaches an awaitable handler.

    Registration: inner closure inside _register_fetch_url_handler that
    captures handle_fetch_url via a local import executed at setup time.
    Post-creation module patches cannot intercept the captured reference, so
    we substitute the registered handler directly.
    """
    from chaoscypher_core.constants import OP_FETCH_URL

    assert OP_FETCH_URL in setup_done.queue.registered_on("operations"), (
        f"{OP_FETCH_URL!r} must be registered on the operations queue"
    )

    mock_body = AsyncMock(return_value={"fetched": True, "url": "https://example.com"})
    _substitute(setup_done, "operations", OP_FETCH_URL, mock_body)

    result = await setup_done.queue.dispatch(
        "operations",
        OP_FETCH_URL,
        {"url": "https://example.com"},
    )

    mock_body.assert_called_once_with({"url": "https://example.com"}, metadata=None, task_id=None)
    assert result == {"fetched": True, "url": "https://example.com"}


# ---------------------------------------------------------------------------
# Test 5: bulk_nodes — Operations queue, partial(bulk_nodes_handler, service)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_bulk_nodes(setup_done: WorkerHarness) -> None:
    """bulk_nodes dispatched on the Operations queue invokes the bulk-node handler.

    Registration: partial(bulk_nodes_handler, service) — the partial captures
    bulk_nodes_handler at service instantiation time.  Substitution is required
    for the same reason as OP_EXTRACT_CHUNK.
    """
    assert "bulk_nodes" in setup_done.queue.registered_on("operations"), (
        "'bulk_nodes' must be registered on the operations queue"
    )

    mock_body = AsyncMock(return_value={"created": 2, "failed": 0})
    _substitute(setup_done, "operations", "bulk_nodes", mock_body)

    result = await setup_done.queue.dispatch(
        "operations",
        "bulk_nodes",
        {"operations": [{"action": "create", "data": {"name": "A"}}]},
    )

    mock_body.assert_called_once_with(
        {"operations": [{"action": "create", "data": {"name": "A"}}]},
        metadata=None,
        task_id=None,
    )
    assert result == {"created": 2, "failed": 0}


# ---------------------------------------------------------------------------
# Test 6: recalculate_quality_scores — Operations queue, neuron-local closure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_recalculate_quality_scores(setup_done: WorkerHarness) -> None:
    """recalculate_quality_scores dispatched on the Operations queue reaches an awaitable.

    Registration: inner closure inside register_quality_score_handler — not
    reachable via module-level patching after setup.  Same direct-substitution
    strategy as OP_FETCH_URL.
    """
    op_name = "recalculate_quality_scores"

    assert op_name in setup_done.queue.registered_on("operations"), (
        f"{op_name!r} must be registered on the operations queue"
    )

    mock_body = AsyncMock(return_value={"recalculated_count": 3, "error_count": 0, "errors": []})
    _substitute(setup_done, "operations", op_name, mock_body)

    result = await setup_done.queue.dispatch(
        "operations",
        op_name,
        {"source_ids": ["s1", "s2", "s3"], "database_name": "test_db"},
    )

    mock_body.assert_called_once_with(
        {"source_ids": ["s1", "s2", "s3"], "database_name": "test_db"},
        metadata=None,
        task_id=None,
    )
    assert result["recalculated_count"] == 3
