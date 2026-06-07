# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Repository for vision_pages — VSA wrapper around VisionStorageProtocol.

The Cortex service layer never reaches into Core's storage adapter
directly (CC012). This thin wrapper exposes only the methods the
two endpoints need and returns storage TypedDicts unchanged — the
service maps to Pydantic responses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog


if TYPE_CHECKING:
    from collections.abc import Sequence

    from chaoscypher_core.ports.storage_vision import (
        VisionJob,
        VisionPageDescription,
        VisionStorageProtocol,
    )
    from chaoscypher_core.vision.states import VisionPageStatus


logger = structlog.get_logger(__name__)


class VisionPagesRepository:
    """VSA repository over VisionStorageProtocol."""

    def __init__(self, storage: VisionStorageProtocol, database_name: str) -> None:
        """Initialize the repository.

        Args:
            storage: The Core vision storage adapter (port).
            database_name: Active database name (kept for parity with
                sibling repositories — not currently used for routing).

        """
        self._storage = storage
        self._database_name = database_name

    def get_job_by_source(self, source_id: str) -> VisionJob | None:
        """Return the vision_jobs row for this source, or None."""
        return self._storage.get_vision_job_by_source(source_id)

    def list_pages(
        self,
        source_id: str,
        statuses: Sequence[VisionPageStatus] | None = None,
    ) -> list[VisionPageDescription]:
        """Return all vision_page_descriptions for the source.

        Args:
            source_id: Target source.
            statuses: Optional status filter (e.g. [FAILED]).

        Returns:
            List of storage TypedDicts.

        """
        return self._storage.list_vision_page_descriptions(source_id, statuses=statuses)

    def reset_for_retry(self, page_id: str) -> bool:
        """Reset one page to PENDING (decrementing the job counter).

        Returns True if reset happened, False if the row was already
        PENDING (no-op) or doesn't exist.
        """
        return self._storage.reset_vision_page_for_retry(page_id=page_id)


__all__ = ["VisionPagesRepository"]
