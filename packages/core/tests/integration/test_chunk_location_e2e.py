# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""End-to-end chunk-location integration test (2026-05-18).

Drives ``ChunkingService.create_chunks(..., location_index=...)`` against
a real ``SqliteAdapter``, persists chunks, queries them back through the
same code path the Cortex/CLI features use, and asserts ``page_number``
landed correctly. This closes the loop for the user-visible feature:
chat citations on PDF sources will display "Page N" in the tooltip.

No real PDF / EPUB / DOCX is needed — those are tested at the loader
boundary (test_pdf_loader_hardening, test_epub_loader, test_docx_loader).
This test verifies the *plumbing* between chunker output and DB row.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.settings import ChunkingSettings, EngineSettings
from chaoscypher_core.utils.chunk import ChunkingService, LocationBoundary, LocationIndex


@pytest.fixture
def sqlite_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_dir = tmp_path / "chaoscypher-chunk-location-e2e"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        yield adapter
    finally:
        adapter.disconnect()


def _seed_source_for_chunks(adapter: SqliteAdapter, source_id: str, *, staging_dir: str) -> None:
    """Insert a minimal SourceRow so chunks have a foreign-key target."""
    adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename="three_pages.pdf",
        file_content=b"%PDF-1.4 stub",
        staging_dir=staging_dir,
    )


@pytest.mark.asyncio
async def test_chunks_land_with_page_number_when_location_index_provided(
    sqlite_adapter: SqliteAdapter, tmp_path: Path
) -> None:
    """End-to-end proof: PDF-shaped location_index → chunker → store_chunks
    → get_chunks_by_source returns rows whose page_number reflects the
    boundary lookup.
    """
    source_id = "src_e2e_chunk_location"
    _seed_source_for_chunks(sqlite_adapter, source_id, staging_dir=str(tmp_path / "staging"))

    # Build text long enough to produce multiple chunks across three pages.
    page1 = "Sentence one. Sentence two. Sentence three. " * 35  # ~1575 chars
    page2 = "Second page sentence. " * 60  # ~1320 chars
    page3 = "Third page sentence. " * 60  # ~1260 chars
    separator = "\n\n"
    full_text = page1 + separator + page2 + separator + page3

    page1_end = len(page1)
    page2_start = page1_end + len(separator)
    page2_end = page2_start + len(page2)
    page3_start = page2_end + len(separator)
    page3_end = page3_start + len(page3)

    location_index: LocationIndex = [
        _make_page(0, page1_end, 1),
        _make_page(page2_start, page2_end, 2),
        _make_page(page3_start, page3_end, 3),
    ]

    service = ChunkingService(
        settings=EngineSettings(chunking=ChunkingSettings()),
        repository=sqlite_adapter,
    )
    result = await service.create_chunks(
        full_text=full_text,
        source_id=source_id,
        store=True,
        location_index=location_index,
    )

    # Sanity: at least one chunk should land on each page.
    pages_in_result = {c["page_number"] for c in result.small_chunks}
    assert pages_in_result == {1, 2, 3}, (
        f"Expected chunks on pages {{1, 2, 3}}, got {pages_in_result}"
    )

    # The user-facing path: query the stored chunks via the public
    # repository method that Cortex/CLI use for citation lookups.
    chunks, total = sqlite_adapter.get_chunks_by_source(
        source_id=source_id,
        page=1,
        page_size=200,
    )
    assert total == len(result.small_chunks), (
        f"Stored chunk count mismatch: {total} vs {len(result.small_chunks)}"
    )
    pages_in_db = {c["page_number"] for c in chunks}
    assert pages_in_db == {1, 2, 3}, (
        f"page_number did not round-trip through storage: got {pages_in_db}"
    )


def _make_page(start: int, end: int, page: int) -> LocationBoundary:
    return {
        "start_char": start,
        "end_char": end,
        "page_number": page,
        "section": None,
    }
