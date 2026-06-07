# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR2a Task 14 - SqliteAdapter satisfies SourceRecoveryPorts."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.ports.source_recovery import SourceRecoveryPorts


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def test_sqlite_adapter_satisfies_source_recovery_ports(adapter: SqliteAdapter) -> None:
    """SqliteAdapter must satisfy SourceRecoveryPorts (structural typing).

    If this assertion fails, check ports/source_recovery.py against the
    adapter mixins - a method was renamed or a new Protocol method was
    added without a corresponding mixin implementation.
    """
    assert isinstance(adapter, SourceRecoveryPorts), (
        "SqliteAdapter must satisfy SourceRecoveryPorts. "
        "Missing methods: check the Protocol against the mixins."
    )


def test_all_methods_are_callable(adapter: SqliteAdapter) -> None:
    """All required methods should exist and be callable on the adapter."""
    methods = [
        "get_source",
        "list_sources_by_statuses",
        "get_system_state",
        "increment_source_recovery_attempts",
        "update_source_last_activity",
        "mark_source_exhausted",
        "get_active_extraction_job",
        "list_extraction_tasks_by_status",
        "list_source_entities",
        "list_source_relationships",
        "get_source_commit_payload",
    ]
    for m in methods:
        assert callable(getattr(adapter, m, None)), f"Missing or non-callable: {m}"
