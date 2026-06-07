# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SourceService.

Covers source CRUD delegation, chunk operations, citation enrichment with
template-name resolution, extraction status, entity/relationship pagination,
and LLM metrics summarisation. Engine service, storage adapter and graph
repository are mocked — no real DB access.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.sources import service as service_module
from chaoscypher_cortex.features.sources.service import SourceService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(default_page_size: int = 50, max_page_size: int = 1000) -> MagicMock:
    """Return a settings stub with pagination + batching sections."""
    settings = MagicMock()
    settings.pagination.default_page_size = default_page_size
    settings.pagination.max_page_size = max_page_size
    settings.pagination.extraction_tasks_page_size = 25
    settings.batching.template_name_cache_size = 100
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
# Source CRUD
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSourceCrud:
    """Tests for SourceService source CRUD delegation."""

    def test_get_source_delegates_to_engine(self) -> None:
        """get_source forwards to engine_service.get_source."""
        engine = MagicMock()
        engine.get_source.return_value = {"id": "s1", "filename": "doc.pdf"}
        service = _make_service(engine_service=engine)

        result = service.get_source("s1")

        engine.get_source.assert_called_once_with("s1")
        assert result == {"id": "s1", "filename": "doc.pdf"}

    def test_get_source_returns_none_when_engine_returns_none(self) -> None:
        """get_source returns None when the engine reports missing source."""
        engine = MagicMock()
        engine.get_source.return_value = None
        service = _make_service(engine_service=engine)
        assert service.get_source("missing") is None

    def test_list_sources_uses_default_page_size_when_none(self) -> None:
        """list_sources fills in the configured default page size when None."""
        engine = MagicMock()
        engine.list_sources.return_value = {"sources": [], "total": 0}
        settings = _settings(default_page_size=25)
        service = _make_service(engine_service=engine, settings=settings)

        service.list_sources(page=1, page_size=None)

        engine.list_sources.assert_called_once_with(
            page=1,
            page_size=25,
            source_type=None,
            status=None,
            enabled=None,
            search=None,
            tag_id=None,
        )

    def test_list_sources_forwards_all_filters(self) -> None:
        """list_sources passes every filter parameter through to the engine."""
        engine = MagicMock()
        engine.list_sources.return_value = {"sources": [], "total": 0}
        service = _make_service(engine_service=engine)

        service.list_sources(
            page=2,
            page_size=10,
            source_type="pdf",
            status="indexed",
            enabled="enabled",
            search="neural",
            tag_id="tag-1",
        )

        engine.list_sources.assert_called_once_with(
            page=2,
            page_size=10,
            source_type="pdf",
            status="indexed",
            enabled="enabled",
            search="neural",
            tag_id="tag-1",
        )

    def test_update_source_forwards_all_fields(self) -> None:
        """update_source forwards title/processing_status/enabled/user_metadata."""
        engine = MagicMock()
        engine.update_source.return_value = {"id": "s1", "title": "New"}
        service = _make_service(engine_service=engine)

        result = service.update_source(
            source_id="s1",
            title="New",
            processing_status="ready",
            enabled=False,
            user_metadata={"author": "me"},
        )

        engine.update_source.assert_called_once_with(
            source_id="s1",
            title="New",
            processing_status="ready",
            enabled=False,
            user_metadata={"author": "me"},
        )
        assert result == {"id": "s1", "title": "New"}

    def test_delete_source_returns_engine_result(self, tmp_path: Any) -> None:
        """delete_source returns whatever engine.delete_source returned."""
        engine = MagicMock()
        engine.delete_source.return_value = True
        settings = _settings()
        settings.data_dir = str(tmp_path)
        graph_repo = MagicMock()
        search_repo = MagicMock()
        service = _make_service(
            engine_service=engine,
            settings=settings,
            graph_repository=graph_repo,
            search_repository=search_repo,
        )

        result = service.delete_source("s1")

        engine.delete_source.assert_called_once_with(
            "s1", graph_repo=graph_repo, search_repo=search_repo
        )
        assert result is True

    def test_delete_source_returns_false_when_engine_returns_false(self) -> None:
        """delete_source returns False when the engine reports no-op."""
        engine = MagicMock()
        engine.delete_source.return_value = False
        service = _make_service(engine_service=engine)
        assert service.delete_source("missing") is False


# ---------------------------------------------------------------------------
# Chunk operations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestChunkOperations:
    """Tests for SourceService chunk operations."""

    def test_get_chunks_delegates_with_default_page_size(self) -> None:
        """get_chunks fills in page_size from settings when omitted."""
        engine = MagicMock()
        engine.get_chunks_by_source.return_value = {"chunks": []}
        settings = _settings(default_page_size=75)
        service = _make_service(engine_service=engine, settings=settings)

        service.get_chunks(source_id="s1")

        engine.get_chunks_by_source.assert_called_once_with(
            source_id="s1", page=1, page_size=75, status=None
        )

    def test_get_chunk_delegates_to_engine(self) -> None:
        """get_chunk forwards chunk_id to the engine."""
        engine = MagicMock()
        engine.get_chunk.return_value = {"id": "chunk-1"}
        service = _make_service(engine_service=engine)
        assert service.get_chunk("chunk-1") == {"id": "chunk-1"}
        engine.get_chunk.assert_called_once_with("chunk-1")


# ---------------------------------------------------------------------------
# Citation operations
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCitations:
    """Tests for SourceService citation resolution."""

    def test_get_citations_resolves_template_ids_from_graph(self) -> None:
        """get_citations replaces entity_type with resolved template name."""
        engine = MagicMock()
        engine.get_citations_by_source.return_value = {
            "citations": [
                {"id": "c1", "entity_type": "template_abc"},
                {"id": "c2", "entity_type": None},
            ],
            "total": 2,
        }
        template = MagicMock()
        template.name = "Person"
        graph_repo = MagicMock()
        graph_repo.get_template.return_value = template

        service = _make_service(engine_service=engine, graph_repository=graph_repo)

        result = service.get_citations(source_id="s1")

        assert result["total"] == 2
        assert result["citations"][0]["entity_type"] == "Person"
        assert result["citations"][1]["entity_type"] is None
        graph_repo.get_template.assert_called_once_with("template_abc")

    def test_get_citations_falls_back_to_original_id_when_unresolved(self) -> None:
        """get_citations uses the original template id when graph has no match."""
        engine = MagicMock()
        engine.get_citations_by_source.return_value = {
            "citations": [{"id": "c1", "entity_type": "template_xyz"}],
            "total": 1,
        }
        graph_repo = MagicMock()
        graph_repo.get_template.return_value = None
        service = _make_service(engine_service=engine, graph_repository=graph_repo)

        result = service.get_citations(source_id="s1")

        assert result["citations"][0]["entity_type"] == "template_xyz"

    def test_get_citations_by_entity_returns_flat_list(self) -> None:
        """get_citations_by_entity unwraps the nested {'citation': ...} shape."""
        engine = MagicMock()
        engine.get_citations_by_entity.return_value = {
            "citations": [
                {"citation": {"id": "c1"}},
                {"citation": {"id": "c2"}},
            ]
        }
        service = _make_service(engine_service=engine)

        result = service.get_citations_by_entity("entity://alice")

        assert result == [{"id": "c1"}, {"id": "c2"}]

    def test_resolve_template_name_returns_none_when_id_none(self) -> None:
        """_resolve_template_name returns None when given None."""
        service = _make_service()
        assert service._resolve_template_name(None) is None

    def test_resolve_template_name_caches_result(self) -> None:
        """_resolve_template_name caches resolved names to avoid repeated lookups."""
        template = MagicMock()
        template.name = "Organization"
        graph_repo = MagicMock()
        graph_repo.get_template.return_value = template
        service = _make_service(graph_repository=graph_repo)

        first = service._resolve_template_name("template_abc")
        second = service._resolve_template_name("template_abc")

        assert first == "Organization"
        assert second == "Organization"
        graph_repo.get_template.assert_called_once_with("template_abc")


# ---------------------------------------------------------------------------
# Extraction status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractionStatus:
    """Tests for SourceService extraction status queries."""

    def test_get_extraction_status_raises_when_source_missing(self) -> None:
        """get_extraction_status raises ValueError when file not found."""
        adapter = MagicMock()
        adapter.get_file.return_value = None
        service = _make_service(storage_adapter=adapter)

        with pytest.raises(ValueError, match="not found"):
            service.get_extraction_status("missing")

    def test_get_extraction_status_no_active_job(self) -> None:
        """get_extraction_status returns has_extraction_job=False when no job id."""
        adapter = MagicMock()
        adapter.get_file.return_value = {
            "id": "s1",
            "status": "indexed",
            "current_extraction_job_id": None,
        }
        service = _make_service(storage_adapter=adapter)

        result = service.get_extraction_status("s1")

        assert result["has_extraction_job"] is False
        assert result["status"] == "indexed"
        assert "No active extraction job" in result["message"]

    def test_get_extraction_status_computes_progress(self) -> None:
        """get_extraction_status computes progress_percent from chunk counts."""
        adapter = MagicMock()
        adapter.get_file.return_value = {
            "id": "s1",
            "status": "extracting",
            "current_extraction_job_id": "job-1",
        }
        adapter.get_extraction_job.return_value = {
            "status": "running",
            "total_chunks": 10,
            "completed_chunks": 4,
            "failed_chunks": 1,
            "extraction_depth": "full",
            "started_at": "2026-01-01T00:00:00",
            "completed_at": None,
        }
        adapter.get_chunk_tasks_summary.return_value = {
            "by_status": {"running": 1, "pending": 4},
            "total_entities": 12,
            "total_relationships": 7,
        }
        adapter.get_running_chunk_task.return_value = {"id": "chunk-5"}

        service = _make_service(storage_adapter=adapter)
        result = service.get_extraction_status("s1")

        assert result["has_extraction_job"] is True
        assert result["total_chunks"] == 10
        assert result["completed_chunks"] == 4
        assert result["failed_chunks"] == 1
        assert result["progress_percent"] == 50.0
        assert result["total_entities"] == 12


# ---------------------------------------------------------------------------
# Entities / Relationships / LLM metrics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEntitiesAndRelationships:
    """Tests for SourceService entity/relationship/LLM-metrics queries."""

    def test_get_entities_raises_when_source_missing(self) -> None:
        """get_entities raises ValueError when source row is missing."""
        adapter = MagicMock()
        adapter.get_source_extraction_metadata.return_value = None
        service = _make_service(storage_adapter=adapter)

        with pytest.raises(ValueError, match="not found"):
            service.get_entities("missing")

    def test_get_entities_returns_paginated_results(self) -> None:
        """get_entities forwards pagination params to the new adapter slice."""
        adapter = MagicMock()
        adapter.get_source_extraction_metadata.return_value = {
            "extraction_domain": None,
            "chunk_count": 0,
        }
        adapter.get_source_entities_page.return_value = {
            "entities": [{"name": "e2"}, {"name": "e3"}],
            "total": 5,
        }

        # Patch out attach_quality_scores so we don't invoke QualityScorer.
        import chaoscypher_cortex.features.sources.service as svc_mod

        original = svc_mod.attach_quality_scores
        svc_mod.attach_quality_scores = MagicMock()
        try:
            service = _make_service(storage_adapter=adapter)
            result = service.get_entities("s1", page=2, per_page=2)
        finally:
            svc_mod.attach_quality_scores = original

        adapter.get_source_entities_page.assert_called_once_with(
            "s1",
            "default",
            page=2,
            per_page=2,
            sort_by="default",
            sort_order="desc",
        )
        assert len(result["entities"]) == 2
        assert result["entities"][0]["name"] == "e2"
        assert result["pagination"]["total"] == 5
        assert result["pagination"]["total_pages"] == 3
        assert result["pagination"]["has_next"] is True
        assert result["pagination"]["has_prev"] is True

    def test_get_relationships_enriches_from_and_to_fields(self) -> None:
        """get_relationships forwards to the adapter's join-enriched slice."""
        adapter = MagicMock()
        adapter.get_source_extraction_metadata.return_value = {
            "extraction_domain": None,
            "chunk_count": 0,
        }
        adapter.get_source_relationships_page.return_value = {
            "relationships": [
                {
                    "id": "rel_1",
                    "source": "ent_alice",
                    "target": "ent_bob",
                    "predicate": "knows",
                    "type": "knows",
                    "from": "Alice",
                    "to": "Bob",
                }
            ],
            "total": 1,
        }
        service = _make_service(storage_adapter=adapter)

        result = service.get_relationships("s1", page=1, per_page=10)

        adapter.get_source_relationships_page.assert_called_once_with(
            "s1",
            "default",
            page=1,
            per_page=10,
        )
        assert result["relationships"][0]["from"] == "Alice"
        assert result["relationships"][0]["to"] == "Bob"
        assert result["pagination"]["total"] == 1

    def test_get_llm_metrics_raises_when_source_missing(self) -> None:
        """get_llm_metrics raises ValueError when file not found."""
        adapter = MagicMock()
        adapter.get_file.return_value = None
        service = _make_service(storage_adapter=adapter)

        with pytest.raises(ValueError, match="not found"):
            service.get_llm_metrics("missing")

    def test_get_llm_metrics_computes_rates(self) -> None:
        """get_llm_metrics computes success/retry/waste rates from raw counts."""
        adapter = MagicMock()
        adapter.get_file.return_value = {
            "llm_total_calls": 10,
            "llm_successful_calls": 8,
            "llm_failed_calls": 2,
            "llm_retry_calls": 3,
            "llm_first_try_successes": 7,
            "llm_retry_successes": 1,
            "llm_permanent_failures": 2,
            "llm_total_input_tokens": 800,
            "llm_total_output_tokens": 200,
            "llm_wasted_tokens": 100,
            "llm_avg_call_duration_ms": 150,
            "llm_total_duration_ms": 1500,
            "llm_estimated_cost_usd": 0.02,
            "llm_error_counts": {"rate_limit": 1},
            "llm_model": "gpt-4",
        }
        service = _make_service(storage_adapter=adapter)

        result = service.get_llm_metrics("s1")

        assert result["has_metrics"] is True
        assert result["summary"]["total_calls"] == 10
        assert result["summary"]["success_rate"] == pytest.approx(0.8)
        assert result["summary"]["retry_rate"] == pytest.approx(0.3)
        assert result["summary"]["waste_percentage"] == pytest.approx(0.1)

    def test_get_llm_metrics_zero_calls_sets_has_metrics_false(self) -> None:
        """get_llm_metrics reports has_metrics=False when no calls recorded."""
        adapter = MagicMock()
        adapter.get_file.return_value = {
            "llm_total_calls": 0,
            "llm_total_input_tokens": 0,
            "llm_total_output_tokens": 0,
            "llm_wasted_tokens": 0,
        }
        service = _make_service(storage_adapter=adapter)

        result = service.get_llm_metrics("s1")

        assert result["has_metrics"] is False
        assert result["summary"]["success_rate"] == 0.0
        assert result["summary"]["retry_rate"] == 0.0
        assert result["summary"]["waste_percentage"] == 0.0


# ---------------------------------------------------------------------------
# Extraction tasks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractionTasks:
    """Tests for SourceService extraction-task helpers."""

    def test_get_extraction_tasks_delegates_to_adapter(self) -> None:
        """get_extraction_tasks uses the configured page size and forwards params."""
        adapter = MagicMock()
        adapter.get_extraction_tasks_for_source.return_value = (
            [{"id": "t1"}, {"id": "t2"}],
            2,
        )
        service = _make_service(storage_adapter=adapter)

        result = service.get_extraction_tasks("s1", include_content=True)

        adapter.get_extraction_tasks_for_source.assert_called_once_with(
            source_id="s1",
            database_name="default",
            page=1,
            per_page=25,
            include_text_content=True,
        )
        assert result["total"] == 2
        assert result["tasks"][0]["id"] == "t1"

    def test_get_cross_chunk_filtering_log_returns_none_when_missing(self) -> None:
        """get_cross_chunk_filtering_log returns None when nothing to return."""
        adapter = MagicMock()
        adapter.get_source_extraction_metadata.return_value = None
        service = _make_service(storage_adapter=adapter)
        assert service.get_cross_chunk_filtering_log("s1") is None

    def test_get_cross_chunk_filtering_log_returns_log_from_column(self) -> None:
        """get_cross_chunk_filtering_log returns the cross_chunk_filtering_log column."""
        adapter = MagicMock()
        adapter.get_source_extraction_metadata.return_value = {
            "extraction_domain": None,
            "chunk_count": 0,
            "cross_chunk_filtering_log": {"stages": [{"stage": "dedup", "removed": 3}]},
        }
        service = _make_service(storage_adapter=adapter)

        result = service.get_cross_chunk_filtering_log("s1")

        assert result == {"stages": [{"stage": "dedup", "removed": 3}]}
