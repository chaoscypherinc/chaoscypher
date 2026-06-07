# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: _import_analysis_handler releases the extraction slot on failure.

Audit fix F41 — without this fix, a single handler exception leaves the
source in status=ERROR but never dispatches the next waiting source, so
the extraction queue stalls until a manual retry.

Contract:
    1. The exception handler must call ``adapter.fail_extraction`` (already
       in place) AND
    2. Must invoke ``trigger_next_waiting_extraction`` so any source that
       was queued behind the failed one gets dispatched, AND
    3. Must re-raise the original exception (queue-level error visibility).
    4. If ``trigger_next_waiting_extraction`` itself raises, the original
       handler exception still propagates (the dispatch failure is logged
       with ``trigger_next_waiting_extraction_failed`` and suppressed — the
       original is more important).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.operations.importing.import_service import (
    ImportOperationsService,
)


def _make_service(adapter: MagicMock) -> ImportOperationsService:
    """Build a minimal service with the given adapter as source_repository."""
    from chaoscypher_core.settings import EngineSettings

    return ImportOperationsService(
        graph_repository=MagicMock(),
        config_manager=MagicMock(),
        source_manager=MagicMock(),
        trigger_service=MagicMock(),
        llm_service=AsyncMock(),
        source_repository=adapter,
        chunking_service=MagicMock(),
        indexing_service=MagicMock(),
        engine_settings=EngineSettings(current_database="default"),
    )


def _make_settings() -> MagicMock:
    """Return a settings mock with the fields the handler reads."""
    settings = MagicMock()
    settings.current_database = "default"
    return settings


# Valid detect_extraction_domain shape for the hoisted confirmation gate, which
# now runs detection BEFORE the slot claim. These body-failure tests proceed
# through the gate (the MagicMock get_source resolves to "proceed"), so the gate
# only needs detection not to blow up on the bare-MagicMock registry.
_DETECT_RESULT = {
    "domain": MagicMock(),
    "detected_domain": "generic",
    "confidence": 0.5,
    "ranking": [],
    "low_confidence": False,
    "entity_guidance": "",
    "relationship_guidance": "",
}


class TestImportAnalysisHandlerReleasesSlotOnException:
    """The exception path must dispatch the next waiting source."""

    @pytest.mark.asyncio
    async def test_trigger_next_waiting_extraction_called_after_failure(
        self,
    ) -> None:
        """After a body exception, the next waiting source must be dispatched.

        Mocks the adapter so it claims the slot (try_claim_extraction → True),
        but force the extraction body to raise by making
        ``get_active_extraction_job`` raise. Verifies that:
          - fail_extraction was called (existing behaviour)
          - trigger_next_waiting_extraction was called (new behaviour)
          - the original exception still propagates
        """
        adapter = MagicMock(unsafe=True)
        adapter.assert_extractable.return_value = None
        adapter.try_claim_extraction.return_value = True
        adapter.get_extracting_source_count.return_value = 0
        adapter.clear_extraction_waiting.return_value = None
        adapter.update_step_progress.return_value = None
        # Force the body to raise after the slot is claimed.
        body_error = RuntimeError("simulated extraction body failure")
        adapter.get_active_extraction_job.side_effect = body_error
        adapter.fail_extraction.return_value = None

        settings = _make_settings()
        service = _make_service(adapter)

        with (
            patch(
                "chaoscypher_core.app_config.get_settings",
                return_value=settings,
            ),
            patch(
                "chaoscypher_core.operations.pause_guard.check_paused",
                return_value=MagicMock(paused=False),
            ),
            patch(
                "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
                return_value=MagicMock(),
            ),
            patch(
                "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
                return_value=_DETECT_RESULT,
            ),
            patch(
                "chaoscypher_core.operations.importing.import_service.trigger_next_waiting_extraction",
                new=AsyncMock(),
            ) as mock_trigger,
        ):
            with pytest.raises(RuntimeError) as exc_info:
                await service._import_analysis_handler(
                    data={
                        "file_id": "src-A",
                        "file_info": {"filename": "a.pdf"},
                        "analysis_depth": "full",
                        "generate_embeddings": True,
                    },
                )

            # 1. The original exception must propagate.
            assert exc_info.value is body_error

            # 2. fail_extraction must have been called for the failing source.
            adapter.fail_extraction.assert_called_once()
            failed_source_id = adapter.fail_extraction.call_args.args[0]
            assert failed_source_id == "src-A"

            # 3. trigger_next_waiting_extraction must have been awaited so any
            #    source queued behind src-A gets dispatched.
            mock_trigger.assert_awaited_once()
            call_args = mock_trigger.await_args
            # Positional args: (adapter, database_name, settings)
            assert call_args.args[0] is adapter
            assert call_args.args[1] == "default"

    @pytest.mark.asyncio
    async def test_trigger_failure_does_not_mask_original_exception(
        self,
    ) -> None:
        """If trigger_next_waiting_extraction itself raises, the original
        handler exception still propagates — the dispatch failure is
        logged with ``trigger_next_waiting_extraction_failed`` and
        suppressed.
        """
        import structlog

        adapter = MagicMock(unsafe=True)
        adapter.assert_extractable.return_value = None
        adapter.try_claim_extraction.return_value = True
        adapter.clear_extraction_waiting.return_value = None
        adapter.update_step_progress.return_value = None
        body_error = RuntimeError("primary body failure")
        adapter.get_active_extraction_job.side_effect = body_error
        adapter.fail_extraction.return_value = None

        settings = _make_settings()
        service = _make_service(adapter)

        async def _trigger_raises(*_args: object, **_kwargs: object) -> None:
            raise OSError("dispatch failed")

        with (
            patch(
                "chaoscypher_core.app_config.get_settings",
                return_value=settings,
            ),
            patch(
                "chaoscypher_core.operations.pause_guard.check_paused",
                return_value=MagicMock(paused=False),
            ),
            patch(
                "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
                return_value=MagicMock(),
            ),
            patch(
                "chaoscypher_core.services.sources.engine.extraction.orchestration.detect_extraction_domain",
                return_value=_DETECT_RESULT,
            ),
            patch(
                "chaoscypher_core.operations.importing.import_service.trigger_next_waiting_extraction",
                new=AsyncMock(side_effect=_trigger_raises),
            ),
            structlog.testing.capture_logs() as cap,
        ):
            with pytest.raises(RuntimeError) as exc_info:
                await service._import_analysis_handler(
                    data={
                        "file_id": "src-A",
                        "file_info": {"filename": "a.pdf"},
                        "analysis_depth": "full",
                        "generate_embeddings": True,
                    },
                )

            # The original RuntimeError still surfaces, not the OSError.
            assert exc_info.value is body_error
            assert "primary body failure" in str(exc_info.value)

            # The dispatch failure must be logged so a regression in
            # ``trigger_next_waiting_extraction`` is observable.
            events = [r["event"] for r in cap]
            assert "trigger_next_waiting_extraction_failed" in events


class TestWaitingSourceDispatchedAfterPriorFailure:
    """End-to-end: source A claims slot, source B waits, A fails, B dispatched.

    Uses a real SQLite adapter (CC040: tmp_path file, not :memory:) to
    verify slot bookkeeping is released and the queue helper sees the
    waiting source.
    """

    @pytest.mark.asyncio
    async def test_failed_extraction_dispatches_waiting_source(
        self,
        tmp_path: object,  # pytest fixture
    ) -> None:
        """Source A claims the slot, B is marked waiting, A's handler raises,
        and trigger_next_waiting_extraction picks up B.

        Verifies that even though we mock the body failure, the existing
        adapter state (B in waiting) is reachable by the dispatch helper.
        """
        from pathlib import Path

        from sqlmodel import SQLModel

        from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
        from chaoscypher_core.adapters.sqlite.engine import get_engine

        assert isinstance(tmp_path, Path)
        db_dir = tmp_path / "cc-test"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "app.db"
        engine = get_engine(str(db_path))
        SQLModel.metadata.create_all(engine, checkfirst=True)
        adapter = SqliteAdapter(str(db_path), database_name="default")
        adapter.connect()

        try:
            # Seed source A (will claim the slot).
            adapter.create_source(
                {
                    "id": "src-A",
                    "database_name": "default",
                    "filename": "a.pdf",
                    "filepath": "/tmp/a.pdf",
                    "file_type": "pdf",
                    "file_size": 100,
                    "content_hash": "hash-a",
                    "status": "indexed",
                }
            )
            # Seed source B (will wait behind A).
            adapter.create_source(
                {
                    "id": "src-B",
                    "database_name": "default",
                    "filename": "b.pdf",
                    "filepath": "/tmp/b.pdf",
                    "file_type": "pdf",
                    "file_size": 200,
                    "content_hash": "hash-b",
                    "status": "indexed",
                }
            )

            # A claims the slot.
            assert adapter.try_claim_extraction("src-A", "default", depth="full")
            # B tries to claim, fails, and is marked waiting.
            assert not adapter.try_claim_extraction("src-B", "default", depth="full")
            adapter.mark_extraction_waiting("src-B", {"filename": "b.pdf"})

            # A's body fails.
            adapter.fail_extraction("src-A", "boom")

            # The new behaviour: trigger_next_waiting_extraction picks up B.
            from chaoscypher_core.operations.extraction import extraction_finalizer

            queued: list[str] = []

            async def _capture(file_id: str, **_kwargs: object) -> None:
                queued.append(file_id)

            settings = _make_settings()
            settings.priorities.background = 0
            # queue_import_analysis is imported lazily inside
            # trigger_next_waiting_extraction, so patch the source module.
            with patch(
                "chaoscypher_core.operations.queue_utils.queue_import_analysis",
                side_effect=_capture,
            ):
                await extraction_finalizer.trigger_next_waiting_extraction(
                    adapter, "default", settings
                )

            assert queued == ["src-B"], (
                f"Expected src-B to be dispatched after src-A failed, got queued={queued}"
            )
        finally:
            adapter.disconnect()
