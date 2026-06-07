# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workstream 11 (2026-05-08): cleaner-stage quality counters wire end-to-end.

Verifies that when the normalizer cleaners trim lines, deduplicate
paragraphs, or strip characters from a real document, the indexing
handler surfaces those counts on the source row via the three
``CLEANER_*`` quality counters.

Regression target: an earlier shape of the pipeline let cleaners track
their per-removal counts internally but never propagated them past the
``ContentNormalizerService`` boundary, so the ``cleaner_lines_removed``,
``cleaner_paragraphs_deduplicated``, and ``cleaner_chars_removed``
columns on ``sources`` stayed at 0 even when the cleaners had been busy.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.operations.importing import indexing_handler


@pytest.fixture
def sqlite_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Per-test file-backed ``SqliteAdapter`` with all tables created.

    Mirrors the fixture in ``tests/unit/services/quality/test_counters.py``.
    """
    db_path = tmp_path / "test.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        yield adapter
    finally:
        adapter.disconnect()


@pytest.fixture
def prepared_source_id(sqlite_adapter: SqliteAdapter, tmp_path: Path) -> str:
    """Upload a tiny source so counter UPDATEs target a real row."""
    source_id = "src-cleaner-counters-1"
    sqlite_adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename="dirty.pdf",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )
    return source_id


@pytest.mark.asyncio
async def test_run_indexing_increments_cleaner_lines_removed_for_ocr_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sqlite_adapter: SqliteAdapter,
    prepared_source_id: str,
) -> None:
    """A PDF-extracted document with short noise lines bumps cleaner_lines_removed.

    Uses a real ``SqliteAdapter`` and a real ``ContentNormalizerService``
    so the test exercises the full path: cleaner → normalizer aggregate →
    ``_extract_text`` return value → ``increment_quality_counter`` →
    SQLite UPDATE → row value.
    """
    # Build content that the OCR cleaner will trim. The
    # ``extraction_method=pypdf_extract`` metadata flag flips the
    # ``OCRCleaner.applies_to`` predicate to True. Short non-content
    # lines (single chars, OCR artifacts, page numbers) trip the
    # gibberish-removal pass.
    dirty_content = (
        "Real paragraph with enough length to clear alpha-ratio checks "
        "and survive the gibberish-detection pass cleanly.\n"
        "i\n"  # OCR artifact
        "Hi\n"  # OCR artifact
        "ie f\n"  # OCR noise pattern
        "Page 12\n"  # page-number artifact
        "Another real paragraph with enough content to look legitimate "
        "to the cleaner's heuristics and not trigger any drop.\n"
    )

    # Mock loader_registry to return a single PDF-style document. The
    # ``extraction_method`` flag is the key — without it the OCR cleaner
    # skips the document via ``applies_to``.
    fake_registry = MagicMock()
    fake_registry.load_document.return_value = [
        {
            "content": dirty_content,
            "metadata": {
                "extraction_method": "pypdf_extract",
                "content_type": "application/pdf",
            },
        }
    ]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
    )

    # No-op vision so we don't need real images.
    monkeypatch.setattr(
        indexing_handler,
        "_apply_vision_processing",
        AsyncMock(side_effect=lambda **kw: (kw["documents"], None)),
    )

    # Stub the chunker so we don't need a real ChunkingService — the
    # counter increments fire before chunking.
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

    monkeypatch.setattr(
        indexing_handler,
        "queue_embed_chunks",
        AsyncMock(return_value="tsk_e1"),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    # Real EngineSettings so the normalizer registry boots correctly.
    from chaoscypher_core.settings import EngineSettings, NormalizerSettings, PathSettings

    engine_settings = EngineSettings(
        paths=PathSettings(data_dir=str(tmp_path)),
        normalizer=NormalizerSettings(
            enable_ocr_cleaning=True,
            enable_duplicate_removal=True,
        ),
    )

    settings = MagicMock()
    settings.priorities.background = 50
    settings.data_dir = str(tmp_path)

    await indexing_handler._run_indexing(
        file_id=prepared_source_id,
        file_info={"filename": "dirty.pdf", "filepath": str(tmp_path / "dirty.pdf")},
        filepath=str(tmp_path / "dirty.pdf"),
        analysis_depth="full",
        enable_normalization=True,
        enable_vision=False,
        adapter=sqlite_adapter,
        chunking_service=chunking_service,
        engine_settings=engine_settings,
        settings=settings,
        database_name="default",
    )

    row = sqlite_adapter.get_source(prepared_source_id, "default")
    assert row is not None, "source row should exist after indexing"

    # The OCR cleaner trimmed at least one short / artifact line, so the
    # row-level counter must reflect that. ``> 0`` is the right
    # assertion: the precise count depends on which heuristics fire and
    # is brittle to tune across cleaner internal changes.
    assert row["cleaner_lines_removed"] > 0, (
        "cleaner_lines_removed must be incremented when the OCR cleaner "
        f"trims short / artifact lines (got {row['cleaner_lines_removed']!r})"
    )

    # ``cleaner_chars_removed`` is the conservative byproduct — every
    # dropped line shortens the content, so this must also have moved.
    assert row["cleaner_chars_removed"] > 0, (
        "cleaner_chars_removed must be incremented when the cleaners "
        f"shorten the content (got {row['cleaner_chars_removed']!r})"
    )


@pytest.mark.asyncio
async def test_run_indexing_increments_cleaner_paragraphs_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sqlite_adapter: SqliteAdapter,
    prepared_source_id: str,
) -> None:
    """Duplicate paragraphs in OCR content bump cleaner_paragraphs_deduplicated.

    The OCR cleaner's duplicate-removal pass collapses exact /
    near-duplicate paragraphs (a common artifact of multi-column page
    layouts misread as duplicate flowing text). Verifies that pass'
    drop count reaches the source row.
    """
    # Three paragraphs: P1, P2, then P1 again. The duplicate-removal
    # pass should drop the second occurrence of P1, bumping the
    # paragraphs_deduplicated counter by exactly 1.
    p1 = (
        "First paragraph with enough length to clear alpha-ratio "
        "checks and look like a legitimate body paragraph to the OCR "
        "cleaner's heuristics."
    )
    p2 = (
        "Second paragraph that is structurally distinct from the first "
        "so the duplicate detector keeps both copies through the "
        "fingerprint compare."
    )
    dirty_content = f"{p1}\n\n{p2}\n\n{p1}\n"

    fake_registry = MagicMock()
    fake_registry.load_document.return_value = [
        {
            "content": dirty_content,
            "metadata": {
                "extraction_method": "pypdf_extract",
                "content_type": "application/pdf",
            },
        }
    ]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
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

    monkeypatch.setattr(
        indexing_handler,
        "queue_embed_chunks",
        AsyncMock(return_value="tsk_e2"),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    from chaoscypher_core.settings import EngineSettings, NormalizerSettings, PathSettings

    engine_settings = EngineSettings(
        paths=PathSettings(data_dir=str(tmp_path)),
        normalizer=NormalizerSettings(
            enable_ocr_cleaning=True,
            enable_duplicate_removal=True,
        ),
    )

    settings = MagicMock()
    settings.priorities.background = 50
    settings.data_dir = str(tmp_path)

    await indexing_handler._run_indexing(
        file_id=prepared_source_id,
        file_info={"filename": "dupes.pdf", "filepath": str(tmp_path / "dupes.pdf")},
        filepath=str(tmp_path / "dupes.pdf"),
        analysis_depth="full",
        enable_normalization=True,
        enable_vision=False,
        adapter=sqlite_adapter,
        chunking_service=chunking_service,
        engine_settings=engine_settings,
        settings=settings,
        database_name="default",
    )

    row = sqlite_adapter.get_source(prepared_source_id, "default")
    assert row is not None
    assert row["cleaner_paragraphs_deduplicated"] >= 1, (
        "cleaner_paragraphs_deduplicated must be incremented when the "
        "OCR cleaner collapses duplicate paragraphs (got "
        f"{row['cleaner_paragraphs_deduplicated']!r})"
    )


@pytest.mark.asyncio
async def test_run_indexing_skips_counter_increments_when_no_removals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sqlite_adapter: SqliteAdapter,
    prepared_source_id: str,
) -> None:
    """Clean content leaves every cleaner counter at zero.

    The increment helper rejects ``n < 1`` and the indexing handler
    skips the call entirely when a count is 0. Pristine content must
    therefore land with all three counters still at the post-upload
    default of 0.
    """
    # Skip normalization entirely — no cleaners fire, no counters move.
    fake_registry = MagicMock()
    fake_registry.load_document.return_value = [
        {
            "content": "A nice clean paragraph that needs no cleaning. " * 10,
            "metadata": {"extraction_method": "read_text"},
        }
    ]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
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

    monkeypatch.setattr(
        indexing_handler,
        "queue_embed_chunks",
        AsyncMock(return_value="tsk_e3"),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())

    settings = MagicMock()
    settings.priorities.background = 50
    settings.data_dir = str(tmp_path)

    await indexing_handler._run_indexing(
        file_id=prepared_source_id,
        file_info={"filename": "clean.txt", "filepath": str(tmp_path / "clean.txt")},
        filepath=str(tmp_path / "clean.txt"),
        analysis_depth="full",
        enable_normalization=False,
        enable_vision=False,
        adapter=sqlite_adapter,
        chunking_service=chunking_service,
        engine_settings=MagicMock(),
        settings=settings,
        database_name="default",
    )

    row = sqlite_adapter.get_source(prepared_source_id, "default")
    assert row is not None
    assert row["cleaner_lines_removed"] == 0
    assert row["cleaner_paragraphs_deduplicated"] == 0
    assert row["cleaner_chars_removed"] == 0
