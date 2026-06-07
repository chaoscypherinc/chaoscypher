# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph database health probe.

Checks entity and relationship counts in the knowledge graph.
"""

from __future__ import annotations

from collections.abc import Callable

from chaoscypher_core.services.events.health.models import ProbeResult


class GraphProbe:
    """Health probe that checks graph database statistics.

    Retrieves entity and relationship counts from the graph to
    determine whether the graph is populated and accessible.

    Attributes:
        name: Probe identifier ("graph").
        category: Probe category ("operational").
        auto_recoverable: Always True (graph may populate over time).
    """

    def __init__(
        self,
        counts_fn: Callable[[], dict[str, int]] | None = None,
    ) -> None:
        """Initialize the graph probe.

        Args:
            counts_fn: Zero-arg callable returning a dict with
                ``knowledge_nodes`` and ``links`` keys, or None
                if the counts service is unavailable.
        """
        self._counts_fn = counts_fn

    @property
    def name(self) -> str:
        """Probe identifier."""
        return "graph"

    @property
    def category(self) -> str:
        """Probe category."""
        return "operational"

    @property
    def auto_recoverable(self) -> bool:
        """Whether the issue can resolve without intervention."""
        return True

    async def check(self) -> ProbeResult:
        """Check graph database entity and relationship counts.

        Returns:
            ProbeResult with graph statistics or warning/error status.
        """
        if not self._counts_fn:
            return ProbeResult(
                name=self.name,
                status="warning",
                message="Counts service unavailable",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )

        try:
            counts = self._counts_fn()
            entities = counts.get("knowledge_nodes", 0)
            relationships = counts.get("links", 0)

            if entities == 0:
                return ProbeResult(
                    name=self.name,
                    status="ok",
                    message="Empty (0 entities)",
                    category=self.category,
                    auto_recoverable=self.auto_recoverable,
                    details={
                        "entity_count": 0,
                        "relationship_count": relationships,
                    },
                )

            return ProbeResult(
                name=self.name,
                status="ok",
                message=f"{entities:,} entities / {relationships:,} relationships",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
                details={
                    "entity_count": entities,
                    "relationship_count": relationships,
                },
            )
        except Exception:
            return ProbeResult(
                name=self.name,
                status="error",
                message="Graph check failed",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )
