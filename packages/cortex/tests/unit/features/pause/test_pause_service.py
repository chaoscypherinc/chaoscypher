# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for PauseService.

Focuses on service-level orchestration: repository delegation on
pause, repository delegation PLUS immediate recovery on resume
(so the user doesn't wait for the next periodic pass), and the
system-state shape returned to the API layer.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_cortex.features.pause.service import PauseService


@pytest.mark.asyncio
async def test_pause_source_delegates() -> None:
    repo = MagicMock()
    recovery = AsyncMock()

    service = PauseService(repository=repo, source_recovery=recovery)
    await service.pause_source(source_id="s-1", database_name="default", reason="x")

    repo.pause_source.assert_called_once_with(source_id="s-1", database_name="default", reason="x")
    recovery.recover_source.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_source_triggers_recovery() -> None:
    """Resuming calls recover_source so the user gets instant feedback."""
    repo = MagicMock()
    recovery = AsyncMock()
    recovery.recover_source = AsyncMock(return_value=True)

    service = PauseService(repository=repo, source_recovery=recovery)
    await service.resume_source(source_id="s-1", database_name="default")

    repo.resume_source.assert_called_once_with(source_id="s-1", database_name="default")
    recovery.recover_source.assert_awaited_once_with(source_id="s-1", database_name="default")


@pytest.mark.asyncio
async def test_pause_sources_bulk_returns_count() -> None:
    repo = MagicMock()
    repo.pause_sources = MagicMock(return_value=5)
    recovery = AsyncMock()

    service = PauseService(repository=repo, source_recovery=recovery)
    count = await service.pause_sources(
        source_ids=["a", "b", "c", "d", "e"],
        database_name="default",
        reason="test",
    )
    assert count == 5


@pytest.mark.asyncio
async def test_resume_sources_triggers_recovery_per_source() -> None:
    repo = MagicMock()
    repo.resume_sources = MagicMock(return_value=3)
    recovery = AsyncMock()
    recovery.recover_source = AsyncMock(return_value=True)

    service = PauseService(repository=repo, source_recovery=recovery)
    count = await service.resume_sources(source_ids=["a", "b", "c"], database_name="default")

    assert count == 3
    assert recovery.recover_source.await_count == 3


@pytest.mark.asyncio
async def test_resume_sources_continues_on_per_source_error() -> None:
    """One failing recover_source call shouldn't abort the bulk resume."""
    repo = MagicMock()
    repo.resume_sources = MagicMock(return_value=3)
    recovery = AsyncMock()
    recovery.recover_source = AsyncMock(side_effect=[True, RuntimeError("boom"), True])

    service = PauseService(repository=repo, source_recovery=recovery)
    count = await service.resume_sources(source_ids=["a", "b", "c"], database_name="default")

    assert count == 3
    assert recovery.recover_source.await_count == 3


@pytest.mark.asyncio
async def test_pause_system() -> None:
    repo = MagicMock()
    recovery = AsyncMock()

    service = PauseService(repository=repo, source_recovery=recovery)
    await service.pause_system(reason="deploy")

    repo.pause_system.assert_called_once_with(reason="deploy", paused_by="user")


@pytest.mark.asyncio
async def test_resume_system_does_not_trigger_per_source_recovery() -> None:
    """Resuming the global flag leaves per-source recovery to the next reconciler pass.

    A global resume shouldn't walk the whole source table from the API handler.
    """
    repo = MagicMock()
    recovery = AsyncMock()

    service = PauseService(repository=repo, source_recovery=recovery)
    await service.resume_system()

    repo.resume_system.assert_called_once()
    recovery.recover_source.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_system_status_shape() -> None:
    repo = MagicMock()
    repo.get_system_state = MagicMock(
        return_value={
            "processing_paused": True,
            "processing_paused_at": "2026-04-11T12:00:00",
            "processing_paused_reason": "test",
        }
    )
    recovery = AsyncMock()

    service = PauseService(repository=repo, source_recovery=recovery)
    status = await service.get_system_status()

    assert status == {
        "paused": True,
        "paused_at": "2026-04-11T12:00:00",
        "reason": "test",
        "paused_by": None,
    }
