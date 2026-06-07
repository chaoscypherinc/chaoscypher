# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""SourceTagStorageProtocol for ChaosCypher storage.

Split from the original SourceStorageProtocol god-protocol (Phase 1 Task 12).
Covers SourceTag and SourceTagAssignment table operations — completely
independent of source CRUD and citation data.
Binds to SourceTagsMixin in the SQLite adapter.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SourceTagStorageProtocol(Protocol):
    """Storage protocol for source tag operations.

    Handles CRUD for SourceTag records and SourceTagAssignment join-table
    entries.  SourceTag and SourceTagAssignment are completely independent
    tables that can be factored out with zero impact on source CRUD or
    citation data.
    """

    def get_tag(self, tag_id: str, database_name: str = "") -> dict[str, Any] | None:
        """Get a tag by ID.

        Args:
            tag_id: Tag UUID
            database_name: Database name (optional)

        """
        ...

    def list_tags(self, database_name: str) -> list[dict[str, Any]]:
        """List all tags for a database."""
        ...

    def create_tag(self, tag: dict[str, Any]) -> dict[str, Any]:
        """Create a new tag."""
        ...

    def update_tag(self, tag: dict[str, Any]) -> dict[str, Any] | None:
        """Update an existing tag.

        Returns:
            Updated tag dictionary, or None if the tag was not found.

        """
        ...

    def delete_tag(self, tag_id: str) -> bool:
        """Delete a tag."""
        ...

    def assign_tag(self, source_id: str, tag_id: str, database_name: str) -> dict[str, Any]:
        """Assign a tag to a source."""
        ...

    def unassign_tag(self, source_id: str, tag_id: str) -> bool:
        """Remove a tag from a source."""
        ...

    def get_source_tags(self, source_id: str) -> list[dict[str, Any]]:
        """Get all tags assigned to a source."""
        ...

    def get_source_tags_batch(
        self,
        source_ids: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Get tags for multiple sources in a single query.

        Args:
            source_ids: List of source IDs to fetch tags for

        Returns:
            Dict mapping source_id to list of tag dicts

        """
        ...

    def get_source_ids_by_tag_ids(self, tag_ids: list[str], database_name: str) -> list[str]:
        """Get all source IDs for given tag IDs.

        Args:
            tag_ids: List of tag IDs to resolve
            database_name: Database name for filtering

        Returns:
            Deduplicated list of source IDs

        """
        ...

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 3).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def clear_all_tag_assignments(self) -> int:
        """Delete every SourceTagAssignment row across the database.

        Returns:
            Number of rows deleted.
        """
        ...

    def clear_all_tags(self) -> int:
        """Delete every SourceTag row across the database.

        Returns:
            Number of rows deleted.
        """
        ...
