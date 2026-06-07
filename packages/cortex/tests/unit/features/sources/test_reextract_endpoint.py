# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SourceService.reextract_source (audit fix #F49).

Covers the explicit "Re-extract" action — distinct from Retry — that
discards any cached commit_payload and re-runs the LLM extraction. The
HTTP endpoint is a thin shim over the service method tested here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.sources.service import SourceService


# ---------------------------------------------------------------------------
# Helpers (mirror test_retry_endpoint.py shape)
# ---------------------------------------------------------------------------


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.pagination.default_page_size = 50
    settings.pagination.max_page_size = 1000
    settings.pagination.extraction_tasks_page_size = 25
    settings.batching.template_name_cache_size = 100
    settings.priorities.background = 50
    settings.data_dir = "/tmp/cc-data"
    return settings


def _make_service(
    *,
    adapter: MagicMock | None = None,
    engine_service: MagicMock | None = None,
    graph_repository: MagicMock | None = None,
    database_name: str = "default",
) -> SourceService:
    if adapter is None:
        adapter = MagicMock()
    # Only set default if the test didn't already override it.
    # MagicMock auto-creates attribute mocks, so we sniff for an explicit
    # return_value override by checking whether get_system_state was already
    # configured.
    if not isinstance(adapter.get_system_state.return_value, dict):
        adapter.get_system_state.return_value = {"processing_paused": False}
    return SourceService(
        engine_service=engine_service or MagicMock(),
        database_name=database_name,
        settings=_settings(),
        storage_adapter=adapter,
        graph_repository=graph_repository,
    )


def _source_dict(
    source_id: str = "src-1",
    status: str = "committed",
    error_stage: str | None = None,
    is_paused: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": source_id,
        "database_name": "default",
        "filename": "doc.pdf",
        "filepath": "/data/uploads/doc.pdf",
        "file_type": "pdf",
        "status": status,
        "error_stage": error_stage,
        "error_message": None,
        "recovery_attempts": 0,
        "extraction_depth": "full",
        "is_paused": is_paused,
        "extraction_results": {},
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        **extra,
    }


def _patched_provider() -> Any:
    """Return a context-manager that stubs the LLM provider check."""
    return patch(
        "chaoscypher_core.llm_queue.get_provider_factory",
        new=MagicMock(return_value=MagicMock(get_chat_provider=MagicMock())),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReextractSource:
    """Unit tests for SourceService.reextract_source."""

    @pytest.mark.asyncio
    async def test_committed_source_uses_force_re_extract(self) -> None:
        """COMMITTED → calls force_re_extract (atomic graph-delete + reset)."""
        adapter = MagicMock()
        graph_repo = MagicMock()
        engine = MagicMock()
        engine.get_source.side_effect = [
            _source_dict(status="committed"),
            _source_dict(status="indexed"),
        ]
        service = _make_service(adapter=adapter, engine_service=engine, graph_repository=graph_repo)

        with (
            patch("chaoscypher_cortex.features.sources.service.force_re_extract") as mock_fre,
            patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
            patch("chaoscypher_cortex.features.sources.service.event_bus"),
            _patched_provider(),
        ):
            mock_queue.queue_import_analysis = AsyncMock(return_value="t1")
            await service.reextract_source("src-1")

        mock_fre.assert_called_once_with(
            source_id="src-1",
            database_name="default",
            storage_adapter=adapter,
            graph_repository=graph_repo,
        )
        # reset_for_retry must NOT be called for COMMITTED — force_re_extract
        # owns the atomic reset.
        adapter.reset_for_retry.assert_not_called()
        mock_queue.queue_import_analysis.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_source_uses_reset_for_retry_with_clear(self) -> None:
        """ERROR → reset_for_retry(target=indexed, clear_commit_payload=True)."""
        adapter = MagicMock()
        engine = MagicMock()
        engine.get_source.side_effect = [
            _source_dict(status="error", error_stage="commit"),
            _source_dict(status="indexed"),
        ]
        service = _make_service(adapter=adapter, engine_service=engine)

        with (
            patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
            patch("chaoscypher_cortex.features.sources.service.event_bus"),
            _patched_provider(),
        ):
            mock_queue.queue_import_analysis = AsyncMock(return_value="t1")
            await service.reextract_source("src-1")

        adapter.reset_for_retry.assert_called_once_with(
            source_id="src-1",
            database_name="default",
            new_status="indexed",
            clear_commit_payload=True,
        )
        mock_queue.queue_import_analysis.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "prior_status",
        ["indexed", "extracted", "extracting", "mcp_extracting", "committing"],
    )
    async def test_force_reset_branch_via_state_machine(self, prior_status: str) -> None:
        """All five non-COMMITTED/non-ERROR branches: state-machine reset + clear_payload.

        The force-reset path applies to any source that has indexing
        artifacts but is not COMMITTED (no graph rows to delete) and is
        not ERROR (no error bookkeeping to clear via reset_for_retry).
        Each of these statuses must take the same teardown shape:

          - ``reset_to_indexed_for_re_extract`` flips status to INDEXED +
            clears extraction state + nulls the active job pointer +
            clears any error fields (Phase 5 Task E state-machine method
            introduced to replace a bare ``update_file({"status": ...})``
            write; see ``SourceIndexingMixin.reset_to_indexed_for_re_extract``),
          - ``clear_source_commit_payload`` nulls the heavy column,
          - ``reset_for_retry`` is NOT called (it is the ERROR-branch path),
          - dispatch happens.
        """
        adapter = MagicMock()
        engine = MagicMock()
        # EXTRACTING/MCP_EXTRACTING/COMMITTING typically carry a job pointer;
        # include it on those rows so the test exercises the cancellation
        # clear too. INDEXED/EXTRACTED have no job pointer in flight.
        extra: dict[str, Any] = (
            {"current_extraction_job_id": "job-x"}
            if prior_status in {"extracting", "mcp_extracting", "committing"}
            else {}
        )
        engine.get_source.side_effect = [
            _source_dict(status=prior_status, **extra),
            _source_dict(status="indexed"),
        ]
        service = _make_service(adapter=adapter, engine_service=engine)

        with (
            patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
            patch("chaoscypher_cortex.features.sources.service.event_bus"),
            _patched_provider(),
        ):
            mock_queue.queue_import_analysis = AsyncMock(return_value="t1")
            await service.reextract_source("src-1")

        # Force-reset path: the state-machine method was invoked.
        adapter.reset_to_indexed_for_re_extract.assert_called_once_with("src-1")
        # update_file MUST NOT be used for SourceStatus transitions
        # (Phase 5 Task E discipline).
        adapter.update_file.assert_not_called()
        # And payload was explicitly cleared.
        adapter.clear_source_commit_payload.assert_called_once_with("src-1", "default")
        adapter.reset_for_retry.assert_not_called()
        mock_queue.queue_import_analysis.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "prior_status",
        ["indexed", "extracted", "extracting", "mcp_extracting", "committing"],
    )
    async def test_force_reset_branch_calls_reset_quality_counters(self, prior_status: str) -> None:
        """Re-extract from a non-COMMITTED state must call reset_quality_counters.

        This is a unit-level wiring guard: it patches the helper and asserts
        it was invoked with the correct arguments. The plan permitted this
        over an integration test because Task 2 already verifies the helper
        actually zeroes the right columns end-to-end (see
        test_reset_quality_counters_clears_vision_and_llm_metrics in
        test_counters.py). Together these two tests cover the same surface
        the plan's integration test would.
        """
        adapter = MagicMock()
        engine = MagicMock()
        extra: dict[str, Any] = (
            {"current_extraction_job_id": "job-x"}
            if prior_status in {"extracting", "mcp_extracting", "committing"}
            else {}
        )
        engine.get_source.side_effect = [
            _source_dict(status=prior_status, **extra),
            _source_dict(status="indexed"),
        ]
        service = _make_service(adapter=adapter, engine_service=engine)

        with (
            patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
            patch("chaoscypher_cortex.features.sources.service.event_bus"),
            patch(
                "chaoscypher_core.services.quality.counters.reset_quality_counters"
            ) as mock_reset,
            _patched_provider(),
        ):
            mock_queue.queue_import_analysis = AsyncMock(return_value="t1")
            await service.reextract_source("src-1")

        mock_reset.assert_called_once_with(adapter, "src-1", "default")

    @pytest.mark.asyncio
    async def test_pending_source_rejected(self) -> None:
        """PENDING → ValidationError (not eligible for re-extract)."""
        from chaoscypher_core.exceptions import ValidationError

        engine = MagicMock()
        engine.get_source.return_value = _source_dict(status="pending")
        service = _make_service(engine_service=engine)

        with pytest.raises(ValidationError) as exc_info:
            await service.reextract_source("src-1")

        assert "indexed" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_indexing_source_rejected(self) -> None:
        """INDEXING → ValidationError (still indexing, no extraction yet)."""
        from chaoscypher_core.exceptions import ValidationError

        engine = MagicMock()
        engine.get_source.return_value = _source_dict(status="indexing")
        service = _make_service(engine_service=engine)

        with pytest.raises(ValidationError):
            await service.reextract_source("src-1")

    @pytest.mark.asyncio
    async def test_missing_source_raises_404(self) -> None:
        from chaoscypher_core.exceptions import NotFoundError

        engine = MagicMock()
        engine.get_source.return_value = None
        service = _make_service(engine_service=engine)

        with pytest.raises(NotFoundError):
            await service.reextract_source("nope")

    @pytest.mark.asyncio
    async def test_paused_source_rejected(self) -> None:
        """is_paused=True → ConflictError."""
        from chaoscypher_core.exceptions import ConflictError

        engine = MagicMock()
        engine.get_source.return_value = _source_dict(status="committed", is_paused=True)
        service = _make_service(engine_service=engine)

        with pytest.raises(ConflictError) as exc_info:
            await service.reextract_source("src-1")

        assert "paused" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_system_paused_rejected(self) -> None:
        """processing_paused=True → ConflictError."""
        from chaoscypher_core.exceptions import ConflictError

        adapter = MagicMock()
        adapter.get_system_state.return_value = {"processing_paused": True}
        engine = MagicMock()
        engine.get_source.return_value = _source_dict(status="committed")
        service = _make_service(adapter=adapter, engine_service=engine)

        with pytest.raises(ConflictError):
            await service.reextract_source("src-1")

    @pytest.mark.asyncio
    async def test_committed_without_graph_repo_raises(self) -> None:
        """COMMITTED + missing graph_repository → OperationError."""
        from chaoscypher_core.exceptions import OperationError

        adapter = MagicMock()
        engine = MagicMock()
        engine.get_source.return_value = _source_dict(status="committed")
        # Note: graph_repository=None
        service = _make_service(adapter=adapter, engine_service=engine, graph_repository=None)

        with _patched_provider(), pytest.raises(OperationError):
            await service.reextract_source("src-1")

    @pytest.mark.asyncio
    async def test_event_emitted_with_correct_payload(self) -> None:
        """reextract_source emits source_reextract_requested with prior_status."""
        adapter = MagicMock()
        engine = MagicMock()
        engine.get_source.side_effect = [
            _source_dict(status="extracted"),
            _source_dict(status="indexed"),
        ]
        service = _make_service(adapter=adapter, engine_service=engine)

        with (
            patch("chaoscypher_cortex.features.sources.service.queue_utils") as mock_queue,
            patch("chaoscypher_cortex.features.sources.service.event_bus") as mock_bus,
            _patched_provider(),
        ):
            mock_queue.queue_import_analysis = AsyncMock(return_value="t1")
            await service.reextract_source("src-1")

        mock_bus.emit.assert_called_once()
        call_kwargs = mock_bus.emit.call_args.kwargs
        assert call_kwargs["details"]["source_id"] == "src-1"
        assert call_kwargs["details"]["prior_status"] == "extracted"
