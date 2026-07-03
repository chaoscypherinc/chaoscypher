# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR 2 Task 12 resume-after-vision branch (2026-05-13).

When the vision finalizer enqueues OP_INDEX_DOCUMENT with
``resume_after_vision=True``, the indexing handler must:

* Skip ``start_indexing`` (the finalizer already CAS'd the source from
  VISION_PENDING -> INDEXING; re-running ``start_indexing`` would reset
  ``indexing_started_at`` and clear ``error_message``).
* Skip the loader-stage quality-counter rollups — those were written on
  the first pass; double-incrementing would silently inflate the
  data-quality tab.
* Read every ``vision_page_descriptions`` row and feed it through
  ``vision_finalizer._splice_descriptions_into_documents`` so the
  vision text reaches the chunker.
* Continue with the post-loader pipeline (normalize -> chunk -> embed).

These tests pin those contracts.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_settings(tmp_path: Path) -> MagicMock:
    settings = MagicMock()
    settings.priorities.background = 50
    settings.data_dir = str(tmp_path)
    return settings


def _make_engine_settings(tmp_path: Path) -> MagicMock:
    """MagicMock engine_settings with a real data_dir.

    _run_indexing computes ``Path(engine_settings.paths.data_dir)`` (original-
    text persistence, vision, error-path cleanup); an unpinned MagicMock
    stringifies into a literal ``<MagicMock name='mock.paths.data_dir' ...>``
    directory at the repo root (issue #249).
    """
    engine_settings = MagicMock()
    engine_settings.paths.data_dir = str(tmp_path)
    return engine_settings


@pytest.mark.asyncio
async def test_run_indexing_resume_after_vision_skips_loader_counters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A resume task does NOT double-count loader QualityCounter increments.

    Concern 3 from Task 11's forward-looking review: loader-stage
    quality counters (loader_warnings, loader_files_skipped, etc.) were
    written on the original pass. On the resume entry the handler MUST
    skip the rollup or the data-quality tab silently inflates.

    We assert this by spying on the typed helper
    ``increment_quality_counter`` and verifying it was never called
    with a loader-stage counter when ``resume_after_vision=True``.
    """
    from chaoscypher_core.operations.importing import indexing_handler

    adapter = MagicMock()
    adapter.list_vision_page_descriptions.return_value = []  # empty splice

    # Loader emits one doc with EVERY counter-eligible metadata key the
    # loader-stage rollups care about — so if the gate is missing, the
    # increment helper will get hammered.
    fake_registry = MagicMock()
    fake_registry.load_document.return_value = [
        {
            "content": "x" * 500,
            "metadata": {
                "loader_warnings": ["w1", "w2", "w3"],
                "loader_files_skipped": 4,
                "replacement_chars_count": 5,
                "loader_pdf_pages_failed": 6,
                "encoding_used": "utf-8",
            },
        }
    ]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
    )

    # Capture every increment_quality_counter call.
    incr_spy = AsyncMock()
    monkeypatch.setattr(
        "chaoscypher_core.services.quality.counters.increment_quality_counter",
        incr_spy,
    )

    # Also spy on the typed encoding helper.
    set_encoding_spy = MagicMock()
    monkeypatch.setattr(
        "chaoscypher_core.services.quality.counters.set_loader_encoding",
        set_encoding_spy,
    )

    monkeypatch.setattr(
        indexing_handler,
        "_extract_text",
        lambda **kw: (
            "x" * 500,
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )

    chunking_service = MagicMock()
    chunking_result = MagicMock(
        total_small_chunks=1,
        total_groups=1,
        chunks_filtered=0,
        normalize_drops=0,
        prestrip_lines_removed=0,
        chunks_skipped_by_depth=0,
    )
    chunking_service.create_chunks = AsyncMock(return_value=chunking_result)
    chunking_service.store_chunks = MagicMock()

    monkeypatch.setattr(indexing_handler, "queue_embed_chunks", AsyncMock(return_value="tsk_e1"))
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    # _apply_vision_processing must NOT be called on the resume path —
    # spy on it so the test fails if the gate is broken.
    vision_spy = AsyncMock()
    monkeypatch.setattr(indexing_handler, "_apply_vision_processing", vision_spy)

    await indexing_handler._run_indexing(
        file_id="src_resume",
        file_info={"filename": "scan.pdf"},
        filepath="/tmp/scan.pdf",
        analysis_depth="full",
        enable_normalization=False,
        enable_vision=True,
        adapter=adapter,
        chunking_service=chunking_service,
        engine_settings=_make_engine_settings(tmp_path),
        settings=_make_settings(tmp_path),
        database_name="default",
        resume_after_vision=True,
    )

    # No loader-stage rollups on the resume path.
    assert incr_spy.await_count == 0, (
        "loader quality-counter rollups must be skipped on the resume "
        "path; got "
        f"{incr_spy.await_count} unexpected increment(s): "
        f"{incr_spy.await_args_list!r}"
    )
    assert set_encoding_spy.call_count == 0, (
        "set_loader_encoding must not be called on the resume path"
    )
    # And the vision-enqueue function must be skipped — the finalizer
    # has already done that work.
    vision_spy.assert_not_called()
    # start_indexing must NOT fire on the resume path (the finalizer
    # already CAS'd to INDEXING; re-running would reset state).
    adapter.start_indexing.assert_not_called()


@pytest.mark.asyncio
async def test_run_indexing_resume_after_vision_calls_splice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The resume path feeds documents through the shared splice helper.

    Pin the contract: the resume branch reads
    ``adapter.list_vision_page_descriptions(source_id)`` and passes the
    rows to ``vision_finalizer._splice_descriptions_into_documents``
    (the same helper the finalizer uses, kept as a single source of
    truth for the splice).
    """
    from chaoscypher_core.operations.importing import indexing_handler, vision_finalizer

    # Fake page-rows the adapter returns: one PDF page with a SUCCEEDED
    # description.
    page_rows = [
        {
            "id": "page-1",
            "source_id": "src_resume",
            "page_number": 1,
            "kind": "pdf_page",
            "status": "succeeded",
            "description": "DESCRIBED page 1",
            "image_path": "/tmp/page_1.png",
        }
    ]
    adapter = MagicMock()
    adapter.list_vision_page_descriptions.return_value = page_rows

    fake_registry = MagicMock()
    # Loader output mirrors the deterministic re-load that produced the
    # original pre-vision documents.
    fake_registry.load_document.return_value = [
        {
            "content": "original page 1 text",
            "metadata": {
                "pages": [{"page_number": 1, "has_images": True}],
                "_page_texts": ["original page 1 text"],
            },
        }
    ]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
    )

    # Capture the documents passed to _extract_text (the post-splice
    # docs that flow into normalization).
    captured: dict[str, list[dict]] = {}

    def _capture_extract(**kw):
        captured["docs"] = kw["documents"]
        return (
            kw["documents"][0]["content"],
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        )

    monkeypatch.setattr(indexing_handler, "_extract_text", _capture_extract)

    chunking_service = MagicMock()
    chunking_result = MagicMock(
        total_small_chunks=1,
        total_groups=1,
        chunks_filtered=0,
        normalize_drops=0,
        prestrip_lines_removed=0,
        chunks_skipped_by_depth=0,
    )
    chunking_service.create_chunks = AsyncMock(return_value=chunking_result)
    chunking_service.store_chunks = MagicMock()

    monkeypatch.setattr(indexing_handler, "queue_embed_chunks", AsyncMock(return_value="tsk_e1"))
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    # Spy on the splice helper at its definition site so the indexing
    # handler's lazy import picks up the wrapped version.
    splice_spy = MagicMock(wraps=vision_finalizer._splice_descriptions_into_documents)
    monkeypatch.setattr(
        vision_finalizer,
        "_splice_descriptions_into_documents",
        splice_spy,
    )

    await indexing_handler._run_indexing(
        file_id="src_resume",
        file_info={"filename": "scan.pdf"},
        filepath="/tmp/scan.pdf",
        analysis_depth="full",
        enable_normalization=False,
        enable_vision=True,
        adapter=adapter,
        chunking_service=chunking_service,
        engine_settings=_make_engine_settings(tmp_path),
        settings=_make_settings(tmp_path),
        database_name="default",
        resume_after_vision=True,
    )

    adapter.list_vision_page_descriptions.assert_called_once_with("src_resume")
    splice_spy.assert_called_once()
    # The spliced document carries the [Visual Content] marker.
    spliced_doc = captured["docs"][0]
    assert "[Visual Content]" in spliced_doc["content"], (
        f"expected splice marker in content, got: {spliced_doc['content']!r}"
    )
    assert "DESCRIBED page 1" in spliced_doc["content"]


@pytest.mark.asyncio
async def test_run_indexing_resume_after_vision_skips_start_indexing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``start_indexing`` MUST NOT fire on the resume path.

    Concern 1 from Task 11's review: the finalizer has already CAS'd
    the source from VISION_PENDING -> INDEXING. If ``start_indexing``
    re-runs here it resets ``indexing_started_at`` and clears
    ``error_message``, losing context (and potentially also clearing
    a valid recovery error mid-flight).

    Also: ``event_bus.emit("task_started", ...)`` must not fire either —
    the resume entry is a continuation, not a new task.
    """
    from chaoscypher_core.operations.importing import indexing_handler

    adapter = MagicMock()
    adapter.list_vision_page_descriptions.return_value = []

    fake_registry = MagicMock()
    fake_registry.load_document.return_value = [{"content": "x" * 200, "metadata": {}}]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
    )
    monkeypatch.setattr(
        indexing_handler,
        "_extract_text",
        lambda **kw: (
            "x" * 200,
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )
    chunking_service = MagicMock()
    chunking_result = MagicMock(
        total_small_chunks=1,
        total_groups=1,
        chunks_filtered=0,
        normalize_drops=0,
        prestrip_lines_removed=0,
        chunks_skipped_by_depth=0,
    )
    chunking_service.create_chunks = AsyncMock(return_value=chunking_result)
    chunking_service.store_chunks = MagicMock()
    monkeypatch.setattr(indexing_handler, "queue_embed_chunks", AsyncMock(return_value="tsk_e1"))
    event_bus_spy = MagicMock()
    monkeypatch.setattr(indexing_handler, "event_bus", event_bus_spy)

    await indexing_handler._run_indexing(
        file_id="src_resume",
        file_info={"filename": "scan.pdf"},
        filepath="/tmp/scan.pdf",
        analysis_depth="full",
        enable_normalization=False,
        enable_vision=True,
        adapter=adapter,
        chunking_service=chunking_service,
        engine_settings=_make_engine_settings(tmp_path),
        settings=_make_settings(tmp_path),
        database_name="default",
        resume_after_vision=True,
    )

    adapter.start_indexing.assert_not_called()
    # No task_started event on the resume entry.
    task_started_events = [
        c for c in event_bus_spy.emit.call_args_list if c.args and c.args[0] == "task_started"
    ]
    assert not task_started_events, (
        f"task_started event must not fire on the resume path, got: {task_started_events!r}"
    )


@pytest.mark.asyncio
async def test_run_indexing_normal_path_still_calls_start_indexing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sanity: on the non-resume path, ``start_indexing`` still fires.

    Pin the inverse of the resume-path test so a future refactor that
    flips the gate the wrong way fails fast.
    """
    from chaoscypher_core.operations.importing import indexing_handler

    adapter = MagicMock()

    fake_registry = MagicMock()
    fake_registry.load_document.return_value = [{"content": "x" * 200, "metadata": {}}]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
    )
    monkeypatch.setattr(
        indexing_handler,
        "_extract_text",
        lambda **kw: (
            "x" * 200,
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )
    monkeypatch.setattr(
        indexing_handler,
        "_apply_vision_processing",
        AsyncMock(side_effect=lambda **kw: (kw["documents"], None)),
    )
    chunking_service = MagicMock()
    chunking_result = MagicMock(
        total_small_chunks=1,
        total_groups=1,
        chunks_filtered=0,
        normalize_drops=0,
        prestrip_lines_removed=0,
        chunks_skipped_by_depth=0,
    )
    chunking_service.create_chunks = AsyncMock(return_value=chunking_result)
    chunking_service.store_chunks = MagicMock()
    monkeypatch.setattr(indexing_handler, "queue_embed_chunks", AsyncMock(return_value="tsk_e1"))
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    await indexing_handler._run_indexing(
        file_id="src_normal",
        file_info={"filename": "doc.txt"},
        filepath="/tmp/doc.txt",
        analysis_depth="full",
        enable_normalization=False,
        enable_vision=False,
        adapter=adapter,
        chunking_service=chunking_service,
        engine_settings=_make_engine_settings(tmp_path),
        settings=_make_settings(tmp_path),
        database_name="default",
        # Default resume_after_vision=False — the normal path.
    )

    adapter.start_indexing.assert_called_once_with("src_normal")
