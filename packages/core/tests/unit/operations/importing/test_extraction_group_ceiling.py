# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""_import_analysis_handler enforces the per-source group fan-out ceiling.

Cost / resource-exhaustion fix (2026-05-25 review pass 2): full-mode
extraction enqueued one OP_EXTRACT_CHUNK task per chunk-group with no
per-document ceiling, so a single pathological upload could explode into
millions of LLM tasks. The handler now hard-fails the source (zero LLM spend,
no tasks enqueued) when the full-mode group count exceeds
``chunking.max_groups_per_source``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.exceptions import SourceFanoutLimitExceededError
from chaoscypher_core.operations.importing.import_service import (
    ImportOperationsService,
)


def _make_service(adapter: MagicMock, *, max_groups_per_source: int = 3) -> ImportOperationsService:
    from chaoscypher_core.settings import EngineSettings

    # The fan-out ceiling is read from the worker-context EngineSettings.
    engine_settings = EngineSettings(current_database="default")
    engine_settings.chunking.max_groups_per_source = max_groups_per_source
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


def _make_settings() -> MagicMock:
    settings = MagicMock()
    settings.current_database = "default"
    return settings


# Valid detect_extraction_domain shape for the hoisted confirmation gate, which
# now runs detection BEFORE the slot claim. The empty get_source dict resolves
# to "proceed", so these ceiling tests run unchanged once detection succeeds.
_DETECT_RESULT = {
    "domain": MagicMock(),
    "detected_domain": "generic",
    "confidence": 0.5,
    "ranking": [],
    "low_confidence": False,
    "entity_guidance": "",
    "relationship_guidance": "",
}


@pytest.mark.asyncio
async def test_full_mode_over_group_ceiling_fails_source_without_enqueue() -> None:
    """5 groups + ceiling=3 (full mode) -> hard fail, no chunk tasks enqueued."""
    adapter = MagicMock(unsafe=True)
    adapter.assert_extractable.return_value = None
    adapter.clear_extraction_waiting.return_value = None
    adapter.update_step_progress.return_value = None
    adapter.get_active_extraction_job.return_value = None
    adapter.get_source.return_value = {}
    adapter.fail_extraction_job.return_value = None
    adapter.fail_extraction.return_value = None

    settings = _make_settings()
    service = _make_service(adapter, max_groups_per_source=3)

    fake_groups = [{"id": f"g{i}", "small_chunk_ids": []} for i in range(5)]

    with (
        patch("chaoscypher_core.app_config.get_settings", return_value=settings),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            return_value=MagicMock(paused=False),
        ),
        patch(
            "chaoscypher_core.operations.importing.import_service._try_claim_or_wait",
            return_value=None,
        ),
        patch("chaoscypher_core.operations.importing.import_service.event_bus"),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value=_DETECT_RESULT,
        ),
        patch(
            "chaoscypher_core.operations.importing.import_service._create_fresh_extraction_job",
            return_value=("job-1", "generic", [{"id": "c0"}]),
        ),
        patch(
            "chaoscypher_core.operations.importing.import_service._apply_content_filtering",
            return_value=([{"id": "c0"}], None),
        ),
        patch(
            "chaoscypher_core.operations.importing.import_service._persist_filter_stats",
            return_value=None,
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.build_extraction_groups",
            return_value=fake_groups,
        ),
        patch(
            "chaoscypher_core.operations.importing.import_service.trigger_next_waiting_extraction",
            new=AsyncMock(),
        ),
        patch.object(service, "_enqueue_chunk_tasks", new=AsyncMock()) as mock_enqueue,
    ):
        with pytest.raises(SourceFanoutLimitExceededError):
            await service._import_analysis_handler(
                data={
                    "file_id": "src-big",
                    "file_info": {"filename": "huge.pdf"},
                    "analysis_depth": "full",
                    "generate_embeddings": True,
                },
            )

        # No chunk-extraction tasks were enqueued — the hard stop fires
        # before any LLM-bound work.
        mock_enqueue.assert_not_called()
        # The source was failed with the ceiling message.
        adapter.fail_extraction.assert_called_once()
        assert adapter.fail_extraction.call_args.args[0] == "src-big"


@pytest.mark.asyncio
async def test_full_mode_at_ceiling_proceeds_to_enqueue() -> None:
    """3 groups + ceiling=3 (boundary) -> proceeds to enqueue (no failure)."""
    adapter = MagicMock(unsafe=True)
    adapter.assert_extractable.return_value = None
    adapter.clear_extraction_waiting.return_value = None
    adapter.update_step_progress.return_value = None
    adapter.get_active_extraction_job.return_value = None
    adapter.get_source.return_value = {}
    adapter.update_extraction_job_total.return_value = None
    adapter.update_source_last_activity.return_value = None

    settings = _make_settings()
    service = _make_service(adapter, max_groups_per_source=3)

    fake_groups = [{"id": f"g{i}", "small_chunk_ids": []} for i in range(3)]

    with (
        patch("chaoscypher_core.app_config.get_settings", return_value=settings),
        patch(
            "chaoscypher_core.operations.pause_guard.check_paused",
            return_value=MagicMock(paused=False),
        ),
        patch(
            "chaoscypher_core.operations.importing.import_service._try_claim_or_wait",
            return_value=None,
        ),
        patch("chaoscypher_core.operations.importing.import_service.event_bus"),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
            return_value=_DETECT_RESULT,
        ),
        patch(
            "chaoscypher_core.operations.importing.import_service._create_fresh_extraction_job",
            return_value=("job-1", "generic", [{"id": "c0"}]),
        ),
        patch(
            "chaoscypher_core.operations.importing.import_service._apply_content_filtering",
            return_value=([{"id": "c0"}], None),
        ),
        patch(
            "chaoscypher_core.operations.importing.import_service._persist_filter_stats",
            return_value=None,
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.orchestration.build_extraction_groups",
            return_value=fake_groups,
        ),
        patch.object(
            service, "_enqueue_chunk_tasks", new=AsyncMock(return_value=3)
        ) as mock_enqueue,
    ):
        result = await service._import_analysis_handler(
            data={
                "file_id": "src-ok",
                "file_info": {"filename": "fine.pdf"},
                "analysis_depth": "full",
                "generate_embeddings": True,
            },
        )

    mock_enqueue.assert_awaited_once()
    assert result["status"] == "queued"
