# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CCX 3.0 Task 1.3: the indexing handler persists the original pre-chunking
text to ``sources.full_text`` at index time.

The CCX 3.0 exporter emits canonical offset-selector chunks whose
``char_start`` / ``char_end`` reference the ORIGINAL pre-chunking upload
text (the same text fed to ``create_chunks(..., original_text=...)`` so the
Phase-5a offset recompute can re-anchor against it). Before this task that
original text was never persisted to the DB column — ``sources.full_text``
stayed ``NULL`` forever, so the exporter had no canonical text to slice.

These tests drive ``_run_indexing`` against a real ``SqliteAdapter`` (mirror
of ``test_indexing_increments_cleaner_counters.py``) and assert the persisted
source row's ``full_text`` equals the raw loader output, NOT the cleaned /
chunked text.
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
from chaoscypher_core.settings import EngineSettings, PathSettings


@pytest.fixture
def sqlite_adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Per-test file-backed ``SqliteAdapter`` with all tables created.

    Mirrors the fixture in ``test_indexing_increments_cleaner_counters.py``.
    """
    db_path = tmp_path / "test.db"
    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine, checkfirst=True)

    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    try:
        yield adapter
    finally:
        adapter.disconnect()


@pytest.fixture
def prepared_source_id(sqlite_adapter: SqliteAdapter, tmp_path: Path) -> str:
    """Upload a tiny source so the ``update_source`` UPDATE targets a real row."""
    source_id = "src-full-text-1"
    sqlite_adapter.upload_source(
        source_id=source_id,
        database_name="default",
        filename="doc.txt",
        file_content=b"x",
        staging_dir=str(tmp_path),
    )
    return source_id


def _make_chunking_service() -> MagicMock:
    """Stubbed ChunkingService: persistence of full_text is independent of the
    chunker internals, so we only need a well-shaped ChunksResult.
    """
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
    return chunking_service


def _wire_handler_stubs(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    """Patch loader / vision / embedding / event_bus so ``_run_indexing`` runs
    end-to-end without real I/O while returning a single document of *content*.
    """
    fake_registry = MagicMock()
    fake_registry.load_document.return_value = [
        {"content": content, "metadata": {"extraction_method": "read_text"}}
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
    monkeypatch.setattr(
        indexing_handler,
        "queue_embed_chunks",
        AsyncMock(return_value="tsk_ft"),
    )
    monkeypatch.setattr(indexing_handler, "event_bus", MagicMock())


@pytest.mark.asyncio
async def test_run_indexing_persists_full_text_to_source_row(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sqlite_adapter: SqliteAdapter,
    prepared_source_id: str,
) -> None:
    """After indexing, the persisted source's ``full_text`` equals the raw
    pre-chunking loader text.

    This is the canonical text the CCX 3.0 exporter will slice with the
    chunks' offset selectors, so it must be the raw loader output captured
    BEFORE chunking — the same value handed to ``create_chunks`` as
    ``original_text``.
    """
    original = (
        "This is the raw upload text. It has multiple sentences so the "
        "chunker has something to work with. It is long enough to clear the "
        "minimum-indexable-characters guard inside the indexing handler. "
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit."
    )
    _wire_handler_stubs(monkeypatch, original)

    # Real EngineSettings so the default
    # ``chunking.preserve_original_text_for_citations`` (True) is honoured and
    # data_dir points into tmp_path.
    engine_settings = EngineSettings(paths=PathSettings(data_dir=str(tmp_path)))

    settings = MagicMock()
    settings.priorities.background = 50
    settings.data_dir = str(tmp_path)

    # Normalization OFF so full_text is byte-for-byte the raw loader output —
    # the assertion below is exact.
    await indexing_handler._run_indexing(
        file_id=prepared_source_id,
        file_info={"filename": "doc.txt", "filepath": str(tmp_path / "doc.txt")},
        filepath=str(tmp_path / "doc.txt"),
        analysis_depth="full",
        enable_normalization=False,
        enable_vision=False,
        adapter=sqlite_adapter,
        chunking_service=_make_chunking_service(),
        engine_settings=engine_settings,
        settings=settings,
        database_name="default",
    )

    row = sqlite_adapter.get_source(prepared_source_id, "default")
    assert row is not None, "source row should exist after indexing"
    assert row["full_text"] == original, (
        "sources.full_text must equal the raw pre-chunking loader text so the "
        f"CCX exporter can slice it with chunk offsets (got {row['full_text']!r})"
    )


@pytest.mark.asyncio
async def test_run_indexing_skips_full_text_when_preserve_toggle_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sqlite_adapter: SqliteAdapter,
    prepared_source_id: str,
) -> None:
    """When ``preserve_original_text_for_citations`` is disabled there is no
    in-memory original text, so ``full_text`` is never written (stays NULL).

    Guards against persisting a value the operator opted out of capturing.
    """
    original = (
        "Storage-conscious operators can disable original-text capture. "
        "When they do, full_text must stay NULL rather than being populated "
        "from some other source. This sentence pads past the indexable floor."
    )
    _wire_handler_stubs(monkeypatch, original)

    from chaoscypher_core.settings import ChunkingSettings

    engine_settings = EngineSettings(
        paths=PathSettings(data_dir=str(tmp_path)),
        chunking=ChunkingSettings(preserve_original_text_for_citations=False),
    )

    settings = MagicMock()
    settings.priorities.background = 50
    settings.data_dir = str(tmp_path)

    await indexing_handler._run_indexing(
        file_id=prepared_source_id,
        file_info={"filename": "doc.txt", "filepath": str(tmp_path / "doc.txt")},
        filepath=str(tmp_path / "doc.txt"),
        analysis_depth="full",
        enable_normalization=False,
        enable_vision=False,
        adapter=sqlite_adapter,
        chunking_service=_make_chunking_service(),
        engine_settings=engine_settings,
        settings=settings,
        database_name="default",
    )

    row = sqlite_adapter.get_source(prepared_source_id, "default")
    assert row is not None
    assert row["full_text"] is None, (
        "full_text must stay NULL when preserve_original_text_for_citations is "
        f"off (got {row['full_text']!r})"
    )
