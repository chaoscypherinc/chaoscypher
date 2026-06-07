# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""sync_auto_embedding_triggers must batch workflow lookups (no N+1)."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

from chaoscypher_cortex.features.settings.trigger_sync_service import (
    TriggerSyncService,
)


class _FakeAdapter:
    """Adapter stub exposing transaction() context manager."""

    @contextmanager
    def transaction(self):
        yield


def test_sync_uses_batch_workflow_lookup() -> None:
    """A single batch call replaces the per-trigger get_workflow loop."""
    trigger_service = MagicMock()
    trigger_service.list_triggers.side_effect = [
        [
            {"id": "t1", "name": "n1", "workflow_id": "w1"},
            {"id": "t2", "name": "n2", "workflow_id": "w2"},
            {"id": "t3", "name": "n3", "workflow_id": "w3"},
        ],
        [],  # node.updated: no triggers
    ]

    workflow_service = MagicMock()
    workflow_service.list_workflows_by_ids.return_value = [
        {"id": "w1", "is_system": True},
        {"id": "w2", "is_system": False},
        {"id": "w3", "is_system": True},
    ]

    service = TriggerSyncService(
        trigger_service=trigger_service,
        workflow_service=workflow_service,
        adapter=_FakeAdapter(),
    )

    count = service.sync_auto_embedding_triggers(enabled=True)

    # Two of three workflows are system → two triggers updated.
    assert count == 2

    # The N+1 get_workflow per trigger must NOT be used.
    workflow_service.get_workflow.assert_not_called()

    # Exactly one batch call per non-empty event source.
    # node.created has 3 triggers → 1 call; node.updated has 0 → skipped.
    assert workflow_service.list_workflows_by_ids.call_count == 1


def test_sync_batch_skips_empty_event_source() -> None:
    """list_workflows_by_ids is not called when an event source has no triggers."""
    trigger_service = MagicMock()
    trigger_service.list_triggers.side_effect = [
        [],  # node.created: no triggers
        [],  # node.updated: no triggers
    ]

    workflow_service = MagicMock()

    service = TriggerSyncService(
        trigger_service=trigger_service,
        workflow_service=workflow_service,
        adapter=_FakeAdapter(),
    )

    count = service.sync_auto_embedding_triggers(enabled=False)

    assert count == 0
    workflow_service.list_workflows_by_ids.assert_not_called()
    workflow_service.get_workflow.assert_not_called()


def test_sync_two_event_sources_two_batch_calls() -> None:
    """One batch call per non-empty event source."""
    trigger_service = MagicMock()
    trigger_service.list_triggers.side_effect = [
        [{"id": "t1", "name": "A", "workflow_id": "w1"}],  # node.created
        [{"id": "t2", "name": "B", "workflow_id": "w2"}],  # node.updated
    ]

    workflow_service = MagicMock()
    workflow_service.list_workflows_by_ids.return_value = [
        {"id": "w1", "is_system": True},
        {"id": "w2", "is_system": True},
    ]

    service = TriggerSyncService(
        trigger_service=trigger_service,
        workflow_service=workflow_service,
        adapter=_FakeAdapter(),
    )

    count = service.sync_auto_embedding_triggers(enabled=True)

    assert count == 2
    # One call per event source, not one call total
    assert workflow_service.list_workflows_by_ids.call_count == 2
    workflow_service.get_workflow.assert_not_called()
