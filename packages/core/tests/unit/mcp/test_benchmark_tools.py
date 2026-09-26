# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""MCP benchmark tool declarations and their dispatch in ``call_tool``."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.mcp.tools import TOOL_DEFINITIONS, get_tools_for_mode


BENCHMARK_TOOLS = {
    "start_benchmark": "start",
    "get_benchmark_task": "next_task",
    "submit_benchmark_output": "submit",
    "get_benchmark_progress": "progress",
    "finish_benchmark": "finish",
}

_RUN = {"run_id": "0123456789ab"}
VALID_ARGS = {
    "start_benchmark": {"suite": "probes", "client": "claude-code", "model": "m"},
    "get_benchmark_task": _RUN,
    "submit_benchmark_output": {
        **_RUN,
        "task_id": "A1-easy",
        "stage": "entities",
        "output_text": "E|x",
    },
    "get_benchmark_progress": _RUN,
    "finish_benchmark": _RUN,
}


def test_benchmark_tools_are_declared_and_available_in_read_mode() -> None:
    """They never touch the graph, so read-mode servers offer them too."""
    by_name = {t.name: t for t in TOOL_DEFINITIONS}
    read_names = {t.name for t in get_tools_for_mode("read")}
    for name in BENCHMARK_TOOLS:
        assert name in by_name, name
        assert by_name[name].write_only is False
        assert name in read_names


def test_task_tool_description_tells_the_client_the_loop() -> None:
    """The instructions live in the tool description and the task's answer_format."""
    desc = next(t for t in TOOL_DEFINITIONS if t.name == "get_benchmark_task").description
    assert "answer_format" in desc and "no tool use" in desc
    assert "submit_benchmark_output" in desc and "finish_benchmark" in desc


def test_submit_schema_requires_the_stage_answer() -> None:
    """submit_benchmark_output needs run, stage and text, and a task_id or probe_id."""
    schema = next(t for t in TOOL_DEFINITIONS if t.name == "submit_benchmark_output").input_schema
    assert schema["required"] == ["run_id", "stage", "output_text"]
    assert {"task_id", "probe_id", "truncated"} <= set(schema["properties"])
    assert schema["properties"]["stage"]["enum"] == ["entities", "relationships", "answer"]


def test_start_schema_offers_every_registered_suite_and_a_reference() -> None:
    """The suite enum comes from the registry; chat takes an optional reference pack."""
    from chaoscypher_core.mcp.benchmark import SUITES

    schema = next(t for t in TOOL_DEFINITIONS if t.name == "start_benchmark").input_schema
    assert schema["properties"]["suite"]["enum"] == list(SUITES)
    assert "chat" in SUITES
    assert "reference" in schema["properties"]
    assert "reference" not in schema["required"]


def test_benchmark_tool_count_is_unchanged() -> None:
    """Suites are an argument, not new tools: five benchmark tools, 36 in all."""
    names = [t.name for t in TOOL_DEFINITIONS]
    assert sum(1 for n in names if n in BENCHMARK_TOOLS) == 5
    assert len(names) == 36


def _engine(mode: str) -> MagicMock:
    """A mock engine good enough for create_mcp_server."""
    engine = MagicMock()
    engine.settings.mcp.mode = mode
    engine.settings.mcp.auto_extract = False
    engine.settings.mcp.completed_history_limit = 20
    engine.settings.current_database = "default"
    engine.embedding_service = None
    return engine


async def _call(server: object, name: str, arguments: dict) -> dict:
    """Invoke a tool through the MCP request handler and decode the JSON."""
    import mcp.types

    handler = server.request_handlers[mcp.types.CallToolRequest]  # type: ignore[attr-defined]
    req = mcp.types.CallToolRequest(
        method="tools/call",
        params=mcp.types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = await handler(req)
    return json.loads(result.root.content[0].text)


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "method"), list(BENCHMARK_TOOLS.items()))
async def test_read_mode_server_routes_each_tool_to_the_bridge(tool: str, method: str) -> None:
    """Each benchmark tool reaches its BenchmarkBridge method, even in read mode."""
    from chaoscypher_core.mcp.server import create_mcp_server

    fake = MagicMock()
    setattr(fake, method, AsyncMock(return_value={"success": True, "called": method}))
    with patch("chaoscypher_core.mcp.server.BenchmarkBridge", return_value=fake):
        server = create_mcp_server(_engine("read"))
    payload = await _call(server, tool, dict(VALID_ARGS[tool]))
    assert payload == {"success": True, "called": method}
    getattr(fake, method).assert_awaited_once_with(**VALID_ARGS[tool])


@pytest.mark.asyncio
async def test_submit_still_routes_the_deprecated_probe_id() -> None:
    """A client on the old schema passes probe_id; it reaches the bridge unchanged."""
    from chaoscypher_core.mcp.server import create_mcp_server

    fake = MagicMock()
    fake.submit = AsyncMock(return_value={"success": True})
    args = {**_RUN, "probe_id": "A1-easy", "stage": "entities", "output_text": "E|x"}
    with patch("chaoscypher_core.mcp.server.BenchmarkBridge", return_value=fake):
        server = create_mcp_server(_engine("read"))
    assert (await _call(server, "submit_benchmark_output", dict(args))) == {"success": True}
    fake.submit.assert_awaited_once_with(**args)


@pytest.mark.asyncio
async def test_bad_arguments_become_an_error_payload() -> None:
    """An argument the bridge method does not take is reported, not raised."""
    from chaoscypher_core.mcp.server import _handle_benchmark_tool

    async def _progress(run_id: str) -> dict:
        """Stand-in bridge method with the real signature."""
        return {"success": True, "run_id": run_id}

    out = await _handle_benchmark_tool(_progress, {"run_id": "r", "extra": 1})
    payload = json.loads(out[0].text)
    assert payload["success"] is False
    assert payload["error_code"] == "INVALID_ARGUMENT"
