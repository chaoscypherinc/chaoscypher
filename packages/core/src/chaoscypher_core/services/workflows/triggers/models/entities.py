# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Trigger Domain Models.

Internal domain models for trigger execution and statistics.
"""

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from datetime import datetime


class TriggerExecutionStatus(StrEnum):
    """Status of a trigger execution."""

    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class TriggerExecution:
    """Represents a single trigger execution."""

    execution_id: str
    trigger_id: str
    trigger_name: str
    workflow_id: str
    workflow_name: str
    status: TriggerExecutionStatus
    event_source: str
    fired_at: datetime
    execution_time: float = 0.0  # seconds
    error: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data["status"] = self.status.value
        data["fired_at"] = self.fired_at.isoformat()
        return data


@dataclass
class TriggerStats:
    """Statistics for a specific trigger."""

    trigger_id: str
    total_executions: int = 0
    successful: int = 0
    failed: int = 0
    avg_execution_time: float = 0.0
    success_rate: float = 0.0  # Decimal 0-1 (e.g., 0.75 = 75%)
