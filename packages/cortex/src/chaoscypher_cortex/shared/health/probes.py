# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Health probe Protocol + HealthStatus shared across Cortex slices.

Features that own a health-checkable component provide a concrete
probe (e.g., SearchHealthProbe). The health feature aggregates probes
passed to it via constructor — it does not import sibling-feature
services directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class HealthStatus:
    """Result of a single probe check."""

    ok: bool
    detail: str = ""
    metrics: dict | None = None


class HealthProbe(Protocol):
    """Protocol for health probes."""

    name: str

    async def check(self) -> HealthStatus:
        """Perform the health check."""
        ...
