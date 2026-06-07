# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Database health probe.

Checks SQLite database integrity via the adapter's ``quick_check()``
method and reports health status to the registry.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from chaoscypher_core.services.events.health.models import ProbeResult


logger = structlog.get_logger(__name__)


class DatabaseProbe:
    """Health probe that checks database accessibility and writability.

    Two-stage check: ``quick_check()`` confirms the database responds to a
    query; ``writable_check()`` confirms it accepts writes via
    ``BEGIN IMMEDIATE + ROLLBACK``.  Both stages must pass before the probe
    reports "ok".  A database mounted read-only (Docker volume
    misconfiguration, FS in failsafe mode) will fail the second stage.

    Attributes:
        name: Probe identifier ("database").
        category: Probe category ("resource").
        auto_recoverable: Always True (database issues may self-resolve).
    """

    def __init__(self, adapter_fn: Callable[[], Any]) -> None:
        """Initialize the database probe.

        Args:
            adapter_fn: Zero-arg callable returning an adapter with
                ``quick_check()`` and ``writable_check()`` methods.
        """
        self._adapter_fn = adapter_fn

    @property
    def name(self) -> str:
        """Probe identifier."""
        return "database"

    @property
    def category(self) -> str:
        """Probe category."""
        return "resource"

    @property
    def auto_recoverable(self) -> bool:
        """Whether the issue can resolve without intervention."""
        return True

    async def check(self) -> ProbeResult:
        """Execute the database health check.

        Two-stage:

        1. ``quick_check()`` — fast ``SELECT 1`` connectivity check.
        2. ``writable_check()`` — ``BEGIN IMMEDIATE + ROLLBACK`` to verify
           the database accepts writes (catches read-only-mounted volumes).

        Returns:
            ProbeResult "ok" when both stages pass, "error" on any failure.
        """
        try:
            adapter = self._adapter_fn()
            is_healthy = adapter.quick_check()
        except Exception as exc:
            logger.warning("database_probe_quick_check_failed", exc_info=True)
            return ProbeResult(
                name=self.name,
                status="error",
                message=str(exc),
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )

        if not is_healthy:
            return ProbeResult(
                name=self.name,
                status="error",
                message="Database unreachable",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )

        try:
            adapter.writable_check()
        except Exception as exc:
            logger.warning("database_probe_writable_check_failed", exc_info=True)
            return ProbeResult(
                name=self.name,
                status="error",
                message=f"Database not writable: {exc}",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )

        return ProbeResult(
            name=self.name,
            status="ok",
            message="Database accessible and writable",
            category=self.category,
            auto_recoverable=self.auto_recoverable,
        )
