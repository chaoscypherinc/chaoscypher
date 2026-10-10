# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: the fatal-chunk-error path releases the slot AND dispatches.

Audit fix F41's slot-lifetime invariant is written verbatim in
``import_service._import_analysis_handler``: a claim must be paired with
either a success-finalize or a failure-release, and a failure-release must
call ``trigger_next_waiting_extraction``. ``_handle_chunk_failure``'s
``is_fatal_error`` arm released the slot (``fail_extraction_job`` +
``fail_extraction``, so status=ERROR frees the CAS) and then simply ended —
the whole module contained no reference to the trigger.

Why that stranded a source permanently rather than merely delaying it: a
source that loses the ``try_claim_extraction`` CAS is parked by
``mark_extraction_waiting``, which writes a row breadcrumb while its own
``OP_IMPORT_ANALYSIS`` task completes *successfully*. The waiter therefore
has no queue task for the reconciler to find, and the only code that reads
the breadcrumb is ``trigger_next_waiting_extraction``.

Contract:
    1. ``fail_extraction`` is still called (existing behaviour).
    2. ``trigger_next_waiting_extraction`` is awaited with
       ``(adapter, database_name, settings)``.
    3. A dispatch failure is logged and suppressed, never raised — the
       original extraction error is what the queue must see.
    4. The non-fatal arm does not dispatch (it finalizes instead).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from chaoscypher_core.exceptions import ModelCapabilityError


_TRIGGER = (
    "chaoscypher_core.operations.extraction.chunk_extraction_service."
    "trigger_next_waiting_extraction"
)


def _service() -> object:
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    return ChunkExtractionOperationsService(source_repository=MagicMock())


def _adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.get_chunk_task = MagicMock(return_value={"retry_count": 0})
    adapter.fail_chunk_task = MagicMock()
    adapter.fail_extraction_job = MagicMock()
    adapter.fail_extraction = MagicMock()
    adapter.get_extraction_job = MagicMock(return_value={"source_id": "src-A"})
    return adapter


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.retries = MagicMock(extraction_chunk_max=3)
    settings.priorities = MagicMock(background=10)
    return settings


async def _run_fatal(service: object, adapter: MagicMock, trigger: AsyncMock) -> dict:
    with patch(_TRIGGER, new=trigger):
        return await service._handle_chunk_failure(
            adapter=adapter,
            exc=ModelCapabilityError("model gone"),
            chunk_task_id="t1",
            job_id="j1",
            database_name="default",
            chunk_index=0,
            settings=_settings(),
            data={"small_chunk_ids": []},
        )


@pytest.mark.asyncio
async def test_fatal_error_dispatches_next_waiting_source() -> None:
    """The arm that frees the slot must hand it to the waiting source."""
    adapter = _adapter()
    trigger = AsyncMock()

    result = await _run_fatal(_service(), adapter, trigger)

    assert result["success"] is False
    assert result["is_capability_error"] is True

    adapter.fail_extraction.assert_called_once()
    assert adapter.fail_extraction.call_args.args[0] == "src-A"

    trigger.assert_awaited_once()
    assert trigger.await_args.args[0] is adapter
    assert trigger.await_args.args[1] == "default"


@pytest.mark.asyncio
async def test_dispatch_failure_is_logged_and_suppressed() -> None:
    """A broken dispatch must not mask the extraction error being reported."""
    adapter = _adapter()

    async def _boom(*_a: object, **_k: object) -> None:
        raise OSError("dispatch failed")

    with structlog.testing.capture_logs() as cap:
        result = await _run_fatal(_service(), adapter, AsyncMock(side_effect=_boom))

    assert result["success"] is False
    assert "trigger_next_waiting_extraction_failed" in [r["event"] for r in cap]


@pytest.mark.asyncio
async def test_no_extraction_job_does_not_dispatch() -> None:
    """No job row means ``fail_extraction`` never ran, so no slot was freed."""
    adapter = _adapter()
    adapter.get_extraction_job = MagicMock(return_value=None)
    trigger = AsyncMock()

    await _run_fatal(_service(), adapter, trigger)

    adapter.fail_extraction.assert_not_called()
    trigger.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_fatal_error_does_not_dispatch() -> None:
    """The non-fatal arm routes through the finalize helper, not the trigger."""
    adapter = _adapter()
    service = _service()
    service.queue_extract_chunk = AsyncMock()  # type: ignore[attr-defined]
    trigger = AsyncMock()

    with patch(_TRIGGER, new=trigger):
        result = await service._handle_chunk_failure(
            adapter=adapter,
            exc=RuntimeError("generic, non-capability failure"),
            chunk_task_id="t1",
            job_id="j1",
            database_name="default",
            chunk_index=0,
            settings=_settings(),
            data={"small_chunk_ids": []},
        )

    assert result["is_capability_error"] is False
    trigger.assert_not_awaited()
