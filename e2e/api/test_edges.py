# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for edge CRUD."""

import httpx


class TestEdges:
    """Test edge creation, listing, and deletion."""

    def _create_two_nodes(self, client: httpx.Client) -> tuple[str, str]:
        """Create a template and two nodes, return their IDs."""
        resp = client.get("/api/v1/templates")
        template_id = None
        for t in resp.json()["data"]:
            if t["name"] == "E2E_EdgeTestPerson":
                template_id = t["id"]
                break
        if template_id is None:
            create_resp = client.post(
                "/api/v1/templates",
                json={
                    "name": "E2E_EdgeTestPerson",
                    "template_type": "node",
                    "properties": [
                        {
                            "name": "name",
                            "display_name": "name",
                            "property_type": "string",
                            "required": True,
                        },
                    ],
                },
            )
            template_id = create_resp.json()["id"]

        node_a = client.post(
            "/api/v1/nodes",
            json={
                "template_id": template_id,
                "label": "EdgeNode A",
                "properties": {"name": "A"},
            },
        ).json()["id"]

        node_b = client.post(
            "/api/v1/nodes",
            json={
                "template_id": template_id,
                "label": "EdgeNode B",
                "properties": {"name": "B"},
            },
        ).json()["id"]

        return node_a, node_b

    def _get_edge_template_id(self, client: httpx.Client) -> str:
        """Get or create an edge template."""
        resp = client.get("/api/v1/templates?template_type=edge")
        for t in resp.json()["data"]:
            if t["name"] == "E2E_TestEdge":
                return t["id"]
        create_resp = client.post(
            "/api/v1/templates",
            json={
                "name": "E2E_TestEdge",
                "template_type": "edge",
                "properties": [],
            },
        )
        return create_resp.json()["id"]

    def test_create_edge(self, client: httpx.Client) -> None:
        """Creating an edge between two nodes returns 201."""
        node_a, node_b = self._create_two_nodes(client)
        edge_template_id = self._get_edge_template_id(client)

        resp = client.post(
            "/api/v1/edges",
            json={
                "template_id": edge_template_id,
                "source_node_id": node_a,
                "target_node_id": node_b,
                "label": "e2e_test_relation",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["source_node_id"] == node_a

    def test_list_edges(self, client: httpx.Client) -> None:
        """Listing edges returns created edges."""
        resp = client.get("/api/v1/edges")
        assert resp.status_code == 200
        assert "data" in resp.json()

    def test_delete_edge(self, client: httpx.Client) -> None:
        """Deleting an edge removes it."""
        node_a, node_b = self._create_two_nodes(client)
        edge_template_id = self._get_edge_template_id(client)

        create_resp = client.post(
            "/api/v1/edges",
            json={
                "template_id": edge_template_id,
                "source_node_id": node_a,
                "target_node_id": node_b,
                "label": "delete_me",
            },
        )
        edge_id = create_resp.json()["id"]

        del_resp = client.delete(f"/api/v1/edges/{edge_id}")
        assert del_resp.status_code == 204
