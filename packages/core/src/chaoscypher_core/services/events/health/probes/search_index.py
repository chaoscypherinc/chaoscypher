# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Search index health probe.

Checks search index statistics and whether a full reindex is needed
due to embedding model changes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from chaoscypher_core.services.events.health.models import ProbeResult


class SearchIndexProbe:
    """Health probe that checks search index status.

    Retrieves index statistics (fulltext doc count, vector index size)
    and checks whether a full reindex is needed due to an embedding
    model mismatch.

    Attributes:
        name: Probe identifier ("search_index").
        category: Probe category ("operational").
        auto_recoverable: Always True (reindex can be triggered).
    """

    def __init__(
        self,
        stats_fn: Callable[[], Any] | None = None,
        needs_reindex_fn: Callable[[], bool] | None = None,
    ) -> None:
        """Initialize the search index probe.

        Args:
            stats_fn: Zero-arg callable returning a stats object with
                ``fulltext_doc_count`` and ``vector_index_size`` attributes,
                or None if the search service is unavailable.
            needs_reindex_fn: Zero-arg callable returning True if stored
                embeddings don't match the current model, or None to skip.
        """
        self._stats_fn = stats_fn
        self._needs_reindex_fn = needs_reindex_fn

    @property
    def name(self) -> str:
        """Probe identifier."""
        return "search_index"

    @property
    def category(self) -> str:
        """Probe category."""
        return "operational"

    @property
    def auto_recoverable(self) -> bool:
        """Whether the issue can resolve without intervention."""
        return True

    async def check(self) -> ProbeResult:
        """Check search index stats and reindex status.

        Returns:
            ProbeResult with index statistics or warning/error status.
        """
        if not self._stats_fn:
            return ProbeResult(
                name=self.name,
                status="warning",
                message="Search service unavailable",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )

        try:
            # Check if model changed and rebuild is needed
            if self._needs_reindex_fn and self._needs_reindex_fn():
                return ProbeResult(
                    name=self.name,
                    status="warning",
                    message="Embedding mismatch",
                    category=self.category,
                    auto_recoverable=self.auto_recoverable,
                    details={
                        "needs_rebuild": True,
                        "tooltip": (
                            "Stored embeddings don't match the current model. "
                            "Rebuild search indexes in Settings > Search to fix."
                        ),
                    },
                )

            stats = self._stats_fn()
            fulltext = stats.fulltext_doc_count
            vectors = stats.vector_index_size

            if fulltext == 0 and vectors == 0:
                return ProbeResult(
                    name=self.name,
                    status="ok",
                    message="Empty (no indexed content)",
                    category=self.category,
                    auto_recoverable=self.auto_recoverable,
                    details={"fulltext_count": 0, "vector_count": 0},
                )

            return ProbeResult(
                name=self.name,
                status="ok",
                message=f"{fulltext:,} docs / {vectors:,} vectors",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
                details={"fulltext_count": fulltext, "vector_count": vectors},
            )
        except Exception:
            return ProbeResult(
                name=self.name,
                status="error",
                message="Index check failed",
                category=self.category,
                auto_recoverable=self.auto_recoverable,
            )
