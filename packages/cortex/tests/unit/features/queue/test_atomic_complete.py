# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the atomic SREM + DEL Lua script."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.queue.client import QueueClient


def _build_client_with_mock_valkey() -> tuple[QueueClient, MagicMock]:
    client = QueueClient()
    valkey = MagicMock()
    client.client = valkey
    return client, valkey


@pytest.mark.asyncio
async def test_complete_task_atomic_runs_lua_script() -> None:
    """complete_task_atomic executes the loaded Lua script.

    It passes the running-set key and heartbeat key.
    """
    client, valkey = _build_client_with_mock_valkey()
    valkey.evalsha = AsyncMock(return_value=1)
    valkey.script_load = AsyncMock(return_value="SHA1HASH")

    await client.complete_task_atomic("llm", "abc-123")

    assert valkey.script_load.await_count >= 1
    valkey.evalsha.assert_awaited_once()

    call = valkey.evalsha.await_args
    assert call.args[0] == "SHA1HASH"
    assert call.args[1] == 2  # numkeys
    assert call.args[2] == "queue:llm:running"
    assert call.args[3] == "queue:task:abc-123:heartbeat"
    assert call.args[4] == "abc-123"  # ARGV[1]


@pytest.mark.asyncio
async def test_complete_task_atomic_caches_sha() -> None:
    """Script is loaded once, then re-used on subsequent calls."""
    client, valkey = _build_client_with_mock_valkey()
    valkey.evalsha = AsyncMock(return_value=1)
    valkey.script_load = AsyncMock(return_value="SHA1HASH")

    await client.complete_task_atomic("llm", "abc-123")
    await client.complete_task_atomic("llm", "def-456")

    assert valkey.script_load.await_count == 1
    assert valkey.evalsha.await_count == 2


@pytest.mark.asyncio
async def test_complete_task_atomic_uses_correct_queue_name() -> None:
    """The Lua KEY is built from the queue parameter."""
    client, valkey = _build_client_with_mock_valkey()
    valkey.evalsha = AsyncMock(return_value=1)
    valkey.script_load = AsyncMock(return_value="SHA")

    await client.complete_task_atomic("operations", "xyz-789")

    call = valkey.evalsha.await_args
    assert call.args[2] == "queue:operations:running"
    assert call.args[3] == "queue:task:xyz-789:heartbeat"
