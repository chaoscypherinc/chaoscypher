# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for get_entity_uris_grouped_by_source in SourcesCitationsMixin."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

import chaoscypher_core.adapters.sqlite.models as _models  # noqa: F401 — registers all tables
from chaoscypher_core.adapters.sqlite.mixins.sources_citations import (
    SourceCitationsMixin,
)
from chaoscypher_core.adapters.sqlite.models import (
    SourceCitation,
    SourceRow,
)


# ---------------------------------------------------------------------------
# Minimal adapter stub that inherits the mixin under test
# ---------------------------------------------------------------------------


class _StubAdapter(SourceCitationsMixin):
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
    """Create a file-backed SQLite adapter with Source and SourceCitation tables."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield _StubAdapter(session)


def _make_source(
    source_id: str, source_type: str = "jpg", filename: str = "photo.jpg"
) -> SourceRow:
    """Create a SourceRow entity for testing."""
    return SourceRow(
        id=source_id,
        database_name=DB_NAME,
        source_type=source_type,
        filename=filename,
        filepath=f"/tmp/{filename}",
        status="committed",
    )


def _make_citation(
    source_id: str,
    entity_uri: str,
    entity_label: str = "Entity",
) -> SourceCitation:
    """Create a SourceCitation entity for testing."""
    return SourceCitation(
        id=str(uuid.uuid4()),
        database_name=DB_NAME,
        entity_uri=entity_uri,
        entity_label=entity_label,
        source_id=source_id,
        chunk_id=str(uuid.uuid4()),
        confidence=0.9,
        extraction_method="ai_extraction",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetEntityUrisGroupedBySource:
    """Tests for get_entity_uris_grouped_by_source."""

    def test_returns_grouped_dict(self, adapter: _StubAdapter):
        """Entity URIs are grouped by source_id."""
        adapter.session.add_all(
            [
                _make_source("src-a", filename="photo_a.jpg"),
                _make_source("src-b", source_type="png", filename="photo_b.png"),
            ]
        )
        adapter.session.flush()

        adapter.session.add_all(
            [
                _make_citation("src-a", "node-0", "Entity 0"),
                _make_citation("src-a", "node-1", "Entity 1"),
                _make_citation("src-a", "node-2", "Entity 2"),
                _make_citation("src-b", "node-3", "Entity 3"),
                _make_citation("src-b", "node-4", "Entity 4"),
            ]
        )
        adapter.session.commit()

        result = adapter.get_entity_uris_grouped_by_source(
            database_name=DB_NAME,
            source_ids=["src-a", "src-b"],
        )

        assert isinstance(result, dict)
        assert set(result.keys()) == {"src-a", "src-b"}
        assert set(result["src-a"]) == {"node-0", "node-1", "node-2"}
        assert set(result["src-b"]) == {"node-3", "node-4"}

    def test_deduplicates(self, adapter: _StubAdapter):
        """Same entity cited twice in one source appears only once."""
        adapter.session.add(_make_source("src-dup", filename="dup.jpg"))
        adapter.session.flush()

        adapter.session.add_all(
            [
                _make_citation("src-dup", "node-same", "Same Entity"),
                _make_citation("src-dup", "node-same", "Same Entity"),
                _make_citation("src-dup", "node-same", "Same Entity"),
            ]
        )
        adapter.session.commit()

        result = adapter.get_entity_uris_grouped_by_source(
            database_name=DB_NAME,
            source_ids=["src-dup"],
        )

        assert result["src-dup"] == ["node-same"]

    def test_empty_sources_returns_empty(self, adapter: _StubAdapter):
        """Empty source_ids list returns empty dict."""
        result = adapter.get_entity_uris_grouped_by_source(
            database_name=DB_NAME,
            source_ids=[],
        )
        assert result == {}

    def test_missing_source_excluded(self, adapter: _StubAdapter):
        """Source IDs with no citations are excluded from result."""
        adapter.session.add(_make_source("src-empty", filename="empty.jpg"))
        adapter.session.commit()

        result = adapter.get_entity_uris_grouped_by_source(
            database_name=DB_NAME,
            source_ids=["src-empty"],
        )

        assert result == {}
