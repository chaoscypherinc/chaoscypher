# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: the no-groups arm releases the slot AND dispatches the next.

``_import_analysis_handler`` states Audit fix F41's slot-lifetime invariant
in a comment above its own claim: a claim must be paired with a
success-finalize or a failure-release, and a failure-release must call
``trigger_next_waiting_extraction``. The handler's ``except`` block does both
(110 lines below), but its ``if not hierarchical_groups:`` early return
called ``fail_extraction_job`` + ``fail_extraction`` -- freeing the single
extraction slot -- and then returned an ``error`` dict without dispatching.

Because a source that lost the ``try_claim_extraction`` CAS is parked with a
row breadcrumb and no queue task, nothing else in the tree re-dispatches it:
``get_oldest_waiting_extraction``'s only caller is the trigger this arm
skipped.

Contract:
    1. The source is still failed (existing behaviour) and the handler still
       returns its ``{"status": "error"}`` dict rather than raising.
    2. ``trigger_next_waiting_extraction`` is awaited with
       ``(adapter, current_database, settings)``.
    3. A dispatch failure is logged and suppressed -- the caller still gets
       the error dict describing the real problem.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from chaoscypher_core.operations.importing.import_service import (
    ImportOperationsService,
)


_TRIGGER = "chaoscypher_core.operations.importing.import_service.trigger_next_waiting_extraction"
_ORCH = "chaoscypher_core.services.sources.engine.extraction.orchestration"
_SVC = "chaoscypher_core.operations.importing.import_service"

_DETECT_RESULT = {
    "domain": MagicMock(),
    "detected_domain": "generic",
    "confidence": 0.5,
    "ranking": [],
    "low_confidence": False,
    "entity_guidance": "",
    "relationship_guidance": "",
}


def _make_service(adapter: MagicMock) -> ImportOperationsService:
    from chaoscypher_core.settings import EngineSettings

    engine_settings = EngineSettings(current_database="default")
    engine_settings.chunking.target_group_tokens = 900
    engine_settings.chunking.group_overlap = 1
    return ImportOperationsService(
        graph_repository=MagicMock(),
        config_manager=MagicMock(),
        source_manager=MagicMock(),
        trigger_service=MagicMock(),
        llm_service=AsyncMock(),
        source_repository=adapter,
        chunking_service=MagicMock(),
        indexing_service=MagicMock(),
        engine_settings=engine_settings,
    )


def _adapter() -> MagicMock:
    adapter = MagicMock(unsafe=True)
    adapter.assert_extractable.return_value = None
    adapter.clear_extraction_waiting.return_value = None
    adapter.update_step_progress.return_value = None
    adapter.get_active_extraction_job.return_value = None
    adapter.get_source.return_value = {}
    adapter.fail_extraction_job.return_value = None
    adapter.fail_extraction.return_value = None
    return adapter


async def _run_no_groups(service: ImportOperationsService, trigger: AsyncMock) -> dict:
    settings = MagicMock()
    settings.current_database = "default"

    with (
        patch("chaoscypher_core.app_config.get_settings", return_value=settings),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            return_value=MagicMock(paused=False),
        ),
        patch(f"{_SVC}._try_claim_or_wait", return_value=None),
        patch(f"{_SVC}.event_bus"),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=MagicMock(),
        ),
        patch(f"{_ORCH}.detect_extraction_domain", return_value=_DETECT_RESULT),
        patch(
            f"{_SVC}._create_fresh_extraction_job",
            return_value=("job-1", "generic", [{"id": "c0"}]),
        ),
        patch(f"{_SVC}._apply_content_filtering", return_value=([{"id": "c0"}], None)),
        patch(f"{_SVC}._persist_filter_stats", return_value=None),
        # The defect's trigger condition: no groups could be built.
        patch(f"{_ORCH}.build_extraction_groups", return_value=[]),
        patch(_TRIGGER, new=trigger),
        patch.object(service, "_enqueue_chunk_tasks", new=AsyncMock()),
    ):
        return await service._import_analysis_handler(
            data={
                "file_id": "src-A",
                "file_info": {"filename": "a.pdf"},
                "analysis_depth": "full",
                "generate_embeddings": True,
            },
        )


@pytest.mark.asyncio
async def test_no_groups_dispatches_next_waiting_source() -> None:
    adapter = _adapter()
    service = _make_service(adapter)
    trigger = AsyncMock()

    result = await _run_no_groups(service, trigger)

    assert result["status"] == "error"
    assert "No extraction groups" in result["error"]

    adapter.fail_extraction.assert_called_once()
    assert adapter.fail_extraction.call_args.args[0] == "src-A"

    trigger.assert_awaited_once()
    assert trigger.await_args.args[0] is adapter
    assert trigger.await_args.args[1] == "default"


@pytest.mark.asyncio
async def test_dispatch_failure_is_logged_and_does_not_break_the_return() -> None:
    adapter = _adapter()
    service = _make_service(adapter)

    async def _boom(*_a: object, **_k: object) -> None:
        raise OSError("dispatch failed")

    with structlog.testing.capture_logs() as cap:
        result = await _run_no_groups(service, AsyncMock(side_effect=_boom))

    assert result["status"] == "error"
    assert "trigger_next_waiting_extraction_failed" in [r["event"] for r in cap]
