# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for race-safe import-with-rename (finding #2)."""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.services.workflows.management.io import (
    WorkflowPortabilityService,
)


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="default")
    a.connect()
    yield a
    a.disconnect()


def _export_payload(name: str) -> dict[str, Any]:
    return {
        "version": "1.0",
        "exported_at": datetime.now(UTC).isoformat() + "Z",
        "workflow": {
            "name": name,
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        },
        "steps": [],
    }


def test_rename_resolves_to_unique_name(adapter: SqliteAdapter) -> None:
    svc = WorkflowPortabilityService(repository=adapter, database_name="default")
    r1 = svc.import_workflow(_export_payload("Flow"), on_duplicate="fail")
    assert r1["workflow_id"]
    r2 = svc.import_workflow(_export_payload("Flow"), on_duplicate="rename")
    wf2 = adapter.get_workflow(r2["workflow_id"])
    assert wf2 is not None
    assert wf2["name"] == "Flow (imported)"


def test_rename_retries_on_integrity_error(
    adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate a concurrent import racing to take `Flow (imported)`."""
    svc = WorkflowPortabilityService(repository=adapter, database_name="default")
    svc.import_workflow(_export_payload("Flow"), on_duplicate="fail")

    # Pre-seed the rename-target to force one IntegrityError.
    adapter.create_workflow(
        {
            "id": "squatter",
            "database_name": "default",
            "name": "Flow (imported)",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )

    r = svc.import_workflow(_export_payload("Flow"), on_duplicate="rename")
    wf = adapter.get_workflow(r["workflow_id"])
    assert wf is not None
    assert wf["name"] == "Flow (imported) (2)"
