# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared display helpers for CLI output formatting."""

from chaoscypher_core.models import SourceStatus


# Status colors for source processing pipeline statuses
_STATUS_COLORS = {
    "uploaded": "yellow",
    SourceStatus.INDEXING: "blue",
    SourceStatus.INDEXED: "cyan",
    SourceStatus.EXTRACTING: "blue",
    SourceStatus.EXTRACTED: "green",
    SourceStatus.COMMITTING: "blue",
    SourceStatus.COMMITTED: "green",
    SourceStatus.AWAITING_CONFIRMATION: "magenta",
    "failed": "red",
}


def get_status_color(status: str) -> str:
    """Get Rich color for a source processing status.

    Args:
        status: Source processing status string

    Returns:
        Rich color name
    """
    return _STATUS_COLORS.get(status, "dim")


def get_quality_color(grade: float) -> str:
    """Get Rich color for a quality grade (0-100).

    Args:
        grade: Quality grade value

    Returns:
        Rich color name
    """
    if grade >= 70:
        return "green"
    if grade >= 50:
        return "cyan"
    if grade >= 30:
        return "yellow"
    return "red"
