# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cloud provider health probe.

Checks whether a cloud LLM provider (OpenAI, Anthropic, Gemini) has
its API key configured.
"""

from __future__ import annotations

from chaoscypher_core.services.events.health.models import ProbeResult


class CloudProviderProbe:
    """Health probe that verifies cloud provider API key configuration.

    Checks if the API key for a given cloud LLM provider is set.
    Does not validate the key against the provider's API.

    Attributes:
        name: Probe identifier ("provider").
        category: Probe category ("service").
        auto_recoverable: Always True (key can be configured at runtime).
    """

    def __init__(self, provider: str, api_key: str | None) -> None:
        """Initialize the cloud provider probe.

        Args:
            provider: Provider name (e.g. "openai", "anthropic", "gemini").
            api_key: The configured API key, or None if not set.
        """
        self._provider = provider
        self._api_key = api_key
        self._display_names = {
            "openai": "OpenAI",
            "anthropic": "Anthropic",
            "gemini": "Gemini",
        }

    @property
    def name(self) -> str:
        """Probe identifier."""
        return "provider"

    @property
    def category(self) -> str:
        """Probe category."""
        return "service"

    @property
    def auto_recoverable(self) -> bool:
        """Whether the issue can resolve without intervention."""
        return True

    async def check(self) -> ProbeResult:
        """Check if the cloud provider API key is configured.

        Returns:
            ProbeResult with "ok" if an API key is set,
            "error" if the key is missing.
        """
        display_name = self._display_names.get(self._provider, self._provider)

        if self._api_key:
            return ProbeResult(
                name=self.name,
                status="ok",
                message=f"{display_name} API key configured",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )

        return ProbeResult(
            name=self.name,
            status="error",
            message=f"{display_name} API key not configured",
            category=self.category,
            auto_recoverable=self.auto_recoverable,
        )
