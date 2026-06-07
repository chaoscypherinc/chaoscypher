# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Health Probe Registry.

Manages registration and execution of health probes. Probes are
self-contained check implementations; the registry only coordinates
their registration and execution order.

Example:
    from chaoscypher_core.services.events.health import HealthRegistry

    registry = HealthRegistry()
    registry.register(disk_probe)
    registry.register(llm_probe)

    results, healthy = await registry.check_all_with_status()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.exceptions import NotFoundError


if TYPE_CHECKING:
    from chaoscypher_core.services.events.health.models import (
        HealthProbe,
        ProbeInfo,
        ProbeResult,
    )

logger = structlog.get_logger(__name__)


class HealthRegistry:
    """Central registry for health probes.

    Probes register at startup and consumers (API endpoints,
    auto-pause evaluator) query the registry for health status.

    Attributes:
        probes: Read-only dict of registered probes keyed by name.
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._probes: dict[str, HealthProbe] = {}

    @property
    def probes(self) -> dict[str, HealthProbe]:
        """Read-only dict of registered probes keyed by name."""
        return dict(self._probes)

    def register(self, probe: HealthProbe) -> None:
        """Register a health probe.

        Args:
            probe: Probe instance implementing the HealthProbe protocol.

        Raises:
            ValueError: If a probe with the same name is already registered.
        """
        if probe.name in self._probes:
            msg = f"Probe already registered: {probe.name}"
            raise ValueError(  # nosemgrep: cc-045-bare-stdlib-raise-in-core - programmer error: probe registration is a startup-time invariant, never user-reachable
                msg
            )

        self._probes[probe.name] = probe
        logger.debug("probe_registered", name=probe.name, category=probe.category)

    def list_probes(self) -> list[ProbeInfo]:
        """Return metadata for all registered probes.

        Returns:
            List of ProbeInfo dataclasses, one per registered probe.
        """
        from chaoscypher_core.services.events.health.models import ProbeInfo

        return [
            ProbeInfo(
                name=probe.name,
                category=probe.category,
                auto_recoverable=probe.auto_recoverable,
            )
            for probe in self._probes.values()
        ]

    async def check_all(self) -> dict[str, ProbeResult]:
        """Run all registered probes and collect results.

        If a probe raises an exception, an error ProbeResult is
        returned for that probe instead of propagating the exception.

        Returns:
            Dict of probe name to ProbeResult.
        """
        from chaoscypher_core.services.events.health.models import ProbeResult

        results: dict[str, ProbeResult] = {}

        for name, probe in self._probes.items():
            try:
                results[name] = await probe.check()
            except Exception:
                logger.warning("probe_failed", name=name, exc_info=True)
                results[name] = ProbeResult(
                    name=name,
                    status="error",
                    message="Probe raised an exception",
                    category=probe.category,
                    auto_recoverable=probe.auto_recoverable,
                )

        return results

    async def check_all_with_status(self) -> tuple[dict[str, ProbeResult], bool]:
        """Run all probes and determine overall health.

        Returns:
            Tuple of (results dict, healthy flag). Healthy is True when
            no probe has status ``"error"``.
        """
        results = await self.check_all()
        healthy = all(r.status != "error" for r in results.values())
        return results, healthy

    async def check(self, name: str) -> ProbeResult:
        """Run a single probe by name.

        Args:
            name: The registered probe name to execute.

        Returns:
            ProbeResult from the probe's check method.

        Raises:
            NotFoundError: If no probe is registered with the given name.
        """
        if name not in self._probes:
            raise NotFoundError("Probe", name)

        return await self._probes[name].check()
