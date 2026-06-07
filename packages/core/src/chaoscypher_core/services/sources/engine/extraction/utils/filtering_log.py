# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Filtering log collector for extraction pipeline diagnostics.

Tracks items removed at each filtering/deduplication stage so users
can understand what was filtered and why.  Each stage records its
input count, the number of items removed, and up to
``MAX_ITEMS_PER_STAGE`` individual ``FilteredItem`` examples.

The resulting structure is stored as JSON on the chunk task (per-chunk
stages) or inside ``extraction_results.metadata`` (cross-chunk stages).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class FilteredItem:
    """A single item removed by a pipeline filter."""

    item_type: str
    """``"entity"`` or ``"relationship"``."""

    name: str
    """Entity name, or ``"EntityA -> EntityB"`` for relationships."""

    entity_type: str
    """Entity type, or relationship type."""

    reason: str
    """Human-readable removal reason."""

    details: dict[str, Any] | None = None
    """Optional extra context (score, threshold, tier, etc.)."""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        d: dict[str, Any] = {
            "item_type": self.item_type,
            "name": self.name,
            "entity_type": self.entity_type,
            "reason": self.reason,
        }
        if self.details:
            d["details"] = self.details
        return d


@dataclass
class _StageRecord:
    """Internal record for a single filtering stage."""

    stage: str
    input_count: int
    removed_count: int
    items: list[FilteredItem]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
            "stage": self.stage,
            "input_count": self.input_count,
            "removed_count": self.removed_count,
            "items": [item.to_dict() for item in self.items],
        }


class FilteringLog:
    """Accumulates per-stage filtering data across a pipeline run.

    Usage::

        log = FilteringLog()
        items = [FilteredItem("entity", "Foo", "Person", "junk name")]
        log.add_stage("entity_evidence_filter", input_count=10,
                       removed_count=1, items=items)
        payload = log.to_dict()  # JSON-safe dict
    """

    MAX_ITEMS_PER_STAGE: int = 50

    def __init__(self) -> None:
        """Initialize an empty filtering log."""
        self._stages: list[_StageRecord] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_stage(
        self,
        stage: str,
        *,
        input_count: int,
        removed_count: int,
        items: list[FilteredItem] | None = None,
    ) -> None:
        """Record one filtering stage.

        Args:
            stage: Machine-readable stage name (e.g. ``"entity_evidence_filter"``).
            input_count: Number of items entering this stage.
            removed_count: Number of items removed by this stage.
            items: Individual removed items (truncated to
                :pyattr:`MAX_ITEMS_PER_STAGE`).
        """
        capped = (items or [])[: self.MAX_ITEMS_PER_STAGE]
        self._stages.append(
            _StageRecord(
                stage=stage,
                input_count=input_count,
                removed_count=removed_count,
                items=capped,
            )
        )

    @property
    def total_removed(self) -> int:
        """Sum of ``removed_count`` across all stages."""
        return sum(s.removed_count for s in self._stages)

    @property
    def has_removals(self) -> bool:
        """``True`` if any stage recorded at least one removal."""
        return any(s.removed_count > 0 for s in self._stages)

    def merge(self, other: FilteringLog) -> None:
        """Append all stages from *other* into this log."""
        self._stages.extend(other._stages)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict.

        Returns:
            Dict with keys ``version``, ``total_removed``, and ``stages``.
        """
        return {
            "version": 1,
            "total_removed": self.total_removed,
            "stages": [s.to_dict() for s in self._stages],
        }
