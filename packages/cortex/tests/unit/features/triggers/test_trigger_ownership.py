# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for require_trigger_ownership dependency."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from chaoscypher_cortex.features.triggers.api import require_trigger_ownership


_NOW = datetime.now(UTC)


def _trigger() -> dict:
    return {
        "id": "t-1",
        "name": "T",
        "event_source": "e",
        "filters": {},
        "workflow_id": "wf",
        "workflow_inputs": None,
        "enabled": True,
        "priority": 0,
        "created_at": _NOW,
        "updated_at": _NOW,
    }


@pytest.mark.unit
class TestRequireTriggerOwnership:
    @pytest.mark.asyncio
    async def test_returns_trigger_when_found(self) -> None:
        """Dependency returns the trigger dict when the trigger exists."""
        service = MagicMock()
        service.get_trigger.return_value = _trigger()

        result = await require_trigger_ownership(
            trigger_id="t-1",
            trigger_service=service,
            _="test-user",
        )

        assert result["id"] == "t-1"
        service.get_trigger.assert_called_once_with("t-1")

    @pytest.mark.asyncio
    async def test_missing_trigger_raises_404(self) -> None:
        """Dependency raises HTTP 404 when the trigger does not exist."""
        service = MagicMock()
        service.get_trigger.return_value = None

        with pytest.raises(HTTPException) as exc:
            await require_trigger_ownership(
                trigger_id="missing",
                trigger_service=service,
                _="test-user",
            )

        assert exc.value.status_code == 404
