# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: find_by_content_hash returns the canonical (oldest) row.

Audit fix F7/F16. If a URL fetch placeholder cleanup fails after a
successful upload, the database holds two rows with the same
content_hash: the canonical row created by upload_file and the orphan
placeholder. The canonical (older) row must win duplicate detection;
otherwise re-uploads can latch onto the orphan and skip a real source.

The fix is a single ORDER BY created_at ASC on the lookup query.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

import chaoscypher_core.adapters.sqlite.models as _models  # noqa: F401 — registers all tables
from chaoscypher_core.adapters.sqlite.mixins.source_files import SourceLifecycleMixin
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceStatus


DB_NAME = "default"
HASH = "a" * 64


class _StubAdapter(SourceLifecycleMixin):
    """Minimal adapter stub with the lifecycle mixin under test."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._connected = True

    def _ensure_connected(self) -> None:
        if not self._connected:
            msg = "Not connected"
            raise RuntimeError(msg)


@pytest.fixture
def adapter(tmp_path: Path) -> _StubAdapter:
    """File-backed SQLite stub adapter with all tables created."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield _StubAdapter(session)


def _seed_source(
    session: Session,
    *,
    source_id: str,
    created_at: datetime,
    status: str,
) -> None:
    """Insert a SourceRow with the same content_hash but a chosen created_at."""
    row = SourceRow(
        id=source_id,
        database_name=DB_NAME,
        filename=f"{source_id}.md",
        filepath=f"/data/{source_id}.md",
        content_hash=HASH,
        status=status,
        source_type="webpage",
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(row)
    session.commit()


def test_canonical_row_returned_when_orphan_placeholder_exists(
    adapter: _StubAdapter,
) -> None:
    """Two rows share a hash; the OLDER (canonical) row wins duplicate detection."""
    # Canonical row created first (older created_at).
    canonical_at = datetime.now(UTC) - timedelta(minutes=5)
    _seed_source(
        adapter.session,
        source_id="src_canonical",
        created_at=canonical_at,
        status=SourceStatus.INDEXED,
    )

    # Orphan placeholder created later (newer created_at, same hash) —
    # simulates a failed placeholder-delete after upload_file succeeded.
    orphan_at = datetime.now(UTC)
    _seed_source(
        adapter.session,
        source_id="src_orphan_placeholder",
        created_at=orphan_at,
        status=SourceStatus.PENDING,
    )

    found = adapter.find_by_content_hash(DB_NAME, HASH)

    assert found is not None
    assert found["id"] == "src_canonical"


def test_returns_none_when_no_match(adapter: _StubAdapter) -> None:
    """Lookup with no matching hash returns None."""
    found = adapter.find_by_content_hash(DB_NAME, HASH)
    assert found is None


def test_canonical_returned_even_if_orphan_inserted_first(
    adapter: _StubAdapter,
) -> None:
    """Insertion order does not influence the result — only created_at does."""
    # Insert orphan first (in row insertion order) but with a NEWER created_at.
    orphan_at = datetime.now(UTC)
    _seed_source(
        adapter.session,
        source_id="src_orphan_placeholder",
        created_at=orphan_at,
        status=SourceStatus.PENDING,
    )

    # Insert canonical second, with an OLDER created_at.
    canonical_at = datetime.now(UTC) - timedelta(minutes=5)
    _seed_source(
        adapter.session,
        source_id="src_canonical",
        created_at=canonical_at,
        status=SourceStatus.INDEXED,
    )

    found = adapter.find_by_content_hash(DB_NAME, HASH)
    assert found is not None
    assert found["id"] == "src_canonical"
