# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for VisionPagesMixin — per-page vision storage operations."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel, select

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import VisionJob, VisionPageDescription
from chaoscypher_core.utils.id import generate_id
from chaoscypher_core.vision.states import VisionPageKind, VisionPageStatus


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Real SQLite adapter on a tmp DB. Schema applied via SQLModel.metadata.create_all."""
    db_dir = tmp_path / "cc-vision-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine, checkfirst=True)

    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def _create_source(adapter: SqliteAdapter, source_id: str | None = None) -> str:
    """Helper: insert a minimal sources row so FK constraints are satisfied."""
    sid = source_id or generate_id("src")
    adapter.create_source(
        {
            "id": sid,
            "database_name": "test",
            "filename": "test.pdf",
            "filepath": "/tmp/test.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": generate_id(),
            "status": "loading",
        }
    )
    return sid


def test_create_vision_job_with_pages_inserts_job_and_rows(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    pages = [
        {"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/tmp/p1.png"},
        {"page_number": 2, "kind": VisionPageKind.PDF_PAGE, "image_path": "/tmp/p2.png"},
        {"page_number": 3, "kind": VisionPageKind.STANDALONE_IMAGE, "image_path": "/tmp/p3.jpg"},
    ]

    job_id = adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)

    assert job_id.startswith("vjob_")

    # Direct SQL verification (other accessor methods land in Tasks 6/7).
    adapter._ensure_connected()
    assert adapter.session is not None
    job = adapter.session.scalars(select(VisionJob).where(VisionJob.id == job_id)).first()
    assert job is not None
    assert job.source_id == source_id
    assert job.total_pages == 3
    assert job.completed == 0
    assert job.failed == 0

    rows = adapter.session.scalars(
        select(VisionPageDescription).where(VisionPageDescription.source_id == source_id)
    ).all()
    assert len(rows) == 3
    assert {r.page_number for r in rows} == {1, 2, 3}
    assert all(r.status == VisionPageStatus.PENDING.value for r in rows)
    assert all(r.region_index == 0 for r in rows)
    assert all(r.id.startswith("vpd_") for r in rows)
    assert all(r.vision_job_id == job_id for r in rows)


# ---------------------------------------------------------------------------
# Task 6: get_vision_job / get_vision_job_by_source
# ---------------------------------------------------------------------------


def test_get_vision_job_returns_typed_dict(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    pages = [{"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p1.png"}]
    job_id = adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)

    job = adapter.get_vision_job(job_id)

    assert job is not None
    assert job["id"] == job_id
    assert job["source_id"] == source_id
    assert job["total_pages"] == 1
    assert job["completed"] == 0
    assert job["failed"] == 0
    assert "created_at" in job
    assert "updated_at" in job


def test_get_vision_job_returns_none_for_unknown_id(adapter: SqliteAdapter) -> None:
    assert adapter.get_vision_job("vjob_does-not-exist") is None


def test_get_vision_job_by_source_returns_most_recent(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    pages = [{"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p.png"}]
    job_id = adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)

    job = adapter.get_vision_job_by_source(source_id)

    assert job is not None
    assert job["id"] == job_id


def test_get_vision_job_by_source_returns_none_when_no_job(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    assert adapter.get_vision_job_by_source(source_id) is None


# ---------------------------------------------------------------------------
# Task 7: list_vision_page_descriptions
# ---------------------------------------------------------------------------


def test_list_vision_page_descriptions_returns_all_ordered(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    pages = [
        {"page_number": 3, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p3.png"},
        {"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p1.png"},
        {"page_number": 2, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p2.png"},
    ]
    adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)

    rows = adapter.list_vision_page_descriptions(source_id)

    assert [r["page_number"] for r in rows] == [1, 2, 3]
    for r in rows:
        assert "description" in r and r["description"] is None
        assert "image_path" in r
        assert "finish_reason" in r and r["finish_reason"] is None
        assert "error_message" in r and r["error_message"] is None
        assert r["attempts"] == 0


def test_list_vision_page_descriptions_filters_by_status(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    pages = [
        {"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p1.png"},
        {"page_number": 2, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p2.png"},
    ]
    adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)

    pending = adapter.list_vision_page_descriptions(source_id, statuses=[VisionPageStatus.PENDING])
    assert len(pending) == 2

    succeeded = adapter.list_vision_page_descriptions(
        source_id, statuses=[VisionPageStatus.SUCCEEDED]
    )
    assert succeeded == []


def test_list_vision_page_descriptions_empty_for_unknown_source(adapter: SqliteAdapter) -> None:
    assert adapter.list_vision_page_descriptions("src_no-such-source") == []


# ---------------------------------------------------------------------------
# Task 8: update_vision_page_description
# ---------------------------------------------------------------------------


def test_update_vision_page_description_happy_path(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    pages = [{"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p.png"}]
    adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)
    page_id = adapter.list_vision_page_descriptions(source_id)[0]["id"]

    rows = adapter.update_vision_page_description(
        page_id=page_id,
        new_status=VisionPageStatus.SUCCEEDED,
        description="A page about cats.",
        finish_reason="stop",
        error_message=None,
    )

    assert rows == 1
    row = adapter.list_vision_page_descriptions(source_id)[0]
    assert row["status"] == VisionPageStatus.SUCCEEDED
    assert row["description"] == "A page about cats."
    assert row["finish_reason"] == "stop"
    assert row["attempts"] == 1


def test_update_vision_page_description_stale_dispatch_returns_zero(adapter: SqliteAdapter) -> None:
    """An UPDATE with a status guard that doesn't match returns rows=0."""
    source_id = _create_source(adapter)
    pages = [{"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p.png"}]
    adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)
    page_id = adapter.list_vision_page_descriptions(source_id)[0]["id"]

    first = adapter.update_vision_page_description(
        page_id=page_id,
        new_status=VisionPageStatus.SUCCEEDED,
        description="first",
        finish_reason="stop",
        error_message=None,
    )
    assert first == 1

    second = adapter.update_vision_page_description(
        page_id=page_id,
        new_status=VisionPageStatus.SUCCEEDED,
        description="second-stale",
        finish_reason="stop",
        error_message=None,
    )
    assert second == 0

    row = adapter.list_vision_page_descriptions(source_id)[0]
    assert row["description"] == "first"
    assert row["attempts"] == 1


def test_update_vision_page_description_truncated_writes_finish_reason(
    adapter: SqliteAdapter,
) -> None:
    source_id = _create_source(adapter)
    pages = [{"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p.png"}]
    adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)
    page_id = adapter.list_vision_page_descriptions(source_id)[0]["id"]

    adapter.update_vision_page_description(
        page_id=page_id,
        new_status=VisionPageStatus.TRUNCATED,
        description="partial transcription...",
        finish_reason="length",
        error_message=None,
    )

    row = adapter.list_vision_page_descriptions(source_id)[0]
    assert row["status"] == VisionPageStatus.TRUNCATED
    assert row["finish_reason"] == "length"
    assert row["description"] == "partial transcription..."


# ---------------------------------------------------------------------------
# Task 9: increment_vision_job_completed_and_check
# ---------------------------------------------------------------------------


def test_increment_succeeded_bumps_completed(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    pages = [
        {"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p1.png"},
        {"page_number": 2, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p2.png"},
    ]
    job_id = adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)

    result = adapter.increment_vision_job_completed_and_check(
        job_id=job_id, outcome=VisionPageStatus.SUCCEEDED
    )

    assert result == {"completed": 1, "failed": 0, "total": 2, "is_terminal": False}


def test_increment_truncated_also_bumps_completed(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    pages = [{"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p.png"}]
    job_id = adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)

    result = adapter.increment_vision_job_completed_and_check(
        job_id=job_id, outcome=VisionPageStatus.TRUNCATED
    )

    assert result["completed"] == 1
    assert result["is_terminal"] is True


def test_increment_failed_bumps_failed(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    pages = [
        {"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p1.png"},
        {"page_number": 2, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p2.png"},
    ]
    job_id = adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)

    result = adapter.increment_vision_job_completed_and_check(
        job_id=job_id, outcome=VisionPageStatus.FAILED
    )

    assert result == {"completed": 0, "failed": 1, "total": 2, "is_terminal": False}


def test_increment_terminal_when_completed_plus_failed_reaches_total(
    adapter: SqliteAdapter,
) -> None:
    source_id = _create_source(adapter)
    pages = [
        {"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p1.png"},
        {"page_number": 2, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p2.png"},
    ]
    job_id = adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)

    adapter.increment_vision_job_completed_and_check(
        job_id=job_id, outcome=VisionPageStatus.SUCCEEDED
    )
    final = adapter.increment_vision_job_completed_and_check(
        job_id=job_id, outcome=VisionPageStatus.FAILED
    )

    assert final == {"completed": 1, "failed": 1, "total": 2, "is_terminal": True}


def test_increment_pending_outcome_raises(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    pages = [{"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p.png"}]
    job_id = adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)

    with pytest.raises(ValueError, match="terminal outcome"):
        adapter.increment_vision_job_completed_and_check(
            job_id=job_id, outcome=VisionPageStatus.PENDING
        )


def test_increment_unknown_job_returns_zero_state(adapter: SqliteAdapter) -> None:
    result = adapter.increment_vision_job_completed_and_check(
        job_id="vjob_does-not-exist", outcome=VisionPageStatus.SUCCEEDED
    )
    assert result == {"completed": 0, "failed": 0, "total": 0, "is_terminal": False}


# ---------------------------------------------------------------------------
# Task 10: reset_vision_page_for_retry
# ---------------------------------------------------------------------------


def test_reset_vision_page_for_retry_from_succeeded(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    pages = [{"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p.png"}]
    job_id = adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)
    page_id = adapter.list_vision_page_descriptions(source_id)[0]["id"]

    adapter.update_vision_page_description(
        page_id=page_id,
        new_status=VisionPageStatus.SUCCEEDED,
        description="hello",
        finish_reason="stop",
        error_message=None,
    )
    adapter.increment_vision_job_completed_and_check(
        job_id=job_id, outcome=VisionPageStatus.SUCCEEDED
    )

    reset_happened = adapter.reset_vision_page_for_retry(page_id=page_id)

    assert reset_happened is True
    row = adapter.list_vision_page_descriptions(source_id)[0]
    assert row["status"] == VisionPageStatus.PENDING
    assert row["description"] is None
    assert row["finish_reason"] is None
    assert row["error_message"] is None

    job = adapter.get_vision_job(job_id)
    assert job is not None
    assert job["completed"] == 0
    assert job["failed"] == 0


def test_reset_vision_page_for_retry_from_failed_decrements_failed(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    pages = [{"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p.png"}]
    job_id = adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)
    page_id = adapter.list_vision_page_descriptions(source_id)[0]["id"]

    adapter.update_vision_page_description(
        page_id=page_id,
        new_status=VisionPageStatus.FAILED,
        description=None,
        finish_reason=None,
        error_message="LLM timeout",
    )
    adapter.increment_vision_job_completed_and_check(job_id=job_id, outcome=VisionPageStatus.FAILED)

    assert adapter.reset_vision_page_for_retry(page_id=page_id) is True

    job = adapter.get_vision_job(job_id)
    assert job is not None
    assert job["completed"] == 0
    assert job["failed"] == 0


def test_reset_vision_page_for_retry_from_pending_is_noop(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    pages = [{"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p.png"}]
    adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)
    page_id = adapter.list_vision_page_descriptions(source_id)[0]["id"]

    assert adapter.reset_vision_page_for_retry(page_id=page_id) is False


def test_reset_vision_page_for_retry_unknown_id_returns_false(adapter: SqliteAdapter) -> None:
    assert adapter.reset_vision_page_for_retry(page_id="vpd_no-such-row") is False


# ---------------------------------------------------------------------------
# Task 11: Concurrent-increment race test
# ---------------------------------------------------------------------------


def test_concurrent_increments_exactly_one_observes_terminal(adapter: SqliteAdapter) -> None:
    """Spawn N concurrent threads, each calling increment_*. Exactly one
    must observe is_terminal=True. Mirrors the extraction_jobs guarantee.
    """
    import threading

    source_id = _create_source(adapter)
    n_pages = 8
    pages = [
        {"page_number": i + 1, "kind": VisionPageKind.PDF_PAGE, "image_path": f"/p{i}.png"}
        for i in range(n_pages)
    ]
    job_id = adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)
    db_path = str(adapter.db_path)

    terminal_observations: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        a = SqliteAdapter(db_path=db_path, database_name="test")
        a.connect()
        try:
            result = a.increment_vision_job_completed_and_check(
                job_id=job_id, outcome=VisionPageStatus.SUCCEEDED
            )
        finally:
            a.disconnect()
        with lock:
            terminal_observations.append(result["is_terminal"])

    threads = [threading.Thread(target=worker) for _ in range(n_pages)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one observation should be terminal=True (the last increment).
    assert sum(1 for t in terminal_observations if t) == 1, (
        f"expected exactly 1 terminal observation, got {terminal_observations}"
    )


# ---------------------------------------------------------------------------
# Task 12: Cascade-delete test
# ---------------------------------------------------------------------------


def test_delete_source_cascades_to_vision_jobs_and_pages(adapter: SqliteAdapter) -> None:
    source_id = _create_source(adapter)
    pages = [
        {"page_number": 1, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p1.png"},
        {"page_number": 2, "kind": VisionPageKind.PDF_PAGE, "image_path": "/p2.png"},
    ]
    job_id = adapter.create_vision_job_with_pages(source_id=source_id, pages=pages)

    assert adapter.get_vision_job(job_id) is not None
    assert len(adapter.list_vision_page_descriptions(source_id)) == 2

    # Use the existing source-delete entry point.
    adapter.delete_source_db(source_id=source_id, database_name="test")

    assert adapter.get_vision_job(job_id) is None
    assert adapter.list_vision_page_descriptions(source_id) == []
