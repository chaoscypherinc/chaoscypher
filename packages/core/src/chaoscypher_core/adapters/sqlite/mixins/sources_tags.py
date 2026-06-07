# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Tags Mixin for SqliteAdapter.

Handles tag CRUD and tag-to-source assignment operations.
Part of the unified SourceStorageProtocol implementation.
"""

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.orm import load_only
from sqlmodel import delete, select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import (
    SourceTag,
    SourceTagAssignment,
)
from chaoscypher_core.ports.storage_source_tags import SourceTagStorageProtocol
from chaoscypher_core.utils.id import generate_id


logger = structlog.get_logger(__name__)


class SourceTagsMixin(SqliteMixinBase, SourceTagStorageProtocol):
    """Mixin providing tag operations for SQLite storage.

    Implements operations for:
    - Tag CRUD (create, get, list)
    - Tag-to-source assignments (assign, unassign, get tags for source)

    Note: This mixin contributes to the unified SourceStorageProtocol.
    """

    def get_tag(self, tag_id: str, database_name: str = "") -> dict[str, Any] | None:
        """Get tag by ID and database."""
        self._ensure_connected()
        tag = self.session.get(SourceTag, tag_id)
        if tag and tag.database_name == database_name:
            return self._entity_to_dict(tag)
        return None

    def create_tag(self, tag_data: dict[str, Any]) -> dict[str, Any]:
        """Create tag."""
        self._ensure_connected()
        tag = SourceTag(**tag_data)
        self.session.add(tag)
        self._maybe_commit()
        self.session.refresh(tag)
        return self._entity_to_dict(tag)

    def list_tags(self, database_name: str) -> list[dict[str, Any]]:
        """List all tags for database."""
        self._ensure_connected()
        stmt = (
            select(SourceTag)
            .options(
                load_only(
                    SourceTag.id,
                    SourceTag.database_name,
                    SourceTag.name,
                    SourceTag.color,
                    SourceTag.description,
                    SourceTag.created_at,
                )
            )
            .where(SourceTag.database_name == database_name)
        )
        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    def update_tag(self, tag_data: dict[str, Any]) -> dict[str, Any] | None:
        """Update tag fields."""
        self._ensure_connected()
        tag = self.session.get(SourceTag, tag_data["id"])
        if not tag:
            return None
        for key, value in tag_data.items():
            if key != "id" and value is not None:
                setattr(tag, key, value)
        self._maybe_commit()
        self.session.refresh(tag)
        return self._entity_to_dict(tag)

    def delete_tag(self, tag_id: str) -> bool:
        """Delete tag and all its assignments."""
        self._ensure_connected()
        tag = self.session.get(SourceTag, tag_id)
        if not tag:
            return False

        # Remove all assignments for this tag
        stmt = select(SourceTagAssignment).where(SourceTagAssignment.tag_id == tag_id)
        assignments = self.session.exec(stmt).all()
        for assignment in assignments:
            self.session.delete(assignment)

        # Flush the child-assignment deletes before deleting the parent tag.
        # There is no ORM relationship between SourceTag and SourceTagAssignment,
        # so SQLAlchemy's unit of work can't infer the dependency and may emit
        # the parent DELETE first, which a foreign-key-enforcing SQLite rejects.
        self.session.flush()

        self.session.delete(tag)
        self._maybe_commit()
        return True

    def assign_tag(self, source_id: str, tag_id: str, database_name: str) -> dict[str, Any]:
        """Assign tag to source. Returns existing assignment if already assigned."""
        self._ensure_connected()

        # Check for existing assignment
        existing_stmt = select(SourceTagAssignment).where(
            SourceTagAssignment.source_id == source_id,
            SourceTagAssignment.tag_id == tag_id,
        )
        existing = self.session.exec(existing_stmt).first()
        if existing:
            return self._entity_to_dict(existing)

        assignment_id = generate_id("assignment")
        assignment = SourceTagAssignment(
            id=assignment_id,
            source_id=source_id,
            tag_id=tag_id,
            database_name=database_name,
            assigned_at=datetime.now(UTC),
        )
        self.session.add(assignment)
        self._maybe_commit()
        self.session.refresh(assignment)
        return self._entity_to_dict(assignment)

    def unassign_tag(self, source_id: str, tag_id: str) -> bool:
        """Remove tag from source."""
        self._ensure_connected()
        stmt = select(SourceTagAssignment).where(
            SourceTagAssignment.source_id == source_id, SourceTagAssignment.tag_id == tag_id
        )
        result = self.session.exec(stmt)
        assignment = result.first()

        if not assignment:
            return False

        self.session.delete(assignment)
        self._maybe_commit()
        return True

    def get_source_ids_by_tag_ids(self, tag_ids: list[str], database_name: str) -> list[str]:
        """Get all source IDs for given tag IDs.

        Args:
            tag_ids: List of tag IDs to resolve
            database_name: Database name for filtering

        Returns:
            Deduplicated list of source IDs

        """
        self._ensure_connected()
        stmt = (
            select(SourceTagAssignment.source_id)
            .where(
                SourceTagAssignment.tag_id.in_(tag_ids),
                SourceTagAssignment.database_name == database_name,
            )
            .distinct()
        )
        results = self.session.exec(stmt)
        return list(results.all())

    def get_source_tags(self, source_id: str) -> list[dict[str, Any]]:
        """Get all tags for a source with full tag details.

        Joins SourceTagAssignment with SourceTag to return complete tag info.

        Args:
            source_id: Source ID to get tags for

        Returns:
            List of tag dicts with id, name, color, description, etc.

        """
        self._ensure_connected()
        stmt = (
            select(SourceTag)
            .join(SourceTagAssignment, SourceTag.id == SourceTagAssignment.tag_id)
            .where(SourceTagAssignment.source_id == source_id)
        )
        results = self.session.exec(stmt)
        return self._entities_to_dicts(results.all())

    def delete_tags_for_source(self, source_id: str) -> None:
        """Delete SourceTagAssignment rows owned by this source."""
        self._ensure_connected()
        stmt = delete(SourceTagAssignment).where(SourceTagAssignment.source_id == source_id)
        self.session.execute(stmt)
        self._maybe_commit()

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
        if not source_ids:
            return {}

        self._ensure_connected()
        stmt = (
            select(SourceTag, SourceTagAssignment.source_id)
            .join(SourceTagAssignment, SourceTag.id == SourceTagAssignment.tag_id)
            .where(
                SourceTagAssignment.source_id.in_(source_ids),  # type: ignore[union-attr]
            )
        )
        rows = self.session.exec(stmt).all()

        tags_by_source: dict[str, list[dict[str, Any]]] = {}
        for tag, src_id in rows:
            tag_dict = self._entity_to_dict(tag)
            tags_by_source.setdefault(src_id, []).append(tag_dict)
        return tags_by_source

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 3).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def clear_all_tag_assignments(self) -> int:
        """Delete every SourceTagAssignment row across the database."""
        self._ensure_connected()
        result = self.session.exec(delete(SourceTagAssignment))
        self._maybe_commit()
        return int(result.rowcount or 0)

    def clear_all_tags(self) -> int:
        """Delete every SourceTag row across the database."""
        self._ensure_connected()
        result = self.session.exec(delete(SourceTag))
        self._maybe_commit()
        return int(result.rowcount or 0)
