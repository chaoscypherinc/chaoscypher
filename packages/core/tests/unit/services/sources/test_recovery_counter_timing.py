# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: recovery counter must increment before dispatch fires.

Audit fix #H7 — if _dispatch raises (queue down, network blip, etc.)
the counter must already have been incremented so the source cannot be
re-attempted indefinitely.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_counter_bumped_when_dispatch_raises() -> None:
    """increment_source_recovery_attempts is called even when _dispatch fails."""
    from chaoscypher_core.services.sources.recovery import RecoveryStats, SourceRecovery

    adapter = MagicMock()
    # system_state: processing not paused
    adapter.get_system_state.return_value = {"processing_paused": False}

    service = SourceRecovery(adapter=adapter, queue_client=AsyncMock())

    # Override private methods so we control the test path.
    service._classify = AsyncMock(
        return_value={"queue": "operations", "operation": "OP_INDEX", "data": {}, "priority": 5}
    )
    service._dispatch = AsyncMock(side_effect=RuntimeError("queue down"))
    service._is_recently_active = MagicMock(return_value=False)

    source = {
        "id": "src_h7",
        "status": "indexing",
        "is_paused": False,
        "recovery_attempts": 0,
    }
    stats = RecoveryStats()

    with pytest.raises(RuntimeError, match="queue down"):
        await service._recover_one(
            source=source,
            database_name="default",
            stats=stats,
            respect_stall_threshold=True,
        )

    # Counter must have been incremented BEFORE dispatch raised — the
    # whole point of audit fix #H7.
    adapter.increment_source_recovery_attempts.assert_called_once_with(
        source_id="src_h7",
        database_name="default",
    )


@pytest.mark.asyncio
async def test_counter_bumped_before_dispatch_on_success() -> None:
    """increment_source_recovery_attempts is called before _dispatch even on the happy path."""
    from chaoscypher_core.services.sources.recovery import RecoveryStats, SourceRecovery

    call_order: list[str] = []

    adapter = MagicMock()
    adapter.get_system_state.return_value = {"processing_paused": False}

    def _record_increment(**_kwargs: object) -> None:
        call_order.append("increment")

    adapter.increment_source_recovery_attempts.side_effect = _record_increment
    adapter.update_source_last_activity.return_value = None
    adapter.record_recovery_event = None  # optional — skip audit trail

    service = SourceRecovery(adapter=adapter, queue_client=AsyncMock())

    async def _fake_dispatch(*_args: object, **_kwargs: object) -> None:
        call_order.append("dispatch")

    service._classify = AsyncMock(
        return_value={"queue": "operations", "operation": "OP_INDEX", "data": {}, "priority": 5}
    )
    service._dispatch = AsyncMock(side_effect=_fake_dispatch)
    service._is_recently_active = MagicMock(return_value=False)

    source = {
        "id": "src_h7_happy",
        "status": "indexing",
        "is_paused": False,
        "recovery_attempts": 0,
    }
    stats = RecoveryStats()

    await service._recover_one(
        source=source,
        database_name="default",
        stats=stats,
        respect_stall_threshold=True,
    )

    # Verify ordering: increment must precede dispatch.
    assert call_order == ["increment", "dispatch"], (
        f"Expected ['increment', 'dispatch'] but got {call_order!r}. "
        "The counter must be bumped before the task is enqueued (audit fix #H7)."
    )
    assert stats.recovered == 1


@pytest.mark.asyncio
async def test_counter_bumped_before_commit_dispatch_raises_extracted() -> None:
    """H7 regression: counter bumps before queue_import_commit for status=extracted.

    Exercises the real _classify path (no _classify stub) with
    _load_commit_data mocked. Simulates Valkey down by making
    queue_import_commit raise. Asserts that the counter already fired
    before the raise propagates.

    Before the H7 fix, _dispatch_commit ran INSIDE _classify, before
    _recover_one's counter increment — meaning a queue blip would allow
    the source to loop forever without the counter advancing.
    """
    from unittest.mock import patch

    from chaoscypher_core.services.sources.recovery import RecoveryStats, SourceRecovery

    call_order: list[str] = []

    adapter = MagicMock()
    adapter.get_system_state.return_value = {"processing_paused": False}

    def _record_increment(**_kwargs: object) -> None:
        call_order.append("increment")

    adapter.increment_source_recovery_attempts.side_effect = _record_increment

    queue_client = AsyncMock()
    queue_client.task_exists_for_source = AsyncMock(return_value=False)

    service = SourceRecovery(adapter=adapter, queue_client=queue_client)
    service._is_recently_active = MagicMock(return_value=False)

    # _load_commit_data is sync; mock it to return a simple payload.
    service._load_commit_data = MagicMock(
        return_value={"entities": [{"name": "E1"}], "relationships": []}
    )

    source = {
        "id": "src_h7_extracted",
        "status": "extracted",
        "filepath": "/data/test.txt",
        "filename": "test.txt",
        "file_type": "text",
        "is_paused": False,
        "recovery_attempts": 0,
        "commit_complete": False,
    }
    stats = RecoveryStats()

    with patch(
        "chaoscypher_core.services.sources.recovery.queue_utils.queue_import_commit",
        new_callable=AsyncMock,
    ) as mock_qic:

        async def _record_then_raise(*_args: object, **_kwargs: object) -> None:
            call_order.append("queue_import_commit")
            raise RuntimeError("Valkey down")

        mock_qic.side_effect = _record_then_raise

        with pytest.raises(RuntimeError, match="Valkey down"):
            await service._recover_one(
                source=source,
                database_name="default",
                stats=stats,
                respect_stall_threshold=True,
            )

    # Counter must have been incremented BEFORE queue_import_commit raised.
    assert call_order == ["increment", "queue_import_commit"], (
        f"Expected ['increment', 'queue_import_commit'] but got {call_order!r}. "
        "H7 regression: counter must bump before any queue interaction on commit path."
    )
    adapter.increment_source_recovery_attempts.assert_called_once_with(
        source_id="src_h7_extracted",
        database_name="default",
    )


@pytest.mark.asyncio
async def test_counter_bumped_before_commit_dispatch_raises_committing() -> None:
    """H7 regression: counter bumps before queue_import_commit for status=committing.

    Parallel to the extracted test — exercises the committing branch of
    _classify, confirming counter-before-dispatch ordering on both new
    commit-dispatch paths.
    """
    from unittest.mock import patch

    from chaoscypher_core.services.sources.recovery import RecoveryStats, SourceRecovery

    call_order: list[str] = []

    adapter = MagicMock()
    adapter.get_system_state.return_value = {"processing_paused": False}

    def _record_increment(**_kwargs: object) -> None:
        call_order.append("increment")

    adapter.increment_source_recovery_attempts.side_effect = _record_increment

    queue_client = AsyncMock()
    queue_client.task_exists_for_source = AsyncMock(return_value=False)

    service = SourceRecovery(adapter=adapter, queue_client=queue_client)
    service._is_recently_active = MagicMock(return_value=False)

    service._load_commit_data = MagicMock(
        return_value={"entities": [{"name": "Bob"}], "relationships": []}
    )

    source = {
        "id": "src_h7_committing",
        "status": "committing",
        "filepath": "/data/other.txt",
        "filename": "other.txt",
        "file_type": "text",
        "is_paused": False,
        "recovery_attempts": 0,
        "commit_complete": False,
    }
    stats = RecoveryStats()

    with patch(
        "chaoscypher_core.services.sources.recovery.queue_utils.queue_import_commit",
        new_callable=AsyncMock,
    ) as mock_qic:

        async def _record_then_raise(*_args: object, **_kwargs: object) -> None:
            call_order.append("queue_import_commit")
            raise RuntimeError("Valkey down")

        mock_qic.side_effect = _record_then_raise

        with pytest.raises(RuntimeError, match="Valkey down"):
            await service._recover_one(
                source=source,
                database_name="default",
                stats=stats,
                respect_stall_threshold=True,
            )

    assert call_order == ["increment", "queue_import_commit"], (
        f"Expected ['increment', 'queue_import_commit'] but got {call_order!r}. "
        "H7 regression: counter must bump before any queue interaction on commit path."
    )
    adapter.increment_source_recovery_attempts.assert_called_once_with(
        source_id="src_h7_committing",
        database_name="default",
    )
