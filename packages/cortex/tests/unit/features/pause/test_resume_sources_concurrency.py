# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""resume_sources must fan out recover_source calls concurrently."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_cortex.features.pause.service import PauseService


@pytest.mark.asyncio
async def test_resume_sources_fans_out_recoveries() -> None:
    """5 recoveries with 100ms latency each must finish in <300ms total."""
    repository = MagicMock()
    repository.resume_sources.return_value = 5

    source_recovery = MagicMock()

    async def slow_recover(source_id: str, database_name: str) -> None:
        await asyncio.sleep(0.1)

    source_recovery.recover_source = AsyncMock(side_effect=slow_recover)

    service = PauseService(repository=repository, source_recovery=source_recovery)

    start = time.perf_counter()
    count = await service.resume_sources(
        source_ids=["s1", "s2", "s3", "s4", "s5"],
        database_name="default",
    )
    elapsed = time.perf_counter() - start

    assert count == 5
    # Serial: ~500ms. Parallel: ~100ms. Gate at 300ms for CI jitter.
    assert elapsed < 0.3, f"resume_sources ran serially: {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_resume_sources_continues_when_one_recovery_fails() -> None:
    """A single recovery failure does not block the rest."""
    repository = MagicMock()
    repository.resume_sources.return_value = 3

    source_recovery = MagicMock()
    calls: list[str] = []

    async def maybe_fail(source_id: str, database_name: str) -> None:
        calls.append(source_id)
        if source_id == "s2":
            msg = "transient"
            raise RuntimeError(msg)

    source_recovery.recover_source = AsyncMock(side_effect=maybe_fail)

    service = PauseService(repository=repository, source_recovery=source_recovery)
    count = await service.resume_sources(
        source_ids=["s1", "s2", "s3"],
        database_name="default",
    )

    assert count == 3
    assert set(calls) == {"s1", "s2", "s3"}  # all three attempted
