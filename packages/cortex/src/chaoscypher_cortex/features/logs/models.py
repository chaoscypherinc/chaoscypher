# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Log Viewer Models.

Pydantic DTOs for log viewing and service status responses.
"""

from pydantic import BaseModel, ConfigDict


class LogEntry(BaseModel):
    """A single parsed log line."""

    timestamp: str
    service: str
    level: str
    message: str

    model_config = ConfigDict(extra="forbid")


class LogResponse(BaseModel):
    """Response containing log lines for a service or all services."""

    service: str | None = None
    lines: list[str]
    total_lines: int

    model_config = ConfigDict(extra="forbid")


class ServiceStatus(BaseModel):
    """Status of a single supervised service."""

    name: str
    state: str
    pid: int | None = None
    uptime_seconds: int | None = None
    start_time: str | None = None
    description: str = ""

    model_config = ConfigDict(extra="forbid")


class ServiceStatusResponse(BaseModel):
    """Response containing all service statuses."""

    available: bool = True
    services: list[ServiceStatus]

    model_config = ConfigDict(extra="forbid")
