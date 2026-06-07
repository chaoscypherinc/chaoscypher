# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for citations batch insert — query count bound (Task 4.3).

Verifies that inserting N citations via create_citations_batch does NOT
trigger a per-row session.refresh() call after commit.  All fields on
SourceCitation and RelationshipCitation are client- or Python-set
(no server_default), so refresh is unnecessary overhead.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

import chaoscypher_core.adapters.sqlite.models as _models  # noqa: F401 — registers all tables
from chaoscypher_core.adapters.sqlite.mixins.sources_citations import SourceCitationsMixin


# ---------------------------------------------------------------------------
# Minimal adapter stub
# ---------------------------------------------------------------------------


class _StubAdapter(SourceCitationsMixin):
    """Minimal adapter providing session and connection state for mixin tests."""

    def __init__(self, session: Session, database_name: str = "test_db") -> None:
        """Initialise stub with a live session."""
        self.session = session
        self._connected = True
        self.database_name = database_name

    def _ensure_connected(self) -> None:
        """Raise if not connected."""
        if not self._connected:
            msg = "Not connected"
            raise RuntimeError(msg)

    def _maybe_commit(self) -> None:
        """Flush and commit immediately (no transaction nesting in tests)."""
        self.session.flush()
        self.session.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DB_NAME = "test_db"
N = 50


@pytest.fixture
def adapter(tmp_path) -> _StubAdapter:
    """File-backed SQLite adapter with SourceCitation table created."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield _StubAdapter(session, database_name=DB_NAME)


def _make_citation_data(source_id: str = "src-1", chunk_id: str = "chunk-1") -> dict:
    """Build a minimal SourceCitation data dict with all fields set Python-side."""
    return {
        "id": str(uuid.uuid4()),
        "database_name": DB_NAME,
        "entity_uri": f"chaoscypher:entity_{uuid.uuid4().hex}",
        "entity_label": "Test Entity",
        "entity_type": "Person",
        "source_id": source_id,
        "chunk_id": chunk_id,
        "confidence": 0.95,
        "extraction_method": "ai_extraction",
        "context_snippet": None,
        "created_at": datetime.now(UTC),
        "citation_metadata": None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBatchCitationsNoRefreshLoop:
    """Batch citation insert must not trigger a per-row session.refresh()."""

    def test_create_citations_batch_no_per_row_refresh(self, adapter: _StubAdapter) -> None:
        """Inserting N citations must not call session.refresh per row.

        Before the fix: refresh was called once per newly-inserted citation,
        producing N SELECTs for N citations.  After the fix: zero refresh calls.
        """
        citations_data = [_make_citation_data() for _ in range(N)]

        with patch.object(Session, "refresh") as mock_refresh:
            result = adapter.create_citations_batch(citations_data)

        assert mock_refresh.call_count == 0, (
            f"session.refresh should not be called per row; "
            f"called {mock_refresh.call_count} times for {N} citations"
        )
        assert len(result) == N

    def test_create_citations_batch_returns_correct_count(self, adapter: _StubAdapter) -> None:
        """Batch insert returns the correct number of citation dicts."""
        citations_data = [_make_citation_data() for _ in range(N)]
        result = adapter.create_citations_batch(citations_data)
        assert len(result) == N

    def test_create_citations_batch_idempotent_no_refresh(self, adapter: _StubAdapter) -> None:
        """Re-inserting the same IDs (idempotent replay) also avoids refresh calls."""
        citations_data = [_make_citation_data() for _ in range(10)]

        # First insert
        adapter.create_citations_batch(citations_data)

        # Second insert with same IDs — should be filtered as existing rows
        with patch.object(Session, "refresh") as mock_refresh:
            result = adapter.create_citations_batch(list(citations_data))

        assert mock_refresh.call_count == 0, (
            f"Replay path must not call refresh; got {mock_refresh.call_count} calls"
        )
        assert len(result) == 10

    def test_create_citations_batch_empty_no_refresh(self, adapter: _StubAdapter) -> None:
        """Empty batch must not call refresh at all."""
        with patch.object(Session, "refresh") as mock_refresh:
            result = adapter.create_citations_batch([])

        assert mock_refresh.call_count == 0
        assert result == []

    def test_create_citation_single_no_refresh(self, adapter: _StubAdapter) -> None:
        """Single-row create_citation should also not call refresh.

        NOTE: This test documents the single-row path too.  The fix for the
        batch path is the primary goal; the single-row path is a bonus check.
        This test will FAIL until create_citation is also fixed.
        """
        data = _make_citation_data()

        with patch.object(Session, "refresh") as mock_refresh:
            adapter.create_citation(data)

        assert mock_refresh.call_count == 0, (
            f"create_citation should not call session.refresh; "
            f"called {mock_refresh.call_count} times"
        )
