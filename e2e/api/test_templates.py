# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for template CRUD."""

import httpx


class TestTemplates:
    """Test template creation, listing, update, and deletion."""

    def test_create_node_template(self, client: httpx.Client) -> None:
        """Creating a node template returns 201."""
        resp = client.post(
            "/api/v1/templates",
            json={
                "name": "E2E_TestPerson",
                "description": "E2E test node template",
                "template_type": "node",
                "properties": [
                    {
                        "name": "full_name",
                        "display_name": "full_name",
                        "property_type": "string",
                        "required": True,
                    },
                    {
                        "name": "age",
                        "display_name": "age",
                        "property_type": "integer",
                        "required": False,
                    },
                ],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "E2E_TestPerson"
        assert data["template_type"] == "node"

    def test_create_edge_template(self, client: httpx.Client) -> None:
        """Creating an edge template returns 201."""
        resp = client.post(
            "/api/v1/templates",
            json={
                "name": "E2E_TestRelation",
                "description": "E2E test edge template",
                "template_type": "edge",
                "properties": [
                    {
                        "name": "weight",
                        "display_name": "weight",
                        "property_type": "float",
                        "required": False,
                    },
                ],
            },
        )
        assert resp.status_code == 201
        assert resp.json()["template_type"] == "edge"

    def test_list_templates(self, client: httpx.Client) -> None:
        """Listing templates includes created ones."""
        resp = client.get("/api/v1/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data

    def test_update_template(self, client: httpx.Client) -> None:
        """Updating a template changes its fields."""
        list_resp = client.get("/api/v1/templates?template_type=node")
        templates = list_resp.json()["data"]
        target = next((t for t in templates if t["name"] == "E2E_TestPerson"), None)
        if target is None:
            # Create it if it doesn't exist
            client.post(
                "/api/v1/templates",
                json={
                    "name": "E2E_TestPerson",
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
            list_resp = client.get("/api/v1/templates?template_type=node")
            templates = list_resp.json()["data"]
            target = next(t for t in templates if t["name"] == "E2E_TestPerson")

        resp = client.patch(
            f"/api/v1/templates/{target['id']}",
            json={"description": "Updated E2E description"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "Updated E2E description"

    def test_delete_template(self, client: httpx.Client) -> None:
        """Deleting a template removes it."""
        create_resp = client.post(
            "/api/v1/templates",
            json={
                "name": "E2E_DeleteMe",
                "template_type": "node",
                "properties": [],
            },
        )
        template_id = create_resp.json()["id"]

        del_resp = client.delete(f"/api/v1/templates/{template_id}?force=true")
        assert del_resp.status_code == 204
