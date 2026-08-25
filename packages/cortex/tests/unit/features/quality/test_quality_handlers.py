# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for quality API handler logic.

Verifies that each handler calls the correct QualityService method with the
correct arguments and transforms the response correctly. FastAPI DI is
bypassed — the service mock is passed directly as a function argument.

Also pins that the three analysis handlers run their synchronous service call
via ``asyncio.to_thread``. ``QualityService`` does blocking SQLite I/O whose
volume is bounded by the source count, not by the request: the unpaginated
calls re-score every version-stale source (four DB round trips each), and even
the paginated one loads the whole source table before cutting its page. Run
directly in an ``async def`` handler that work blocks the event loop for the
whole cortex process.
"""

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from chaoscypher_cortex.features.quality.api import (
    analyze_sources,
    analyze_sources_get,
    compare_domains,
    get_outdated_sources,
    get_quality_summary,
    recalculate_scores,
    score_source,
    score_source_details,
)
from chaoscypher_cortex.features.quality.models import (
    QualityAnalysisRequest,
    RecalculateRequest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_score(
    source_id: str = "src-1",
    total_score: float = 75.0,
    avg_entity_quality: float = 60.0,
    entity_count: int = 10,
) -> dict:
    """Return a minimal source quality score dict."""
    return {
        "source_id": source_id,
        "source_title": f"Source {source_id}",
        "domain": "general",
        "entity_count": entity_count,
        "relationship_count": 5,
        "entity_contribution": 50.0,
        "relationship_contribution": 25.0,
        "connectivity_bonus": 0.0,
        "total_score": total_score,
        "avg_entity_quality": avg_entity_quality,
        "avg_relationship_quality": 55.0,
        "connectivity_ratio": 0.8,
        "quality_grade": 70.0,
        "quality_label": "Good",
        "low_quality_entity_count": 1,
        "low_quality_relationship_count": 0,
        "density_ratio": 0.5,
        "density_score": 50.0,
        "topology_score": 60.0,
        "pollution_penalty": 2.0,
        "coverage_score": 80.0,
    }


def _analysis_result(sources: list[dict] | None = None, total: int | None = None) -> dict:
    """Return a minimal analyze_sources service result.

    ``sources`` is the page the service already sorted and sliced; ``total``
    is the full match count it reports alongside it (defaults to the page
    length, which is what an unpaginated call returns).
    """
    if sources is None:
        sources = [_source_score()]
    return {
        "sources": sources,
        "total_sources": len(sources) if total is None else total,
        "avg_score": 75.0,
        "avg_entity_quality": 60.0,
        "avg_relationship_quality": 55.0,
    }


# ---------------------------------------------------------------------------
# TestScoreSource
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreSource:
    """Tests for the score_source handler."""

    @pytest.mark.asyncio
    async def test_returns_source_score(self) -> None:
        """Handler calls score_source with include_details=False and returns the result."""
        mock_service = MagicMock()
        mock_service.score_source.return_value = _source_score("src-1")

        result = await score_source(
            _="test-user",
            source_id="src-1",
            service=mock_service,
            force_recalculate=False,
        )

        mock_service.score_source.assert_called_once_with(
            "src-1", include_details=False, force_recalculate=False
        )
        assert result["source_id"] == "src-1"

    @pytest.mark.asyncio
    async def test_passes_force_recalculate(self) -> None:
        """Handler forwards force_recalculate=True to the service."""
        mock_service = MagicMock()
        mock_service.score_source.return_value = _source_score()

        await score_source(
            _="test-user",
            source_id="src-1",
            service=mock_service,
            force_recalculate=True,
        )

        mock_service.score_source.assert_called_once_with(
            "src-1", include_details=False, force_recalculate=True
        )

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self) -> None:
        """Handler raises HTTP 404 when service returns None."""
        mock_service = MagicMock()
        mock_service.score_source.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await score_source(
                _="test-user",
                source_id="missing",
                service=mock_service,
                force_recalculate=False,
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestScoreSourceDetails
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoreSourceDetails:
    """Tests for the score_source_details handler."""

    @pytest.mark.asyncio
    async def test_returns_source_details(self) -> None:
        """Handler calls score_source with include_details=True and returns the result."""
        mock_service = MagicMock()
        detail = {**_source_score("src-2"), "entity_scores": [], "relationship_scores": []}
        mock_service.score_source.return_value = detail

        result = await score_source_details(
            _="test-user",
            source_id="src-2",
            service=mock_service,
            force_recalculate=False,
        )

        mock_service.score_source.assert_called_once_with(
            "src-2", include_details=True, force_recalculate=False
        )
        assert result["source_id"] == "src-2"

    @pytest.mark.asyncio
    async def test_passes_force_recalculate(self) -> None:
        """Handler forwards force_recalculate=True to the service."""
        mock_service = MagicMock()
        mock_service.score_source.return_value = {
            **_source_score(),
            "entity_scores": [],
            "relationship_scores": [],
        }

        await score_source_details(
            _="test-user",
            source_id="src-1",
            service=mock_service,
            force_recalculate=True,
        )

        mock_service.score_source.assert_called_once_with(
            "src-1", include_details=True, force_recalculate=True
        )

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self) -> None:
        """Handler raises HTTP 404 when service returns None."""
        mock_service = MagicMock()
        mock_service.score_source.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await score_source_details(
                _="test-user",
                source_id="missing",
                service=mock_service,
                force_recalculate=False,
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestRecalculateScores
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecalculateScores:
    """Tests for the recalculate_scores handler."""

    @pytest.mark.asyncio
    async def test_recalculates_all_sources(self) -> None:
        """Handler calls recalculate_all_scores with domain=None and returns result."""
        mock_service = MagicMock()
        mock_service.recalculate_all_scores.return_value = {
            "recalculated_count": 5,
            "errors": [],
        }

        request = RecalculateRequest(domain=None)

        result = await recalculate_scores(
            _="test-user",
            request=request,
            service=mock_service,
        )

        mock_service.recalculate_all_scores.assert_called_once_with(domain=None)
        assert result["recalculated_count"] == 5
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_passes_domain_filter(self) -> None:
        """Handler forwards domain filter to the service."""
        mock_service = MagicMock()
        mock_service.recalculate_all_scores.return_value = {
            "recalculated_count": 2,
            "errors": [],
        }

        request = RecalculateRequest(domain="medical")

        await recalculate_scores(
            _="test-user",
            request=request,
            service=mock_service,
        )

        mock_service.recalculate_all_scores.assert_called_once_with(domain="medical")


# ---------------------------------------------------------------------------
# TestGetOutdatedSources
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetOutdatedSources:
    """Tests for the get_outdated_sources handler."""

    @pytest.mark.asyncio
    async def test_returns_outdated_count_and_sources(self) -> None:
        """Handler wraps service list in dict with outdated_count and sources."""
        mock_service = MagicMock()
        outdated = [
            {
                "id": "src-1",
                "title": "Old Source",
                "cached_scores_version": 1,
                "current_version": 2,
            },
            {"id": "src-2", "title": None, "cached_scores_version": None, "current_version": 2},
        ]
        mock_service.get_outdated_sources.return_value = outdated

        result = await get_outdated_sources(_="test-user", service=mock_service)

        mock_service.get_outdated_sources.assert_called_once_with()
        assert result["outdated_count"] == 2
        assert len(result["sources"]) == 2
        assert result["sources"][0]["id"] == "src-1"

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_outdated(self) -> None:
        """Handler returns outdated_count=0 when service returns empty list."""
        mock_service = MagicMock()
        mock_service.get_outdated_sources.return_value = []

        result = await get_outdated_sources(_="test-user", service=mock_service)

        assert result["outdated_count"] == 0
        assert result["sources"] == []


# ---------------------------------------------------------------------------
# analyze_sources POST handler tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnalyzeSources:
    """Tests for the analyze_sources (POST) handler."""

    @pytest.mark.asyncio
    async def test_calls_service_with_request_params(self) -> None:
        """Handler calls analyze_sources with source_ids, domain, and min_entities."""
        mock_service = MagicMock()
        mock_service.analyze_sources.return_value = _analysis_result()

        request = QualityAnalysisRequest(
            source_ids=["src-1", "src-2"],
            domain="medical",
            min_entities=5,
        )

        result = await analyze_sources(_="test-user", request=request, service=mock_service)

        mock_service.analyze_sources.assert_called_once_with(
            source_ids=["src-1", "src-2"],
            domain="medical",
            min_entities=5,
        )
        assert result["total_sources"] == 1

    @pytest.mark.asyncio
    async def test_passes_none_source_ids(self) -> None:
        """Handler passes source_ids=None to get all sources."""
        mock_service = MagicMock()
        mock_service.analyze_sources.return_value = _analysis_result()

        request = QualityAnalysisRequest(source_ids=None)

        await analyze_sources(_="test-user", request=request, service=mock_service)

        call_kwargs = mock_service.analyze_sources.call_args[1]
        assert call_kwargs["source_ids"] is None


# ---------------------------------------------------------------------------
# analyze_sources GET handler tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnalyzeSourcesGet:
    """Tests for the analyze_sources_get (GET) handler."""

    @pytest.mark.asyncio
    async def test_returns_the_service_page_verbatim(self) -> None:
        """Sorting and slicing live in the service; the handler passes the page through."""
        mock_service = MagicMock()
        sources = [
            _source_score("src-2", total_score=90.0),
            _source_score("src-3", total_score=70.0),
            _source_score("src-1", total_score=50.0),
        ]
        mock_service.analyze_sources.return_value = _analysis_result(sources)

        result = await analyze_sources_get(
            _="test-user",
            service=mock_service,
            pagination=(1, 10),
            domain=None,
            min_entities=0,
            sort_by="total_score",
            sort_order="desc",
        )

        assert [s["source_id"] for s in result["sources"]] == ["src-2", "src-3", "src-1"]
        assert result["total_sources"] == 3

    @pytest.mark.asyncio
    async def test_forwards_sort_and_page_params_to_the_service(self) -> None:
        """Every pagination/ordering param is pushed down instead of applied here."""
        mock_service = MagicMock()
        mock_service.analyze_sources.return_value = _analysis_result()

        await analyze_sources_get(
            _="test-user",
            service=mock_service,
            pagination=(2, 25),
            domain="medical",
            min_entities=3,
            sort_by="avg_entity_quality",
            sort_order="asc",
        )

        mock_service.analyze_sources.assert_called_once_with(
            domain="medical",
            min_entities=3,
            page=2,
            page_size=25,
            sort_by="avg_entity_quality",
            sort_order="asc",
        )

    @pytest.mark.asyncio
    async def test_pagination_metadata_uses_the_service_total(self) -> None:
        """``total`` comes from the service's full match count, not the page length."""
        mock_service = MagicMock()
        page = [_source_score(f"src-{i}") for i in range(2)]
        mock_service.analyze_sources.return_value = _analysis_result(page, total=6)

        result = await analyze_sources_get(
            _="test-user",
            service=mock_service,
            pagination=(2, 2),
            domain=None,
            min_entities=0,
            sort_by="total_score",
            sort_order="desc",
        )

        assert len(result["sources"]) == 2
        assert result["total_sources"] == 6
        assert result["pagination"]["page"] == 2
        assert result["pagination"]["page_size"] == 2
        assert result["pagination"]["total"] == 6
        assert result["pagination"]["total_pages"] == 3
        assert result["pagination"]["has_next"] is True
        assert result["pagination"]["has_prev"] is True

    @pytest.mark.asyncio
    async def test_empty_sources_returns_one_page(self) -> None:
        """Handler returns total_pages=1 when there are no sources."""
        mock_service = MagicMock()
        mock_service.analyze_sources.return_value = _analysis_result([])

        result = await analyze_sources_get(
            _="test-user",
            service=mock_service,
            pagination=(1, 10),
            domain=None,
            min_entities=0,
            sort_by="total_score",
            sort_order="desc",
        )

        assert result["total_sources"] == 0
        assert result["pagination"]["total_pages"] == 1
        assert result["pagination"]["has_next"] is False
        assert result["pagination"]["has_prev"] is False

    @pytest.mark.asyncio
    async def test_forwards_aggregate_metrics(self) -> None:
        """The service's global averages are surfaced unchanged."""
        mock_service = MagicMock()
        mock_service.analyze_sources.return_value = _analysis_result(
            [_source_score("src-1")], total=40
        )

        result = await analyze_sources_get(
            _="test-user",
            service=mock_service,
            pagination=(1, 1),
            domain=None,
            min_entities=0,
            sort_by="total_score",
            sort_order="desc",
        )

        assert result["avg_score"] == 75.0
        assert result["avg_entity_quality"] == 60.0
        assert result["avg_relationship_quality"] == 55.0


# ---------------------------------------------------------------------------
# TestCompareDomains
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompareDomains:
    """Tests for the compare_domains handler."""

    @pytest.mark.asyncio
    async def test_returns_compare_domains_result(self) -> None:
        """Handler delegates to service.compare_domains() and returns the result."""
        mock_service = MagicMock()
        mock_service.compare_domains.return_value = {
            "domains": [
                {
                    "domain": "medical",
                    "source_count": 3,
                    "avg_total_score": 80.0,
                    "avg_entity_quality": 70.0,
                    "avg_relationship_quality": 65.0,
                    "avg_connectivity_ratio": 0.9,
                    "total_entities": 100,
                    "total_relationships": 50,
                }
            ]
        }

        result = await compare_domains(_="test-user", service=mock_service)

        mock_service.compare_domains.assert_called_once_with()
        assert len(result["domains"]) == 1
        assert result["domains"][0]["domain"] == "medical"


# ---------------------------------------------------------------------------
# TestGetQualitySummary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetQualitySummary:
    """Tests for the get_quality_summary handler."""

    @pytest.mark.asyncio
    async def test_returns_summary(self) -> None:
        """Handler delegates to service.get_summary() and returns the result."""
        mock_service = MagicMock()
        mock_service.get_summary.return_value = {
            "total_sources": 10,
            "total_entities": 200,
            "total_relationships": 150,
            "avg_total_score": 72.5,
            "avg_entity_quality": 65.0,
            "avg_relationship_quality": 60.0,
            "avg_quality_grade": 68.0,
            "avg_connectivity_ratio": 0.85,
            "top_sources": [],
            "bottom_sources": [],
        }

        result = await get_quality_summary(_="test-user", service=mock_service)

        mock_service.get_summary.assert_called_once_with()
        assert result["total_sources"] == 10
        assert result["avg_total_score"] == 72.5


# ---------------------------------------------------------------------------
# Event-loop offload — the analysis handlers must not run sync DB work inline
# ---------------------------------------------------------------------------


_API_MODULE = "chaoscypher_cortex.features.quality.api"


def _record_offloads() -> tuple[Any, list[str]]:
    """Patch the api module's ``asyncio.to_thread``, recording what it offloads."""
    offloaded: list[str] = []
    real_to_thread = asyncio.to_thread

    async def recording_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        offloaded.append(getattr(func, "_mock_name", None) or repr(func))
        return await real_to_thread(func, *args, **kwargs)

    return patch(f"{_API_MODULE}.asyncio.to_thread", recording_to_thread), offloaded


@pytest.mark.unit
class TestAnalysisHandlersOffloadBlockingWork:
    """The synchronous QualityService calls run in a worker thread."""

    @pytest.mark.asyncio
    async def test_get_quality_summary_offloads_to_thread(self) -> None:
        """``/quality/summary`` re-scores every stale source — never on the loop."""
        mock_service = MagicMock()
        mock_service.get_summary.return_value = {"total_sources": 0}

        patcher, offloaded = _record_offloads()
        with patcher:
            await get_quality_summary(_="test-user", service=mock_service)

        assert offloaded == ["get_summary"]
        mock_service.get_summary.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_analyze_sources_post_offloads_to_thread(self) -> None:
        """``POST /quality/analyze`` scores the whole match set — never on the loop."""
        mock_service = MagicMock()
        mock_service.analyze_sources.return_value = _analysis_result()

        request = QualityAnalysisRequest(source_ids=None, domain=None, min_entities=0)
        patcher, offloaded = _record_offloads()
        with patcher:
            await analyze_sources(_="test-user", request=request, service=mock_service)

        assert offloaded == ["analyze_sources"]

    @pytest.mark.asyncio
    async def test_analyze_sources_get_offloads_to_thread(self) -> None:
        """The paginated read still loads the whole source table before slicing."""
        mock_service = MagicMock()
        mock_service.analyze_sources.return_value = _analysis_result()

        patcher, offloaded = _record_offloads()
        with patcher:
            await analyze_sources_get(
                _="test-user",
                service=mock_service,
                pagination=(1, 10),
                domain=None,
                min_entities=0,
                sort_by="total_score",
                sort_order="desc",
            )

        assert offloaded == ["analyze_sources"]

    @pytest.mark.asyncio
    async def test_recalculate_scores_offloads_to_thread(self) -> None:
        """``POST /quality/recalculate`` rescores the whole table — never on the loop."""
        mock_service = MagicMock()
        mock_service.recalculate_all_scores.return_value = {
            "recalculated_count": 0,
            "errors": [],
        }

        request = RecalculateRequest(domain=None)
        patcher, offloaded = _record_offloads()
        with patcher:
            await recalculate_scores(_="test-user", request=request, service=mock_service)

        assert offloaded == ["recalculate_all_scores"]
        mock_service.recalculate_all_scores.assert_called_once_with(domain=None)

    @pytest.mark.asyncio
    async def test_get_outdated_sources_offloads_to_thread(self) -> None:
        """``GET /quality/outdated`` reads the whole source table."""
        mock_service = MagicMock()
        mock_service.get_outdated_sources.return_value = []

        patcher, offloaded = _record_offloads()
        with patcher:
            result = await get_outdated_sources(_="test-user", service=mock_service)

        assert offloaded == ["get_outdated_sources"]
        assert result["outdated_count"] == 0

    @pytest.mark.asyncio
    async def test_compare_domains_offloads_to_thread(self) -> None:
        """``GET /quality/domains`` reads the whole source table."""
        mock_service = MagicMock()
        mock_service.compare_domains.return_value = {"domains": []}

        patcher, offloaded = _record_offloads()
        with patcher:
            await compare_domains(_="test-user", service=mock_service)

        assert offloaded == ["compare_domains"]
