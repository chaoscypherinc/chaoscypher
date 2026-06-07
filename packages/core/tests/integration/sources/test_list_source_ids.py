# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""End-to-end coverage for ``adapter.list_source_ids``.

Read-only single-column projection used by the orphan-file cleanup
sweep to diff staging_dir contents against committed source rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _make_source(adapter: SqliteAdapter, source_id: str, *, database_name: str = "default") -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": database_name,
            "filename": f"{source_id}.txt",
            "filepath": f"/tmp/{source_id}.txt",
            "file_type": "text",
            "file_size": 10,
            "content_hash": f"hash-{source_id}",
            "status": "extracting",
        }
    )


def test_returns_empty_set_when_no_sources(integration_adapter: SqliteAdapter) -> None:
    assert integration_adapter.list_source_ids("default") == set()


def test_returns_all_source_ids_for_database(integration_adapter: SqliteAdapter) -> None:
    _make_source(integration_adapter, "src-1")
    _make_source(integration_adapter, "src-2")
    _make_source(integration_adapter, "src-3")

    result = integration_adapter.list_source_ids("default")
    assert result == {"src-1", "src-2", "src-3"}


def test_isolates_results_by_database_name(integration_adapter: SqliteAdapter) -> None:
    """Multi-DB isolation: results scope to the requested database_name."""
    _make_source(integration_adapter, "src-default", database_name="default")
    _make_source(integration_adapter, "src-other", database_name="other")

    assert integration_adapter.list_source_ids("default") == {"src-default"}
    assert integration_adapter.list_source_ids("other") == {"src-other"}
    assert integration_adapter.list_source_ids("unknown") == set()
