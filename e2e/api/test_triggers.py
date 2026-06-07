# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for trigger endpoints."""

import uuid

import httpx


class TestTriggers:
    """Test trigger CRUD."""

    def _create_workflow(self, client: httpx.Client) -> str:
        """Create a workflow for trigger testing with a unique name."""
        resp = client.post(
            "/api/v1/workflows",
            json={
                "name": f"E2E Trigger Test Workflow-{uuid.uuid4().hex[:8]}",
                "input_schema": {},
            },
        )
        assert resp.status_code == 201, f"Create failed: {resp.text}"
        return resp.json()["id"]

    def test_list_triggers(self, client: httpx.Client) -> None:
        """Listing triggers returns paginated response."""
        resp = client.get("/api/v1/triggers")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "pagination" in data

    def test_create_trigger(self, client: httpx.Client) -> None:
        """Creating a trigger returns 201."""
        workflow_id = self._create_workflow(client)
        resp = client.post(
            "/api/v1/triggers",
            json={
                "name": "E2E Test Trigger",
                "event_source": "webhook",
                "filters": {},
                "workflow_id": workflow_id,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "E2E Test Trigger"
        assert data["workflow_id"] == workflow_id

    def test_get_trigger(self, client: httpx.Client) -> None:
        """Getting a trigger by ID returns its details."""
        workflow_id = self._create_workflow(client)
        create_resp = client.post(
            "/api/v1/triggers",
            json={
                "name": "E2E Get Trigger",
                "event_source": "webhook",
                "filters": {},
                "workflow_id": workflow_id,
            },
        )
        trigger_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/triggers/{trigger_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == trigger_id

    def test_update_trigger(self, client: httpx.Client) -> None:
        """Updating a trigger changes its fields."""
        workflow_id = self._create_workflow(client)
        create_resp = client.post(
            "/api/v1/triggers",
            json={
                "name": "E2E Update Trigger",
                "event_source": "webhook",
                "filters": {},
                "workflow_id": workflow_id,
            },
        )
        trigger_id = create_resp.json()["id"]

        resp = client.patch(f"/api/v1/triggers/{trigger_id}", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_delete_trigger(self, client: httpx.Client) -> None:
        """Deleting a trigger removes it."""
        workflow_id = self._create_workflow(client)
        create_resp = client.post(
            "/api/v1/triggers",
            json={
                "name": "E2E Delete Trigger",
                "event_source": "webhook",
                "filters": {},
                "workflow_id": workflow_id,
            },
        )
        trigger_id = create_resp.json()["id"]

        del_resp = client.delete(f"/api/v1/triggers/{trigger_id}")
        assert del_resp.status_code == 204
