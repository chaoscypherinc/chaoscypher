# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit/integration tests for ``streaming.chat.tools`` helpers.

Covers the pure deduplication / signature / guidance helpers and the async
stream-processing + tool-execution + approval-gate helpers. The pure helpers
need no I/O; the async helpers are driven with small async-iterable stubs and
``MagicMock`` collaborators (no real LLM provider, settings, or DB).

The top-level ``_handle_tool_calls`` / ``stream_chat_response`` orchestration is
intentionally out of scope here (it needs full provider + Settings wiring).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.streaming.chat.tools import (
    _extract_found_nodes,
    _extract_tool_defaults,
    _filter_duplicate_tool_calls,
    _generate_duplicate_guidance,
    _generate_unfulfilled_guidance,
    _normalize_tool_args,
    _process_iteration_stream,
    _tool_call_signature,
    _track_tool_signature,
)


# --------------------------------------------------------------------------- #
# Local helpers (copied — no cross-test imports allowed).
# --------------------------------------------------------------------------- #
def _parse_sse(blob: bytes) -> dict[str, Any]:
    r"""Parse a single ``data: {...}\n\n`` SSE frame into the JSON payload."""
    text = blob.decode()
    assert text.startswith("data: ")
    assert text.endswith("\n\n")
    return json.loads(text[len("data: ") : -2])


class _AsyncChunks:
    """Minimal async-iterable yielding the given chunk dicts."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks
        self.closed = False

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[dict[str, Any]]:
        for c in self._chunks:
            yield c

    async def aclose(self) -> None:
        self.closed = True


class _NoAiter:
    """Object that does NOT implement __aiter__ (early-return path)."""


async def _collect(agen: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in agen]


def _tool_call(
    name: str,
    arguments: Any,
    *,
    call_id: str = "call_1",
) -> dict[str, Any]:
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


# --------------------------------------------------------------------------- #
# _extract_tool_defaults
# --------------------------------------------------------------------------- #
def test_extract_tool_defaults_collects_only_params_with_defaults() -> None:
    tools = [
        {
            "function": {
                "name": "search_nodes",
                "parameters": {
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 10},
                        "fuzzy": {"type": "boolean", "default": False},
                    }
                },
            }
        },
        # Tool with no name is skipped.
        {"function": {"name": "", "parameters": {"properties": {}}}},
        # Tool whose params have no defaults contributes nothing.
        {
            "function": {
                "name": "no_defaults",
                "parameters": {"properties": {"x": {"type": "string"}}},
            }
        },
    ]

    defaults = _extract_tool_defaults(tools)

    assert defaults == {"search_nodes": {"limit": 10, "fuzzy": False}}


# --------------------------------------------------------------------------- #
# _normalize_tool_args
# --------------------------------------------------------------------------- #
def test_normalize_tool_args_prunes_null_empty_and_defaults_and_trims() -> None:
    args = {
        "query": "  hello  ",
        "none_val": None,
        "empty_str": "",
        "empty_list": [],
        "limit": 10,  # matches default -> stripped
        "keep": 5,
    }
    defaults = {"limit": 10}

    result = _normalize_tool_args(args, defaults)

    assert result == {"query": "hello", "keep": 5}


def test_normalize_tool_args_without_defaults_keeps_meaningful_values() -> None:
    result = _normalize_tool_args({"a": 1, "b": "x"})
    assert result == {"a": 1, "b": "x"}


# --------------------------------------------------------------------------- #
# _tool_call_signature  (string vs dict args produce identical signatures)
# --------------------------------------------------------------------------- #
def test_tool_call_signature_string_and_dict_args_match() -> None:
    tc_dict = _tool_call("search_nodes", {"query": "Pierre", "limit": 10})
    tc_str = _tool_call("search_nodes", json.dumps({"limit": 10, "query": "Pierre"}))
    defaults = {"search_nodes": {"limit": 10}}

    name_d, sig_d = _tool_call_signature(tc_dict, defaults)
    name_s, sig_s = _tool_call_signature(tc_str, defaults)

    assert name_d == name_s == "search_nodes"
    # 'limit' matches default and is stripped from both forms.
    assert sig_d == sig_s == 'search_nodes:{"query": "Pierre"}'


def test_tool_call_signature_malformed_json_string_falls_back_to_str() -> None:
    tc = _tool_call("search_nodes", "not-json{")
    name, sig = _tool_call_signature(tc)
    assert name == "search_nodes"
    assert sig == "search_nodes:not-json{"


# --------------------------------------------------------------------------- #
# _filter_duplicate_tool_calls  (across-batch + within-batch)
# --------------------------------------------------------------------------- #
def test_filter_duplicate_across_batch_via_executed_signatures() -> None:
    executed: dict[str, int] = {}
    tc = _tool_call("search_nodes", {"query": "Pierre"})
    _track_tool_signature(tc, executed)  # mark as already executed

    filtered, dupes = _filter_duplicate_tool_calls([tc], executed, "chat-1", 2)

    assert filtered == []
    assert dupes == [tc]


def test_filter_duplicate_within_same_batch() -> None:
    executed: dict[str, int] = {}
    tc1 = _tool_call("search_nodes", {"query": "Pierre"}, call_id="a")
    tc2 = _tool_call("search_nodes", {"query": "Pierre"}, call_id="b")

    filtered, dupes = _filter_duplicate_tool_calls([tc1, tc2], executed, "chat-1", 1)

    # First instance kept, second flagged as a within-batch duplicate.
    assert filtered == [tc1]
    assert dupes == [tc2]


# --------------------------------------------------------------------------- #
# _track_tool_signature
# --------------------------------------------------------------------------- #
def test_track_tool_signature_increments_count() -> None:
    executed: dict[str, int] = {}
    tc = _tool_call("get_node_edges", {"node_id": "n1"})

    _track_tool_signature(tc, executed)
    _track_tool_signature(tc, executed)

    _, sig = _tool_call_signature(tc)
    assert executed[sig] == 2


# --------------------------------------------------------------------------- #
# _extract_found_nodes
# --------------------------------------------------------------------------- #
def test_extract_found_nodes_from_search_and_resolve_and_malformed() -> None:
    messages = [
        {
            "role": "tool",
            "name": "search_nodes",
            "content": json.dumps(
                {
                    "results": [
                        {"id": "n1", "name": "Pierre"},
                        {"id": "n2", "label": "Andrei"},  # uses label fallback
                        {"name": "no id - skipped"},
                    ]
                }
            ),
        },
        {
            "role": "tool",
            "name": "resolve_node",
            "content": json.dumps({"id": "n3", "name": "Marie"}),
        },
        # Malformed JSON in a relevant tool message -> swallowed.
        {"role": "tool", "name": "search_nodes", "content": "not-json{"},
        # Unrelated role / tool name -> ignored.
        {"role": "assistant", "content": "irrelevant"},
        {"role": "tool", "name": "other_tool", "content": json.dumps({"id": "x"})},
    ]

    found = _extract_found_nodes(messages)

    assert found == [
        {"id": "n1", "name": "Pierre"},
        {"id": "n2", "name": "Andrei"},
        {"id": "n3", "name": "Marie"},
    ]


# --------------------------------------------------------------------------- #
# _generate_unfulfilled_guidance
# --------------------------------------------------------------------------- #
def _search_msg() -> dict[str, Any]:
    return {
        "role": "tool",
        "name": "search_nodes",
        "content": json.dumps(
            {"results": [{"id": "n1", "name": "Pierre"}, {"id": "n2", "name": "Andrei"}]}
        ),
    }


def test_unfulfilled_guidance_with_nodes_and_edges_intent() -> None:
    guidance = _generate_unfulfilled_guidance(
        [_search_msg()], "I'll check the relationships between them"
    )
    assert "Call get_node_edges NOW" in guidance
    assert 'get_node_edges(node_id="n1")' in guidance
    # Two nodes -> traverse_path hint included.
    assert 'traverse_path(source_node_id="n1"' in guidance


def test_unfulfilled_guidance_with_nodes_generic() -> None:
    guidance = _generate_unfulfilled_guidance([_search_msg()], "Let me think about this")
    assert "You have already found these nodes" in guidance
    assert "Pierre (id: n1)" in guidance


def test_unfulfilled_guidance_no_nodes() -> None:
    guidance = _generate_unfulfilled_guidance([], "I'll search for something")
    assert "didn't call any tools" in guidance
    assert "search_nodes" in guidance


# --------------------------------------------------------------------------- #
# _generate_duplicate_guidance
# --------------------------------------------------------------------------- #
def test_duplicate_guidance_with_nodes_and_search_dup() -> None:
    guidance = _generate_duplicate_guidance([_search_msg()], ["search_nodes"])
    assert "STOP repeating search_nodes" in guidance
    assert 'get_node_edges(node_id="n1")' in guidance
    assert 'traverse_path(source_node_id="n1"' in guidance


def test_duplicate_guidance_generic_fallback() -> None:
    # No found nodes -> generic fallback regardless of dup name.
    guidance = _generate_duplicate_guidance([], ["get_node_edges"])
    assert "You already called get_node_edges" in guidance
    assert "search_chunks" in guidance


# --------------------------------------------------------------------------- #
# _process_iteration_stream
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_process_iteration_stream_no_aiter_early_returns_done() -> None:
    out = [c async for c in _process_iteration_stream(_NoAiter(), "chat-1", 1)]
    assert out == [{"_internal_type": "done", "content": "", "thinking": None, "tool_calls": None}]


@pytest.mark.asyncio
async def test_process_iteration_stream_content_thinking_done() -> None:
    stream = _AsyncChunks(
        [
            {"type": "content", "delta": "Hel", "accumulated": "Hel"},
            {"type": "thinking_delta", "accumulated": "pondering"},
            {
                "type": "done",
                "content": "Hello",
                "thinking": "done-think",
                "tool_calls": [{"id": "c1"}],
                "provider_timings": {"x": 1},
                "usage": {"completion_tokens": 5},
            },
        ]
    )

    out = [c async for c in _process_iteration_stream(stream, "chat-1", 1)]

    types = [c["_internal_type"] for c in out]
    assert types == ["content", "thinking", "done"]
    done = out[-1]
    assert done["content"] == "Hello"
    assert done["thinking"] == "done-think"
    assert done["tool_calls"] == [{"id": "c1"}]
    assert done["provider_timings"] == {"x": 1}
    assert done["usage"] == {"completion_tokens": 5}


@pytest.mark.asyncio
async def test_process_iteration_stream_error_chunk_stops() -> None:
    stream = _AsyncChunks(
        [
            {"type": "content", "delta": "x", "accumulated": "x"},
            {"type": "error", "error": "boom", "error_code": "LLM_ERROR"},
            {"type": "done", "content": "never-reached"},
        ]
    )

    out = [c async for c in _process_iteration_stream(stream, "chat-1", 1)]

    types = [c["_internal_type"] for c in out]
    assert types == ["content", "error"]
    assert out[-1]["error"] == "boom"
    assert out[-1]["error_code"] == "LLM_ERROR"


def _engine_settings_stub(approval: str, mutating: list[str]) -> MagicMock:
    es = MagicMock()
    es.chat.tool_approval = approval
    es.chat.mutating_tools = mutating
    return es
