# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ExtractionSubmissionsMixin."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from chaoscypher_core.adapters.sqlite.mixins.extraction_submissions import (
    ExtractionSubmissionsMixin,
)
from chaoscypher_core.adapters.sqlite.models import (
    ExtractionSubmission,
)


# ---------------------------------------------------------------------------
# Minimal adapter stub that inherits the mixin under test
# ---------------------------------------------------------------------------


class _StubAdapter(ExtractionSubmissionsMixin):
    """Minimal adapter providing session and connection state for tests."""

    def __init__(self, session: Session):
        self.session = session
        self._connected = True

    def _ensure_connected(self) -> None:
        if not self._connected:
            msg = "Not connected"
            raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DB_NAME = "test_db"


@pytest.fixture
def adapter(tmp_path):
    """Create a file-backed SQLite adapter with the ExtractionSubmission table."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine, tables=[ExtractionSubmission.__table__])
    with Session(engine) as session:
        yield _StubAdapter(session)


def _make_data(
    source_id: str = "src-1",
    chunk_group_index: int = 0,
    **overrides,
) -> dict:
    """Build a submission data dict with sensible defaults."""
    data = {
        "source_id": source_id,
        "chunk_group_index": chunk_group_index,
        "entities_text": "Person: Alice",
        "relationships_text": "Alice KNOWS Bob",
        "entity_count": 1,
        "relationship_count": 1,
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateSubmission:
    """create_extraction_submission basics."""

    def test_create_submission(self, adapter):
        """Creating a row returns a dict with all expected fields."""
        data = _make_data()
        result = adapter.create_extraction_submission(data, DB_NAME)

        assert isinstance(result, dict)
        assert result["source_id"] == "src-1"
        assert result["chunk_group_index"] == 0
        assert result["database_name"] == DB_NAME
        assert result["entities_text"] == "Person: Alice"
        assert result["relationships_text"] == "Alice KNOWS Bob"
        assert result["entity_count"] == 1
        assert result["relationship_count"] == 1
        # id should be auto-generated
        assert result["id"]
        uuid.UUID(result["id"])  # validates format

    def test_create_submission_upsert(self, adapter):
        """Creating with the same (source_id, chunk_group_index) overwrites."""
        data = _make_data(entity_count=1, entities_text="Person: Alice")
        first = adapter.create_extraction_submission(data, DB_NAME)

        updated_data = _make_data(entity_count=5, entities_text="Person: Bob")
        second = adapter.create_extraction_submission(updated_data, DB_NAME)

        # Same row, updated values
        assert second["id"] == first["id"]
        assert second["entity_count"] == 5
        assert second["entities_text"] == "Person: Bob"

        # Only one row in the database
        assert adapter.count_extraction_submissions("src-1", DB_NAME) == 1


class TestGetSubmission:
    """get_extraction_submission lookup."""

    def test_get_submission(self, adapter):
        """Returns the matching submission dict."""
        adapter.create_extraction_submission(_make_data(), DB_NAME)
        result = adapter.get_extraction_submission("src-1", 0, DB_NAME)

        assert result is not None
        assert result["source_id"] == "src-1"
        assert result["chunk_group_index"] == 0

    def test_get_submission_not_found(self, adapter):
        """Returns None when no matching row exists."""
        result = adapter.get_extraction_submission("nonexistent", 0, DB_NAME)
        assert result is None


class TestListSubmissions:
    """list_extraction_submissions ordering and empty case."""

    def test_list_submissions_ordered(self, adapter):
        """Returns rows ordered by chunk_group_index."""
        # Insert out of order
        adapter.create_extraction_submission(_make_data(chunk_group_index=2), DB_NAME)
        adapter.create_extraction_submission(_make_data(chunk_group_index=0), DB_NAME)
        adapter.create_extraction_submission(_make_data(chunk_group_index=1), DB_NAME)

        results = adapter.list_extraction_submissions("src-1", DB_NAME)
        assert len(results) == 3
        assert [r["chunk_group_index"] for r in results] == [0, 1, 2]

    def test_list_submissions_empty(self, adapter):
        """Returns an empty list when no rows match."""
        results = adapter.list_extraction_submissions("nonexistent", DB_NAME)
        assert results == []


class TestCountSubmissions:
    """count_extraction_submissions."""

    def test_count_submissions(self, adapter):
        """Returns the correct count of submissions."""
        for i in range(4):
            adapter.create_extraction_submission(_make_data(chunk_group_index=i), DB_NAME)

        assert adapter.count_extraction_submissions("src-1", DB_NAME) == 4

    def test_count_submissions_zero(self, adapter):
        """Returns 0 when no rows exist."""
        assert adapter.count_extraction_submissions("nonexistent", DB_NAME) == 0


class TestDeleteSubmissions:
    """delete_extraction_submissions."""

    def test_delete_submissions(self, adapter):
        """Deletes all rows for a source and returns the count."""
        for i in range(3):
            adapter.create_extraction_submission(_make_data(chunk_group_index=i), DB_NAME)

        deleted = adapter.delete_extraction_submissions("src-1", DB_NAME)
        assert deleted == 3

        # Confirm they are gone
        assert adapter.count_extraction_submissions("src-1", DB_NAME) == 0

    def test_delete_submissions_none(self, adapter):
        """Returns 0 when no rows exist to delete."""
        deleted = adapter.delete_extraction_submissions("nonexistent", DB_NAME)
        assert deleted == 0
