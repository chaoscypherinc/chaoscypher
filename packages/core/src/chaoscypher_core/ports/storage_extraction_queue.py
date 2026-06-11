# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ExtractionQueueStorageProtocol for ChaosCypher storage.

Split from the original SourceStorageProtocol god-protocol.
Covers extraction queue gating — the "at most one extraction at a time per
database" coordination primitive.
Binds to SourceIndexingMixin in the SQLite adapter.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ExtractionQueueStorageProtocol(Protocol):
    """Storage protocol for extraction queue gating.

    These 5 methods form a self-contained coordination primitive ensuring
    at most one source extracts at a time per database.  Used exclusively
    by the extraction gate logic in the operations worker.
    """

    def get_extracting_source_count(self, database_name: str) -> int:
        """Get count of sources currently in 'extracting' status.

        Used to gate new extractions - only one source should extract at a time.

        Args:
            database_name: Database context

        Returns:
            Count of sources with status='extracting'
        """
        ...

    def try_claim_extraction(self, source_id: str, database_name: str, depth: str = "full") -> bool:
        """Atomically claim extraction slot if no other source is extracting.

        Uses a single UPDATE ... WHERE query to avoid TOCTOU race conditions
        when multiple workers check-then-set concurrently.

        Args:
            source_id: Source to claim extraction for
            database_name: Database context
            depth: Extraction depth level

        Returns:
            True if this source claimed the extraction slot, False if another
            source is already extracting.
        """
        ...

    def mark_extraction_waiting(self, source_id: str, file_info: dict[str, Any]) -> None:
        """Mark a source as waiting for extraction.

        Sets extraction_queued_at timestamp to track queue order.
        Stores file_info for later use when extraction starts.

        Args:
            source_id: Source ID to mark as waiting
            file_info: File info dict to store for later extraction
        """
        ...

    def get_oldest_waiting_extraction(self, database_name: str) -> dict[str, Any] | None:
        """Get the oldest source waiting for extraction.

        Finds sources that:
        - Have status='indexed' (ready for extraction)
        - Have extraction_queued_at set (waiting in queue)

        Args:
            database_name: Database context

        Returns:
            Source dict with file_info or None if no waiting sources
        """
        ...

    def clear_extraction_waiting(self, source_id: str) -> None:
        """Clear the extraction waiting flag after extraction starts.

        Args:
            source_id: Source ID to clear waiting flag
        """
        ...

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 6).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def count_extraction_jobs(self, *, database_name: str) -> int:
        """Count ChunkExtractionJob rows in one database."""
        ...

    def delete_extraction_jobs(self, *, database_name: str) -> int:
        """Delete every ChunkExtractionJob in one database. Returns count."""
        ...

    def count_extraction_tasks(self, *, database_name: str) -> int:
        """Count ChunkExtractionTask rows in one database."""
        ...

    def delete_extraction_tasks(self, *, database_name: str) -> int:
        """Delete every ChunkExtractionTask in one database. Returns count."""
        ...

    def clear_all_extraction_jobs(self) -> int:
        """Delete every ChunkExtractionJob across databases. Returns count."""
        ...

    def clear_all_extraction_tasks(self) -> int:
        """Delete every ChunkExtractionTask across databases. Returns count."""
        ...
