# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Verify list_chunks(...) does not return content by default.

Covers:
- SourcesChunksMixin.list_chunks has include_content=False as default
- StorageChunksProtocol.list_chunks has include_content=False as default
- Runtime: list_chunks() excludes content; list_chunks(include_content=True) includes it
"""

from __future__ import annotations

import inspect
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.mixins.sources_chunks import SourceChunksMixin
from chaoscypher_core.ports.storage_chunks import ChunkStorageProtocol


# ---------------------------------------------------------------------------
# Signature-level tests (fast, no DB required)
# ---------------------------------------------------------------------------


def test_mixin_list_chunks_default_is_false() -> None:
    """SourceChunksMixin.list_chunks has include_content=False by default."""
    sig = inspect.signature(SourceChunksMixin.list_chunks)
    assert sig.parameters["include_content"].default is False


def test_protocol_list_chunks_default_is_false() -> None:
    """ChunkStorageProtocol.list_chunks has include_content=False by default."""
    sig = inspect.signature(ChunkStorageProtocol.list_chunks)
    assert sig.parameters["include_content"].default is False


# ---------------------------------------------------------------------------
# Runtime tests (file-backed adapter)
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    """Per-test file-backed SqliteAdapter."""
    db_dir = tmp_path / "cc-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"

    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)

    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


def _seed_source_and_chunk(adapter: SqliteAdapter) -> None:
    """Insert one source and one chunk with known content."""
    adapter.create_source(
        {
            "id": "src-1",
            "database_name": "default",
            "filename": "test.txt",
            "filepath": "/tmp/test.txt",
            "file_type": "txt",
            "file_size": 100,
            "content_hash": "abc123",
            "status": "indexed",
        }
    )
    adapter.create_chunk(
        {
            "id": "chunk-1",
            "database_name": "default",
            "source_id": "src-1",
            "chunk_index": 0,
            "content": "hello world chunk content",
            "status": "indexed",
        }
    )


def test_list_chunks_default_excludes_content(adapter: SqliteAdapter) -> None:
    """Default call (include_content not passed) returns chunks without content."""
    _seed_source_and_chunk(adapter)
    chunks = adapter.list_chunks(database_name="default")
    assert len(chunks) == 1
    # content was not loaded — value is None (load_only leaves it unset)
    assert chunks[0].get("content") in (None, "")


def test_list_chunks_with_include_content_returns_content(adapter: SqliteAdapter) -> None:
    """Explicit include_content=True returns the chunk text."""
    _seed_source_and_chunk(adapter)
    chunks = adapter.list_chunks(database_name="default", include_content=True)
    assert len(chunks) == 1
    assert chunks[0].get("content") == "hello world chunk content"
