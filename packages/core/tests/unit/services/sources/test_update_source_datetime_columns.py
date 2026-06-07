# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for ``SourceService.update_source`` field handling.

Disabling a source (PATCH ``enabled=false``) returned a 500 when the
source had populated datetime columns that are NOT listed in the storage
adapter's ``skip_fields`` denylist (``last_activity_at``,
``vector_indexed_at``, ``paused_at``).

Root cause: ``update_source`` read the full source dict via ``get_source()``
— which serializes datetime columns to ISO strings — then forwarded the
*entire* dict back to the storage layer. SQLite's ``DateTime`` column type
rejects ISO strings, raising ``TypeError: SQLite DateTime type only accepts
Python datetime and date objects as input``.

The service must forward only the fields it was actually asked to change.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from chaoscypher_core.services.graph.management.source import SourceService


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _make_service(adapter: SqliteAdapter) -> SourceService:
    return SourceService(repository=adapter, database_name="default")


def test_toggle_enabled_with_populated_datetime_columns(
    in_memory_adapter: SqliteAdapter,
) -> None:
    """Disabling a source whose datetime columns are populated must succeed.

    Reproduces the production 500: ``last_activity_at`` and
    ``vector_indexed_at`` come back from ``get_source()`` as ISO strings and
    previously crashed the UPDATE.
    """
    now = datetime.datetime(2026, 5, 27, 21, 22, 4, tzinfo=datetime.UTC)
    in_memory_adapter.create_source(
        {
            "id": "src-toggle-1",
            "database_name": "default",
            "filename": "doc.pdf",
            "filepath": "/tmp/doc.pdf",
            "status": "committed",
            "enabled": True,
            "last_activity_at": now,
            "vector_indexed_at": now,
        }
    )

    service = _make_service(in_memory_adapter)

    updated = service.update_source("src-toggle-1", enabled=False)

    assert updated is not None
    assert updated["enabled"] is False
    # The pre-existing datetime columns must be preserved, not wiped.
    assert updated["last_activity_at"] is not None
    assert updated["vector_indexed_at"] is not None


def test_update_processing_status_is_applied(
    in_memory_adapter: SqliteAdapter,
) -> None:
    """A processing_status update must actually persist.

    ``get_source()`` serializes the lifecycle column under the model field
    name ``status`` (DB column ``processing_status``). The service must map
    the ``processing_status`` argument onto that field; otherwise the change
    is silently dropped by the storage layer's ``hasattr`` guard.
    """
    in_memory_adapter.create_source(
        {
            "id": "src-status-1",
            "database_name": "default",
            "filename": "doc.pdf",
            "filepath": "/tmp/doc.pdf",
            "status": "indexing",
        }
    )

    service = _make_service(in_memory_adapter)

    updated = service.update_source("src-status-1", processing_status="committed")

    assert updated is not None
    assert updated["status"] == "committed"


def test_update_missing_source_returns_none(
    in_memory_adapter: SqliteAdapter,
) -> None:
    """Updating a non-existent source returns None (drives the API 404)."""
    service = _make_service(in_memory_adapter)
    assert service.update_source("does-not-exist", enabled=False) is None
