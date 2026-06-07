# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Ollama health probes.

OllamaProbe checks Ollama server connectivity, version, and lists
installed models. ModelProbe verifies a specific model is installed.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import structlog

from chaoscypher_core.services.events.health.models import ProbeResult


logger = structlog.get_logger(__name__)


class OllamaProbe:
    """Health probe that checks Ollama server connectivity.

    Connects to the Ollama HTTP API to verify the server is reachable,
    fetches the server version, and lists installed models.

    Attributes:
        name: Probe identifier ("ollama").
        category: Probe category ("service").
        auto_recoverable: Always True (Ollama may come back online).
    """

    def __init__(self, base_url: str, timeout: float) -> None:
        """Initialize the Ollama probe.

        Args:
            base_url: Ollama server base URL (e.g. "http://localhost:11434").
            timeout: HTTP request timeout in seconds.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._installed_models: list[str] = []

    @property
    def name(self) -> str:
        """Probe identifier."""
        return "ollama"

    @property
    def category(self) -> str:
        """Probe category."""
        return "service"

    @property
    def auto_recoverable(self) -> bool:
        """Whether the issue can resolve without intervention."""
        return True

    @property
    def installed_models(self) -> list[str]:
        """List of model names discovered during the last check."""
        return list(self._installed_models)

    async def check(self) -> ProbeResult:
        """Check Ollama connectivity, version, and installed models.

        Returns:
            ProbeResult with connection status, version info, and
            list of installed models in details.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                # Root endpoint
                root_resp = await client.get(self._base_url)
                if "ollama" not in root_resp.text.lower():
                    self._installed_models = []
                    return ProbeResult(
                        name=self.name,
                        status="error",
                        message=f"Unexpected response from {self._base_url}",
                        category=self.category,
                        auto_recoverable=self.auto_recoverable,
                        details={"base_url": self._base_url},
                    )

                # Version
                version = None
                try:
                    version_resp = await client.get(f"{self._base_url}/api/version")
                    if version_resp.status_code == 200:
                        version = version_resp.json().get("version")
                except Exception as exc:
                    logger.debug("ollama_version_fetch_failed", error=str(exc))

                # Models
                installed: list[str] = []
                try:
                    tags_resp = await client.get(f"{self._base_url}/api/tags")
                    if tags_resp.status_code == 200:
                        installed = [
                            m.get("name", "unknown") for m in tags_resp.json().get("models", [])
                        ]
                except Exception as exc:
                    logger.debug("ollama_models_fetch_failed", error=str(exc))

                self._installed_models = installed
                message = f"Connected (v{version})" if version else "Connected"

                return ProbeResult(
                    name=self.name,
                    status="ok",
                    message=message,
                    category=self.category,
                    auto_recoverable=self.auto_recoverable,
                    details={"base_url": self._base_url, "version": version},
                )

        except httpx.ConnectError:
            self._installed_models = []
            return ProbeResult(
                name=self.name,
                status="error",
                message="Chat Provider Ollama Offline",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
                details={
                    "base_url": self._base_url,
                    "tooltip": f"Not reachable at {self._base_url}",
                },
            )
        except httpx.TimeoutException:
            self._installed_models = []
            return ProbeResult(
                name=self.name,
                status="error",
                message="Chat Provider Ollama Unresponsive",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
                details={
                    "base_url": self._base_url,
                    "tooltip": f"Timed out at {self._base_url}",
                },
            )
        except Exception as exc:
            logger.warning("ollama_health_check_failed", error=str(exc))
            self._installed_models = []
            return ProbeResult(
                name=self.name,
                status="error",
                message="Chat Provider Ollama Offline",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
                details={
                    "base_url": self._base_url,
                    "tooltip": f"Health check failed at {self._base_url}: {exc}",
                },
            )


class ModelProbe:
    """Health probe that checks if a specific model is installed on Ollama.

    Uses a callable to retrieve the current list of installed models
    (typically from OllamaProbe.installed_models) and checks whether
    the target model is present.

    Attributes:
        name: Probe identifier (e.g. "chat_model", "extraction_model").
        category: Probe category ("service").
        auto_recoverable: Always True (models can be pulled).
    """

    def __init__(
        self,
        model_name: str,
        label: str,
        probe_name: str,
        installed_models_fn: Callable[[], list[str]],
    ) -> None:
        """Initialize the model probe.

        Args:
            model_name: The configured model name to look for.
            label: Human-readable label (e.g. "Chat model").
            probe_name: Unique probe name (e.g. "chat_model").
            installed_models_fn: Zero-arg callable returning current
                installed model names.
        """
        self._model_name = model_name
        self._label = label
        self._probe_name = probe_name
        self._installed_models_fn = installed_models_fn

    @property
    def name(self) -> str:
        """Probe identifier."""
        return self._probe_name

    @property
    def category(self) -> str:
        """Probe category."""
        return "service"

    @property
    def auto_recoverable(self) -> bool:
        """Whether the issue can resolve without intervention."""
        return True

    async def check(self) -> ProbeResult:
        """Check if the model is installed.

        Returns:
            ProbeResult with "ok" if the model is installed,
            "error" if it is not found.
        """
        installed = self._installed_models_fn()

        if self._model_name in installed:
            return ProbeResult(
                name=self.name,
                status="ok",
                message=f"{self._model_name} installed",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
                details={"model": self._model_name},
            )

        return ProbeResult(
            name=self.name,
            status="error",
            message=f"{self._model_name} not installed",
            category=self.category,
            auto_recoverable=self.auto_recoverable,
            details={"model": self._model_name},
        )
