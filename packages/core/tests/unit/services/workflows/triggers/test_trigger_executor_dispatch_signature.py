# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Signature-binding tests pinning TriggerExecutor's dispatch kwargs to the
REAL ``execute_workflow_task`` signature.

Every other TriggerExecutor test wires ``execute_workflow_fn`` to a bare
``MagicMock``/``AsyncMock``, which accepts arbitrary kwargs and so hides
drift between the dispatch call sites and the callee's actual parameter
list (entry 614: both call sites passed a ``discovery_service`` kwarg the
real ``execute_workflow_task`` had already dropped, and the resulting
``TypeError`` was silently absorbed into a ``success=False`` stats row).

These tests use ``inspect.signature(execute_workflow_task).bind(...)`` on
the exact kwargs the executor passes at call time, so a call site handing
the callee an argument it no longer accepts fails the test instead of
being swallowed.
"""

import inspect
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.operations.workflows.orchestrator import execute_workflow_task
from chaoscypher_core.services.workflows.triggers.engine.executor import TriggerExecutor


def _binding_spy() -> tuple[object, list[dict]]:
    """Build an async stub that binds every call against the REAL
    ``execute_workflow_task`` signature before recording it.

    Returns:
        A ``(spy, calls)`` pair. ``spy`` is the async callable to inject as
        ``execute_workflow_fn``; ``calls`` accumulates the kwargs of every
        call that bound successfully (calls that fail to bind raise
        ``TypeError`` instead of being recorded).
    """
    calls: list[dict] = []

    async def spy(**kwargs: object) -> dict[str, str]:
        """Bind kwargs against the real signature, then record and return."""
        inspect.signature(execute_workflow_task).bind(**kwargs)
        calls.append(kwargs)
        return {"execution_id": "exec-1"}

    return spy, calls


def _executor(execute_fn: object, workflow_service: MagicMock) -> TriggerExecutor:
    """Build a TriggerExecutor wired to ``execute_fn`` and stubbed collaborators."""
    return TriggerExecutor(
        trigger_service=MagicMock(),
        workflow_service=workflow_service,
        tool_service=MagicMock(),
        llm_service=MagicMock(),
        graph_repository=MagicMock(),
        search_repository=MagicMock(),
        database_name="test_db",
        execute_workflow_fn=execute_fn,
    )


@pytest.mark.asyncio
async def test_sync_dispatch_kwargs_bind_against_real_execute_workflow_task_signature() -> None:
    """_dispatch_trigger_workflow's synchronous (non-auto-embed) call site must
    invoke execute_workflow_fn with kwargs the real execute_workflow_task accepts.
    """
    workflow_service = MagicMock()
    workflow_service.get_workflow.return_value = {"id": "wf-1", "name": "Regular workflow"}
    spy, calls = _binding_spy()
    executor = _executor(spy, workflow_service)

    trigger = {"id": "t-1", "name": "Regular trigger", "workflow_id": "wf-1"}

    await executor._dispatch_trigger_workflow(
        trigger=trigger,
        event_data={"entity_id": "n1"},
        event_source="node.created",
        is_auto_embed=False,
    )

    assert len(calls) == 1, "execute_workflow_fn should have bound and been called once"
    assert executor.stats_tracker.trigger_stats["t-1"].successful == 1
    assert executor.stats_tracker.trigger_stats["t-1"].failed == 0


@pytest.mark.asyncio
async def test_async_dispatch_kwargs_bind_against_real_execute_workflow_task_signature() -> None:
    """_execute_workflow_async's fire-and-forget (auto-embed) call site must
    invoke execute_workflow_fn with kwargs the real execute_workflow_task accepts.
    """
    workflow_service = MagicMock()
    workflow_service.get_workflow.return_value = {
        "id": "system_workflow_generate_embeddings_v1",
        "name": "Auto-embed workflow",
    }
    spy, calls = _binding_spy()
    executor = _executor(spy, workflow_service)

    trigger = {
        "id": "t-2",
        "name": "Auto-embed trigger",
        "workflow_id": "system_workflow_generate_embeddings_v1",
    }

    await executor._execute_workflow_async(
        trigger=trigger,
        merged_inputs={"entity_id": "n2"},
        event_source="node.created",
        start_time=0.0,
    )

    assert len(calls) == 1, "execute_workflow_fn should have bound and been called once"
    assert executor.stats_tracker.trigger_stats["t-2"].successful == 1
    assert executor.stats_tracker.trigger_stats["t-2"].failed == 0
