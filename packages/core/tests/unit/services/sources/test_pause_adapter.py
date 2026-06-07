# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for pause/resume adapter methods.

Covers set_source_paused, bulk_set_sources_paused, get_system_state,
and set_system_paused. Uses the shared `in_memory_adapter` fixture
defined in this directory's conftest.
"""


def _seed_source(adapter, source_id: str) -> None:
    """Create a minimal non-terminal source for pause tests."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.pdf",
            "filepath": f"/tmp/{source_id}.pdf",
            "file_type": "pdf",
            "file_size": 100,
            "content_hash": f"hash-{source_id}",
            "status": "pending",
        }
    )


def test_set_source_paused_updates_fields(in_memory_adapter) -> None:
    _seed_source(in_memory_adapter, "s-1")

    in_memory_adapter.set_source_paused(
        source_id="s-1",
        database_name="default",
        is_paused=True,
        reason="manual test",
    )

    source = in_memory_adapter.get_source(source_id="s-1", database_name="default")
    assert source is not None
    assert source["is_paused"] is True
    assert source["paused_reason"] == "manual test"
    assert source["paused_at"] is not None


def test_set_source_paused_false_clears_metadata(in_memory_adapter) -> None:
    _seed_source(in_memory_adapter, "s-1")

    in_memory_adapter.set_source_paused(
        source_id="s-1",
        database_name="default",
        is_paused=True,
        reason="x",
    )
    in_memory_adapter.set_source_paused(
        source_id="s-1",
        database_name="default",
        is_paused=False,
    )

    source = in_memory_adapter.get_source(source_id="s-1", database_name="default")
    assert source is not None
    assert source["is_paused"] is False
    assert source["paused_at"] is None
    assert source["paused_reason"] is None


def test_bulk_set_sources_paused(in_memory_adapter) -> None:
    for i in range(3):
        _seed_source(in_memory_adapter, f"s-{i}")

    count = in_memory_adapter.bulk_set_sources_paused(
        source_ids=["s-0", "s-1", "s-2"],
        database_name="default",
        is_paused=True,
        reason="maintenance",
    )
    assert count == 3

    for i in range(3):
        source = in_memory_adapter.get_source(source_id=f"s-{i}", database_name="default")
        assert source is not None
        assert source["is_paused"] is True
        assert source["paused_reason"] == "maintenance"


def test_bulk_set_sources_paused_with_empty_list_returns_zero(
    in_memory_adapter,
) -> None:
    count = in_memory_adapter.bulk_set_sources_paused(
        source_ids=[],
        database_name="default",
        is_paused=True,
        reason=None,
    )
    assert count == 0


def test_get_system_state_creates_default_if_absent(in_memory_adapter) -> None:
    state = in_memory_adapter.get_system_state()
    assert state["id"] == 1
    assert state["processing_paused"] is False
    assert state["processing_paused_at"] is None
    assert state["processing_paused_reason"] is None


def test_set_system_paused(in_memory_adapter) -> None:
    in_memory_adapter.set_system_paused(is_paused=True, reason="deploy")

    state = in_memory_adapter.get_system_state()
    assert state["processing_paused"] is True
    assert state["processing_paused_reason"] == "deploy"
    assert state["processing_paused_at"] is not None


def test_set_system_paused_false_clears_metadata(in_memory_adapter) -> None:
    in_memory_adapter.set_system_paused(is_paused=True, reason="deploy")
    in_memory_adapter.set_system_paused(is_paused=False)

    state = in_memory_adapter.get_system_state()
    assert state["processing_paused"] is False
    assert state["processing_paused_at"] is None
    assert state["processing_paused_reason"] is None
