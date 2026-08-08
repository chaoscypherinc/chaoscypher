# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workflow-system reset must be atomic and report accurate counts.

The reset claimed delete+reseed ran "inside the same transaction", but the
seed helpers each called ``session.commit()`` directly — a reseed failing
partway left the DB half-reset (deletes + partial reseed committed). The
helpers no longer commit; the caller's ``adapter.transaction()`` owns the
boundary, so a failing reseed rolls everything back.

``workflow_executions_deleted`` also reported an active-only count while
``clear_all_workflow_executions`` deletes ALL executions — it now reports
the actual deleted rowcount.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, select

import chaoscypher_core.adapters.sqlite.models  # noqa: F401 — register metadata
from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.models import Trigger, Workflow
from chaoscypher_core.database.seed import (
    seed_default_triggers,
    seed_default_workflows,
    seed_system_tools,
)
from chaoscypher_core.services.reset import workflow_system_reset as wsr_mod
from chaoscypher_core.services.reset.workflow_system_reset import (
    WorkflowSystemResetService,
)


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def seeded_db(tmp_path: Path):
    """Create a file-backed DB pre-seeded with tools, workflows, triggers."""
    db_path = tmp_path / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    with Session(engine) as session:
        seed_system_tools(session)
        seed_default_workflows(session, "default")
        seed_default_triggers(session, "default")
        session.commit()
    yield db_path
    evict_engine(db_path)


def _real_adapter(db_path: Path) -> SqliteAdapter:
    """Build a connected SqliteAdapter over the tmp database."""
    adapter = SqliteAdapter(str(db_path), database_name="default")
    adapter.connect()
    return adapter


class TestResetAtomicity:
    """A reseed that fails partway must leave the DB unchanged."""

    def test_failed_reseed_rolls_back_all_changes(self, seeded_db: Path) -> None:
        """seed_default_triggers raising rolls back deletes AND prior reseeds."""
        service = WorkflowSystemResetService("default")

        with (
            patch.object(
                wsr_mod,
                "get_sqlite_adapter",
                side_effect=lambda **_kw: _real_adapter(seeded_db),
            ),
            patch.object(
                wsr_mod,
                "seed_default_triggers",
                side_effect=RuntimeError("seed boom"),
            ),
        ):
            with pytest.raises(RuntimeError, match="seed boom"):
                service.reset_all_components()

        # Everything must still be present — nothing committed mid-reset.
        engine = get_engine(str(seeded_db))
        with Session(engine) as session:
            workflows = list(session.exec(select(Workflow)).all())
            triggers = list(session.exec(select(Trigger)).all())

        assert len(workflows) == 1
        assert workflows[0].id == "system_workflow_generate_embeddings_v1"
        assert len(triggers) == 1
        assert triggers[0].id == "system_trigger_auto_embed_create_v1"

    def test_successful_reset_reseeds_and_persists(self, seeded_db: Path) -> None:
        """The happy path commits at transaction exit and reports reseed counts."""
        service = WorkflowSystemResetService("default")

        with patch.object(
            wsr_mod,
            "get_sqlite_adapter",
            side_effect=lambda **_kw: _real_adapter(seeded_db),
        ):
            result = service.reset_all_components()

        assert result["status"] == "success"
        assert result["workflows_created"] == 1
        assert result["triggers_created"] == 1
        assert result["system_tools_created"] > 0

        # Reseeded rows persist after the service disconnected its session.
        engine = get_engine(str(seeded_db))
        with Session(engine) as session:
            workflows = list(session.exec(select(Workflow)).all())
            triggers = list(session.exec(select(Trigger)).all())
        assert len(workflows) == 1
        assert len(triggers) == 1


class TestDeletedExecutionCount:
    """workflow_executions_deleted reflects what was actually deleted."""

    def test_count_matches_clear_all_rowcount(self) -> None:
        """ALL executions are deleted, so the stat must be the full rowcount."""
        adapter = MagicMock(name="adapter")
        adapter.count_workflows.side_effect = [5, 1]
        adapter.count_user_tools.return_value = 2
        adapter.count_triggers.side_effect = [4, 1]
        adapter.count_system_tools.return_value = 40
        adapter.clear_all_trigger_executions.return_value = 3
        # 7 total executions deleted; an active-only count would see 0.
        adapter.clear_all_workflow_executions.return_value = 7
        adapter.list_active_executions.return_value = []

        with (
            patch.object(wsr_mod, "get_sqlite_adapter", return_value=adapter),
            patch.object(wsr_mod, "seed_system_tools"),
            patch.object(wsr_mod, "seed_default_workflows"),
            patch.object(wsr_mod, "seed_default_triggers"),
        ):
            result = WorkflowSystemResetService("default").reset_all_components()

        assert result["workflow_executions_deleted"] == 7
        assert result["trigger_executions_deleted"] == 3
