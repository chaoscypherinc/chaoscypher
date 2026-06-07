# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for workflows API handler logic.

Verifies that each handler calls the correct service method with the correct
arguments. FastAPI DI is bypassed — service mocks are passed directly as
function arguments.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from chaoscypher_cortex.features.workflows.api import (
    create_workflow,
    create_workflow_step,
    delete_workflow,
    delete_workflow_step,
    export_workflow,
    get_global_stats,
    get_workflow,
    get_workflow_step,
    import_workflow,
    list_workflow_steps,
    list_workflow_triggers,
    list_workflows,
    reorder_workflow_steps,
    update_workflow,
    update_workflow_step,
)
from chaoscypher_cortex.features.workflows.models import (
    WorkflowCreate,
    WorkflowStepCreate,
    WorkflowStepReorderRequest,
    WorkflowStepUpdate,
    WorkflowUpdate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _workflow_dict(workflow_id: str = "wf-1") -> dict:
    """Return a minimal WorkflowDict-compatible mapping."""
    return {
        "id": workflow_id,
        "database_name": "default",
        "name": "My Workflow",
        "description": None,
        "category": None,
        "is_system": False,
        "is_active": True,
        "expose_as_ai_tool": False,
        "input_schema": {},
        "output_schema": None,
        "allow_parallel_execution": True,
        "timeout_seconds": None,
        "max_retries": 0,
        "tags": [],
        "icon": None,
        "version": "1.0",
        "created_by": None,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "last_executed_at": None,
    }


def _step_dict(step_id: str = "step-1", workflow_id: str = "wf-1") -> dict:
    """Return a minimal WorkflowStepDict-compatible mapping."""
    return {
        "id": step_id,
        "workflow_id": workflow_id,
        "step_number": 1,
        "name": "Step One",
        "description": None,
        "tool_type": "system",
        "tool_id": "sys-tool-1",
        "configuration": {},
        "condition": None,
        "retry_on_failure": False,
        "timeout_seconds": None,
        "depends_on": [],
        "continue_on_error": False,
        "thinking_mode": None,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


def _stats_dict() -> dict:
    """Return a minimal GlobalWorkflowStatsResponse-compatible mapping."""
    return {
        "total_workflows": 5,
        "active_workflows": 4,
        "inactive_workflows": 1,
        "total_executions": 20,
        "successful_executions": 18,
        "failed_executions": 2,
        "cancelled_executions": 0,
        "success_rate": 0.9,
    }


def _trigger_dict(trigger_id: str = "trig-1") -> dict:
    """Return a minimal TriggerDict-compatible mapping."""
    return {
        "id": trigger_id,
        "name": "On Schedule",
        "workflow_id": "wf-1",
        "event_type": "schedule",
        "configuration": {},
        "is_active": True,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


def _minimal_step_create() -> WorkflowStepCreate:
    """Return a minimal WorkflowStepCreate instance."""
    from chaoscypher_core.models import StepToolType

    return WorkflowStepCreate(
        step_number=1,
        name="Step One",
        tool_type=StepToolType.SYSTEM_TOOL,
        tool_id="sys-tool-1",
        configuration={},
    )


# ---------------------------------------------------------------------------
# TestGetGlobalStats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetGlobalStats:
    """Tests for the get_global_stats handler."""

    @pytest.mark.asyncio
    async def test_returns_stats_dict(self) -> None:
        """Handler delegates to workflow_service.get_global_stats and returns result."""
        mock_service = MagicMock()
        mock_service.get_global_stats.return_value = _stats_dict()

        result = await get_global_stats(_="test-user", workflow_service=mock_service)

        mock_service.get_global_stats.assert_called_once_with()
        assert result["total_workflows"] == 5
        assert result["success_rate"] == 0.9


# ---------------------------------------------------------------------------
# TestListWorkflows
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListWorkflows:
    """Tests for the list_workflows handler."""

    @pytest.mark.asyncio
    async def test_returns_paginated_workflows(self) -> None:
        """Handler calls list_workflows and wraps results in PaginatedWorkflowsResponse."""
        mock_service = MagicMock()
        mock_service.list_workflows.return_value = [
            _workflow_dict("wf-1"),
            _workflow_dict("wf-2"),
        ]

        result = await list_workflows(
            workflow_service=mock_service,
            pagination=(1, 50),
            _="test-user",
            category=None,
            is_system=None,
            is_active=None,
            expose_as_ai_tool=None,
        )

        mock_service.list_workflows.assert_called_once_with(
            category=None,
            is_system=None,
            is_active=None,
            expose_as_ai_tool=None,
        )
        assert len(result.data) == 2
        assert result.data[0].id == "wf-1"

    @pytest.mark.asyncio
    async def test_passes_filters_to_service(self) -> None:
        """Handler forwards all filter parameters to the service."""
        mock_service = MagicMock()
        mock_service.list_workflows.return_value = [_workflow_dict()]

        await list_workflows(
            workflow_service=mock_service,
            pagination=(1, 50),
            _="test-user",
            category="research",
            is_system=False,
            is_active=True,
            expose_as_ai_tool=True,
        )

        mock_service.list_workflows.assert_called_once_with(
            category="research",
            is_system=False,
            is_active=True,
            expose_as_ai_tool=True,
        )

    @pytest.mark.asyncio
    async def test_pagination_slices_results(self) -> None:
        """Handler slices list according to page and page_size params."""
        mock_service = MagicMock()
        mock_service.list_workflows.return_value = [_workflow_dict(f"wf-{i}") for i in range(5)]

        result = await list_workflows(
            workflow_service=mock_service,
            pagination=(2, 2),
            _="test-user",
            category=None,
            is_system=None,
            is_active=None,
            expose_as_ai_tool=None,
        )

        assert len(result.data) == 2
        assert result.data[0].id == "wf-2"
        assert result.pagination.page == 2

    @pytest.mark.asyncio
    async def test_returns_empty_response_when_no_workflows(self) -> None:
        """Handler returns empty paginated response when service returns empty list."""
        mock_service = MagicMock()
        mock_service.list_workflows.return_value = []

        result = await list_workflows(
            workflow_service=mock_service,
            pagination=(1, 50),
            _="test-user",
            category=None,
            is_system=None,
            is_active=None,
            expose_as_ai_tool=None,
        )

        assert result.data == []
        assert result.pagination.total == 0


# ---------------------------------------------------------------------------
# TestCreateWorkflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateWorkflow:
    """Tests for the create_workflow handler."""

    @pytest.mark.asyncio
    async def test_creates_and_returns_workflow(self) -> None:
        """Handler calls create_workflow, then get_workflow, and returns the result."""
        mock_service = MagicMock()
        mock_service.create_workflow.return_value = "wf-new"
        mock_service.get_workflow.return_value = _workflow_dict("wf-new")

        workflow_create = WorkflowCreate(name="New Workflow", input_schema={})

        result = await create_workflow(
            workflow_create=workflow_create,
            workflow_service=mock_service,
            _="test-user",
        )

        mock_service.create_workflow.assert_called_once()
        payload = mock_service.create_workflow.call_args[0][0]
        assert payload["name"] == "New Workflow"

        mock_service.get_workflow.assert_called_once_with("wf-new")
        assert result["id"] == "wf-new"

    @pytest.mark.asyncio
    async def test_raises_500_when_get_returns_none(self) -> None:
        """Handler raises HTTP 500 when get_workflow returns None after creation."""
        mock_service = MagicMock()
        mock_service.create_workflow.return_value = "wf-new"
        mock_service.get_workflow.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await create_workflow(
                workflow_create=WorkflowCreate(name="Broken", input_schema={}),
                workflow_service=mock_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 500
        # ``detail`` is the structured error envelope dict
        # (see shared/api/errors.py) — assert against its ``message`` key.
        assert "Failed to create workflow" in exc_info.value.detail["message"]

    @pytest.mark.asyncio
    async def test_passes_full_model_dump_to_service(self) -> None:
        """Handler passes model_dump() with all fields to create_workflow."""
        mock_service = MagicMock()
        mock_service.create_workflow.return_value = "wf-1"
        mock_service.get_workflow.return_value = _workflow_dict()

        workflow_create = WorkflowCreate(
            name="Full Workflow",
            description="A description",
            input_schema={"type": "object"},
            tags=["research", "export"],
            allow_parallel_execution=False,
        )

        await create_workflow(
            workflow_create=workflow_create,
            workflow_service=mock_service,
            _="test-user",
        )

        payload = mock_service.create_workflow.call_args[0][0]
        assert payload["description"] == "A description"
        assert payload["tags"] == ["research", "export"]
        assert payload["allow_parallel_execution"] is False


# ---------------------------------------------------------------------------
# TestGetWorkflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetWorkflow:
    """Tests for the get_workflow handler."""

    @pytest.mark.asyncio
    async def test_returns_workflow_dict(self) -> None:
        """Handler calls get_workflow and returns the result."""
        mock_service = MagicMock()
        mock_service.get_workflow.return_value = _workflow_dict("wf-99")

        result = await get_workflow(
            workflow_id="wf-99",
            workflow_service=mock_service,
            _="test-user",
        )

        mock_service.get_workflow.assert_called_once_with("wf-99")
        assert result["id"] == "wf-99"

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self) -> None:
        """Handler raises HTTP 404 when service returns None."""
        mock_service = MagicMock()
        mock_service.get_workflow.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_workflow(
                workflow_id="missing",
                workflow_service=mock_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestUpdateWorkflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateWorkflow:
    """Tests for the update_workflow handler."""

    @pytest.mark.asyncio
    async def test_updates_and_returns_workflow(self) -> None:
        """Handler calls update_workflow, then get_workflow, and returns fresh state."""
        mock_service = MagicMock()
        updated = _workflow_dict("wf-5")
        updated["name"] = "Renamed Workflow"
        mock_service.update_workflow.return_value = True
        mock_service.get_workflow.return_value = updated

        workflow_update = WorkflowUpdate(name="Renamed Workflow")

        result = await update_workflow(
            workflow_id="wf-5",
            workflow_update=workflow_update,
            workflow_service=mock_service,
            _="test-user",
        )

        mock_service.update_workflow.assert_called_once_with("wf-5", {"name": "Renamed Workflow"})
        mock_service.get_workflow.assert_called_once_with("wf-5")
        assert result["name"] == "Renamed Workflow"

    @pytest.mark.asyncio
    async def test_raises_404_when_update_returns_falsy(self) -> None:
        """Handler raises HTTP 404 when update_workflow returns False (not found)."""
        mock_service = MagicMock()
        mock_service.update_workflow.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            await update_workflow(
                workflow_id="missing",
                workflow_update=WorkflowUpdate(name="x"),
                workflow_service=mock_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_500_when_get_after_update_returns_none(self) -> None:
        """Handler raises HTTP 500 when get_workflow returns None after update."""
        mock_service = MagicMock()
        mock_service.update_workflow.return_value = True
        mock_service.get_workflow.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await update_workflow(
                workflow_id="wf-5",
                workflow_update=WorkflowUpdate(name="x"),
                workflow_service=mock_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_excludes_unset_fields_from_update_payload(self) -> None:
        """update_workflow uses model_dump(exclude_unset=True) so only set fields are sent."""
        mock_service = MagicMock()
        mock_service.update_workflow.return_value = True
        mock_service.get_workflow.return_value = _workflow_dict()

        workflow_update = WorkflowUpdate(is_active=False)

        await update_workflow(
            workflow_id="wf-1",
            workflow_update=workflow_update,
            workflow_service=mock_service,
            _="test-user",
        )

        payload = mock_service.update_workflow.call_args[0][1]
        assert payload == {"is_active": False}
        assert "name" not in payload


# ---------------------------------------------------------------------------
# TestDeleteWorkflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteWorkflow:
    """Tests for the delete_workflow handler."""

    @pytest.mark.asyncio
    async def test_deletes_and_returns_204_response(self) -> None:
        """Handler calls delete_workflow and returns a 204 Response."""
        mock_service = MagicMock()
        mock_service.delete_workflow.return_value = True

        result = await delete_workflow(
            workflow_id="wf-del",
            workflow_service=mock_service,
            _="test-user",
        )

        mock_service.delete_workflow.assert_called_once_with("wf-del")
        assert result.status_code == 204

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self) -> None:
        """Handler raises HTTP 404 when delete_workflow returns False."""
        mock_service = MagicMock()
        mock_service.delete_workflow.return_value = False

        with pytest.raises(HTTPException) as exc_info:
            await delete_workflow(
                workflow_id="missing",
                workflow_service=mock_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestExportWorkflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExportWorkflow:
    """Tests for the export_workflow handler."""

    @pytest.mark.asyncio
    async def test_returns_export_response(self) -> None:
        """Handler calls service.export_workflow and wraps result in WorkflowExportResponse."""
        mock_service = MagicMock()
        mock_service.export_workflow.return_value = {"workflow": {"id": "wf-1"}, "steps": []}

        result = await export_workflow(
            workflow_id="wf-1",
            service=mock_service,
            _="test-user",
        )

        mock_service.export_workflow.assert_called_once_with("wf-1")
        assert result.data["workflow"]["id"] == "wf-1"
        assert result.message == "Workflow exported successfully"

    @pytest.mark.asyncio
    async def test_raises_404_on_value_error(self) -> None:
        """Handler raises HTTP 404 when service raises ValueError (not found)."""
        mock_service = MagicMock()
        mock_service.export_workflow.side_effect = ValueError("not found")

        with pytest.raises(HTTPException) as exc_info:
            await export_workflow(
                workflow_id="missing",
                service=mock_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestImportWorkflow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImportWorkflow:
    """Tests for the import_workflow handler."""

    @pytest.mark.asyncio
    async def test_imports_and_returns_response(self) -> None:
        """Handler calls service.import_workflow and returns a WorkflowImportResponse."""
        mock_service = MagicMock()
        mock_service.import_workflow.return_value = {
            "workflow_id": "wf-imported",
            "message": "Workflow imported successfully",
            "was_existing": False,
        }

        import_request = MagicMock()
        import_request.workflow_data = {"workflow": {"name": "Test"}, "steps": []}
        import_request.on_duplicate = "fail"
        import_request.new_name = None
        import_request.import_as_inactive = False

        result = await import_workflow(
            import_request=import_request,
            service=mock_service,
            _="test-user",
        )

        mock_service.import_workflow.assert_called_once_with(
            workflow_data=import_request.workflow_data,
            on_duplicate="fail",
            new_name=None,
            import_as_inactive=False,
        )
        assert result.workflow_id == "wf-imported"
        assert result.was_existing is False

    @pytest.mark.asyncio
    async def test_raises_400_on_value_error(self) -> None:
        """Handler raises HTTP 400 when service raises ValueError (validation error)."""
        mock_service = MagicMock()
        mock_service.import_workflow.side_effect = ValueError("Duplicate name")

        import_request = MagicMock()
        import_request.workflow_data = {}
        import_request.on_duplicate = "fail"
        import_request.new_name = None
        import_request.import_as_inactive = False

        with pytest.raises(HTTPException) as exc_info:
            await import_workflow(
                import_request=import_request,
                service=mock_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_passes_all_import_options_to_service(self) -> None:
        """Handler forwards workflow_data, on_duplicate, new_name, import_as_inactive."""
        mock_service = MagicMock()
        mock_service.import_workflow.return_value = {
            "workflow_id": "wf-1",
            "message": "done",
            "was_existing": True,
        }

        import_request = MagicMock()
        import_request.workflow_data = {"workflow": {"name": "Copy"}}
        import_request.on_duplicate = "rename"
        import_request.new_name = "My Copy"
        import_request.import_as_inactive = True

        await import_workflow(
            import_request=import_request,
            service=mock_service,
            _="test-user",
        )

        mock_service.import_workflow.assert_called_once_with(
            workflow_data={"workflow": {"name": "Copy"}},
            on_duplicate="rename",
            new_name="My Copy",
            import_as_inactive=True,
        )


# ---------------------------------------------------------------------------
# TestListWorkflowSteps
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListWorkflowSteps:
    """Tests for the list_workflow_steps handler."""

    @pytest.mark.asyncio
    async def test_returns_steps_list(self) -> None:
        """Handler delegates to steps_service.list_steps and returns the result."""
        mock_steps_service = MagicMock()
        mock_steps_service.list_steps.return_value = [
            _step_dict("step-1"),
            _step_dict("step-2"),
        ]

        result = await list_workflow_steps(
            workflow_id="wf-1",
            steps_service=mock_steps_service,
            _="test-user",
        )

        mock_steps_service.list_steps.assert_called_once_with("wf-1")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestCreateWorkflowStep
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateWorkflowStep:
    """Tests for the create_workflow_step handler."""

    @pytest.mark.asyncio
    async def test_creates_and_returns_step(self) -> None:
        """Handler calls steps_service.create_step and returns the result."""
        mock_steps_service = MagicMock()
        mock_steps_service.create_step.return_value = _step_dict("step-new")

        step_create = _minimal_step_create()

        result = await create_workflow_step(
            workflow_id="wf-1",
            step_create=step_create,
            steps_service=mock_steps_service,
            _="test-user",
        )

        mock_steps_service.create_step.assert_called_once()
        call_args = mock_steps_service.create_step.call_args
        assert call_args[0][0] == "wf-1"
        assert result["id"] == "step-new"

    @pytest.mark.asyncio
    async def test_passes_model_dump_to_service(self) -> None:
        """Handler calls create_step with step_create.model_dump()."""
        mock_steps_service = MagicMock()
        mock_steps_service.create_step.return_value = _step_dict()

        step_create = _minimal_step_create()

        await create_workflow_step(
            workflow_id="wf-1",
            step_create=step_create,
            steps_service=mock_steps_service,
            _="test-user",
        )

        call_data = mock_steps_service.create_step.call_args[0][1]
        assert call_data["name"] == "Step One"
        assert call_data["tool_id"] == "sys-tool-1"


# ---------------------------------------------------------------------------
# TestGetWorkflowStep
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetWorkflowStep:
    """Tests for the get_workflow_step handler."""

    @pytest.mark.asyncio
    async def test_returns_step_dict(self) -> None:
        """Handler delegates to steps_service.get_step and returns the result."""
        mock_steps_service = MagicMock()
        mock_steps_service.get_step.return_value = _step_dict("step-42")

        result = await get_workflow_step(
            workflow_id="wf-1",
            step_id="step-42",
            steps_service=mock_steps_service,
            _="test-user",
        )

        mock_steps_service.get_step.assert_called_once_with("wf-1", "step-42")
        assert result["id"] == "step-42"

    @pytest.mark.asyncio
    async def test_propagates_not_found_exception(self) -> None:
        """Handler propagates exceptions raised by the service."""
        mock_steps_service = MagicMock()
        mock_steps_service.get_step.side_effect = HTTPException(status_code=404, detail="Not found")

        with pytest.raises(HTTPException) as exc_info:
            await get_workflow_step(
                workflow_id="wf-1",
                step_id="missing",
                steps_service=mock_steps_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestUpdateWorkflowStep
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateWorkflowStep:
    """Tests for the update_workflow_step handler."""

    @pytest.mark.asyncio
    async def test_updates_and_returns_step(self) -> None:
        """Handler calls steps_service.update_step and returns the updated step."""
        mock_steps_service = MagicMock()
        updated = _step_dict("step-5")
        updated["name"] = "Renamed Step"
        mock_steps_service.update_step.return_value = updated

        step_update = WorkflowStepUpdate(name="Renamed Step")

        result = await update_workflow_step(
            workflow_id="wf-1",
            step_id="step-5",
            step_update=step_update,
            steps_service=mock_steps_service,
            _="test-user",
        )

        mock_steps_service.update_step.assert_called_once_with(
            "wf-1", "step-5", {"name": "Renamed Step"}
        )
        assert result["name"] == "Renamed Step"

    @pytest.mark.asyncio
    async def test_excludes_unset_fields_from_update_payload(self) -> None:
        """Handler uses model_dump(exclude_unset=True) so only set fields are sent."""
        mock_steps_service = MagicMock()
        mock_steps_service.update_step.return_value = _step_dict()

        step_update = WorkflowStepUpdate(retry_on_failure=True)

        await update_workflow_step(
            workflow_id="wf-1",
            step_id="step-1",
            step_update=step_update,
            steps_service=mock_steps_service,
            _="test-user",
        )

        payload = mock_steps_service.update_step.call_args[0][2]
        assert payload == {"retry_on_failure": True}
        assert "name" not in payload


# ---------------------------------------------------------------------------
# TestDeleteWorkflowStep
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteWorkflowStep:
    """Tests for the delete_workflow_step handler."""

    @pytest.mark.asyncio
    async def test_deletes_and_returns_204_response(self) -> None:
        """Handler calls steps_service.delete_step and returns a 204 Response."""
        mock_steps_service = MagicMock()
        mock_steps_service.delete_step.return_value = None

        result = await delete_workflow_step(
            workflow_id="wf-1",
            step_id="step-del",
            steps_service=mock_steps_service,
            _="test-user",
        )

        mock_steps_service.delete_step.assert_called_once_with("wf-1", "step-del")
        assert result.status_code == 204

    @pytest.mark.asyncio
    async def test_propagates_not_found_exception(self) -> None:
        """Handler propagates exceptions raised by the service."""
        mock_steps_service = MagicMock()
        mock_steps_service.delete_step.side_effect = HTTPException(
            status_code=404, detail="Not found"
        )

        with pytest.raises(HTTPException) as exc_info:
            await delete_workflow_step(
                workflow_id="wf-1",
                step_id="missing",
                steps_service=mock_steps_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestReorderWorkflowSteps
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReorderWorkflowSteps:
    """Tests for the reorder_workflow_steps handler."""

    @pytest.mark.asyncio
    async def test_reorders_and_returns_steps(self) -> None:
        """Handler calls steps_service.reorder_steps and returns the reordered list."""
        mock_steps_service = MagicMock()
        mock_steps_service.reorder_steps.return_value = [
            _step_dict("step-2"),
            _step_dict("step-1"),
        ]

        reorder_request = WorkflowStepReorderRequest(step_order=["step-2", "step-1"])

        result = await reorder_workflow_steps(
            workflow_id="wf-1",
            reorder_request=reorder_request,
            steps_service=mock_steps_service,
            _="test-user",
        )

        mock_steps_service.reorder_steps.assert_called_once_with("wf-1", ["step-2", "step-1"])
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestListWorkflowTriggers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListWorkflowTriggers:
    """Tests for the list_workflow_triggers handler."""

    @pytest.mark.asyncio
    async def test_returns_triggers_for_existing_workflow(self) -> None:
        """Handler verifies workflow exists then returns triggers from trigger_service."""
        mock_workflow_service = MagicMock()
        mock_workflow_service.get_workflow.return_value = _workflow_dict("wf-1")
        mock_trigger_service = MagicMock()
        mock_trigger_service.list_triggers.return_value = [
            _trigger_dict("trig-1"),
            _trigger_dict("trig-2"),
        ]

        result = await list_workflow_triggers(
            workflow_id="wf-1",
            trigger_service=mock_trigger_service,
            workflow_service=mock_workflow_service,
            _="test-user",
        )

        mock_workflow_service.get_workflow.assert_called_once_with("wf-1")
        mock_trigger_service.list_triggers.assert_called_once_with(workflow_id="wf-1")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_raises_404_when_workflow_not_found(self) -> None:
        """Handler raises HTTP 404 if workflow does not exist before listing triggers."""
        mock_workflow_service = MagicMock()
        mock_workflow_service.get_workflow.return_value = None
        mock_trigger_service = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await list_workflow_triggers(
                workflow_id="missing",
                trigger_service=mock_trigger_service,
                workflow_service=mock_workflow_service,
                _="test-user",
            )

        assert exc_info.value.status_code == 404
        mock_trigger_service.list_triggers.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_triggers(self) -> None:
        """Handler returns empty list when workflow has no triggers."""
        mock_workflow_service = MagicMock()
        mock_workflow_service.get_workflow.return_value = _workflow_dict("wf-5")
        mock_trigger_service = MagicMock()
        mock_trigger_service.list_triggers.return_value = []

        result = await list_workflow_triggers(
            workflow_id="wf-5",
            trigger_service=mock_trigger_service,
            workflow_service=mock_workflow_service,
            _="test-user",
        )

        assert result == []
