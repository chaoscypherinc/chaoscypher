# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for the cross-request health cache.

``get_health_service`` builds a fresh ``HealthService`` per request, so an
instance-level cache never survives between requests: ``_minimal_health``
always saw an empty cache (reporting ``healthy=True`` unconditionally to
unauthenticated callers) and ``_full_health``'s TTL never hit. The cache is
now class-level so all instances share it; ``HealthService.reset_cache()``
restores isolation for tests.
"""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.services.events.health.models import ProbeResult
from chaoscypher_cortex.features.health.service import HealthService


@pytest.fixture(autouse=True)
def _reset_health_cache() -> Generator[None]:
    """Isolate the shared class-level cache between tests."""
    HealthService.reset_cache()
    yield
    HealthService.reset_cache()


@pytest.fixture
def mock_settings() -> MagicMock:
    """Create mock settings for an Ollama-backed deployment."""
    from chaoscypher_core.settings import OllamaInstance

    settings = MagicMock()
    settings.llm.chat_provider = "ollama"
    settings.llm.ollama_chat_model = "qwen3:30b"
    settings.llm.ollama_extraction_model = "qwen3:30b-instruct"
    settings.llm.ollama_vision_model = None
    settings.llm.ollama_instances = [
        OllamaInstance(id="default", name="Default", base_url="http://localhost:11434"),
    ]
    settings.llm.primary_ollama_url = "http://localhost:11434"
    settings.current_database = "default"
    settings.timeouts.ollama_verify_timeout = 5
    return settings


def _make_service(mock_settings: MagicMock, results: dict[str, ProbeResult]) -> HealthService:
    """Build a HealthService whose registry returns canned probe results."""
    service = HealthService(settings=mock_settings, queue_client=None)
    service._registry.check_all = AsyncMock(return_value=results)  # type: ignore[method-assign]
    return service


def _degraded_results() -> dict[str, ProbeResult]:
    """Probe results with a critical (queue) error → overall degraded."""
    return {
        "queue": ProbeResult(
            name="queue",
            status="error",
            message="Queue unavailable",
            category="service",
            auto_recoverable=True,
        ),
    }


def _healthy_results() -> dict[str, ProbeResult]:
    """Probe results with no errors → overall healthy."""
    return {
        "queue": ProbeResult(
            name="queue",
            status="ok",
            message="Queue connected",
            category="service",
            auto_recoverable=True,
        ),
    }


class TestMinimalHealthUsesSharedCache:
    """Unauthenticated minimal health must reflect the last full probe run."""

    @pytest.mark.asyncio
    async def test_minimal_health_reflects_degraded_cached_state(
        self, mock_settings: MagicMock
    ) -> None:
        """A degraded full run on one instance degrades minimal health on another.

        Mirrors production: each request gets a fresh HealthService, so the
        cache populated by an authed full check must be visible to a later
        unauthenticated minimal check on a *different* instance.
        """
        full_service = _make_service(mock_settings, _degraded_results())
        full_response = await full_service.check_health(detailed=True)
        assert full_response.healthy is False  # precondition

        fresh_service = HealthService(settings=mock_settings, queue_client=None)
        minimal = await fresh_service.check_health(detailed=False)

        assert minimal.healthy is False
        assert minimal.status == "degraded"
        # Minimal payload never leaks per-subsystem details.
        assert minimal.checks is None

    @pytest.mark.asyncio
    async def test_minimal_health_without_cache_defaults_healthy(
        self, mock_settings: MagicMock
    ) -> None:
        """With no cached full run yet, minimal health keeps the placeholder."""
        service = HealthService(settings=mock_settings, queue_client=None)
        minimal = await service.check_health(detailed=False)

        assert minimal.healthy is True
        assert minimal.status == "ok"
        assert minimal.checks is None


class TestFullHealthTtlSharedAcrossInstances:
    """The 5s TTL must apply across per-request service instances."""

    @pytest.mark.asyncio
    async def test_second_instance_within_ttl_does_not_rerun_probes(
        self, mock_settings: MagicMock
    ) -> None:
        """A second instance's full check within TTL returns the cached response."""
        first = _make_service(mock_settings, _healthy_results())
        first_response = await first.check_health(detailed=True)

        second = HealthService(settings=mock_settings, queue_client=None)
        second_check_all = AsyncMock(return_value=_healthy_results())
        second._registry.check_all = second_check_all  # type: ignore[method-assign]

        second_response = await second.check_health(detailed=True)

        assert second_response is first_response
        second_check_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reset_cache_forces_fresh_probe_run(self, mock_settings: MagicMock) -> None:
        """reset_cache() clears the shared cache so the next check re-probes."""
        first = _make_service(mock_settings, _healthy_results())
        await first.check_health(detailed=True)

        HealthService.reset_cache()

        second = _make_service(mock_settings, _degraded_results())
        response = await second.check_health(detailed=True)

        assert response.healthy is False
