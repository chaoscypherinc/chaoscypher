# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The memory link-state repository: compare-and-set claims and the marks that need them.

Each function opens its own short session on the engine, so these tests drive it
against a temp SQLite file with rows written through ``SourceRow``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel

import chaoscypher_core.adapters.sqlite.models as _models  # noqa: F401 — registers all tables
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.adapters.sqlite.repos.memory_link import MemoryLinkRepository
from chaoscypher_core.models import MemoryLinkState, SourceErrorStage


DB = "default"
NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
STALE_BEFORE = NOW - timedelta(seconds=300)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Engine]:
    path = tmp_path / "app.db"
    engine = get_engine(path)
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        evict_engine(path)


def _row(db: Engine, source_id: str, **fields: Any) -> None:
    values: dict[str, Any] = {
        "id": source_id,
        "database_name": DB,
        "filename": f"{source_id}.txt",
        "filepath": f"/memories/{source_id}.txt",
        "source_type": "memory",
        "memory_link_state": MemoryLinkState.PENDING.value,
    }
    values.update(fields)
    with Session(db) as session:
        session.add(SourceRow(**values))
        session.commit()


def _get(db: Engine, source_id: str) -> SourceRow:
    with Session(db) as session:
        row = session.get(SourceRow, source_id)
        assert row is not None
        return row


def _claim(db: Engine, source_id: str, token: str, **kwargs: Any) -> bool:
    return MemoryLinkRepository(db).claim(
        source_id, token, now=NOW, stale_before=STALE_BEFORE, **kwargs
    )


def test_a_free_row_is_claimed_once(db: Engine) -> None:
    _row(db, "m1")
    assert _claim(db, "m1", "a") is True
    assert _claim(db, "m1", "b") is False
    row = _get(db, "m1")
    assert row.memory_link_state == MemoryLinkState.LINKING.value
    assert row.memory_link_claim == "a"
    assert row.last_activity_at is not None


def test_a_stale_claim_is_taken_over_and_a_live_one_is_not(db: Engine) -> None:
    _row(
        db,
        "stale",
        memory_link_state="linking",
        memory_link_claim="a",
        last_activity_at=NOW - timedelta(seconds=301),
    )
    _row(
        db,
        "live",
        memory_link_state="linking",
        memory_link_claim="a",
        last_activity_at=NOW - timedelta(seconds=10),
    )
    assert _claim(db, "stale", "b") is True
    assert _get(db, "stale").memory_link_claim == "b"
    assert _claim(db, "live", "b") is False
    assert _get(db, "live").memory_link_claim == "a"


def test_the_holder_may_claim_again(db: Engine) -> None:
    _row(db, "m1", memory_link_state="linking", memory_link_claim="a", last_activity_at=NOW)
    assert _claim(db, "m1", "a") is True


def test_a_linked_row_is_never_claimed_and_linked_empty_only_with_include_empty(
    db: Engine,
) -> None:
    _row(db, "linked", memory_link_state="linked", memory_linked_at=NOW)
    _row(db, "empty", memory_link_state="linked_empty", memory_linked_at=NOW)
    assert _claim(db, "linked", "a") is False
    assert _claim(db, "linked", "a", include_empty=True) is False
    assert _claim(db, "empty", "a") is False
    assert _claim(db, "empty", "a", include_empty=True) is True
    assert _get(db, "empty").memory_link_state == MemoryLinkState.LINKING.value


def test_mark_failed_counts_and_mark_no_model_does_not(db: Engine) -> None:
    _row(db, "failed")
    _row(db, "no_model")
    assert _claim(db, "failed", "a") and _claim(db, "no_model", "a")
    assert MemoryLinkRepository(db).mark_failed(
        "failed",
        "a",
        stage=SourceErrorStage.EXTRACTION,
        reason="The extraction model failed.",
        now=NOW,
    )
    assert MemoryLinkRepository(db).mark_no_model("no_model", "a")
    failed = _get(db, "failed")
    assert failed.memory_link_state == MemoryLinkState.FAILED.value
    assert failed.recovery_attempts == 1
    assert failed.error_stage == "extraction"
    assert failed.error_message == "The extraction model failed."
    assert failed.memory_link_claim is None
    no_model = _get(db, "no_model")
    assert no_model.memory_link_state == MemoryLinkState.NO_MODEL.value
    assert no_model.recovery_attempts == 0
    assert no_model.memory_link_claim is None


def test_marks_need_the_token(db: Engine) -> None:
    _row(db, "m1")
    assert _claim(db, "m1", "a")
    assert MemoryLinkRepository(db).mark_linked("m1", "b", empty=False, now=NOW) is False
    assert MemoryLinkRepository(db).touch("m1", "b", now=NOW) is False
    assert MemoryLinkRepository(db).release("m1", "b") is False
    row = _get(db, "m1")
    assert row.memory_link_state == MemoryLinkState.LINKING.value
    assert row.memory_link_claim == "a"
    assert row.memory_linked_at is None


def test_mark_linked_and_release(db: Engine) -> None:
    _row(db, "linked", error_stage="extraction", error_message="The extraction model failed.")
    _row(db, "released")
    assert _claim(db, "linked", "a") and _claim(db, "released", "a")
    assert MemoryLinkRepository(db).mark_linked("linked", "a", empty=True, now=NOW)
    assert MemoryLinkRepository(db).release("released", "a")
    linked = _get(db, "linked")
    assert linked.memory_link_state == MemoryLinkState.LINKED_EMPTY.value
    assert linked.memory_linked_at is not None
    assert linked.memory_link_claim is None
    assert linked.error_stage is None and linked.error_message is None
    released = _get(db, "released")
    assert released.memory_link_state == MemoryLinkState.PENDING.value
    assert released.memory_link_claim is None


def test_a_claim_with_include_empty_reopens_the_commit(db: Engine) -> None:
    _row(db, "empty", memory_link_state="linked_empty", memory_linked_at=NOW, commit_complete=True)
    _row(db, "pending", commit_complete=False)
    assert _claim(db, "empty", "a", include_empty=True)
    assert _claim(db, "pending", "a", include_empty=True)
    row = _get(db, "empty")
    assert row.commit_complete is False
    assert row.memory_linked_at is None
    assert row.memory_link_state == MemoryLinkState.LINKING.value
    assert _get(db, "pending").commit_complete is False


def test_a_released_include_empty_claim_is_claimable_again(db: Engine) -> None:
    _row(db, "empty", memory_link_state="linked_empty", memory_linked_at=NOW, commit_complete=True)
    assert _claim(db, "empty", "a", include_empty=True)
    assert MemoryLinkRepository(db).release("empty", "a")
    assert _get(db, "empty").memory_link_state == MemoryLinkState.PENDING.value
    assert _claim(db, "empty", "b")


def test_claimability_follows_the_state(db: Engine) -> None:
    """A linked state is never claimed, whatever memory_linked_at says; any other state is."""
    _row(db, "linked_no_time", memory_link_state="linked", memory_linked_at=None)
    _row(db, "failed_with_time", memory_link_state="failed", memory_linked_at=NOW)
    assert _claim(db, "linked_no_time", "a") is False
    assert _claim(db, "failed_with_time", "a") is True


def test_a_document_row_is_never_claimed(db: Engine) -> None:
    _row(db, "doc", source_type="file", memory_link_state=None)
    assert _claim(db, "doc", "a") is False
    assert _get(db, "doc").memory_link_claim is None


def test_read_link_row_is_aware_utc_and_carries_the_text(db: Engine) -> None:
    _row(
        db,
        "m1",
        full_text="Pierre moved to Lyon.",
        memory_by="user",
        memory_scope="travel",
        last_activity_at=NOW,
    )
    row = MemoryLinkRepository(db).read_link_row("m1")
    assert row is not None
    assert row.state is MemoryLinkState.PENDING
    assert row.full_text == "Pierre moved to Lyon."
    assert (row.memory_by, row.memory_scope) == ("user", "travel")
    assert row.last_activity_at == NOW
    assert row.last_activity_at is not None and row.last_activity_at.tzinfo is not None
    assert MemoryLinkRepository(db).read_link_row("missing") is None


def test_link_rows_is_oldest_first_and_memory_only(db: Engine) -> None:
    _row(db, "new", created_at=NOW, full_text="later")
    _row(db, "old", created_at=NOW - timedelta(days=2), full_text="earlier")
    _row(db, "mid", created_at=NOW - timedelta(days=1))
    _row(db, "other_db", database_name="other", created_at=NOW - timedelta(days=3))
    _row(db, "doc", source_type="file", memory_link_state=None, created_at=NOW - timedelta(days=4))
    rows = MemoryLinkRepository(db).link_rows(DB)
    assert [r.id for r in rows] == ["old", "mid", "new"]
    assert all(r.full_text is None for r in rows)
    picked = MemoryLinkRepository(db).link_rows(DB, source_ids=["new", "doc", "old"])
    assert [r.id for r in picked] == ["old", "new"]


def test_a_memory_row_is_written_pending_whatever_writes_it(db: Engine) -> None:
    """No writer can leave a memory's state NULL; a document keeps NULL."""
    with Session(db) as session:
        for source_id, source_type in (("memo", "memory"), ("doc", "file")):
            session.add(
                SourceRow(
                    id=source_id,
                    database_name=DB,
                    filename=f"{source_id}.txt",
                    filepath=f"{source_id}.txt",
                    source_type=source_type,
                )
            )
        session.commit()
    assert _get(db, "memo").memory_link_state == MemoryLinkState.PENDING.value
    assert _get(db, "doc").memory_link_state is None
