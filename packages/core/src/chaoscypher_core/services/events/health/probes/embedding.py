# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Embedding provider health probe.

Checks embedding provider health by calling an async health check
function and converting the result into a ProbeResult.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from chaoscypher_core.services.events.health.models import ProbeResult


logger = structlog.get_logger(__name__)


_PROVIDER_DISPLAY = {
    "ollama": "Ollama",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "local": "Local",
}


class EmbeddingProbe:
    """Health probe that checks the embedding provider.

    Calls an async health check function and converts the returned
    health status into a ProbeResult. The function should return an
    object with ``healthy``, ``provider``, ``model``, ``dimensions``,
    ``response_time_ms``, and ``message`` attributes.

    Attributes:
        name: Probe identifier ("embeddings").
        category: Probe category ("service").
        auto_recoverable: Always True (provider may recover).
    """

    def __init__(
        self,
        health_check_fn: Callable[[], Coroutine[Any, Any, Any]],
    ) -> None:
        """Initialize the embedding probe.

        Args:
            health_check_fn: Async callable returning an embedding health
                result object with ``healthy``, ``provider``, ``model``,
                ``dimensions``, ``response_time_ms``, and ``message`` attrs.
        """
        self._health_check_fn = health_check_fn

    @property
    def name(self) -> str:
        """Probe identifier."""
        return "embeddings"

    @property
    def category(self) -> str:
        """Probe category."""
        return "service"

    @property
    def auto_recoverable(self) -> bool:
        """Whether the issue can resolve without intervention."""
        return True

    async def check(self) -> ProbeResult:
        """Check embedding provider health.

        Returns:
            ProbeResult with status based on the provider's health report.
        """
        try:
            health = await self._health_check_fn()

            details: dict[str, Any] = {
                "provider": health.provider,
                "model": health.model,
                "dimensions": health.dimensions,
            }
            if health.response_time_ms is not None:
                details["response_time_ms"] = health.response_time_ms

            if health.healthy:
                short_name = health.model.split("/")[-1] if "/" in health.model else health.model
                return ProbeResult(
                    name=self.name,
                    status="ok",
                    message=f"{short_name} ready ({health.provider})",
                    category=self.category,
                    auto_recoverable=self.auto_recoverable,
                    details=details,
                )

            display = _PROVIDER_DISPLAY.get(health.provider, health.provider.capitalize())
            if health.message:
                details["tooltip"] = health.message
            return ProbeResult(
                name=self.name,
                status="error",
                message=f"Embedding Provider {display} Offline",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
                details=details,
            )
        except Exception as exc:
            logger.warning("embedding_health_check_failed", error=str(exc))
            return ProbeResult(
                name=self.name,
                status="error",
                message="Embedding check failed",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )
