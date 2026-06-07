# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for TriggerService."""

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.workflows.triggers.management.service import TriggerService


@pytest.fixture
def mock_storage():
    """Create a mock TriggerStorageProtocol."""
    return MagicMock()


@pytest.fixture
def service(mock_storage):
    """Create TriggerService with mock storage."""
    return TriggerService(storage=mock_storage, database_name="test_db")


# ============================================================================
# list_triggers
# ============================================================================


class TestListTriggers:
    """Tests for TriggerService.list_triggers."""

    def test_delegates_to_storage(self, service, mock_storage) -> None:
        mock_storage.list_triggers.return_value = [{"id": "t1"}]
        result = service.list_triggers()
        mock_storage.list_triggers.assert_called_once_with(
            database_name="test_db", event_source=None, enabled=None
        )
        assert result == [{"id": "t1"}]

    def test_passes_event_source_filter(self, service, mock_storage) -> None:
        mock_storage.list_triggers.return_value = []
        service.list_triggers(event_source="node.create")
        mock_storage.list_triggers.assert_called_once_with(
            database_name="test_db", event_source="node.create", enabled=None
        )

    def test_passes_enabled_filter(self, service, mock_storage) -> None:
        mock_storage.list_triggers.return_value = []
        service.list_triggers(enabled=True)
        mock_storage.list_triggers.assert_called_once_with(
            database_name="test_db", event_source=None, enabled=True
        )

    def test_filters_by_workflow_id_in_service(self, service, mock_storage) -> None:
        mock_storage.list_triggers.return_value = [
            {"id": "t1", "workflow_id": "w1"},
            {"id": "t2", "workflow_id": "w2"},
            {"id": "t3", "workflow_id": "w1"},
        ]
        result = service.list_triggers(workflow_id="w1")
        assert len(result) == 2
        assert all(t["workflow_id"] == "w1" for t in result)


# ============================================================================
# get_trigger
# ============================================================================


class TestGetTrigger:
    """Tests for TriggerService.get_trigger."""

    def test_returns_trigger(self, service, mock_storage) -> None:
        mock_storage.get_trigger.return_value = {"id": "t1", "name": "Test"}
        result = service.get_trigger("t1")
        assert result["name"] == "Test"
        mock_storage.get_trigger.assert_called_once_with("t1", "test_db")

    def test_returns_none_when_not_found(self, service, mock_storage) -> None:
        mock_storage.get_trigger.return_value = None
        assert service.get_trigger("missing") is None


# ============================================================================
# create_trigger
# ============================================================================


class TestCreateTrigger:
    """Tests for TriggerService.create_trigger."""

    @pytest.fixture
    def _stub_create(self, mock_storage):
        """Make create_trigger return a full dict so the logger doesn't KeyError."""
        mock_storage.create_trigger.side_effect = lambda d: d

    @pytest.mark.usefixtures("_stub_create")
    def test_creates_with_required_fields(self, service, mock_storage) -> None:
        service.create_trigger(
            {
                "name": "Test",
                "event_source": "node.create",
                "workflow_id": "w1",
            }
        )
        call_args = mock_storage.create_trigger.call_args[0][0]
        assert call_args["name"] == "Test"
        assert call_args["event_source"] == "node.create"
        assert call_args["workflow_id"] == "w1"
        assert call_args["database_name"] == "test_db"
        assert "id" in call_args
        assert "created_at" in call_args
        assert "updated_at" in call_args

    @pytest.mark.usefixtures("_stub_create")
    def test_applies_defaults(self, service, mock_storage) -> None:
        service.create_trigger(
            {
                "name": "Test",
                "event_source": "node.create",
                "workflow_id": "w1",
            }
        )
        call_args = mock_storage.create_trigger.call_args[0][0]
        assert call_args["enabled"] is True
        assert call_args["priority"] == 0
        assert call_args["filters"] == {}
        assert call_args["workflow_inputs"] is None

    @pytest.mark.usefixtures("_stub_create")
    def test_uses_provided_id(self, service, mock_storage) -> None:
        service.create_trigger(
            {
                "id": "custom-id",
                "name": "Test",
                "event_source": "node.create",
                "workflow_id": "w1",
            }
        )
        call_args = mock_storage.create_trigger.call_args[0][0]
        assert call_args["id"] == "custom-id"

    @pytest.mark.usefixtures("_stub_create")
    def test_returns_created_id(self, service, mock_storage) -> None:
        result = service.create_trigger(
            {
                "name": "Test",
                "event_source": "node.create",
                "workflow_id": "w1",
            }
        )
        assert isinstance(result, str)
        assert len(result) > 0


# ============================================================================
# update_trigger
# ============================================================================


class TestUpdateTrigger:
    """Tests for TriggerService.update_trigger."""

    def test_updates_allowed_fields(self, service, mock_storage) -> None:
        mock_storage.get_trigger.return_value = {"id": "t1", "name": "Old"}
        result = service.update_trigger("t1", {"name": "New", "enabled": False})
        assert result is True
        call_args = mock_storage.update_trigger.call_args[0]
        assert call_args[0] == "t1"
        assert call_args[1]["name"] == "New"
        assert call_args[1]["enabled"] is False
        assert "updated_at" in call_args[1]

    def test_rejects_unknown_fields(self, service, mock_storage) -> None:
        mock_storage.get_trigger.return_value = {"id": "t1"}
        service.update_trigger("t1", {"name": "New", "hacked": "bad"})
        call_args = mock_storage.update_trigger.call_args[0][1]
        assert "name" in call_args
        assert "hacked" not in call_args

    def test_returns_false_when_not_found(self, service, mock_storage) -> None:
        mock_storage.get_trigger.return_value = None
        result = service.update_trigger("missing", {"name": "X"})
        assert result is False
        mock_storage.update_trigger.assert_not_called()

    def test_all_allowed_fields_accepted(self, service, mock_storage) -> None:
        mock_storage.get_trigger.return_value = {"id": "t1"}
        service.update_trigger(
            "t1",
            {
                "name": "N",
                "event_source": "e",
                "filters": {"k": "v"},
                "workflow_id": "w",
                "workflow_inputs": {"i": 1},
                "enabled": True,
                "priority": 5,
            },
        )
        call_args = mock_storage.update_trigger.call_args[0][1]
        for field in [
            "name",
            "event_source",
            "filters",
            "workflow_id",
            "workflow_inputs",
            "enabled",
            "priority",
        ]:
            assert field in call_args


# ============================================================================
# delete_trigger
# ============================================================================


class TestDeleteTrigger:
    """Tests for TriggerService.delete_trigger."""

    def test_deletes_existing_trigger(self, service, mock_storage) -> None:
        mock_storage.get_trigger.return_value = {"id": "t1", "name": "Test"}
        result = service.delete_trigger("t1")
        assert result is True
        mock_storage.delete_trigger.assert_called_once_with("t1")

    def test_returns_false_when_not_found(self, service, mock_storage) -> None:
        mock_storage.get_trigger.return_value = None
        result = service.delete_trigger("missing")
        assert result is False
        mock_storage.delete_trigger.assert_not_called()

    def test_checks_existence_first(self, service, mock_storage) -> None:
        mock_storage.get_trigger.return_value = {"id": "t1"}
        service.delete_trigger("t1")
        mock_storage.get_trigger.assert_called_once_with("t1", "test_db")


# ============================================================================
# toggle_trigger
# ============================================================================


class TestToggleTrigger:
    """Tests for TriggerService.toggle_trigger."""

    def test_enables_trigger(self, service, mock_storage) -> None:
        mock_storage.get_trigger.return_value = {"id": "t1"}
        result = service.toggle_trigger("t1", enabled=True)
        assert result is True
        call_args = mock_storage.update_trigger.call_args[0][1]
        assert call_args["enabled"] is True

    def test_disables_trigger(self, service, mock_storage) -> None:
        mock_storage.get_trigger.return_value = {"id": "t1"}
        result = service.toggle_trigger("t1", enabled=False)
        assert result is True
        call_args = mock_storage.update_trigger.call_args[0][1]
        assert call_args["enabled"] is False

    def test_returns_false_when_not_found(self, service, mock_storage) -> None:
        mock_storage.get_trigger.return_value = None
        result = service.toggle_trigger("missing", enabled=True)
        assert result is False
