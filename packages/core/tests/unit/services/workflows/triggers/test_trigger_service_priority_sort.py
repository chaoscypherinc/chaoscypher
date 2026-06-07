# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for TriggerService.list_triggers priority sort."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.workflows.triggers.management.service import TriggerService


@pytest.fixture
def service() -> TriggerService:
    storage = MagicMock()
    return TriggerService(storage=storage, database_name="test_db")


def test_list_triggers_sorts_by_priority_desc(service: TriggerService) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    service.storage.list_triggers.return_value = [
        {"id": "a", "priority": 0, "created_at": base},
        {"id": "b", "priority": 10, "created_at": base + timedelta(seconds=1)},
        {"id": "c", "priority": 5, "created_at": base + timedelta(seconds=2)},
    ]
    result = service.list_triggers()
    assert [t["id"] for t in result] == ["b", "c", "a"]


def test_list_triggers_ties_broken_by_created_at_asc(service: TriggerService) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    service.storage.list_triggers.return_value = [
        {"id": "new", "priority": 5, "created_at": base + timedelta(seconds=10)},
        {"id": "old", "priority": 5, "created_at": base + timedelta(seconds=1)},
        {"id": "mid", "priority": 5, "created_at": base + timedelta(seconds=5)},
    ]
    result = service.list_triggers()
    assert [t["id"] for t in result] == ["old", "mid", "new"]


def test_sort_tolerates_missing_priority_and_created_at(service: TriggerService) -> None:
    """Corrupt rows don't break the list."""
    service.storage.list_triggers.return_value = [
        {"id": "weird"},  # no priority, no created_at
        {"id": "b", "priority": 10, "created_at": datetime(2026, 1, 1, tzinfo=UTC)},
    ]
    result = service.list_triggers()
    # "b" (priority 10) must still come first
    assert result[0]["id"] == "b"
