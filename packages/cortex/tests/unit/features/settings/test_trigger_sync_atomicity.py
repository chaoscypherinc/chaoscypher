# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for TriggerSyncService atomicity."""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.settings.trigger_sync_service import TriggerSyncService


class _FakeAdapter:
    """Adapter stub exposing transaction() context manager."""

    def __init__(self) -> None:
        self.entered = 0
        self.exited_normal = 0
        self.exited_error = 0

    @contextmanager
    def transaction(self):
        self.entered += 1
        try:
            yield
            self.exited_normal += 1
        except Exception:
            self.exited_error += 1
            raise


@pytest.fixture
def adapter() -> _FakeAdapter:
    return _FakeAdapter()


def test_mid_loop_failure_rolls_back(adapter: _FakeAdapter) -> None:
    trigger_service = MagicMock()
    trigger_service.list_triggers.side_effect = [
        [
            {"id": "t1", "name": "A", "workflow_id": "wf-sys"},
            {"id": "t2", "name": "B", "workflow_id": "wf-sys"},
        ],
        [],  # second event_source
    ]
    # First update succeeds, second raises
    trigger_service.update_trigger.side_effect = [True, RuntimeError("db down")]

    workflow_service = MagicMock()
    workflow_service.list_workflows_by_ids.return_value = [{"id": "wf-sys", "is_system": True}]

    sync = TriggerSyncService(
        trigger_service=trigger_service,
        workflow_service=workflow_service,
        adapter=adapter,  # new required arg
    )

    with pytest.raises(RuntimeError):
        sync.sync_auto_embedding_triggers(True)

    # The transaction must have been entered exactly once and exited with error
    assert adapter.entered == 1
    assert adapter.exited_error == 1
    assert adapter.exited_normal == 0


def test_happy_path_commits_once(adapter: _FakeAdapter) -> None:
    trigger_service = MagicMock()
    trigger_service.list_triggers.side_effect = [
        [{"id": "t1", "name": "A", "workflow_id": "wf-sys"}],
        [],
    ]
    trigger_service.update_trigger.return_value = True

    workflow_service = MagicMock()
    workflow_service.list_workflows_by_ids.return_value = [{"id": "wf-sys", "is_system": True}]

    sync = TriggerSyncService(
        trigger_service=trigger_service,
        workflow_service=workflow_service,
        adapter=adapter,
    )

    synced = sync.sync_auto_embedding_triggers(True)

    assert synced == 1
    assert adapter.entered == 1
    assert adapter.exited_normal == 1
    assert adapter.exited_error == 0
