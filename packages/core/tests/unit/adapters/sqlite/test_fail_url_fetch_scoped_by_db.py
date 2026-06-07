# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: fail_url_fetch is scoped by database_name (audit fix F18).

Previously fail_url_fetch did ``session.get(SourceRow, source_id)`` with
no database_name filter. With multi-tenant SQLite databases sharing
the same SQLAlchemy session (during testing) or with a stale source_id
that happens to collide with a row in a different tenant's database,
the wrong row could be marked failed.

The fix: filter by ``id == source_id AND database_name == database_name``.
A miss logs ``fail_url_fetch_row_not_found`` at WARNING and no-ops.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog.testing
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

import chaoscypher_core.adapters.sqlite.models as _models  # noqa: F401 — registers all tables
from chaoscypher_core.adapters.sqlite.mixins.source_files import SourceLifecycleMixin
from chaoscypher_core.adapters.sqlite.mixins.source_files_indexing import SourceIndexingMixin
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceErrorStage, SourceStatus


class _StubAdapter(SourceIndexingMixin, SourceLifecycleMixin):
    """Combined-mixin stub matching test_error_stage_writers_use_enum."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._connected = True

    def _ensure_connected(self) -> None:
        if not self._connected:
            msg = "Not connected"
            raise RuntimeError(msg)


@pytest.fixture
def adapter(tmp_path: Path) -> _StubAdapter:
    """File-backed SQLite stub adapter; tables created."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield _StubAdapter(session)


def _seed_placeholder(session: Session, *, source_id: str, database_name: str) -> None:
    """Insert a URL placeholder row matching create_url_placeholder's shape."""
    row = SourceRow(
        id=source_id,
        database_name=database_name,
        filename="https://example.com/doc",
        filepath="",
        status=SourceStatus.PENDING,
        source_type="webpage",
        origin_url="https://example.com/doc",
        step_description="Fetching URL",
        current_step=1,
        total_steps=2,
    )
    session.add(row)
    session.commit()


def test_correct_database_marks_row_failed(adapter: _StubAdapter) -> None:
    """fail_url_fetch with the matching database_name updates the row."""
    _seed_placeholder(adapter.session, source_id="src_x", database_name="alpha")

    adapter.fail_url_fetch(source_id="src_x", error="HTTP 500", database_name="alpha")

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, "src_x")
    assert row is not None
    assert row.status == SourceStatus.ERROR
    assert row.error_stage == SourceErrorStage.URL_FETCH.value
    assert "HTTP 500" in (row.error_message or "")


def test_wrong_database_is_noop_and_logs_warning(adapter: _StubAdapter) -> None:
    """fail_url_fetch with a non-matching database_name does NOT update the row."""
    # Seed in tenant 'alpha'.
    _seed_placeholder(adapter.session, source_id="src_x", database_name="alpha")

    with structlog.testing.capture_logs() as captured:
        adapter.fail_url_fetch(source_id="src_x", error="oops", database_name="beta")

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, "src_x")
    # Untouched: still PENDING, no error_stage set.
    assert row is not None
    assert row.status == SourceStatus.PENDING
    assert row.error_stage is None or row.error_stage == ""

    # WARNING surfaces the mismatch for ops.
    matching = [e for e in captured if e.get("event") == "fail_url_fetch_row_not_found"]
    assert len(matching) == 1, f"expected one WARNING, got {captured}"
    event = matching[0]
    assert event["log_level"] == "warning"
    assert event["source_id"] == "src_x"
    assert event["database_name"] == "beta"


def test_unknown_source_id_is_noop_and_logs_warning(adapter: _StubAdapter) -> None:
    """fail_url_fetch on a missing row no-ops with a WARNING (was silent before)."""
    with structlog.testing.capture_logs() as captured:
        adapter.fail_url_fetch(source_id="src_missing", error="x", database_name="alpha")

    matching = [e for e in captured if e.get("event") == "fail_url_fetch_row_not_found"]
    assert len(matching) == 1
    assert matching[0]["source_id"] == "src_missing"
    assert matching[0]["database_name"] == "alpha"
