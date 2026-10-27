# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A tool that fails answers a fixed reason the client can act on, never the error's text.

``tool_handler`` answered ``"Operation failed"`` for everything, so a client could not tell a
missing template from a busy database. It now answers one fixed sentence and a code per
class of error. The error's own text never reaches the answer: a database error quotes the
SQL and its parameters, which can be a memory's words.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pydantic
import pytest
import structlog
from sqlalchemy.exc import IntegrityError, OperationalError

from chaoscypher_core.adapters.sqlite.models import GraphEdge
from chaoscypher_core.adapters.sqlite.session import get_session
from chaoscypher_core.database.migrations.runner import upgrade_to_head
from chaoscypher_core.exceptions import NotFoundError, ValidationError
from chaoscypher_core.repo_factories import get_graph_repository
from chaoscypher_core.services.workflows.tools.engine.executor import ToolExecutorService
from chaoscypher_core.services.workflows.tools.engine.handlers.decorators import tool_handler
from chaoscypher_core.utils.logging.privacy import private_text
from chaoscypher_core.utils.retry import is_sqlite_lock_error
from chaoscypher_core.utils.tool_failure import TOOL_FAILURES, tool_failure


MEMORY_TEXT = "Pierre Bezukhov moved to Lyon"


class _Model(pydantic.BaseModel):
    count: int


def _pydantic_error() -> pydantic.ValidationError:
    try:
        _Model(count=MEMORY_TEXT)  # type: ignore[arg-type]
    except pydantic.ValidationError as error:
        return error
    raise AssertionError


def _integrity(message: str) -> IntegrityError:
    return IntegrityError(f"INSERT ... ('{MEMORY_TEXT}')", {}, sqlite3.IntegrityError(message))


ERRORS: list[tuple[BaseException, str]] = [
    (
        OperationalError(
            f"UPDATE ('{MEMORY_TEXT}')", {}, sqlite3.OperationalError("database is locked")
        ),
        "DATABASE_BUSY",
    ),
    (_integrity("FOREIGN KEY constraint failed"), "MISSING_REFERENCE"),
    (_integrity("UNIQUE constraint failed: graph_nodes.id"), "DUPLICATE"),
    (ValidationError(MEMORY_TEXT, field="label"), "INVALID_INPUT"),
    (_pydantic_error(), "INVALID_INPUT"),
    (NotFoundError("Node", MEMORY_TEXT), "NOT_FOUND"),
    (RuntimeError(MEMORY_TEXT), "OPERATION_FAILED"),
]


async def _answer(error: BaseException) -> dict[str, Any]:
    @tool_handler("probe_failed")
    async def probe() -> dict[str, Any]:
        raise error

    return await probe()


@pytest.mark.parametrize(("error", "code"), ERRORS, ids=[c for _e, c in ERRORS])
async def test_each_class_of_error_answers_its_fixed_reason(
    error: BaseException, code: str
) -> None:
    answer = await _answer(error)
    assert answer == {"success": False, "error": TOOL_FAILURES[code], "code": code}
    assert MEMORY_TEXT not in str(answer)


def test_every_reason_is_a_fixed_sentence() -> None:
    for code, sentence in TOOL_FAILURES.items():
        assert code.isupper() and sentence.endswith(".")
        assert tool_failure(RuntimeError(sentence))["code"] == "OPERATION_FAILED"


async def test_inside_private_text_the_failure_log_holds_no_text() -> None:
    with structlog.testing.capture_logs() as logs, private_text():
        await _answer(RuntimeError(MEMORY_TEXT))
    [record] = [r for r in logs if r["event"] == "probe_failed"]
    assert record["log_level"] == "error"
    assert record["error_type"] == "RuntimeError"
    assert MEMORY_TEXT not in str(record)


# ----- a failed call leaves the session the next call uses ------------------


@pytest.fixture
def graph(tmp_path: Path) -> Any:
    db = tmp_path / "app.db"
    upgrade_to_head(db)
    session = get_session(str(db))
    yield get_graph_repository(session, "notes")
    session.close()


def _fail_inside_a_flush(graph: Any) -> None:
    graph.session.add(
        GraphEdge(
            id="edge_to_nowhere",
            database_name="notes",
            graph_name="knowledge",
            template_id="no_such_template",
            source_node_id="no_such_node",
            target_node_id="no_such_node",
            label="about",
            properties={},
        )
    )
    graph.session.flush()


class _Handlers:
    def __init__(self, graph: Any) -> None:
        self.graph = graph

    @tool_handler("write_failed")
    async def write(self) -> dict[str, Any]:
        _fail_inside_a_flush(self.graph)
        return {"success": True}


async def test_a_failed_handler_leaves_its_graph_session_usable(graph: Any) -> None:
    answer = await _Handlers(graph).write()
    assert answer["code"] == "MISSING_REFERENCE"
    assert graph.list_templates() == []  # no PendingRollbackError


async def test_a_failed_undecorated_tool_leaves_the_executors_session_usable(graph: Any) -> None:
    executor = ToolExecutorService(graph_repository=graph, search_repository=MagicMock())

    async def failing(**_kwargs: Any) -> dict[str, Any]:
        _fail_inside_a_flush(graph)
        return {"success": True}

    executor._tool_handlers["graphrag_search"] = failing
    answer = await executor.execute_tool("graphrag_search", {})
    assert answer["code"] == "MISSING_REFERENCE"
    assert graph.list_templates() == []


# ----- busy is the database's own error code, never a phrase in the parameters --


def _real_busy_error(tmp_path: Path) -> sqlite3.OperationalError:
    """The error SQLite raises for a write while another connection holds the writer lock."""
    db = tmp_path / "busy.db"
    holder = sqlite3.connect(db)
    holder.execute("CREATE TABLE t (x)")
    holder.commit()
    holder.execute("BEGIN IMMEDIATE")
    other = sqlite3.connect(db, timeout=0)
    try:
        other.execute("INSERT INTO t VALUES (1)")
    except sqlite3.OperationalError as error:
        return error
    finally:
        holder.rollback()
        holder.close()
        other.close()
    raise AssertionError


def test_a_foreign_key_failure_quoting_the_busy_phrase_is_a_missing_reference() -> None:
    error = IntegrityError(
        "INSERT INTO graph_nodes ...",
        {"label": "the database is locked again"},
        sqlite3.IntegrityError("FOREIGN KEY constraint failed"),
    )
    assert tool_failure(error)["code"] == "MISSING_REFERENCE"


def test_an_error_that_only_says_the_busy_phrase_is_not_busy() -> None:
    assert tool_failure(ValueError("database is locked"))["code"] == "OPERATION_FAILED"
    assert not is_sqlite_lock_error(ValueError("database is locked"))


def test_a_real_busy_database_answers_database_busy(tmp_path: Path) -> None:
    raw = _real_busy_error(tmp_path)
    assert is_sqlite_lock_error(raw)
    wrapped = OperationalError("INSERT INTO t VALUES (?)", (MEMORY_TEXT,), raw)
    assert tool_failure(wrapped)["code"] == "DATABASE_BUSY"


# ----- a cleanup that raises never replaces the call's own answer -------------


class _DeadGraph:
    """A repository whose session cannot even roll back (its connection is gone)."""

    def discard_failed_work(self) -> None:
        raise RuntimeError(MEMORY_TEXT)


class _FailsOnADeadConnection:
    def __init__(self) -> None:
        self.graph = _DeadGraph()

    @tool_handler("write_failed")
    async def write(self) -> dict[str, Any]:
        raise _integrity("FOREIGN KEY constraint failed")


async def test_a_handler_whose_cleanup_raises_still_answers_its_own_failure() -> None:
    with structlog.testing.capture_logs() as logs:
        answer = await _FailsOnADeadConnection().write()
    assert answer["code"] == "MISSING_REFERENCE"
    [cleanup] = [r for r in logs if r["event"] == "tool_cleanup_failed"]
    assert cleanup["error_type"] == "RuntimeError"
    assert all(MEMORY_TEXT not in str(r) for r in logs)


async def test_an_executor_whose_cleanup_raises_still_answers_its_own_failure() -> None:
    executor = ToolExecutorService(graph_repository=_DeadGraph(), search_repository=MagicMock())  # type: ignore[arg-type]

    async def failing(**_kwargs: Any) -> dict[str, Any]:
        raise _integrity("UNIQUE constraint failed: graph_nodes.id")

    executor._tool_handlers["graphrag_search"] = failing
    assert (await executor.execute_tool("graphrag_search", {}))["code"] == "DUPLICATE"
