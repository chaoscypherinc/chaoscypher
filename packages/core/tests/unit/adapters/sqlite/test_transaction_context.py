# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for adapter-level transaction() context manager."""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import SourceStatus


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Provide a fresh connected SqliteAdapter backed by a tmp_path file DB."""
    db_dir = tmp_path / "chaoscypher-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


def test_transaction_context_commits_on_success(adapter: SqliteAdapter) -> None:
    """Successful exit commits the transaction."""
    with adapter.transaction():
        adapter._maybe_commit()  # No-op inside transaction
    assert adapter.session._transaction_depth == 0


def test_transaction_context_rolls_back_on_exception(adapter: SqliteAdapter) -> None:
    """Exception inside context triggers rollback and re-raises."""
    with pytest.raises(ValueError, match="boom"), adapter.transaction():
        raise ValueError("boom")
    assert adapter.session._transaction_depth == 0


def test_transaction_context_nests_correctly(adapter: SqliteAdapter) -> None:
    """Nested contexts only commit on outermost exit."""
    with adapter.transaction():
        assert adapter.session._transaction_depth == 1
        with adapter.transaction():
            assert adapter.session._transaction_depth == 2
        assert adapter.session._transaction_depth == 1
    assert adapter.session._transaction_depth == 0


def test_transaction_rollback_undoes_all_writes(adapter: SqliteAdapter) -> None:
    """Writes inside a rolled-back transaction must not be visible after."""
    source_id = "test_source_1"

    seed = SourceRow(
        id=source_id,
        database_name="default",
        filename="a.txt",
        filepath="/tmp/a.txt",
        file_type="text",
        status=SourceStatus.EXTRACTED,
        created_at=datetime.now(UTC),
    )
    adapter.session.add(seed)
    adapter.session.commit()

    try:
        with adapter.transaction():
            row = adapter.session.get(SourceRow, source_id)
            row.status = SourceStatus.COMMITTED
            adapter._maybe_commit()  # flush, not commit
            raise ValueError("boom")
    except ValueError:
        pass

    adapter.session.expire_all()
    row = adapter.session.get(SourceRow, source_id)
    assert row.status == SourceStatus.EXTRACTED
