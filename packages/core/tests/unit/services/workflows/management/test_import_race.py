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
    adapter: SqliteAdapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent import takes `Flow (imported)` after the name was chosen.

    The rival commits through its own connection between the name check and the
    insert, so the first insert trips the real unique constraint; the retry must
    re-read the names and land on `Flow (imported) (2)` on the second attempt.
    """
    svc = WorkflowPortabilityService(repository=adapter, database_name="default")
    svc.import_workflow(_export_payload("Flow"), on_duplicate="fail")

    real_create = adapter.create_workflow_safe
    attempted: list[str] = []

    def racing_create(*, workflow: dict[str, Any]) -> dict[str, Any]:
        attempted.append(workflow["name"])
        if len(attempted) == 1:
            rival = SqliteAdapter(str(tmp_path / "app.db"), database_name="default")
            rival.connect()
            try:
                rival.create_workflow(
                    {
                        "id": "rival",
                        "database_name": "default",
                        "name": workflow["name"],
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "created_at": datetime.now(UTC),
                        "updated_at": datetime.now(UTC),
                    }
                )
            finally:
                rival.disconnect()
        return real_create(workflow=workflow)

    monkeypatch.setattr(adapter, "create_workflow_safe", racing_create)

    r = svc.import_workflow(_export_payload("Flow"), on_duplicate="rename")
    wf = adapter.get_workflow(r["workflow_id"])
    assert wf is not None
    assert wf["name"] == "Flow (imported) (2)"
    assert attempted == ["Flow (imported)", "Flow (imported) (2)"]
