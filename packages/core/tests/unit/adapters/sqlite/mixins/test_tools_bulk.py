# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PR2a Task 9 — bulk tool methods on ToolStorageProtocol."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import (
    SystemTool,
    ToolStatistics,
    UserTool,
)


@pytest.fixture
def adapter(tmp_path: Path) -> Generator[SqliteAdapter]:
    db_dir = tmp_path / "cc-test"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "app.db"
    engine = get_engine(str(db_path))
    SQLModel.metadata.create_all(engine, checkfirst=True)
    a = SqliteAdapter(str(db_path), database_name="test")
    a.connect()
    yield a
    a.disconnect()


def test_count_system_tools(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        for i in range(4):
            adapter.session.add(
                SystemTool(
                    id=f"s{i}",
                    name=f"t{i}",
                    category="test",
                    description=f"d{i}",
                    input_schema={},
                    output_schema={},
                )
            )
    assert adapter.count_system_tools() == 4


def test_count_system_tools_empty(adapter: SqliteAdapter) -> None:
    assert adapter.count_system_tools() == 0


def test_clear_all_system_tools(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        for i in range(3):
            adapter.session.add(
                SystemTool(
                    id=f"s{i}",
                    name=f"t{i}",
                    category="test",
                    description=f"d{i}",
                    input_schema={},
                    output_schema={},
                )
            )
    assert adapter.clear_all_system_tools() == 3


def test_clear_all_tool_statistics(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        adapter.session.add(
            SystemTool(
                id="s1", name="t1", category="t", description="d", input_schema={}, output_schema={}
            )
        )
    with adapter.transaction():
        adapter.session.add(ToolStatistics(tool_type="system", tool_id="s1", total_calls=1))
    assert adapter.clear_all_tool_statistics() == 1


def test_count_user_tools_scoped(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        adapter.session.add(
            SystemTool(
                id="s1", name="t1", category="t", description="d", input_schema={}, output_schema={}
            )
        )
    with adapter.transaction():
        adapter.session.add(
            UserTool(id="u1", database_name="a", system_tool_id="s1", name="n1", configuration={})
        )
        adapter.session.add(
            UserTool(id="u2", database_name="b", system_tool_id="s1", name="n2", configuration={})
        )
    assert adapter.count_user_tools(database_name="a") == 1
    assert adapter.count_user_tools(database_name="b") == 1


def test_delete_all_user_tools_scoped(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        adapter.session.add(
            SystemTool(
                id="s1", name="t1", category="t", description="d", input_schema={}, output_schema={}
            )
        )
    with adapter.transaction():
        adapter.session.add(
            UserTool(id="u1", database_name="a", system_tool_id="s1", name="n1", configuration={})
        )
        adapter.session.add(
            UserTool(id="u2", database_name="a", system_tool_id="s1", name="n2", configuration={})
        )
        adapter.session.add(
            UserTool(id="u3", database_name="b", system_tool_id="s1", name="n3", configuration={})
        )
    assert adapter.delete_all_user_tools(database_name="a") == 2
    assert adapter.count_user_tools(database_name="b") == 1
