# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for heartbeat key helpers on QueueClient."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.queue.client import QueueClient


def _build_client_with_mock_valkey() -> tuple[QueueClient, MagicMock]:
    """Construct a QueueClient with a fresh mocked Valkey connection.

    The real QueueClient assigns self.client in connect(); for unit tests
    we skip the connection step and assign the mock directly.
    """
    client = QueueClient()
    valkey = MagicMock()
    client.client = valkey
    return client, valkey


@pytest.mark.asyncio
async def test_set_heartbeat_sets_key_with_ttl() -> None:
    client, valkey = _build_client_with_mock_valkey()
    valkey.set = AsyncMock(return_value=True)

    await client.set_heartbeat("abc-123", ttl_seconds=30)

    valkey.set.assert_awaited_once_with("queue:task:abc-123:heartbeat", "1", ex=30)


@pytest.mark.asyncio
async def test_refresh_heartbeat_extends_ttl() -> None:
    client, valkey = _build_client_with_mock_valkey()
    valkey.expire = AsyncMock(return_value=True)

    await client.refresh_heartbeat("abc-123", ttl_seconds=30)

    valkey.expire.assert_awaited_once_with("queue:task:abc-123:heartbeat", 30)


@pytest.mark.asyncio
async def test_delete_heartbeat_removes_key() -> None:
    client, valkey = _build_client_with_mock_valkey()
    valkey.delete = AsyncMock(return_value=1)

    await client.delete_heartbeat("abc-123")

    valkey.delete.assert_awaited_once_with("queue:task:abc-123:heartbeat")


@pytest.mark.asyncio
async def test_heartbeat_exists_checks_key() -> None:
    client, valkey = _build_client_with_mock_valkey()
    valkey.exists = AsyncMock(return_value=1)

    result = await client.heartbeat_exists("abc-123")

    assert result is True
    valkey.exists.assert_awaited_once_with("queue:task:abc-123:heartbeat")


@pytest.mark.asyncio
async def test_heartbeat_exists_returns_false_when_missing() -> None:
    client, valkey = _build_client_with_mock_valkey()
    valkey.exists = AsyncMock(return_value=0)

    result = await client.heartbeat_exists("abc-123")

    assert result is False
