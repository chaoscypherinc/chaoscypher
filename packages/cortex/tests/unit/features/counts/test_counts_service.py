# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the counts feature.

Covers the factory function and the GET /api/v1/counts endpoint handler,
including happy-path and empty-database scenarios.
"""

from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_cortex.features.counts.models import CountsResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COUNTS_FULL: dict[str, int] = {
    "knowledge_nodes": 42,
    "links": 18,
    "templates": 7,
    "workflows": 3,
    "lenses": 2,
    "sources": 5,
}

_COUNTS_EMPTY: dict[str, int] = {
    "knowledge_nodes": 0,
    "links": 0,
    "templates": 0,
    "workflows": 0,
    "lenses": 0,
    "sources": 0,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings() -> MagicMock:
    """Minimal settings mock for the counts feature."""
    settings = MagicMock()
    settings.current_database = "default"
    return settings


@pytest.fixture
def mock_counts_service() -> MagicMock:
    """Mock CountsService with a default return value."""
    service = MagicMock()
    service.get_counts.return_value = _COUNTS_FULL
    return service


# ---------------------------------------------------------------------------
# Tests: factory function
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetCountsServiceFactory:
    """Tests for the get_counts_service factory function."""

    def test_factory_creates_service_with_correct_database(self, mock_settings: MagicMock) -> None:
        """Factory passes the current database name to CountsService."""
        from chaoscypher_cortex.features.counts.api import get_counts_service

        mock_session = MagicMock()
        mock_adapter = MagicMock()
        mock_graph_repo = MagicMock()

        with (
            patch(
                "chaoscypher_cortex.features.counts.api.get_sqlite_adapter",
                return_value=mock_adapter,
            ) as patched_adapter,
            patch(
                "chaoscypher_cortex.features.counts.api.get_graph_repository",
                return_value=mock_graph_repo,
            ) as patched_graph,
            patch(
                "chaoscypher_cortex.features.counts.api.CountsService"
            ) as mock_counts_service_cls,
        ):
            get_counts_service(session=mock_session, settings=mock_settings)

            patched_adapter.assert_called_once_with(database_name="default")
            patched_graph.assert_called_once_with(mock_session, "default")
            mock_counts_service_cls.assert_called_once_with(
                graph_repository=mock_graph_repo,
                sources_repository=mock_adapter,
                database_name="default",
            )

    def test_factory_returns_counts_service_instance(self, mock_settings: MagicMock) -> None:
        """Factory returns the constructed CountsService instance."""
        from chaoscypher_cortex.features.counts.api import get_counts_service

        mock_session = MagicMock()
        fake_service_instance = MagicMock()

        with (
            patch("chaoscypher_cortex.features.counts.api.get_sqlite_adapter"),
            patch("chaoscypher_cortex.features.counts.api.get_graph_repository"),
            patch(
                "chaoscypher_cortex.features.counts.api.CountsService",
                return_value=fake_service_instance,
            ),
        ):
            result = get_counts_service(session=mock_session, settings=mock_settings)

        assert result is fake_service_instance


# ---------------------------------------------------------------------------
# Tests: endpoint handler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetCountsEndpoint:
    """Tests for the get_counts endpoint handler."""

    @pytest.mark.asyncio
    async def test_returns_counts_response_with_correct_values(
        self, mock_counts_service: MagicMock
    ) -> None:
        """Handler returns a CountsResponse populated from the service dict."""
        from chaoscypher_cortex.features.counts.api import get_counts

        result = await get_counts(_="test-user", counts_service=mock_counts_service)

        assert isinstance(result, CountsResponse)
        assert result.knowledge_nodes == 42
        assert result.links == 18
        assert result.templates == 7
        assert result.workflows == 3
        assert result.lenses == 2
        assert result.sources == 5

    @pytest.mark.asyncio
    async def test_calls_get_counts_with_system_template_ids(
        self, mock_counts_service: MagicMock
    ) -> None:
        """Handler passes SYSTEM_TEMPLATE_IDS to get_counts."""
        from chaoscypher_core.constants import SYSTEM_TEMPLATE_IDS
        from chaoscypher_cortex.features.counts.api import get_counts

        await get_counts(_="test-user", counts_service=mock_counts_service)

        mock_counts_service.get_counts.assert_called_once_with(
            system_template_ids=SYSTEM_TEMPLATE_IDS
        )

    @pytest.mark.asyncio
    async def test_returns_zeros_for_empty_database(self) -> None:
        """Handler returns all-zero CountsResponse when database is empty."""
        from chaoscypher_cortex.features.counts.api import get_counts

        empty_service = MagicMock()
        empty_service.get_counts.return_value = _COUNTS_EMPTY

        result = await get_counts(_="test-user", counts_service=empty_service)

        assert isinstance(result, CountsResponse)
        assert result.knowledge_nodes == 0
        assert result.links == 0
        assert result.templates == 0
        assert result.workflows == 0
        assert result.lenses == 0
        assert result.sources == 0
