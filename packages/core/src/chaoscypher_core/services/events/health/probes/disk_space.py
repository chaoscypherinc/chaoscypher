# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Disk space health probe.

Checks free disk space at a configured path and returns warning or error
status based on configurable byte thresholds. Defaults to warning below
2 GB and error below 1 GB.
"""

from __future__ import annotations

import shutil

import structlog

from chaoscypher_core.services.events.health.models import ProbeResult


logger = structlog.get_logger(__name__)


def _format_bytes(n: int) -> str:
    """Format a byte count as a human-readable string.

    Args:
        n: Number of bytes to format.

    Returns:
        Human-readable string (e.g. "1.5 GB", "423 MB", "12 KB").
    """
    for unit in ("TB", "GB", "MB", "KB"):
        threshold = {"TB": 1 << 40, "GB": 1 << 30, "MB": 1 << 20, "KB": 1 << 10}[unit]
        if n >= threshold:
            value = n / threshold
            return f"{value:.1f} {unit}"
    return f"{n} B"


class DiskSpaceProbe:
    """Health probe that monitors free disk space.

    Checks free space at the configured path using ``shutil.disk_usage``
    and compares against warning and error thresholds.

    Args:
        path: Filesystem path to check disk usage for.
        warn_bytes: Free-space threshold for warning status (default 2 GB).
        error_bytes: Free-space threshold for error status (default 1 GB).
    """

    def __init__(
        self,
        path: str,
        warn_bytes: int = 2_147_483_648,
        error_bytes: int = 1_073_741_824,
    ) -> None:
        """Initialize the disk space probe.

        Args:
            path: Filesystem path to check disk usage for.
            warn_bytes: Free-space threshold for warning status (default 2 GB).
            error_bytes: Free-space threshold for error status (default 1 GB).
        """
        self._path = path
        self._warn_bytes = warn_bytes
        self._error_bytes = error_bytes

    @property
    def name(self) -> str:
        """Unique identifier for this probe."""
        return "disk_space"

    @property
    def category(self) -> str:
        """Probe category."""
        return "resource"

    @property
    def auto_recoverable(self) -> bool:
        """Low disk space requires human intervention."""
        return False

    async def check(self) -> ProbeResult:
        """Check free disk space against configured thresholds.

        Returns:
            ProbeResult with status "ok", "warning", or "error" based on
            available free space relative to thresholds.
        """
        try:
            usage = shutil.disk_usage(self._path)
            free = usage.free
        except OSError as exc:
            logger.exception(
                "disk_space_check_failed",
                path=self._path,
            )
            return ProbeResult(
                name=self.name,
                status="error",
                message=f"Failed to check disk space: {exc}",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
                details={"path": self._path},
            )

        details = {
            "free_bytes": free,
            "free_human": _format_bytes(free),
            "warn_threshold": self._warn_bytes,
            "error_threshold": self._error_bytes,
            "path": self._path,
        }

        if free < self._error_bytes:
            status = "error"
            message = (
                f"Critical disk space: {_format_bytes(free)} free "
                f"(threshold: {_format_bytes(self._error_bytes)})"
            )
        elif free < self._warn_bytes:
            status = "warning"
            message = (
                f"Low disk space: {_format_bytes(free)} free "
                f"(threshold: {_format_bytes(self._warn_bytes)})"
            )
        else:
            status = "ok"
            message = f"Disk space OK: {_format_bytes(free)} free"

        logger.debug(
            "disk_space_checked",
            path=self._path,
            free_bytes=free,
            status=status,
        )

        return ProbeResult(
            name=self.name,
            status=status,
            message=message,
            category=self.category,
            auto_recoverable=self.auto_recoverable,
            details=details,
        )
