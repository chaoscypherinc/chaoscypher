# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""mark_source_exhausted preserves the prior error_stage in last_failed_stage."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

import chaoscypher_core.adapters.sqlite.models as _models  # noqa: F401 — registers all tables
from chaoscypher_core.adapters.sqlite.mixins.source_files_indexing import (
    SourceIndexingMixin,
)
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceErrorStage, SourceStatus


# ---------------------------------------------------------------------------
# Minimal adapter stub that inherits the mixin under test
# ---------------------------------------------------------------------------


class _StubAdapter(SourceIndexingMixin):
    """Minimal adapter providing session and connection state for tests."""

    def __init__(self, session: Session, database_name: str = "default") -> None:
        self.session = session
        self._connected = True
        self.database_name = database_name

    def _ensure_connected(self) -> None:
        if not self._connected:
            msg = "Not connected"
            raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DB_NAME = "default"


@pytest.fixture
def adapter(tmp_path: Path) -> _StubAdapter:
    """Create a file-backed SQLite adapter with SourceRow table."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield _StubAdapter(session, database_name=DB_NAME)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mark_exhausted_copies_prior_stage_to_last_failed_stage(
    adapter: _StubAdapter,
) -> None:
    """mark_source_exhausted must copy error_stage into last_failed_stage."""
    source = SourceRow(
        id="src_exh",
        database_name=DB_NAME,
        filename="x.txt",
        filepath="/tmp/x.txt",
        status=SourceStatus.ERROR,
        error_stage=SourceErrorStage.COMMIT.value,
        error_message="commit blew up",
    )
    adapter.session.add(source)
    adapter.session.commit()

    # Pre-condition: error_stage is set, last_failed_stage is unset.
    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, "src_exh")
    assert row is not None
    assert row.error_stage == SourceErrorStage.COMMIT.value
    assert row.last_failed_stage is None

    adapter.mark_source_exhausted(
        source_id="src_exh",
        database_name=DB_NAME,
        error_message="exhausted after 10 attempts",
    )

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, "src_exh")
    assert row is not None
    assert row.error_stage == SourceErrorStage.RECOVERY_EXHAUSTED.value
    assert row.last_failed_stage == SourceErrorStage.COMMIT.value


def test_mark_exhausted_idempotent_on_already_exhausted_row(
    adapter: _StubAdapter,
) -> None:
    """Re-exhausting an already-exhausted row must NOT overwrite last_failed_stage."""
    source = SourceRow(
        id="src_exh2",
        database_name=DB_NAME,
        filename="y.txt",
        filepath="/tmp/y.txt",
        status=SourceStatus.ERROR,
        error_stage=SourceErrorStage.RECOVERY_EXHAUSTED.value,
        last_failed_stage=SourceErrorStage.EXTRACTION.value,
    )
    adapter.session.add(source)
    adapter.session.commit()

    adapter.mark_source_exhausted(
        source_id="src_exh2",
        database_name=DB_NAME,
        error_message="exhausted again",
    )

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, "src_exh2")
    assert row is not None
    assert row.error_stage == SourceErrorStage.RECOVERY_EXHAUSTED.value
    # last_failed_stage must still be the original prior stage, not RECOVERY_EXHAUSTED
    assert row.last_failed_stage == SourceErrorStage.EXTRACTION.value
