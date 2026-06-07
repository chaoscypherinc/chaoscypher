# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SearchHealthProbe — SearchService-backed health probe.

Lives in the search feature because it knows SearchService internals.
The health feature will consume it via injection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from chaoscypher_cortex.shared.health.probes import HealthStatus


if TYPE_CHECKING:
    from chaoscypher_cortex.features.search.service import SearchService


logger = structlog.get_logger(__name__)


class SearchHealthProbe:
    """Checks search-index health via SearchService.get_stats()."""

    name = "search"

    def __init__(self, search_service: SearchService) -> None:
        """Initialise with the search service the probe will introspect."""
        self._search = search_service

    async def check(self) -> HealthStatus:
        """Perform the health check by fetching search index statistics."""
        try:
            stats = self._search.get_stats()
        except Exception as exc:
            logger.warning("search_health_probe_failed", exc_info=True)
            return HealthStatus(ok=False, detail=f"search stats failed: {exc}")

        return HealthStatus(
            ok=True,
            detail=f"{stats.fulltext_doc_count} docs, {stats.vector_index_size} vectors",
            metrics={
                "fulltext_doc_count": stats.fulltext_doc_count,
                "vector_index_size": stats.vector_index_size,
                "vector_dimension": stats.vector_dimension,
            },
        )
