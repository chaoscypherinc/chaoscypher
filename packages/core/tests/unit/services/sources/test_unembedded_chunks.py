# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for list_unembedded_chunks / mark_chunks_embedded adapter methods."""

import datetime
import uuid


def _seed_source(adapter, source_id: str) -> None:
    """Seed a minimal SourceRow so chunks can FK-reference it."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": "default",
            "filename": f"test-{source_id}.txt",
            "filepath": f"/tmp/test-{source_id}.txt",
            "file_type": "text/plain",
            "file_size": 100,
            "content_hash": "deadbeef",
            "status": "indexing",
        }
    )


def _seed_chunks(adapter, source_id: str, count: int, embedded_count: int) -> None:
    """Seed `count` chunks for `source_id`, with the first `embedded_count` marked embedded."""
    _seed_source(adapter, source_id)
    now = datetime.datetime(2026, 4, 11, 12, 0, 0, tzinfo=datetime.UTC)
    for i in range(count):
        chunk_data = {
            "id": f"{source_id}-chunk-{i}",
            "database_name": "default",
            "source_id": source_id,
            "chunk_index": i,
            "content": f"chunk content {i}",
            "status": "indexed",
            "embedded_at": now if i < embedded_count else None,
        }
        adapter.create_chunk(chunk_data)


def test_list_unembedded_chunks_returns_only_unembedded(in_memory_adapter) -> None:
    """3 chunks, 1 embedded, 2 unembedded → returns only the 2."""
    source_id = str(uuid.uuid4())
    _seed_chunks(in_memory_adapter, source_id, count=3, embedded_count=1)

    unembedded = in_memory_adapter.list_unembedded_chunks(
        source_id=source_id, database_name="default"
    )
    assert len(unembedded) == 2
    assert {c["chunk_index"] for c in unembedded} == {1, 2}


def test_list_unembedded_chunks_returns_sorted_by_chunk_index(
    in_memory_adapter,
) -> None:
    source_id = str(uuid.uuid4())
    _seed_chunks(in_memory_adapter, source_id, count=5, embedded_count=0)

    unembedded = in_memory_adapter.list_unembedded_chunks(
        source_id=source_id, database_name="default"
    )
    indices = [c["chunk_index"] for c in unembedded]
    assert indices == sorted(indices)


def test_list_unembedded_chunks_empty_when_all_done(in_memory_adapter) -> None:
    source_id = str(uuid.uuid4())
    _seed_chunks(in_memory_adapter, source_id, count=3, embedded_count=3)

    result = in_memory_adapter.list_unembedded_chunks(source_id=source_id, database_name="default")
    assert result == []


def test_mark_chunks_embedded_updates_timestamp(in_memory_adapter) -> None:
    source_id = str(uuid.uuid4())
    _seed_chunks(in_memory_adapter, source_id, count=3, embedded_count=0)
    now = datetime.datetime(2026, 4, 11, 13, 0, 0, tzinfo=datetime.UTC)

    count = in_memory_adapter.mark_chunks_embedded(
        chunk_ids=[f"{source_id}-chunk-0", f"{source_id}-chunk-1"],
        embedded_at=now,
        database_name="default",
    )
    assert count == 2

    unembedded = in_memory_adapter.list_unembedded_chunks(
        source_id=source_id, database_name="default"
    )
    assert len(unembedded) == 1
    assert unembedded[0]["chunk_index"] == 2


def test_list_unembedded_chunks_respects_limit(in_memory_adapter) -> None:
    """Keyset pagination: limit=2 returns only the first 2 (by chunk_index)."""
    source_id = str(uuid.uuid4())
    _seed_chunks(in_memory_adapter, source_id, count=5, embedded_count=0)

    wave = in_memory_adapter.list_unembedded_chunks(
        source_id=source_id, database_name="default", limit=2
    )
    assert [c["chunk_index"] for c in wave] == [0, 1]


def test_list_unembedded_chunks_after_chunk_index_is_keyset_cursor(
    in_memory_adapter,
) -> None:
    """after_chunk_index advances the cursor: rows with chunk_index > N only."""
    source_id = str(uuid.uuid4())
    _seed_chunks(in_memory_adapter, source_id, count=5, embedded_count=0)

    wave = in_memory_adapter.list_unembedded_chunks(
        source_id=source_id, database_name="default", after_chunk_index=1, limit=2
    )
    assert [c["chunk_index"] for c in wave] == [2, 3]


def test_list_unembedded_chunks_waves_cover_all_rows_exactly_once(
    in_memory_adapter,
) -> None:
    """Looping waves with the keyset cursor visits every unembedded chunk
    exactly once and terminates (the embedding-handler wave contract).
    """
    source_id = str(uuid.uuid4())
    _seed_chunks(in_memory_adapter, source_id, count=5, embedded_count=0)

    seen: list[int] = []
    cursor = None
    while True:
        wave = in_memory_adapter.list_unembedded_chunks(
            source_id=source_id,
            database_name="default",
            after_chunk_index=cursor,
            limit=2,
        )
        if not wave:
            break
        seen.extend(c["chunk_index"] for c in wave)
        cursor = wave[-1]["chunk_index"]

    assert seen == [0, 1, 2, 3, 4]


def test_list_unembedded_chunks_default_returns_all(in_memory_adapter) -> None:
    """Backward compatible: no limit / no cursor returns every unembedded row."""
    source_id = str(uuid.uuid4())
    _seed_chunks(in_memory_adapter, source_id, count=4, embedded_count=0)

    result = in_memory_adapter.list_unembedded_chunks(source_id=source_id, database_name="default")
    assert [c["chunk_index"] for c in result] == [0, 1, 2, 3]


def test_count_unembedded_chunks(in_memory_adapter) -> None:
    """count_unembedded_chunks returns the number of embedded_at IS NULL rows."""
    source_id = str(uuid.uuid4())
    _seed_chunks(in_memory_adapter, source_id, count=5, embedded_count=2)

    assert (
        in_memory_adapter.count_unembedded_chunks(source_id=source_id, database_name="default") == 3
    )


def test_count_unembedded_chunks_zero_when_all_done(in_memory_adapter) -> None:
    source_id = str(uuid.uuid4())
    _seed_chunks(in_memory_adapter, source_id, count=3, embedded_count=3)

    assert (
        in_memory_adapter.count_unembedded_chunks(source_id=source_id, database_name="default") == 0
    )


def test_mark_chunks_embedded_with_empty_list_is_noop(in_memory_adapter) -> None:
    source_id = str(uuid.uuid4())
    _seed_chunks(in_memory_adapter, source_id, count=2, embedded_count=0)
    now = datetime.datetime(2026, 4, 11, 13, 0, 0, tzinfo=datetime.UTC)

    count = in_memory_adapter.mark_chunks_embedded(
        chunk_ids=[], embedded_at=now, database_name="default"
    )
    assert count == 0

    unembedded = in_memory_adapter.list_unembedded_chunks(
        source_id=source_id, database_name="default"
    )
    assert len(unembedded) == 2  # Nothing was marked
