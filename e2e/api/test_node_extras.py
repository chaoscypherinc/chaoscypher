# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for node connections, citations, batch, and position."""

import httpx


class TestNodeExtras:
    """Test node connections, citations, batch operations, and position."""

    def _setup_template(self, client: httpx.Client) -> str:
        """Get or create a node template."""
        resp = client.get("/api/v1/templates")
        for t in resp.json()["data"]:
            if t["name"] == "E2E_ExtrasPerson":
                return t["id"]
        create_resp = client.post(
            "/api/v1/templates",
            json={
                "name": "E2E_ExtrasPerson",
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
        return create_resp.json()["id"]

    def _create_node(self, client: httpx.Client, template_id: str, label: str) -> str:
        """Create a node and return its ID."""
        resp = client.post(
            "/api/v1/nodes",
            json={
                "template_id": template_id,
                "label": label,
                "properties": {"name": label},
            },
        )
        return resp.json()["id"]

    def test_get_node_connections_empty(self, client: httpx.Client) -> None:
        """Getting connections for an isolated node returns empty list."""
        template_id = self._setup_template(client)
        node_id = self._create_node(client, template_id, "Isolated Node")

        resp = client.get(f"/api/v1/nodes/{node_id}/connections")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "pagination" in data

    def test_get_node_citations_empty(self, client: httpx.Client) -> None:
        """Getting citations for a manually-created node returns empty."""
        template_id = self._setup_template(client)
        node_id = self._create_node(client, template_id, "No Citations Node")

        resp = client.get(f"/api/v1/nodes/{node_id}/citations")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert data["pagination"]["total"] == 0

    def test_update_node_position(self, client: httpx.Client) -> None:
        """Updating node position only returns the node."""
        template_id = self._setup_template(client)
        node_id = self._create_node(client, template_id, "Position Node")

        resp = client.patch(
            f"/api/v1/nodes/{node_id}/position",
            json={"position": {"x": 123.5, "y": 456.7}},
        )
        assert resp.status_code == 200
        assert resp.json()["position"]["x"] == 123.5
        assert resp.json()["position"]["y"] == 456.7

    def test_batch_create_nodes(self, client: httpx.Client) -> None:
        """Batch node creation returns a task_id."""
        template_id = self._setup_template(client)
        resp = client.post(
            "/api/v1/nodes/batch",
            json={
                "operations": [
                    {
                        "operation": "create",
                        "data": {
                            "template_id": template_id,
                            "label": "Batch Node 1",
                            "properties": {"name": "Batch Node 1"},
                        },
                    },
                    {
                        "operation": "create",
                        "data": {
                            "template_id": template_id,
                            "label": "Batch Node 2",
                            "properties": {"name": "Batch Node 2"},
                        },
                    },
                ]
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "task_id" in data


class TestEdgeBatch:
    """Test batch edge operations."""

    def test_batch_create_edges(self, client: httpx.Client) -> None:
        """Batch edge creation returns a task_id."""
        # Setup: create template and two nodes
        template_resp = client.get("/api/v1/templates")
        template_id = None
        for t in template_resp.json()["data"]:
            if t["name"] == "E2E_BatchEdgePerson":
                template_id = t["id"]
                break
        if template_id is None:
            create_resp = client.post(
                "/api/v1/templates",
                json={
                    "name": "E2E_BatchEdgePerson",
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
                "label": "BatchEdge A",
                "properties": {"name": "A"},
            },
        ).json()["id"]
        node_b = client.post(
            "/api/v1/nodes",
            json={
                "template_id": template_id,
                "label": "BatchEdge B",
                "properties": {"name": "B"},
            },
        ).json()["id"]

        # Create edge template
        edge_tmpls = client.get("/api/v1/templates?template_type=edge").json()["data"]
        edge_template_id = None
        for t in edge_tmpls:
            if t["name"] == "E2E_BatchEdgeType":
                edge_template_id = t["id"]
                break
        if edge_template_id is None:
            edge_create = client.post(
                "/api/v1/templates",
                json={
                    "name": "E2E_BatchEdgeType",
                    "template_type": "edge",
                    "properties": [],
                },
            )
            edge_template_id = edge_create.json()["id"]

        # Batch create edges
        resp = client.post(
            "/api/v1/edges/batch",
            json={
                "operations": [
                    {
                        "operation": "create",
                        "data": {
                            "template_id": edge_template_id,
                            "source_node_id": node_a,
                            "target_node_id": node_b,
                            "label": "batch_relation",
                        },
                    },
                ]
            },
        )
        assert resp.status_code == 202
        assert "task_id" in resp.json()
