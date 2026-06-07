# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for QueueClient handler registration with HandlerSpec."""

from chaoscypher_core.queue.client import QueueClient
from chaoscypher_core.queue.handler_spec import HandlerSpec


async def _h1(*a, **k):
    return "h1"


async def _h2(*a, **k):
    return "h2"


def test_register_bare_callables_defaults_retry_off() -> None:
    client = QueueClient()
    client.register_handlers("llm", {"op_a": _h1})
    assert client.get_retry_policy("llm", "op_a") is False
    assert client.get_handler("llm", "op_a") is _h1


def test_register_handler_spec_preserves_flag() -> None:
    client = QueueClient()
    client.register_handlers(
        "llm",
        {
            "op_a": HandlerSpec(_h1, retry_on_crash=True),
            "op_b": _h2,  # bare, default False
        },
    )
    assert client.get_retry_policy("llm", "op_a") is True
    assert client.get_retry_policy("llm", "op_b") is False


def test_get_retry_policy_unknown_returns_false() -> None:
    client = QueueClient()
    assert client.get_retry_policy("llm", "nonexistent") is False


def test_register_preserves_existing_handlers_on_second_call() -> None:
    """Calling register_handlers twice merges rather than replacing."""
    client = QueueClient()
    client.register_handlers("llm", {"op_a": _h1})
    client.register_handlers("llm", {"op_b": HandlerSpec(_h2, retry_on_crash=True)})
    assert client.get_handler("llm", "op_a") is _h1
    assert client.get_handler("llm", "op_b") is _h2
    assert client.get_retry_policy("llm", "op_a") is False
    assert client.get_retry_policy("llm", "op_b") is True
