# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Health Probe Models.

Defines the core data structures and protocol for health probes.
Probes check system health (disk space, LLM providers, queue, etc.)
and return structured results used by the health registry.

Example:
    from chaoscypher_core.services.events.health import HealthProbe, ProbeResult

    class MyProbe:
        @property
        def name(self) -> str:
            return "my_check"

        @property
        def category(self) -> str:
            return "resource"

        @property
        def auto_recoverable(self) -> bool:
            return True

        async def check(self) -> ProbeResult:
            return ProbeResult(
                name=self.name,
                status="ok",
                message="All good",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ProbeResult:
    """Result of a single health probe check.

    Attributes:
        name: Identifier for the probe (e.g. "disk_space").
        status: Health status - "ok", "warning", or "error".
        message: Human-readable description of the current state.
        category: Probe category - "resource", "service", or "operational".
        auto_recoverable: Whether the issue can resolve without intervention.
        details: Optional additional context (metrics, thresholds, etc.).
    """

    name: str
    status: str
    message: str
    category: str
    auto_recoverable: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProbeInfo:
    """Static metadata about a registered health probe.

    Attributes:
        name: Identifier for the probe (e.g. "disk_space").
        category: Probe category - "resource", "service", or "operational".
        auto_recoverable: Whether issues from this probe can self-resolve.
    """

    name: str
    category: str
    auto_recoverable: bool


@runtime_checkable
class HealthProbe(Protocol):
    """Protocol for health check probes.

    Each probe monitors a specific aspect of system health and returns
    a structured result. Probes are registered with the HealthRegistry
    and executed periodically.
    """

    @property
    def name(self) -> str:
        """Unique identifier for this probe."""
        ...

    @property
    def category(self) -> str:
        """Probe category: 'resource', 'service', or 'operational'."""
        ...

    @property
    def auto_recoverable(self) -> bool:
        """Whether issues detected by this probe can resolve without intervention."""
        ...

    async def check(self) -> ProbeResult:
        """Execute the health check and return the result."""
        ...
