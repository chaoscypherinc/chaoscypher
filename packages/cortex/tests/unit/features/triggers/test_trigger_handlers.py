# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for triggers API handler logic.

Verifies that each handler calls the correct TriggerService method with the
correct arguments and transforms the response correctly.  FastAPI DI is
bypassed — the service mock is passed directly as a function argument.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.triggers.api import (
    create_trigger,
    delete_trigger,
    get_trigger,
    list_triggers,
    update_trigger,
)
from chaoscypher_cortex.features.triggers.models import TriggerCreate, TriggerUpdate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _trigger_dict(trigger_id: str = "t-1") -> dict:
    """Return a minimal TriggerDict-compatible mapping."""
    return {
        "id": trigger_id,
        "name": "On new source",
        "event_source": "source.created",
        "filters": {"key": "val"},
        "workflow_id": "wf-1",
        "workflow_inputs": None,
        "enabled": True,
        "priority": 0,
        "created_at": _NOW,
        "updated_at": _NOW,
    }


def _summary_dict(trigger_id: str = "t-1") -> dict:
    """Return a minimal TriggerSummaryDict-compatible mapping."""
    return {
        "id": trigger_id,
        "name": "On new source",
        "event_source": "source.created",
        "workflow_id": "wf-1",
        "enabled": True,
        "priority": 0,
        "created_at": _NOW,
        "updated_at": _NOW,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListTriggers:
    """Tests for the list_triggers handler."""

    @pytest.mark.asyncio
    async def test_returns_paginated_response(self) -> None:
        """Handler calls list_triggers and wraps results in PaginatedTriggersResponse."""
        mock_service = MagicMock()
        mock_service.list_triggers.return_value = [_summary_dict("t-1"), _summary_dict("t-2")]

        result = await list_triggers(
            trigger_service=mock_service,
            pagination=(1, 50),
            _="test-user",
            event_source=None,
            enabled=None,
        )

        mock_service.list_triggers.assert_called_once_with(event_source=None, enabled=None)
        assert len(result.data) == 2
        assert result.data[0].id == "t-1"
        assert result.data[1].id == "t-2"
        assert result.pagination.total == 2

    @pytest.mark.asyncio
    async def test_passes_filters_to_service(self) -> None:
        """Handler forwards event_source and enabled filters to the service."""
        mock_service = MagicMock()
        mock_service.list_triggers.return_value = [_summary_dict()]

        await list_triggers(
            trigger_service=mock_service,
            pagination=(1, 50),
            _="test-user",
            event_source="source.created",
            enabled=True,
        )

        mock_service.list_triggers.assert_called_once_with(
            event_source="source.created", enabled=True
        )

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_triggers(self) -> None:
        """Handler returns an empty data list when the service returns []."""
        mock_service = MagicMock()
        mock_service.list_triggers.return_value = []

        result = await list_triggers(
            trigger_service=mock_service,
            pagination=(1, 50),
            _="test-user",
            event_source=None,
            enabled=None,
        )

        assert result.data == []
        assert result.pagination.total == 0

    @pytest.mark.asyncio
    async def test_pagination_slices_results(self) -> None:
        """Handler slices the full list according to the page/page_size params."""
        mock_service = MagicMock()
        items = [_summary_dict(f"t-{i}") for i in range(5)]
        mock_service.list_triggers.return_value = items

        result = await list_triggers(
            trigger_service=mock_service,
            pagination=(2, 2),
            _="test-user",
            event_source=None,
            enabled=None,
        )

        assert len(result.data) == 2
        assert result.data[0].id == "t-2"
        assert result.pagination.page == 2


@pytest.mark.unit
class TestCreateTrigger:
    """Tests for the create_trigger handler."""

    @pytest.mark.asyncio
    async def test_creates_and_returns_trigger(self) -> None:
        """Handler calls create_trigger then get_trigger and returns the result."""
        mock_service = MagicMock()
        mock_service.create_trigger.return_value = "t-new"
        mock_service.get_trigger.return_value = _trigger_dict("t-new")

        trigger_create = TriggerCreate(
            name="Watch sources",
            event_source="source.created",
            filters={},
            workflow_id="wf-1",
        )

        result = await create_trigger(
            trigger_create=trigger_create,
            trigger_service=mock_service,
            _="test-user",
        )

        mock_service.create_trigger.assert_called_once()
        call_kwargs = mock_service.create_trigger.call_args[0][0]
        assert call_kwargs["name"] == "Watch sources"
        assert call_kwargs["event_source"] == "source.created"
        assert call_kwargs["workflow_id"] == "wf-1"

        mock_service.get_trigger.assert_called_once_with("t-new")
        # FastAPI serializes the returned dict via the response_model — the raw
        # dict is the return value here because response_model is on the router.
        assert result["id"] == "t-new"

    @pytest.mark.asyncio
    async def test_excludes_unset_fields_from_create_data(self) -> None:
        """Optional fields absent in the request are not forwarded to the service.

        create_trigger calls model_dump(exclude_unset=True).
        """
        mock_service = MagicMock()
        mock_service.create_trigger.return_value = "t-1"
        mock_service.get_trigger.return_value = _trigger_dict()

        trigger_create = TriggerCreate(
            name="Min",
            event_source="evt",
            filters={},
            workflow_id="wf-1",
        )

        await create_trigger(
            trigger_create=trigger_create,
            trigger_service=mock_service,
            _="test-user",
        )

        payload = mock_service.create_trigger.call_args[0][0]
        # enabled and priority have defaults so they ARE set; workflow_inputs should be absent
        assert "workflow_inputs" not in payload


@pytest.mark.unit
class TestGetTrigger:
    """Tests for the get_trigger handler."""

    @pytest.mark.asyncio
    async def test_returns_trigger_dict(self) -> None:
        """Handler forwards trigger_id to the service and returns its result."""
        mock_service = MagicMock()
        mock_service.get_trigger.return_value = _trigger_dict("t-99")

        result = await get_trigger(
            trigger_id="t-99",
            trigger_service=mock_service,
            _="test-user",
        )

        mock_service.get_trigger.assert_called_once_with("t-99")
        assert result["id"] == "t-99"

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self) -> None:
        """Handler raises HTTP 404 via ``raise_if_not_found`` when service returns None."""
        from fastapi import HTTPException

        mock_service = MagicMock()
        mock_service.get_trigger.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_trigger(
                trigger_id="missing",
                trigger_service=mock_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 404


@pytest.mark.unit
class TestUpdateTrigger:
    """Tests for the update_trigger handler."""

    @pytest.mark.asyncio
    async def test_updates_and_returns_trigger(self) -> None:
        """Handler calls update_trigger then get_trigger and returns the fresh state."""
        mock_service = MagicMock()
        updated = _trigger_dict("t-1")
        updated["name"] = "Renamed"
        mock_service.get_trigger.return_value = updated

        trigger_update = TriggerUpdate(name="Renamed")

        result = await update_trigger(
            trigger_id="t-1",
            trigger_update=trigger_update,
            trigger_service=mock_service,
            _="test-user",
            trigger=_trigger_dict("t-1"),
        )

        mock_service.update_trigger.assert_called_once_with("t-1", {"name": "Renamed"})
        mock_service.get_trigger.assert_called_once_with("t-1")
        assert result["name"] == "Renamed"

    @pytest.mark.asyncio
    async def test_excludes_unset_fields_from_update_payload(self) -> None:
        """update_trigger calls model_dump(exclude_unset=True) so only set fields are sent."""
        mock_service = MagicMock()
        mock_service.get_trigger.return_value = _trigger_dict()

        trigger_update = TriggerUpdate(enabled=False)

        await update_trigger(
            trigger_id="t-1",
            trigger_update=trigger_update,
            trigger_service=mock_service,
            _="test-user",
            trigger=_trigger_dict("t-1"),
        )

        payload = mock_service.update_trigger.call_args[0][1]
        assert payload == {"enabled": False}
        assert "name" not in payload


@pytest.mark.unit
class TestDeleteTrigger:
    """Tests for the delete_trigger handler."""

    @pytest.mark.asyncio
    async def test_calls_delete_and_returns_none(self) -> None:
        """Handler delegates to delete_trigger and returns None (204)."""
        mock_service = MagicMock()

        result = await delete_trigger(
            trigger_id="t-del",
            trigger_service=mock_service,
            _="test-user",
            trigger=_trigger_dict("t-del"),
        )

        mock_service.delete_trigger.assert_called_once_with("t-del")
        assert result is None


@pytest.mark.unit
class TestUpdateTriggerOwnership:
    """PATCH handler must require ownership-checked trigger from dependency."""

    @pytest.mark.asyncio
    async def test_update_passes_owned_trigger_through(self) -> None:
        """Handler accepts pre-fetched trigger from require_trigger_ownership."""
        from chaoscypher_cortex.features.triggers.api import update_trigger
        from chaoscypher_cortex.features.triggers.models import TriggerUpdate

        mock_service = MagicMock()
        mock_service.get_trigger.return_value = _trigger_dict("t-1")
        owned = _trigger_dict("t-1")

        result = await update_trigger(
            trigger_id="t-1",
            trigger_update=TriggerUpdate(name="Renamed"),
            trigger_service=mock_service,
            _="test-user",
            trigger=owned,  # injected by require_trigger_ownership
        )

        mock_service.update_trigger.assert_called_once_with("t-1", {"name": "Renamed"})
        assert result["id"] == "t-1"


@pytest.mark.unit
class TestDeleteTriggerOwnership:
    @pytest.mark.asyncio
    async def test_delete_passes_owned_trigger_through(self) -> None:
        """Handler accepts pre-fetched trigger from require_trigger_ownership."""
        from chaoscypher_cortex.features.triggers.api import delete_trigger

        mock_service = MagicMock()
        owned = _trigger_dict("t-del")

        result = await delete_trigger(
            trigger_id="t-del",
            trigger_service=mock_service,
            _="test-user",
            trigger=owned,
        )

        mock_service.delete_trigger.assert_called_once_with("t-del")
        assert result is None
