# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The server's ``call_tool`` guard: what a tool call answers when its error escapes the tool.

One busy sentence, ``TOOL_FAILURES["DATABASE_BUSY"]``, read by the guard and by
``tool_failure`` alike; each tool family keeps its shape: the memory and site tools answer
plain text lines (Tsukumo reads them), every other tool answers JSON.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog.testing
from sqlalchemy.exc import OperationalError

import chaoscypher_core
from chaoscypher_core.mcp import memory
from chaoscypher_core.mcp import server as mcp_server
from chaoscypher_core.utils import tool_failure as tool_failure_module
from chaoscypher_core.utils.tool_failure import TOOL_FAILURES, tool_failure
from tests.unit.mcp.memory_world import build_world, call_raw


MARKER = "Natasha-Rostova-7f3e"
"""A memory's words, carried by the escaping error's message and parameters."""

BUSY_SENTENCE = "Not done: the database is busy with another process's write. Try again."
NOT_STORED_SENTENCE = "Not stored: the database is busy. Try again."
FAILED_SENTENCE = "Not done: the operation failed. The server's log has the error's type."


def _lock_error() -> OperationalError:
    return OperationalError(
        f"UPDATE sources SET full_text = '{MARKER}'",
        {"full_text": MARKER},
        sqlite3.OperationalError("database is locked"),
    )


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    w = build_world(tmp_path, monkeypatch)
    yield w
    w.engine.close()


def _raising(error: BaseException) -> Any:
    async def handler(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    return handler


def _raising_sync(error: BaseException) -> Any:
    def handler(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    return handler


# ----- one busy sentence ------------------------------------------------------


def test_the_busy_sentence_is_written_once_in_the_core_package() -> None:
    root = Path(chaoscypher_core.__file__).parent
    holders = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "the database is busy with another process's write" in path.read_text("utf-8")
    )
    assert holders == ["utils/tool_failure.py"]
    assert not hasattr(mcp_server, "DATABASE_BUSY")


async def test_the_guard_and_tool_failure_read_the_one_busy_constant(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(TOOL_FAILURES, "DATABASE_BUSY", "the one busy sentence")
    monkeypatch.setattr(memory, "handle_list_memories", _raising(_lock_error()))
    assert await call_raw(world.server, "list_memories", {}) == ("the one busy sentence", True)
    assert tool_failure(_lock_error())["error"] == "the one busy sentence"
    assert tool_failure_module.TOOL_FAILURES is TOOL_FAILURES


def test_the_busy_sentence_reads_word_for_word() -> None:
    assert TOOL_FAILURES["DATABASE_BUSY"] == BUSY_SENTENCE
    assert memory.NOT_STORED == NOT_STORED_SENTENCE


# ----- a busy database, by family ----------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args", "handler"),
    [
        ("list_memories", {}, "handle_list_memories"),
        ("recall", {"query": "Lyon"}, "handle_recall"),
        ("digest", {}, "handle_digest"),
    ],
)
async def test_a_memory_tool_on_a_busy_database_answers_the_busy_line(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    args: dict[str, Any],
    handler: str,
) -> None:
    monkeypatch.setattr(memory, handler, _raising(_lock_error()))
    assert await call_raw(world.server, tool, args) == (BUSY_SENTENCE, True)


async def test_a_site_tool_on_a_busy_database_answers_the_busy_line(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_server, "handle_site_tool", _raising(_lock_error()))
    answer = await call_raw(world.server, "add_site", {"url": "https://example.com/"})
    assert answer == (BUSY_SENTENCE, True)


async def test_a_json_tool_on_a_busy_database_answers_the_busy_failure_as_json(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(world.storage, "list_sources", _raising_sync(_lock_error()))
    text, is_error = await call_raw(world.server, "get_document_status", {})
    assert is_error
    assert json.loads(text) == {
        "success": False,
        "error": BUSY_SENTENCE,
        "code": "DATABASE_BUSY",
    }


async def test_a_graph_tool_on_a_busy_database_answers_the_busy_failure_as_json(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The graph tools' own catch (``tool_handler``) reads the same sentence."""
    monkeypatch.setattr(world.graph, "list_templates", _raising_sync(_lock_error()))
    text, is_error = await call_raw(world.server, "list_templates", {})
    assert is_error
    assert json.loads(text) == {
        "success": False,
        "error": BUSY_SENTENCE,
        "code": "DATABASE_BUSY",
    }
    assert MARKER not in text


# ----- any other escaping error: the fixed failure, never its text --------------


class _LeakyError(Exception):
    """An error whose message carries a memory's words."""


async def _call_logged(
    world: SimpleNamespace, caplog: pytest.LogCaptureFixture, tool: str, args: dict[str, Any]
) -> tuple[str, bool, list[dict[str, Any]]]:
    caplog.set_level("DEBUG")
    with structlog.testing.capture_logs() as events:
        text, is_error = await call_raw(world.server, tool, args)
    assert MARKER not in caplog.text
    assert MARKER not in repr(events)
    return text, is_error, events


def _failure_event(events: list[dict[str, Any]], tool: str) -> dict[str, Any]:
    [event] = [e for e in events if e.get("event") == "mcp_tool_failed"]
    assert event["tool"] == tool
    return event


async def test_a_memory_tool_raising_any_other_error_answers_the_fixed_line_and_logs_no_text(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    error = _LeakyError(f"could not read the memory '{MARKER}'")
    monkeypatch.setattr(memory, "handle_list_memories", _raising(error))
    text, is_error, events = await _call_logged(world, caplog, "list_memories", {})
    assert (text, is_error) == (FAILED_SENTENCE, True)
    event = _failure_event(events, "list_memories")
    assert (event["error_type"], event["outcome"], event["log_level"]) == (
        "_LeakyError",
        "OPERATION_FAILED",
        "error",
    )


async def test_a_json_tool_raising_any_other_error_answers_the_fixed_failure_as_json(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    error = _LeakyError(f"could not list '{MARKER}'")
    monkeypatch.setattr(world.storage, "list_sources", _raising_sync(error))
    text, is_error, events = await _call_logged(world, caplog, "get_document_status", {})
    assert is_error
    assert json.loads(text) == {
        "success": False,
        "error": FAILED_SENTENCE,
        "code": "OPERATION_FAILED",
    }
    assert _failure_event(events, "get_document_status")["error_type"] == "_LeakyError"


async def test_a_busy_database_is_logged_by_type_and_code_alone(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(memory, "handle_list_memories", _raising(_lock_error()))
    text, _is_error, events = await _call_logged(world, caplog, "list_memories", {})
    assert text == BUSY_SENTENCE
    event = _failure_event(events, "list_memories")
    assert (event["error_type"], event["outcome"], event["log_level"]) == (
        "OperationalError",
        "DATABASE_BUSY",
        "warning",
    )
