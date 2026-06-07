# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for the stream chat handler's disconnect plumbing.

The promise that disconnecting the client allowed the response to
complete in the background was never actually honored (CancelledError
unwound the generator before persistence), so the plumbing was deleted
along with the false promise. These tests guard against its return.
"""

from __future__ import annotations

import inspect

from chaoscypher_core.streaming.chat import handler, tools


def test_stream_chat_response_has_no_is_disconnected_param() -> None:
    sig = inspect.signature(handler.stream_chat_response)
    assert "is_disconnected" not in sig.parameters


def test_process_llm_stream_has_no_is_disconnected_param() -> None:
    sig = inspect.signature(handler._process_llm_stream)
    assert "is_disconnected" not in sig.parameters


def test_handle_tool_calls_has_no_is_disconnected_param() -> None:
    sig = inspect.signature(handler._handle_tool_calls)
    assert "is_disconnected" not in sig.parameters


def test_tool_calling_state_has_no_client_disconnected_field() -> None:
    fields = {f.name for f in tools.ToolCallingState.__dataclass_fields__.values()}
    assert "client_disconnected" not in fields


def test_check_client_disconnected_is_removed() -> None:
    assert not hasattr(tools, "_check_client_disconnected")
    assert "_check_client_disconnected" not in tools.__all__
