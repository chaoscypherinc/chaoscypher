# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared queue utilities.

Common helpers used across queue client, worker, service, and monitor.
"""

from datetime import UTC, datetime
from typing import Any


def iso_now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def decode_bytes(value: Any) -> Any:
    """Decode bytes to string if needed, otherwise return as-is."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value
