# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for workflow endpoints."""

import uuid

import httpx


class TestWorkflows:
    """Test workflow CRUD, steps, export, and stats."""

    def _create_workflow(self, client: httpx.Client, name: str) -> str:
        """Create a workflow with a uuid-suffixed name; return its ID.

        Suffix avoids the duplicate-name 500 the server emits when two
        runs collide on a non-reset DB.
        """
        unique = f"{name}-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/api/v1/workflows",
            json={"name": unique, "input_schema": {}},
        )
        assert resp.status_code == 201, f"Create failed: {resp.text}"
        return resp.json()["id"]

    def test_list_workflows(self, client: httpx.Client) -> None:
        """Listing workflows returns paginated response."""
        resp = client.get("/api/v1/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "pagination" in data

    def test_create_workflow_minimal(self, client: httpx.Client) -> None:
        """Creating a workflow with minimal fields returns 201."""
        name = f"E2E Minimal Workflow-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/api/v1/workflows",
            json={"name": name, "input_schema": {}},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == name
        assert "id" in data

    def test_get_workflow(self, client: httpx.Client) -> None:
        """Getting a workflow by ID returns its details."""
        workflow_id = self._create_workflow(client, "E2E Get Workflow")
        resp = client.get(f"/api/v1/workflows/{workflow_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == workflow_id

    def test_update_workflow(self, client: httpx.Client) -> None:
        """Updating a workflow changes its fields."""
        workflow_id = self._create_workflow(client, "E2E Update Workflow")
        resp = client.patch(
            f"/api/v1/workflows/{workflow_id}",
            json={"description": "Updated description"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "Updated description"

    def test_delete_workflow(self, client: httpx.Client) -> None:
        """Deleting a workflow removes it."""
        workflow_id = self._create_workflow(client, "E2E Delete Workflow")
        del_resp = client.delete(f"/api/v1/workflows/{workflow_id}")
        assert del_resp.status_code == 204

        get_resp = client.get(f"/api/v1/workflows/{workflow_id}")
        assert get_resp.status_code == 404

    def test_export_workflow(self, client: httpx.Client) -> None:
        """Exporting a workflow returns its serialized form."""
        workflow_id = self._create_workflow(client, "E2E Export Workflow")
        resp = client.get(f"/api/v1/workflows/{workflow_id}/export")
        assert resp.status_code == 200
        assert "data" in resp.json()

    def test_workflow_stats(self, client: httpx.Client) -> None:
        """Global workflow stats endpoint returns metrics."""
        resp = client.get("/api/v1/workflows/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_workflows" in data
        assert "total_executions" in data

    def test_list_workflow_steps(self, client: httpx.Client) -> None:
        """Listing workflow steps returns an empty list for new workflow."""
        workflow_id = self._create_workflow(client, "E2E Steps Workflow")
        resp = client.get(f"/api/v1/workflows/{workflow_id}/steps")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_workflow_triggers(self, client: httpx.Client) -> None:
        """Listing workflow triggers returns empty list for new workflow."""
        workflow_id = self._create_workflow(client, "E2E Triggers Workflow")
        resp = client.get(f"/api/v1/workflows/{workflow_id}/triggers")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
