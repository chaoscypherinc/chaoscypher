# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Diagnostic Report Models.

Pydantic models for system diagnostic reports used in bug reporting
and troubleshooting.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SystemInfo(BaseModel):
    """System version and platform information."""

    chaoscypher_version: str
    python_version: str
    platform: str
    packages: dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class DiagnosticDatabaseStats(BaseModel):
    """Database statistics for diagnostics."""

    database_name: str
    file_size_bytes: int | None = None
    table_counts: dict[str, int] = Field(default_factory=dict)
    index_stats: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class DiagnosticReport(BaseModel):
    """Complete diagnostic report for bug reports."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    system: SystemInfo
    database: DiagnosticDatabaseStats
    settings: dict[str, Any] = Field(default_factory=dict)
    logs: dict[str, str] = Field(default_factory=dict)
    queue: dict[str, Any] | None = None
    services: list[dict[str, Any]] | None = None

    model_config = ConfigDict(extra="forbid")
