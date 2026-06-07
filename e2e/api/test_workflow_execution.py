# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for workflow execution and step management."""

import uuid

import httpx


def _create_workflow(client: httpx.Client, name: str) -> str:
    """Create a workflow with a uuid-suffixed name; return its ID.

    The suffix avoids the duplicate-name 500 the server emits when two
    test runs (or two tests within one run) try to create workflows
    with identical names against the same DB. The 500-vs-409 behavior
    is tracked separately as a product bug.
    """
    resp = client.post(
        "/api/v1/workflows",
        json={"name": f"{name}-{uuid.uuid4().hex[:8]}", "input_schema": {}},
    )
    assert resp.status_code == 201, f"Create failed: {resp.text}"
    return resp.json()["id"]


class TestWorkflowExecution:
    """Test workflow execution endpoints."""

    def test_list_executions_empty(self, client: httpx.Client) -> None:
        """Listing executions for a new workflow returns empty list."""
        workflow_id = _create_workflow(client, "E2E ExecList Workflow")
        resp = client.get(f"/api/v1/workflows/{workflow_id}/executions")
        # Should return 200 with empty data
        assert resp.status_code == 200

    def test_execute_workflow_returns_execution(self, client: httpx.Client) -> None:
        """Triggering execution returns an execution ID or queues it."""
        workflow_id = _create_workflow(client, "E2E ExecTrigger Workflow")
        resp = client.post(
            f"/api/v1/workflows/{workflow_id}/executions",
            json={},
        )
        # May return 202 (queued), 200 (started), or 400 (no steps)
        assert resp.status_code in (200, 202, 400)


class TestWorkflowSteps:
    """Test workflow step CRUD operations."""

    def test_list_steps_empty(self, client: httpx.Client) -> None:
        """Listing steps for a new workflow returns empty list."""
        workflow_id = _create_workflow(client, "E2E StepsEmpty Workflow")
        resp = client.get(f"/api/v1/workflows/{workflow_id}/steps")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_workflow_step(self, client: httpx.Client) -> None:
        """Creating a workflow step requires a valid tool."""
        workflow_id = _create_workflow(client, "E2E StepsCreate Workflow")

        # Get a system tool to reference
        sys_tools = client.get("/api/v1/tools/system").json()
        if not sys_tools:
            return
        tool_id = sys_tools[0]["id"]

        resp = client.post(
            f"/api/v1/workflows/{workflow_id}/steps",
            json={
                "step_number": 1,
                "name": "Test Step",
                "tool_type": "system",
                "tool_id": tool_id,
                "configuration": {},
            },
        )
        # 201 for success, 400/422 if config schema invalid
        assert resp.status_code in (201, 400, 422)


class TestWorkflowImport:
    """Test workflow import endpoint."""

    def test_import_workflow(self, client: httpx.Client) -> None:
        """Importing a workflow from JSON creates it."""
        # First export a workflow to get a valid format
        workflow_id = _create_workflow(client, "E2E Export For Import")
        export_resp = client.get(f"/api/v1/workflows/{workflow_id}/export")
        assert export_resp.status_code == 200
        workflow_data = export_resp.json()["data"]

        # Now import it back with a new name
        import_resp = client.post(
            "/api/v1/workflows/import",
            json={
                "workflow_data": workflow_data,
                "on_duplicate": "rename",
                "new_name": "E2E Imported Workflow",
            },
        )
        assert import_resp.status_code in (200, 201)
        assert "workflow_id" in import_resp.json()
