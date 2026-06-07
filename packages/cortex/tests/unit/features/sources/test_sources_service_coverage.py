# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Additional coverage for SourceService methods not exercised elsewhere.

Targets the previously-uncovered helper methods: list_sources_enriched,
get_chunks_by_ids, _resolve_template_name system-template branch,
cancel_extraction / abort_processing control paths, list_llm_calls,
extraction-task detail / stats / filtering aggregation, recovery events,
chart tasks, source stats, get_source_templates, and trigger_extraction's
queue-failure branch. Engine service, storage adapter, and graph repo are
mocked — no DB access. Mirrors the ``_make_service`` MagicMock-adapter
harness from test_sources_service.py (copied, not imported, per the
--import-mode=importlib constraint).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.sources import mappers as mappers_module
from chaoscypher_cortex.features.sources import service as service_module
from chaoscypher_cortex.features.sources.service import SourceService


# ---------------------------------------------------------------------------
# Helpers (copied from test_sources_service.py)
# ---------------------------------------------------------------------------


def _settings(default_page_size: int = 50, max_page_size: int = 1000) -> MagicMock:
    """Return a settings stub with pagination + batching sections."""
    settings = MagicMock()
    settings.pagination.default_page_size = default_page_size
    settings.pagination.max_page_size = max_page_size
    settings.pagination.extraction_tasks_page_size = 25
    settings.batching.template_name_cache_size = 100
    settings.priorities.background = 50
    settings.data_dir = "/tmp/cc-data"
    return settings


def _make_service(
    *,
    engine_service: MagicMock | None = None,
    storage_adapter: MagicMock | None = None,
    graph_repository: MagicMock | None = None,
    search_repository: MagicMock | None = None,
    settings: MagicMock | None = None,
    database_name: str = "default",
) -> SourceService:
    """Return a SourceService wired with MagicMock collaborators."""
    return SourceService(
        engine_service=engine_service or MagicMock(),
        database_name=database_name,
        settings=settings or _settings(),
        storage_adapter=storage_adapter or MagicMock(),
        graph_repository=graph_repository,
        search_repository=search_repository,
    )


@pytest.fixture(autouse=True)
def _clear_template_cache() -> None:
    """Reset the module-level template name cache between tests."""
    service_module._template_name_cache.clear()


# ---------------------------------------------------------------------------
# list_sources_enriched
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListSourcesEnriched:
    """Tests for SourceService.list_sources_enriched (N+1 tag batching + icons)."""

    def test_enriches_tags_durations_and_domain_icons(self) -> None:
        """Each source gets a tags list, duration fields, and a domain icon."""
        engine = MagicMock()
        engine.list_sources.return_value = {
            "sources": [{"id": "s1", "status": "indexed"}],
            "total": 1,
        }
        engine.get_source_tags_batch.return_value = {
            "s1": [{"id": "t1", "name": "Research", "color": "#fff"}]
        }
        service = _make_service(engine_service=engine)

        with (
            patch.object(mappers_module, "add_duration_fields", side_effect=lambda s: s),
            patch.object(mappers_module, "build_domain_icon_map", return_value={}) as mock_icons,
            patch.object(mappers_module, "enrich_domain_icons") as mock_enrich_icons,
            patch.object(mappers_module, "build_domain_fingerprint_map", return_value={}),
            patch.object(mappers_module, "enrich_domain_changed") as mock_enrich_changed,
        ):
            result = service.list_sources_enriched(page=1, page_size=10)

        assert result["total"] == 1
        assert result["sources"][0]["tags"] == [{"id": "t1", "name": "Research", "color": "#fff"}]
        engine.get_source_tags_batch.assert_called_once_with(["s1"])
        mock_icons.assert_called_once_with("default")
        mock_enrich_icons.assert_called_once()
        mock_enrich_changed.assert_called_once()

    def test_handles_source_with_no_tags(self) -> None:
        """A source absent from the batch map gets an empty tags list."""
        engine = MagicMock()
        engine.list_sources.return_value = {
            "sources": [{"id": "s2", "status": "committed"}],
            "total": 1,
        }
        engine.get_source_tags_batch.return_value = {}
        service = _make_service(engine_service=engine)

        with (
            patch.object(mappers_module, "add_duration_fields", side_effect=lambda s: s),
            patch.object(mappers_module, "build_domain_icon_map", return_value={}),
            patch.object(mappers_module, "enrich_domain_icons"),
            patch.object(mappers_module, "build_domain_fingerprint_map", return_value={}),
            patch.object(mappers_module, "enrich_domain_changed"),
        ):
            result = service.list_sources_enriched()

        assert result["sources"][0]["tags"] == []


# ---------------------------------------------------------------------------
# get_chunks_by_ids
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetChunksByIds:
    """Tests for SourceService.get_chunks_by_ids."""

    def test_returns_empty_list_for_empty_input(self) -> None:
        """Empty chunk_ids short-circuits without touching the adapter."""
        adapter = MagicMock()
        service = _make_service(storage_adapter=adapter)

        assert service.get_chunks_by_ids([]) == []
        adapter.get_chunks_by_ids.assert_not_called()

    def test_delegates_to_adapter(self) -> None:
        """Non-empty ids forward to adapter.get_chunks_by_ids with the db name."""
        adapter = MagicMock()
        adapter.database_name = "default"
        adapter.get_chunks_by_ids.return_value = [{"id": "c1"}, {"id": "c2"}]
        service = _make_service(storage_adapter=adapter)

        result = service.get_chunks_by_ids(["c1", "c2"])

        adapter.get_chunks_by_ids.assert_called_once_with(
            chunk_ids=["c1", "c2"], database_name="default"
        )
        assert result == [{"id": "c1"}, {"id": "c2"}]


# ---------------------------------------------------------------------------
# _resolve_template_name — system template branch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveTemplateNameSystem:
    """Tests for the system-template + cache-eviction branches."""

    def test_resolves_system_template_friendly_name(self) -> None:
        """A known system_template id resolves to its friendly name."""
        service = _make_service()

        result = service._resolve_template_name("system_template_person")

        assert result == "Person"
        assert service_module._template_name_cache["system_template_person"] == "Person"

    def test_unresolvable_id_caches_original(self) -> None:
        """An id the graph repo can't resolve caches and returns the original id."""
        graph_repo = MagicMock()
        graph_repo.get_template.return_value = None
        service = _make_service(graph_repository=graph_repo)

        result = service._resolve_template_name("template_unknown")

        assert result == "template_unknown"
        assert service_module._template_name_cache["template_unknown"] == "template_unknown"

    def test_cache_clears_when_size_bound_exceeded(self) -> None:
        """When the cache hits the configured bound it is cleared before insert."""
        settings = _settings()
        settings.batching.template_name_cache_size = 1
        service = _make_service(settings=settings)
        # Seed the cache so it is already at the bound.
        service_module._template_name_cache["pre_existing"] = "Old"

        result = service._resolve_template_name("system_template_person")

        # Cache was cleared (pre_existing gone) and the new entry inserted.
        assert "pre_existing" not in service_module._template_name_cache
        assert result == "Person"


# ---------------------------------------------------------------------------
# cancel_extraction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelExtraction:
    """Tests for SourceService.cancel_extraction."""

    @pytest.mark.asyncio
    async def test_raises_when_source_missing(self) -> None:
        """cancel_extraction raises ValueError when the source is missing."""
        adapter = MagicMock()
        adapter.get_file.return_value = None
        service = _make_service(storage_adapter=adapter)

        with pytest.raises(ValueError, match="not found"):
            await service.cancel_extraction("missing")

    @pytest.mark.asyncio
    async def test_raises_when_no_active_job(self) -> None:
        """cancel_extraction raises ValueError when there is no active job id."""
        adapter = MagicMock()
        adapter.get_file.return_value = {"id": "s1", "current_extraction_job_id": None}
        service = _make_service(storage_adapter=adapter)

        with pytest.raises(ValueError, match="No active extraction job"):
            await service.cancel_extraction("s1")

    @pytest.mark.asyncio
    async def test_cancels_job_and_chunks(self) -> None:
        """cancel_extraction cancels the queue, marks the job cancelled, cancels chunks."""
        adapter = MagicMock()
        adapter.get_file.return_value = {"id": "s1", "current_extraction_job_id": "job-1"}
        service = _make_service(storage_adapter=adapter)

        with patch("chaoscypher_core.queue.queue_client") as mock_queue:
            mock_queue.cancel_by_metadata = AsyncMock()
            await service.cancel_extraction("s1")

        mock_queue.cancel_by_metadata.assert_awaited_once()
        adapter.update_extraction_job.assert_called_once_with("job-1", {"status": "cancelled"})
        adapter.cancel_extraction.assert_called_once_with("s1")


# ---------------------------------------------------------------------------
# abort_processing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAbortProcessing:
    """Tests for SourceService.abort_processing control paths."""

    @pytest.mark.asyncio
    async def test_raises_when_source_missing(self) -> None:
        """abort_processing raises ValueError when the source is missing."""
        adapter = MagicMock()
        adapter.get_file.return_value = None
        service = _make_service(storage_adapter=adapter)

        with pytest.raises(ValueError, match="not found"):
            await service.abort_processing("missing")

    @pytest.mark.asyncio
    async def test_raises_when_not_processing(self) -> None:
        """abort_processing raises RuntimeError for a terminal (indexed) source."""
        adapter = MagicMock()
        adapter.get_file.return_value = {"id": "s1", "status": "indexed"}
        service = _make_service(storage_adapter=adapter)

        with patch("chaoscypher_core.queue.queue_client"):
            with pytest.raises(RuntimeError, match="not currently processing"):
                await service.abort_processing("s1")

    @pytest.mark.asyncio
    async def test_extracting_cancels_job_then_aborts(self) -> None:
        """EXTRACTING with a job id cancels the LLM queue + marks the job cancelled."""
        adapter = MagicMock()
        adapter.get_file.return_value = {
            "id": "s1",
            "status": "extracting",
            "current_extraction_job_id": "job-9",
        }
        service = _make_service(storage_adapter=adapter)

        with patch("chaoscypher_core.queue.queue_client") as mock_queue:
            mock_queue.cancel_by_metadata = AsyncMock()
            await service.abort_processing("s1")

        mock_queue.cancel_by_metadata.assert_awaited_once()
        adapter.update_extraction_job.assert_called_once_with("job-9", {"status": "cancelled"})
        # The state-machine abort always runs after stage-specific cancellation.
        adapter.abort_processing.assert_called_once()
        _, kwargs = adapter.abort_processing.call_args
        assert kwargs["error_stage"] == "extraction"


# ---------------------------------------------------------------------------
# list_llm_calls
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListLlmCalls:
    """Tests for SourceService.list_llm_calls pagination."""

    def test_raises_when_source_missing(self) -> None:
        """list_llm_calls raises ValueError when the file is missing."""
        adapter = MagicMock()
        adapter.get_file.return_value = None
        service = _make_service(storage_adapter=adapter)

        with pytest.raises(ValueError, match="not found"):
            service.list_llm_calls("missing")

    def test_paginates_and_forwards_filters(self) -> None:
        """list_llm_calls forwards offset/limit/success and builds pagination."""
        adapter = MagicMock()
        adapter.get_file.return_value = {"id": "s1"}
        adapter.count_llm_call_metrics.return_value = 5
        adapter.list_llm_call_metrics.return_value = [{"id": "call-3"}, {"id": "call-4"}]
        service = _make_service(storage_adapter=adapter)

        result = service.list_llm_calls("s1", page=2, per_page=2, success=True)

        adapter.count_llm_call_metrics.assert_called_once_with(
            database_name="default", source_id="s1", success=True
        )
        adapter.list_llm_call_metrics.assert_called_once_with(
            database_name="default",
            source_id="s1",
            success=True,
            limit=2,
            offset=2,
        )
        assert result["calls"][0]["id"] == "call-3"
        assert result["pagination"]["total"] == 5
        assert result["pagination"]["total_pages"] == 3
        assert result["pagination"]["has_next"] is True
        assert result["pagination"]["has_prev"] is True

    def test_uses_default_page_size_when_per_page_none(self) -> None:
        """per_page=None falls back to settings.pagination.default_page_size."""
        adapter = MagicMock()
        adapter.get_file.return_value = {"id": "s1"}
        adapter.count_llm_call_metrics.return_value = 0
        adapter.list_llm_call_metrics.return_value = []
        settings = _settings(default_page_size=33)
        service = _make_service(storage_adapter=adapter, settings=settings)

        result = service.list_llm_calls("s1")

        assert result["pagination"]["page_size"] == 33
        assert result["pagination"]["total_pages"] == 1


# ---------------------------------------------------------------------------
# Extraction task detail / stats / filtering aggregation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractionTaskQueries:
    """Tests for extraction-task detail, stats, and filtering aggregation."""

    def test_get_extraction_task_delegates(self) -> None:
        """get_extraction_task forwards the task id to the adapter."""
        adapter = MagicMock()
        adapter.get_extraction_task_detail.return_value = {"id": "task-1"}
        service = _make_service(storage_adapter=adapter)

        assert service.get_extraction_task("task-1") == {"id": "task-1"}
        adapter.get_extraction_task_detail.assert_called_once_with("task-1")

    def test_get_extraction_task_stats_returns_none_when_no_stats(self) -> None:
        """get_extraction_task_stats returns None when the adapter has no stats."""
        adapter = MagicMock()
        adapter.get_extraction_task_stats.return_value = None
        service = _make_service(storage_adapter=adapter)

        assert service.get_extraction_task_stats("s1") is None

    def test_get_extraction_task_stats_merges_filtering_aggregate(self) -> None:
        """Stats dict is merged with the aggregated filtering summary."""
        adapter = MagicMock()
        adapter.get_extraction_task_stats.return_value = {"total_tasks": 3}
        adapter.get_extraction_tasks_filtering_logs.return_value = [
            {
                "filtering_log": {
                    "stages": [
                        {"stage": "entity_dedup", "removed_count": 4},
                        {"stage": "relationship_cap", "removed_count": 2},
                    ]
                }
            },
            {"filtering_log": {"stages": [{"stage": "entity_dedup", "removed_count": 1}]}},
            {"filtering_log": None},  # skipped — no log
            {"filtering_log": "not-a-dict"},  # skipped — wrong type
        ]
        service = _make_service(storage_adapter=adapter)

        stats = service.get_extraction_task_stats("s1")

        assert stats is not None
        # entity_dedup classified as entity-filtered: 4 + 1 = 5
        assert stats["total_entities_filtered"] == 5
        # relationship_cap classified as relationship-filtered: 2
        assert stats["total_relationships_filtered"] == 2
        summary = {s["stage"]: s for s in stats["filtering_stage_summary"]}
        assert summary["entity_dedup"]["total_removed"] == 5
        assert summary["entity_dedup"]["chunk_count"] == 2
        assert summary["relationship_cap"]["total_removed"] == 2

    def test_aggregate_filtering_stats_empty_when_no_tasks(self) -> None:
        """_aggregate_filtering_stats returns {} when there are no logged tasks."""
        adapter = MagicMock()
        adapter.get_extraction_tasks_filtering_logs.return_value = []
        service = _make_service(storage_adapter=adapter)

        assert service._aggregate_filtering_stats("s1") == {}


# ---------------------------------------------------------------------------
# Recovery events / chart tasks / source stats / extraction tasks (default pg)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestThinDelegations:
    """Thin pass-through delegations to the adapter / engine service."""

    def test_list_recovery_events_forwards_limit(self) -> None:
        """list_recovery_events forwards source_id, db name, and limit."""
        adapter = MagicMock()
        adapter.list_recovery_events.return_value = [{"id": "ev-1"}]
        service = _make_service(storage_adapter=adapter)

        result = service.list_recovery_events("s1", limit=10)

        adapter.list_recovery_events.assert_called_once_with(
            source_id="s1", database_name="default", limit=10
        )
        assert result == [{"id": "ev-1"}]

    def test_get_extraction_tasks_for_charts_delegates(self) -> None:
        """get_extraction_tasks_for_charts forwards to the adapter."""
        adapter = MagicMock()
        adapter.get_extraction_tasks_for_charts.return_value = [{"id": "c1"}]
        service = _make_service(storage_adapter=adapter)

        result = service.get_extraction_tasks_for_charts("s1")

        adapter.get_extraction_tasks_for_charts.assert_called_once_with(
            source_id="s1", database_name="default"
        )
        assert result == [{"id": "c1"}]

    def test_get_source_stats_delegates_to_engine(self) -> None:
        """get_source_stats forwards to the engine service."""
        engine = MagicMock()
        engine.get_source_stats.return_value = {"total_chunks": 7}
        service = _make_service(engine_service=engine)

        assert service.get_source_stats("s1") == {"total_chunks": 7}
        engine.get_source_stats.assert_called_once_with("s1")

    def test_get_extraction_tasks_uses_configured_page_size_default(self) -> None:
        """get_extraction_tasks defaults page_size to extraction_tasks_page_size."""
        adapter = MagicMock()
        adapter.get_extraction_tasks_for_source.return_value = ([], 0)
        service = _make_service(storage_adapter=adapter)

        result = service.get_extraction_tasks("s1")

        adapter.get_extraction_tasks_for_source.assert_called_once_with(
            source_id="s1",
            database_name="default",
            page=1,
            per_page=25,
            include_text_content=False,
        )
        assert result["page_size"] == 25


# ---------------------------------------------------------------------------
# get_source_templates
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSourceTemplates:
    """Tests for SourceService.get_source_templates."""

    def test_raises_when_source_missing(self) -> None:
        """get_source_templates raises ValueError when the file is missing."""
        adapter = MagicMock()
        adapter.get_file.return_value = None
        service = _make_service(storage_adapter=adapter)

        with pytest.raises(ValueError, match="not found"):
            service.get_source_templates("missing")

    def test_raises_when_graph_repo_missing(self) -> None:
        """get_source_templates raises RuntimeError without a graph repository."""
        adapter = MagicMock()
        adapter.get_file.return_value = {"id": "s1"}
        service = _make_service(storage_adapter=adapter, graph_repository=None)

        with pytest.raises(RuntimeError, match="graph_repository is required"):
            service.get_source_templates("s1")

    def test_returns_paginated_templates(self) -> None:
        """get_source_templates wraps the TemplateService list result."""
        adapter = MagicMock()
        adapter.get_file.return_value = {"id": "s1"}
        graph_repo = MagicMock()
        service = _make_service(storage_adapter=adapter, graph_repository=graph_repo)

        fake_template_service = MagicMock()
        fake_template_service.list_templates.return_value = {
            "data": [{"id": "tpl-1"}],
            "pagination": {
                "page": 1,
                "page_size": 50,
                "total": 1,
                "total_pages": 1,
                "has_next": False,
                "has_prev": False,
            },
        }

        with patch(
            "chaoscypher_core.services.graph.management.template.TemplateService",
            return_value=fake_template_service,
        ):
            result = service.get_source_templates("s1", template_type="node", page=1, per_page=50)

        fake_template_service.list_templates.assert_called_once_with(
            template_type="node",
            page=1,
            page_size=50,
            source_id="s1",
        )
        assert result["templates"] == [{"id": "tpl-1"}]
        assert result["pagination"]["total"] == 1


# ---------------------------------------------------------------------------
# trigger_extraction — queue-failure branch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTriggerExtractionFailures:
    """Tests for trigger_extraction error branches not covered elsewhere."""

    @pytest.mark.asyncio
    async def test_no_llm_provider_raises_external_service_error(self) -> None:
        """An LLM-provider lookup failure raises ExternalServiceError."""
        from chaoscypher_core.exceptions import ExternalServiceError

        engine = MagicMock()
        engine.get_source.return_value = {"id": "s1", "status": "indexed"}
        service = _make_service(engine_service=engine)

        with patch(
            "chaoscypher_core.llm_queue.get_provider_factory",
            side_effect=RuntimeError("no provider"),
        ):
            with pytest.raises(ExternalServiceError):
                await service.trigger_extraction("s1")

    @pytest.mark.asyncio
    async def test_queue_failure_raises_operation_error(self) -> None:
        """A queue_import_analysis failure surfaces as OperationError."""
        from chaoscypher_core.exceptions import OperationError

        engine = MagicMock()
        engine.get_source.return_value = {
            "id": "s1",
            "status": "indexed",
            "filepath": "/tmp/x.pdf",
            "file_type": "pdf",
            "filename": "x.pdf",
        }
        adapter = MagicMock()
        service = _make_service(engine_service=engine, storage_adapter=adapter)

        with (
            patch(
                "chaoscypher_core.llm_queue.get_provider_factory",
                return_value=MagicMock(get_chat_provider=MagicMock()),
            ),
            patch.object(service_module, "gate_decision", return_value="proceed"),
            patch.object(
                service_module.queue_utils,
                "queue_import_analysis",
                new=AsyncMock(side_effect=RuntimeError("queue down")),
            ),
        ):
            with pytest.raises(OperationError):
                await service.trigger_extraction("s1")

    @pytest.mark.asyncio
    async def test_invalid_status_raises_validation_error(self) -> None:
        """A non-indexed source (without force) raises ValidationError."""
        from chaoscypher_core.exceptions import ValidationError

        engine = MagicMock()
        engine.get_source.return_value = {"id": "s1", "status": "pending"}
        service = _make_service(engine_service=engine)

        with pytest.raises(ValidationError, match="indexed"):
            await service.trigger_extraction("s1")
