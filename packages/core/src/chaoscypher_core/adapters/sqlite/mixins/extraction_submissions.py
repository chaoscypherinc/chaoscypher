# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Extraction Submission Storage Protocol Mixin for SqliteAdapter."""

from typing import Any

from sqlalchemy import func as sa_func
from sqlmodel import delete, select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import ExtractionSubmission
from chaoscypher_core.ports.storage_extraction_submissions import (
    ExtractionSubmissionStorageProtocol,
)
from chaoscypher_core.utils.id import generate_id


class ExtractionSubmissionsMixin(SqliteMixinBase, ExtractionSubmissionStorageProtocol):
    """Mixin implementing ExtractionSubmissionStorageProtocol for SQLite storage.

    Provides CRUD operations for transient extraction submission rows that
    track partial results during MCP-driven entity extraction.
    """

    def create_extraction_submission(
        self, data: dict[str, Any], database_name: str
    ) -> dict[str, Any]:
        """Create or replace a submission for a chunk group.

        Uses upsert semantics: if a row with the same (database_name,
        source_id, chunk_group_index) already exists, its fields are
        updated in place. Otherwise a new row is created.

        Args:
            data: Submission data dict (source_id, chunk_group_index, etc.)
            database_name: Database name for scoping

        Returns:
            Dict representation of the created or updated submission

        """
        self._ensure_connected()

        source_id = data["source_id"]
        chunk_group_index = data["chunk_group_index"]

        # Check for existing row by unique constraint fields
        stmt = select(ExtractionSubmission).where(
            ExtractionSubmission.database_name == database_name,
            ExtractionSubmission.source_id == source_id,
            ExtractionSubmission.chunk_group_index == chunk_group_index,
        )
        existing = self.session.exec(stmt).first()

        if existing:
            # Update existing row with new values
            for key, value in data.items():
                if key not in ("id", "database_name"):
                    setattr(existing, key, value)
            self.session.add(existing)
            self._maybe_commit()
            self.session.refresh(existing)
            return self._entity_to_dict(existing)

        # Create new row
        submission_data = {**data, "database_name": database_name}
        if "id" not in submission_data:
            submission_data["id"] = generate_id()

        submission = ExtractionSubmission(**submission_data)
        self.session.add(submission)
        self._maybe_commit()
        self.session.refresh(submission)
        return self._entity_to_dict(submission)

    def get_extraction_submission(
        self, source_id: str, chunk_group_index: int, database_name: str
    ) -> dict[str, Any] | None:
        """Get a single submission by source and chunk index.

        Args:
            source_id: Source file identifier
            chunk_group_index: Chunk group index within the source
            database_name: Database name for scoping

        Returns:
            Dict representation of the submission, or None if not found

        """
        self._ensure_connected()
        stmt = select(ExtractionSubmission).where(
            ExtractionSubmission.database_name == database_name,
            ExtractionSubmission.source_id == source_id,
            ExtractionSubmission.chunk_group_index == chunk_group_index,
        )
        result = self.session.exec(stmt).first()
        return self._entity_to_dict(result) if result else None

    def list_extraction_submissions(
        self, source_id: str, database_name: str
    ) -> list[dict[str, Any]]:
        """List all submissions for a source, ordered by chunk_group_index.

        Args:
            source_id: Source file identifier
            database_name: Database name for scoping

        Returns:
            List of dict representations ordered by chunk_group_index

        """
        self._ensure_connected()
        # No load_only(): finalize_extraction() reads entities_text and
        # relationships_text from every row, so all columns are needed.
        stmt = (
            select(ExtractionSubmission)
            .where(
                ExtractionSubmission.database_name == database_name,
                ExtractionSubmission.source_id == source_id,
            )
            .order_by(ExtractionSubmission.chunk_group_index)
        )
        results = self.session.exec(stmt).all()
        return self._entities_to_dicts(results)

    def count_extraction_submissions(self, source_id: str, database_name: str) -> int:
        """Count submitted chunks for a source.

        Args:
            source_id: Source file identifier
            database_name: Database name for scoping

        Returns:
            Number of submissions for the source

        """
        self._ensure_connected()
        stmt = (
            select(sa_func.count())
            .select_from(ExtractionSubmission)
            .where(
                ExtractionSubmission.database_name == database_name,
                ExtractionSubmission.source_id == source_id,
            )
        )
        return self.session.exec(stmt).one()

    def delete_extraction_submissions(self, source_id: str, database_name: str) -> int:
        """Delete all submissions for a source. Returns count deleted.

        Args:
            source_id: Source file identifier
            database_name: Database name for scoping

        Returns:
            Number of rows deleted

        """
        self._ensure_connected()
        stmt = delete(ExtractionSubmission).where(
            ExtractionSubmission.database_name == database_name,
            ExtractionSubmission.source_id == source_id,
        )
        result = self.session.exec(stmt)
        self._maybe_commit()
        return result.rowcount
