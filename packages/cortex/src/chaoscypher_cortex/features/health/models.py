# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Health Models.

Pydantic DTOs for system health check responses.
"""

from typing import Any

from pydantic import BaseModel


class HealthCheckItem(BaseModel):
    """A single health check result."""

    status: str  # "ok" | "warning" | "error"
    message: str
    details: dict[str, Any] | None = None
    category: str | None = None  # "resource" | "service" | "operational"
    auto_recoverable: bool | None = None


class HealthCheckResponse(BaseModel):
    """Consolidated system health response.

    Unauthenticated callers receive only ``{healthy, status}``; ``checks`` is
    omitted for unauthenticated callers so LAN scanners cannot fingerprint
    the deployed LLM stack, queue worker topology, or graph stats.

    Authenticated callers receive the full ``checks`` payload.

    The route uses ``response_model_exclude_none=True`` so that ``checks=None``
    is not serialized into the response JSON at all.
    """

    healthy: bool
    # "ok" when healthy, "degraded" otherwise — always present so Docker
    # HEALTHCHECK and unauthenticated probes can read a single scalar field
    # without parsing the detailed checks dict.
    status: str  # "ok" | "degraded"
    # Detailed per-subsystem checks — omitted for unauthenticated callers so
    # LAN scanners cannot fingerprint the deployed LLM stack.
    checks: dict[str, HealthCheckItem] | None = None
