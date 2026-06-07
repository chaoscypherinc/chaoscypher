# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for OP_VISION_FINALIZE handler (vision_finalizer.handle_vision_finalize).

Coverage:
- happy path: descriptions splice into documents, state advances,
  resume task enqueued.
- idempotency: re-running finalize when state has advanced returns
  skipped_already_advanced and does not re-enqueue.
- all-pages-failed: best-effort — state still advances, documents
  reflect what the loader produced (no augmentation).
- missing job / missing source guards.
- splice helper standalone (pure-function unit test) — both PDF and
  standalone-image kinds, plus FAILED rows ignored.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.utils.id import generate_id
from chaoscypher_core.vision.states import VisionPageKind, VisionPageStatus


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Real SQLite adapter on a tmp DB. Matches test_vision_page_handler."""
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    try:
        yield a
    finally:
        a.disconnect()


def _setup_finished_vision_job(
    adapter: SqliteAdapter,
    *,
    filepath: str = "/tmp/fake.pdf",
    page_outcomes: list[tuple[int, VisionPageStatus, str | None]] | None = None,
) -> tuple[str, str]:
    """Create a source + vision_job + terminal page rows.

    Args:
        adapter: Storage adapter.
        filepath: Path stored on the source row.
        page_outcomes: List of (page_number, status, description) tuples.
            Defaults to a single SUCCEEDED PDF page with a description.

    Returns:
        (source_id, job_id).
    """
    if page_outcomes is None:
        page_outcomes = [(1, VisionPageStatus.SUCCEEDED, "A diagram of a cat.")]

    source_id = generate_id("src")
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "test",
            "filename": "fake.pdf",
            "filepath": filepath,
            "file_type": "pdf",
            "status": SourceStatus.VISION_PENDING.value,
        }
    )
    job_id = adapter.create_vision_job_with_pages(
        source_id=source_id,
        pages=[
            {
                "page_number": page_num,
                "kind": VisionPageKind.PDF_PAGE,
                "image_path": filepath,
            }
            for page_num, _, _ in page_outcomes
        ],
    )
    # Bring each page to its terminal state via the adapter's atomic
    # update_vision_page_description + increment_vision_job_completed_and_check
    # so the job counter mirrors a real run.
    rows = adapter.list_vision_page_descriptions(source_id)
    for row, (_page_num, status, description) in zip(rows, page_outcomes, strict=True):
        adapter.update_vision_page_description(
            page_id=row["id"],
            new_status=status,
            description=description,
            finish_reason="stop" if status == VisionPageStatus.SUCCEEDED else None,
            error_message=None if status != VisionPageStatus.FAILED else "stub-failure",
        )
        adapter.increment_vision_job_completed_and_check(
            job_id=job_id,
            outcome=status,
        )
    return source_id, job_id


@pytest.mark.asyncio
async def test_finalize_merges_descriptions_and_enqueues_resume(
    adapter: SqliteAdapter,
) -> None:
    """Happy path: descriptions splice into documents, source state
    advances to INDEXING, resume task enqueued.
    """
    from chaoscypher_core.operations.importing.vision_finalizer import (
        handle_vision_finalize,
    )

    source_id, job_id = _setup_finished_vision_job(adapter)

    # Deterministic loader stub: returns a single PDF document with one
    # page of text. The finalizer splices the description into the
    # page's text — we assert this happened via the in-memory return,
    # since the resume task carries no documents (it'll re-load).
    fake_documents = [
        {
            "content": "Page 1 body text.",
            "metadata": {
                "_page_texts": ["Page 1 body text."],
                "pages": [{"page_number": 1, "has_images": True}],
            },
        }
    ]

    settings = MagicMock()

    with (
        patch(
            "chaoscypher_core.operations.importing.vision_finalizer._reload_documents",
            return_value=fake_documents,
        ),
        patch(
            "chaoscypher_core.operations.importing.vision_finalizer._enqueue_resume_indexing",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        result = await handle_vision_finalize(
            data={
                "source_id": source_id,
                "job_id": job_id,
                "database_name": "test",
            },
            adapter=adapter,
            settings=settings,
        )

    assert result["status"] == "success"
    assert result["total_pages"] == 1
    assert result["succeeded"] == 1
    assert result["truncated"] == 0
    assert result["failed"] == 0

    # State advanced past vision_pending.
    src = adapter.get_source(source_id, "test")
    assert src is not None
    assert src["status"] == SourceStatus.INDEXING.value

    # Resume task enqueued exactly once.
    mock_enqueue.assert_awaited_once()
    # Carries the right keys.
    kwargs = mock_enqueue.call_args.kwargs
    assert kwargs["source_id"] == source_id
    assert kwargs["database_name"] == "test"
    assert kwargs["file_info"]["filepath"] == "/tmp/fake.pdf"

    # Splice mutated the in-memory document.
    assert "[Visual Content]" in fake_documents[0]["content"]
    assert "A diagram of a cat." in fake_documents[0]["content"]


@pytest.mark.asyncio
async def test_finalize_idempotent_when_already_advanced(
    adapter: SqliteAdapter,
) -> None:
    """If the source has fully advanced past indexing, finalize bails
    without splicing or re-enqueuing the resume task.

    NOTE: ``INDEXING + vision_job`` is the post-CAS-pre-enqueue crash
    window and is handled by the new ``re_emitted_resume`` branch — see
    ``test_finalize_re_emits_resume_when_indexing_and_no_resume_queued``.
    This test pins the terminal "already done past indexing" case.
    """
    from chaoscypher_core.operations.importing.vision_finalizer import (
        handle_vision_finalize,
    )

    source_id, job_id = _setup_finished_vision_job(adapter)
    # Pre-advance the source row past INDEXING into INDEXED (simulates
    # a prior finalize win + completed indexing run, or external manual
    # transition).
    adapter.transition_source_status(
        source_id,
        from_status=SourceStatus.VISION_PENDING.value,
        to_status=SourceStatus.INDEXING.value,
        database_name="test",
    )
    adapter.transition_source_status(
        source_id,
        from_status=SourceStatus.INDEXING.value,
        to_status=SourceStatus.INDEXED.value,
        database_name="test",
    )

    settings = MagicMock()

    with (
        patch(
            "chaoscypher_core.operations.importing.vision_finalizer._reload_documents",
        ) as mock_reload,
        patch(
            "chaoscypher_core.operations.importing.vision_finalizer._enqueue_resume_indexing",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        result = await handle_vision_finalize(
            data={
                "source_id": source_id,
                "job_id": job_id,
                "database_name": "test",
            },
            adapter=adapter,
            settings=settings,
        )

    assert result["status"] == "skipped_already_advanced"
    # Neither loader re-run nor resume enqueue happened.
    mock_reload.assert_not_called()
    mock_enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_handles_all_pages_failed(adapter: SqliteAdapter) -> None:
    """Best-effort: when every page failed, finalize still advances the
    source state and enqueues the resume task. The documents the resume
    handler picks up will be the un-augmented loader output.
    """
    from chaoscypher_core.operations.importing.vision_finalizer import (
        handle_vision_finalize,
    )

    source_id, job_id = _setup_finished_vision_job(
        adapter,
        page_outcomes=[
            (1, VisionPageStatus.FAILED, None),
            (2, VisionPageStatus.FAILED, None),
        ],
    )

    fake_documents = [
        {
            "content": "Body.",
            "metadata": {
                "_page_texts": ["Page 1.", "Page 2."],
                "pages": [
                    {"page_number": 1, "has_images": True},
                    {"page_number": 2, "has_images": True},
                ],
            },
        }
    ]

    settings = MagicMock()

    with (
        patch(
            "chaoscypher_core.operations.importing.vision_finalizer._reload_documents",
            return_value=fake_documents,
        ),
        patch(
            "chaoscypher_core.operations.importing.vision_finalizer._enqueue_resume_indexing",
            new_callable=AsyncMock,
        ) as mock_enqueue,
    ):
        result = await handle_vision_finalize(
            data={
                "source_id": source_id,
                "job_id": job_id,
                "database_name": "test",
            },
            adapter=adapter,
            settings=settings,
        )

    assert result["status"] == "success"
    assert result["failed"] == 2
    assert result["succeeded"] == 0
    # No [Visual Content] block was inserted — the documents are unchanged.
    assert "[Visual Content]" not in fake_documents[0]["content"]

    # State still advanced.
    src = adapter.get_source(source_id, "test")
    assert src is not None
    assert src["status"] == SourceStatus.INDEXING.value
    # Resume still enqueued — chunking should still happen on the loader
    # text alone.
    mock_enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_skips_when_job_missing(adapter: SqliteAdapter) -> None:
    """Missing vision_jobs row → skipped_missing, no state mutation."""
    from chaoscypher_core.operations.importing.vision_finalizer import (
        handle_vision_finalize,
    )

    source_id = generate_id("src")
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "test",
            "filename": "x.pdf",
            "filepath": "/tmp/x.pdf",
            "status": SourceStatus.VISION_PENDING.value,
        }
    )
    settings = MagicMock()

    with patch(
        "chaoscypher_core.operations.importing.vision_finalizer._enqueue_resume_indexing",
        new_callable=AsyncMock,
    ) as mock_enqueue:
        result = await handle_vision_finalize(
            data={
                "source_id": source_id,
                "job_id": "job-does-not-exist",
                "database_name": "test",
            },
            adapter=adapter,
            settings=settings,
        )

    assert result["status"] == "skipped_missing"
    mock_enqueue.assert_not_awaited()

    src = adapter.get_source(source_id, "test")
    assert src is not None
    assert src["status"] == SourceStatus.VISION_PENDING.value


@pytest.mark.asyncio
async def test_finalize_skips_when_source_missing(adapter: SqliteAdapter) -> None:
    """Missing source row → skipped_missing.

    Construct a job row attached to a source that we then never persist
    (we'd typically create the source first; this fixture skips it on
    purpose to exercise the guard).
    """
    from chaoscypher_core.operations.importing.vision_finalizer import (
        handle_vision_finalize,
    )

    # We need an existing source for create_vision_job_with_pages to
    # succeed; create one, then call the handler with a source_id that
    # doesn't exist in the given database_name (cross-DB lookup).
    source_id = generate_id("src")
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "test",
            "filename": "x.pdf",
            "filepath": "/tmp/x.pdf",
            "status": SourceStatus.VISION_PENDING.value,
        }
    )
    job_id = adapter.create_vision_job_with_pages(
        source_id=source_id,
        pages=[
            {
                "page_number": 1,
                "kind": VisionPageKind.PDF_PAGE,
                "image_path": "/tmp/x.pdf",
            }
        ],
    )
    settings = MagicMock()

    # Call with a database_name that won't match.
    with patch(
        "chaoscypher_core.operations.importing.vision_finalizer._enqueue_resume_indexing",
        new_callable=AsyncMock,
    ) as mock_enqueue:
        result = await handle_vision_finalize(
            data={
                "source_id": source_id,
                "job_id": job_id,
                "database_name": "wrong-db",
            },
            adapter=adapter,
            settings=settings,
        )

    assert result["status"] == "skipped_missing"
    mock_enqueue.assert_not_awaited()


# ----------------------------------------------------------------------------
# _splice_descriptions_into_documents — pure-function unit tests.
# ----------------------------------------------------------------------------


def test_splice_pdf_page_appends_visual_content_block() -> None:
    """SUCCEEDED PDF page row → [Visual Content] block appended to its page text."""
    from chaoscypher_core.operations.importing.vision_finalizer import (
        _splice_descriptions_into_documents,
    )

    documents = [
        {
            "content": "Page one.\n\nPage two.",
            "metadata": {
                "_page_texts": ["Page one.", "Page two."],
                "pages": [
                    {"page_number": 1, "has_images": True},
                    {"page_number": 2, "has_images": False},
                ],
            },
        }
    ]
    page_rows = [
        {
            "id": "p1",
            "page_number": 1,
            "kind": VisionPageKind.PDF_PAGE.value,
            "status": VisionPageStatus.SUCCEEDED.value,
            "description": "A flowchart.",
            "image_path": "/tmp/p1.png",
        }
    ]
    result = _splice_descriptions_into_documents(documents, page_rows)
    assert "[Visual Content]\nA flowchart.\n[/Visual Content]" in result[0]["content"]
    # First page text grew, second untouched.
    assert "Page one." in result[0]["metadata"]["_page_texts"][0]
    assert result[0]["metadata"]["_page_texts"][1] == "Page two."
    # image_path carried into per-page metadata.
    assert result[0]["metadata"]["pages"][0]["image_path"] == "/tmp/p1.png"


def test_splice_truncated_page_also_inserted() -> None:
    """TRUNCATED rows splice in (the partial description is real content)."""
    from chaoscypher_core.operations.importing.vision_finalizer import (
        _splice_descriptions_into_documents,
    )

    documents = [
        {
            "content": "Page one.",
            "metadata": {
                "_page_texts": ["Page one."],
                "pages": [{"page_number": 1, "has_images": True}],
            },
        }
    ]
    page_rows = [
        {
            "id": "p1",
            "page_number": 1,
            "kind": VisionPageKind.PDF_PAGE.value,
            "status": VisionPageStatus.TRUNCATED.value,
            "description": "Partial description...",
            "image_path": None,
        }
    ]
    result = _splice_descriptions_into_documents(documents, page_rows)
    assert "Partial description..." in result[0]["content"]


def test_splice_failed_page_skipped() -> None:
    """FAILED rows contribute nothing — document content is unchanged."""
    from chaoscypher_core.operations.importing.vision_finalizer import (
        _splice_descriptions_into_documents,
    )

    documents = [
        {
            "content": "Page one.",
            "metadata": {
                "_page_texts": ["Page one."],
                "pages": [{"page_number": 1, "has_images": True}],
            },
        }
    ]
    page_rows = [
        {
            "id": "p1",
            "page_number": 1,
            "kind": VisionPageKind.PDF_PAGE.value,
            "status": VisionPageStatus.FAILED.value,
            "description": None,
            "image_path": None,
        }
    ]
    result = _splice_descriptions_into_documents(documents, page_rows)
    assert result[0]["content"] == "Page one."
    assert "[Visual Content]" not in result[0]["content"]


def test_splice_standalone_image_replaces_content() -> None:
    """STANDALONE_IMAGE row → document content replaced with the description."""
    from chaoscypher_core.operations.importing.vision_finalizer import (
        _splice_descriptions_into_documents,
    )

    documents = [
        {
            "content": "",
            "metadata": {"extraction_method": "vision_pending"},
        }
    ]
    page_rows = [
        {
            "id": "p1",
            "page_number": 1,
            "kind": VisionPageKind.STANDALONE_IMAGE.value,
            "status": VisionPageStatus.SUCCEEDED.value,
            "description": "A cat sitting on a mat.",
            "image_path": "/tmp/cat.png",
        }
    ]
    result = _splice_descriptions_into_documents(documents, page_rows)
    assert result[0]["content"] == "A cat sitting on a mat."
    assert result[0]["metadata"]["extraction_method"] == "vision"


# ----------------------------------------------------------------------------
# Recovery-driven idempotency: INDEXING + vision_job exists -> re-emit resume.
#
# The recovery scanner (commit 0086888d) routes INDEXING-stuck sources with a
# vision_job to OP_VISION_FINALIZE on the assumption that the finalizer's
# idempotency converges them. These tests pin that converging behaviour for
# the post-CAS-pre-enqueue crash window — without them, the dispatch is a
# no-op and the source stalls permanently.
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_re_emits_resume_when_indexing_and_no_resume_queued(
    adapter: SqliteAdapter,
) -> None:
    """status==INDEXING + vision_job exists + no OP_INDEX_DOCUMENT queued ->
    re-emit the resume task.

    Simulates the crash window between the VISION_PENDING -> INDEXING CAS
    (line 358-364 of vision_finalizer.py) and the resume-task enqueue
    (line 385): the source row is already INDEXING but no resume task ever
    landed on the queue. The next OP_VISION_FINALIZE dispatch (from the
    recovery scanner) must re-emit OP_INDEX_DOCUMENT, NOT bail with
    skipped_already_advanced.
    """
    from chaoscypher_core.operations.importing.vision_finalizer import (
        handle_vision_finalize,
    )

    source_id, job_id = _setup_finished_vision_job(adapter)
    # Pre-advance the source past VISION_PENDING — mirrors the post-CAS state.
    adapter.transition_source_status(
        source_id,
        from_status=SourceStatus.VISION_PENDING.value,
        to_status=SourceStatus.INDEXING.value,
        database_name="test",
    )

    settings = MagicMock()

    with (
        patch(
            "chaoscypher_core.operations.importing.vision_finalizer._enqueue_resume_indexing",
            new_callable=AsyncMock,
        ) as mock_enqueue,
        patch(
            "chaoscypher_core.operations.importing.vision_finalizer.queue_client.task_exists_for_source",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        result = await handle_vision_finalize(
            data={
                "source_id": source_id,
                "job_id": job_id,
                "database_name": "test",
            },
            adapter=adapter,
            settings=settings,
        )

    assert result["status"] == "re_emitted_resume"

    # Resume task re-enqueued exactly once with the canonical file_info shape.
    mock_enqueue.assert_awaited_once()
    kwargs = mock_enqueue.call_args.kwargs
    assert kwargs["source_id"] == source_id
    assert kwargs["database_name"] == "test"
    assert kwargs["file_info"]["filepath"] == "/tmp/fake.pdf"
    assert kwargs["file_info"]["filename"] == "fake.pdf"
    assert kwargs["file_info"]["file_type"] == "pdf"

    # Source status stays at INDEXING (no double CAS).
    src = adapter.get_source(source_id, "test")
    assert src is not None
    assert src["status"] == SourceStatus.INDEXING.value


@pytest.mark.asyncio
async def test_finalize_skips_re_emit_when_index_document_already_queued(
    adapter: SqliteAdapter,
) -> None:
    """status==INDEXING + vision_job exists + OP_INDEX_DOCUMENT already queued
    -> skipped_resume_already_queued (debounce, no double-enqueue).

    The recovery scanner re-runs every ~60s. If a prior finalize already
    re-emitted the resume task, the second scan must not enqueue a duplicate.
    """
    from chaoscypher_core.operations.importing.vision_finalizer import (
        handle_vision_finalize,
    )

    source_id, job_id = _setup_finished_vision_job(adapter)
    adapter.transition_source_status(
        source_id,
        from_status=SourceStatus.VISION_PENDING.value,
        to_status=SourceStatus.INDEXING.value,
        database_name="test",
    )

    settings = MagicMock()

    with (
        patch(
            "chaoscypher_core.operations.importing.vision_finalizer._enqueue_resume_indexing",
            new_callable=AsyncMock,
        ) as mock_enqueue,
        patch(
            "chaoscypher_core.operations.importing.vision_finalizer.queue_client.task_exists_for_source",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        result = await handle_vision_finalize(
            data={
                "source_id": source_id,
                "job_id": job_id,
                "database_name": "test",
            },
            adapter=adapter,
            settings=settings,
        )

    assert result["status"] == "skipped_resume_already_queued"
    mock_enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_skips_already_advanced_when_no_vision_job_for_source(
    adapter: SqliteAdapter,
) -> None:
    """status==INDEXED (or any other non-INDEXING non-VISION_PENDING) ->
    skipped_already_advanced (no re-emit, no state mutation).

    Pins that the new INDEXING+vision_job branch does NOT fire for terminal
    statuses — those still take the original "advanced past vision_pending"
    skip path.
    """
    from chaoscypher_core.operations.importing.vision_finalizer import (
        handle_vision_finalize,
    )

    source_id, job_id = _setup_finished_vision_job(adapter)
    # Pre-advance the source through INDEXING into INDEXED.
    adapter.transition_source_status(
        source_id,
        from_status=SourceStatus.VISION_PENDING.value,
        to_status=SourceStatus.INDEXING.value,
        database_name="test",
    )
    adapter.transition_source_status(
        source_id,
        from_status=SourceStatus.INDEXING.value,
        to_status=SourceStatus.INDEXED.value,
        database_name="test",
    )

    settings = MagicMock()

    with (
        patch(
            "chaoscypher_core.operations.importing.vision_finalizer._enqueue_resume_indexing",
            new_callable=AsyncMock,
        ) as mock_enqueue,
        patch(
            "chaoscypher_core.operations.importing.vision_finalizer.queue_client.task_exists_for_source",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_qcheck,
    ):
        result = await handle_vision_finalize(
            data={
                "source_id": source_id,
                "job_id": job_id,
                "database_name": "test",
            },
            adapter=adapter,
            settings=settings,
        )

    assert result["status"] == "skipped_already_advanced"
    mock_enqueue.assert_not_awaited()
    # No queue probe — terminal statuses skip without touching the queue.
    mock_qcheck.assert_not_called()
