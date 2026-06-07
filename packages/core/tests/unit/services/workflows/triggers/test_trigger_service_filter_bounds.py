# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for trigger-save filter bounds validation."""

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import TriggerValidationError
from chaoscypher_core.services.workflows.triggers.management.service import TriggerService


@pytest.fixture
def service() -> TriggerService:
    storage = MagicMock()
    storage.create_trigger.side_effect = lambda d: d
    storage.get_trigger.return_value = {
        "id": "t-1",
        "database_name": "test_db",
        "filters": {},
        "user_id": None,
    }
    storage.update_trigger.side_effect = lambda tid, upd: {**upd, "id": tid}
    return TriggerService(storage=storage, database_name="test_db")


def _nested(depth: int) -> dict:
    """Return a dict nested `depth` levels deep."""
    out: dict = {}
    cur = out
    for _ in range(depth):
        cur["k"] = {}
        cur = cur["k"]
    cur["leaf"] = "v"
    return out


def test_filters_depth_over_limit_rejected(service: TriggerService) -> None:
    with pytest.raises(TriggerValidationError):
        service.create_trigger(
            {
                "name": "x",
                "event_source": "e",
                "workflow_id": "wf",
                "filters": _nested(6),  # >5
            }
        )


def test_filters_depth_at_limit_accepted(service: TriggerService) -> None:
    # depth==5 is fine
    service.create_trigger(
        {
            "name": "x",
            "event_source": "e",
            "workflow_id": "wf",
            "filters": _nested(5),
        }
    )


def test_filters_too_many_keys_rejected(service: TriggerService) -> None:
    many = {f"k{i}": i for i in range(51)}
    with pytest.raises(TriggerValidationError):
        service.create_trigger(
            {
                "name": "x",
                "event_source": "e",
                "workflow_id": "wf",
                "filters": many,
            }
        )


def test_filters_oversize_rejected(service: TriggerService) -> None:
    big_val = "x" * 17_000
    with pytest.raises(TriggerValidationError):
        service.create_trigger(
            {
                "name": "x",
                "event_source": "e",
                "workflow_id": "wf",
                "filters": {"k": big_val},
            }
        )


def test_update_filters_also_validated(service: TriggerService) -> None:
    with pytest.raises(TriggerValidationError):
        service.update_trigger("t-1", {"filters": _nested(6)})
