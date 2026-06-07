# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for node CRUD."""

import httpx


class TestNodes:
    """Test node creation, listing, retrieval, update, and deletion."""

    def _get_template_id(self, client: httpx.Client, name: str) -> str:
        """Find a template by name, create if missing."""
        resp = client.get("/api/v1/templates")
        for t in resp.json()["data"]:
            if t["name"] == name:
                return t["id"]
        create_resp = client.post(
            "/api/v1/templates",
            json={
                "name": name,
                "template_type": "node",
                "properties": [
                    {
                        "name": "full_name",
                        "display_name": "full_name",
                        "property_type": "string",
                        "required": True,
                    },
                ],
            },
        )
        return create_resp.json()["id"]

    def test_create_node(self, client: httpx.Client) -> None:
        """Creating a node returns 201 with node data."""
        template_id = self._get_template_id(client, "E2E_NodeTestPerson")
        resp = client.post(
            "/api/v1/nodes",
            json={
                "template_id": template_id,
                "label": "E2E Alice",
                "properties": {"full_name": "E2E Alice"},
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["label"] == "E2E Alice"

    def test_list_nodes(self, client: httpx.Client) -> None:
        """Listing nodes returns created nodes."""
        resp = client.get("/api/v1/nodes")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "pagination" in data

    def test_get_node(self, client: httpx.Client) -> None:
        """Getting a node by ID returns full details."""
        template_id = self._get_template_id(client, "E2E_NodeTestPerson")
        create_resp = client.post(
            "/api/v1/nodes",
            json={
                "template_id": template_id,
                "label": "E2E GetTest",
                "properties": {"full_name": "E2E GetTest"},
            },
        )
        node_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/nodes/{node_id}")
        assert resp.status_code == 200
        assert resp.json()["label"] == "E2E GetTest"

    def test_update_node(self, client: httpx.Client) -> None:
        """Updating a node changes its fields."""
        template_id = self._get_template_id(client, "E2E_NodeTestPerson")
        create_resp = client.post(
            "/api/v1/nodes",
            json={
                "template_id": template_id,
                "label": "E2E UpdateBefore",
                "properties": {"full_name": "E2E UpdateBefore"},
            },
        )
        node_id = create_resp.json()["id"]

        resp = client.patch(f"/api/v1/nodes/{node_id}", json={"label": "E2E UpdateAfter"})
        assert resp.status_code == 200
        assert resp.json()["label"] == "E2E UpdateAfter"

    def test_delete_node(self, client: httpx.Client) -> None:
        """Deleting a node removes it."""
        template_id = self._get_template_id(client, "E2E_NodeTestPerson")
        create_resp = client.post(
            "/api/v1/nodes",
            json={
                "template_id": template_id,
                "label": "E2E DeleteMe",
                "properties": {"full_name": "E2E DeleteMe"},
            },
        )
        node_id = create_resp.json()["id"]

        del_resp = client.delete(f"/api/v1/nodes/{node_id}")
        assert del_resp.status_code == 204

        get_resp = client.get(f"/api/v1/nodes/{node_id}")
        assert get_resp.status_code == 404
