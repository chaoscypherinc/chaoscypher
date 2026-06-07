# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage for non-bulk ToolsMixin methods.

Exercises system-tool, user-tool, and tool-statistics CRUD + filtered
listing against a real file-backed SQLite database (the bulk reset path is
covered separately in ``test_tools_bulk.py``).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.models import SystemTool
from chaoscypher_core.exceptions import NotFoundError


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


def _seed_system_tool(adapter: SqliteAdapter, tool_id: str = "sys-1") -> None:
    with adapter.transaction():
        adapter.session.add(
            SystemTool(
                id=tool_id,
                name=f"name-{tool_id}",
                category="search",
                description="d",
                input_schema={},
                output_schema={},
            )
        )


# ---------------------------------------------------------------------------
# System tools
# ---------------------------------------------------------------------------


def test_create_and_get_system_tool(adapter: SqliteAdapter) -> None:
    created = adapter.create_system_tool(
        {
            "id": "sys-1",
            "name": "Search",
            "category": "search",
            "description": "desc",
            "input_schema": {"a": 1},
            "output_schema": {"b": 2},
        }
    )
    assert created["id"] == "sys-1"
    assert created["input_schema"] == {"a": 1}

    fetched = adapter.get_system_tool("sys-1")
    assert fetched is not None
    assert fetched["name"] == "Search"


def test_get_system_tool_not_found(adapter: SqliteAdapter) -> None:
    assert adapter.get_system_tool("missing") is None


def test_list_system_tools_no_filters(adapter: SqliteAdapter) -> None:
    _seed_system_tool(adapter, "sys-1")
    _seed_system_tool(adapter, "sys-2")
    rows = adapter.list_system_tools()
    assert {r["id"] for r in rows} == {"sys-1", "sys-2"}


def test_list_system_tools_empty(adapter: SqliteAdapter) -> None:
    assert adapter.list_system_tools() == []


def test_list_system_tools_filter_by_category(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        adapter.session.add(
            SystemTool(
                id="a",
                name="a",
                category="search",
                description="d",
                input_schema={},
                output_schema={},
            )
        )
        adapter.session.add(
            SystemTool(
                id="b",
                name="b",
                category="export",
                description="d",
                input_schema={},
                output_schema={},
            )
        )
    rows = adapter.list_system_tools(category="export")
    assert [r["id"] for r in rows] == ["b"]


def test_list_system_tools_filter_by_is_active(adapter: SqliteAdapter) -> None:
    with adapter.transaction():
        adapter.session.add(
            SystemTool(
                id="a",
                name="a",
                category="search",
                description="d",
                input_schema={},
                output_schema={},
                is_active=True,
            )
        )
        adapter.session.add(
            SystemTool(
                id="b",
                name="b",
                category="search",
                description="d",
                input_schema={},
                output_schema={},
                is_active=False,
            )
        )
    active = adapter.list_system_tools(is_active=True)
    assert [r["id"] for r in active] == ["a"]
    inactive = adapter.list_system_tools(is_active=False)
    assert [r["id"] for r in inactive] == ["b"]


def test_update_system_tool(adapter: SqliteAdapter) -> None:
    _seed_system_tool(adapter)
    updated = adapter.update_system_tool("sys-1", {"description": "new desc", "version": "2.0.0"})
    assert updated["description"] == "new desc"
    assert updated["version"] == "2.0.0"
    assert updated["updated_at"] is not None


def test_update_system_tool_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.update_system_tool("missing", {"description": "x"})


# ---------------------------------------------------------------------------
# User tools
# ---------------------------------------------------------------------------


def test_create_and_get_user_tool(adapter: SqliteAdapter) -> None:
    _seed_system_tool(adapter)
    created = adapter.create_user_tool(
        {
            "id": "u-1",
            "database_name": "test",
            "system_tool_id": "sys-1",
            "name": "MyTool",
            "configuration": {"k": "v"},
        }
    )
    assert created["id"] == "u-1"
    fetched = adapter.get_user_tool("u-1", "test")
    assert fetched is not None
    assert fetched["name"] == "MyTool"


def test_get_user_tool_wrong_database_returns_none(adapter: SqliteAdapter) -> None:
    _seed_system_tool(adapter)
    adapter.create_user_tool(
        {
            "id": "u-1",
            "database_name": "test",
            "system_tool_id": "sys-1",
            "name": "MyTool",
            "configuration": {},
        }
    )
    # Row exists but database scope mismatches.
    assert adapter.get_user_tool("u-1", "other") is None


def test_get_user_tool_missing_returns_none(adapter: SqliteAdapter) -> None:
    assert adapter.get_user_tool("missing", "test") is None


def test_list_user_tools_scoped_and_filtered(adapter: SqliteAdapter) -> None:
    _seed_system_tool(adapter, "sys-1")
    _seed_system_tool(adapter, "sys-2")
    with adapter.transaction():
        # database test
        from chaoscypher_core.adapters.sqlite.models import UserTool

        adapter.session.add(
            UserTool(
                id="u-1",
                database_name="test",
                system_tool_id="sys-1",
                name="n1",
                configuration={},
                user_id=1,
                is_active=True,
            )
        )
        adapter.session.add(
            UserTool(
                id="u-2",
                database_name="test",
                system_tool_id="sys-2",
                name="n2",
                configuration={},
                user_id=2,
                is_active=False,
            )
        )
        # different database — must be excluded by database_name filter
        adapter.session.add(
            UserTool(
                id="u-3",
                database_name="other",
                system_tool_id="sys-1",
                name="n3",
                configuration={},
                user_id=1,
                is_active=True,
            )
        )

    all_test = adapter.list_user_tools("test")
    assert {r["id"] for r in all_test} == {"u-1", "u-2"}

    by_user = adapter.list_user_tools("test", user_id=1)
    assert [r["id"] for r in by_user] == ["u-1"]

    by_system_tool = adapter.list_user_tools("test", system_tool_id="sys-2")
    assert [r["id"] for r in by_system_tool] == ["u-2"]

    active = adapter.list_user_tools("test", is_active=True)
    assert [r["id"] for r in active] == ["u-1"]


def test_list_user_tools_empty(adapter: SqliteAdapter) -> None:
    assert adapter.list_user_tools("test") == []


def test_update_user_tool(adapter: SqliteAdapter) -> None:
    _seed_system_tool(adapter)
    adapter.create_user_tool(
        {
            "id": "u-1",
            "database_name": "test",
            "system_tool_id": "sys-1",
            "name": "Old",
            "configuration": {},
        }
    )
    updated = adapter.update_user_tool("u-1", {"name": "New", "description": "added"})
    assert updated["name"] == "New"
    assert updated["description"] == "added"
    assert updated["updated_at"] is not None


def test_update_user_tool_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.update_user_tool("missing", {"name": "x"})


def test_delete_user_tool(adapter: SqliteAdapter) -> None:
    _seed_system_tool(adapter)
    adapter.create_user_tool(
        {
            "id": "u-1",
            "database_name": "test",
            "system_tool_id": "sys-1",
            "name": "n",
            "configuration": {},
        }
    )
    assert adapter.delete_user_tool("u-1") is True
    assert adapter.get_user_tool("u-1", "test") is None


def test_delete_user_tool_missing_returns_false(adapter: SqliteAdapter) -> None:
    assert adapter.delete_user_tool("missing") is False


# ---------------------------------------------------------------------------
# Tool statistics
# ---------------------------------------------------------------------------


def test_create_and_get_tool_statistics(adapter: SqliteAdapter) -> None:
    _seed_system_tool(adapter)
    created = adapter.create_tool_statistics(
        {"tool_type": "system", "tool_id": "sys-1", "total_calls": 3}
    )
    assert created["total_calls"] == 3
    fetched = adapter.get_tool_statistics("system", "sys-1")
    assert fetched is not None
    assert fetched["tool_id"] == "sys-1"


def test_get_tool_statistics_not_found(adapter: SqliteAdapter) -> None:
    assert adapter.get_tool_statistics("system", "missing") is None


def test_update_tool_statistics(adapter: SqliteAdapter) -> None:
    _seed_system_tool(adapter)
    adapter.create_tool_statistics({"tool_type": "system", "tool_id": "sys-1", "total_calls": 1})
    updated = adapter.update_tool_statistics(
        "system", "sys-1", {"total_calls": 10, "successful_calls": 9}
    )
    assert updated["total_calls"] == 10
    assert updated["successful_calls"] == 9
    assert updated["updated_at"] is not None


def test_update_tool_statistics_missing_raises(adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError):
        adapter.update_tool_statistics("system", "missing", {"total_calls": 1})


def test_list_tool_statistics(adapter: SqliteAdapter) -> None:
    _seed_system_tool(adapter, "sys-1")
    _seed_system_tool(adapter, "sys-2")
    adapter.create_tool_statistics({"tool_type": "system", "tool_id": "sys-1", "total_calls": 1})
    adapter.create_tool_statistics({"tool_type": "system", "tool_id": "sys-2", "total_calls": 2})
    rows = adapter.list_tool_statistics()
    assert {r["tool_id"] for r in rows} == {"sys-1", "sys-2"}


def test_list_tool_statistics_empty(adapter: SqliteAdapter) -> None:
    assert adapter.list_tool_statistics() == []
