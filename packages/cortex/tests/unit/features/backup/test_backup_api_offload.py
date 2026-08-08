# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Backup create/restore handlers must offload blocking disk I/O.

Both handlers copied/vacuumed the whole database inline in async route
handlers, blocking the event loop for the duration. The scheduler already
offloads the same calls via asyncio.to_thread (see lifespan.py) — the
HTTP handlers now do the same.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.backup import api as backup_api


@pytest.mark.unit
class TestBackupHandlersOffload:
    """create/restore route handlers run service calls in a thread."""

    @pytest.mark.asyncio
    async def test_create_backup_offloads_and_returns_result(self) -> None:
        """create_backup runs service.create_backup via asyncio.to_thread."""
        service = MagicMock()
        expected = {
            "database": "default",
            "filename": "app_20260723_000000.db",
            "size": 42,
            "created_at": "20260723_000000",
        }
        service.create_backup.return_value = expected

        real_to_thread = asyncio.to_thread
        to_thread_spy = AsyncMock(side_effect=real_to_thread)

        with patch("asyncio.to_thread", to_thread_spy):
            result = await backup_api.create_backup("user", "default", service)

        assert result == expected
        to_thread_spy.assert_awaited_once_with(service.create_backup, "default")
        service.create_backup.assert_called_once_with("default")

    @pytest.mark.asyncio
    async def test_restore_backup_offloads_and_returns_result(self) -> None:
        """restore_backup runs service.restore_backup via asyncio.to_thread."""
        service = MagicMock()
        expected = {"database": "default", "restored_from": "app_20260723_000000.db"}
        service.restore_backup.return_value = expected

        real_to_thread = asyncio.to_thread
        to_thread_spy = AsyncMock(side_effect=real_to_thread)

        with patch("asyncio.to_thread", to_thread_spy):
            result = await backup_api.restore_backup(
                "user", "app_20260723_000000.db", "default", service
            )

        assert result == expected
        to_thread_spy.assert_awaited_once_with(
            service.restore_backup, "default", "app_20260723_000000.db"
        )
        service.restore_backup.assert_called_once_with("default", "app_20260723_000000.db")
