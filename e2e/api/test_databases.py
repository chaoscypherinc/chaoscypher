# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for database management endpoints."""

import httpx


class TestDatabases:
    """Test database CRUD and switching."""

    def test_list_databases(self, client: httpx.Client) -> None:
        """Listing databases returns at least the default database."""
        resp = client.get("/api/v1/databases")
        assert resp.status_code == 200
        data = resp.json()
        assert "databases" in data
        names = [db["name"] for db in data["databases"]]
        assert "default" in names

    def test_create_database(self, client: httpx.Client) -> None:
        """Creating a new database returns 201 (or 400 if already exists)."""
        resp = client.post("/api/v1/databases", json={"name": "e2e-test-db"})
        # Either created fresh or already exists
        assert resp.status_code in (201, 400)
        if resp.status_code == 201:
            assert resp.json()["name"] == "e2e-test-db"

    def test_switch_database(self, client: httpx.Client) -> None:
        """Switching databases changes the current one."""
        client.post("/api/v1/databases", json={"name": "e2e-switch-db"})

        resp = client.patch("/api/v1/databases/current", json={"name": "e2e-switch-db"})
        assert resp.status_code == 200

        # Switch back to default
        client.patch("/api/v1/databases/current", json={"name": "default"})

    def test_data_isolation(self, client: httpx.Client) -> None:
        """Nodes in one database are not visible in another."""
        # Ensure we're on default
        client.patch("/api/v1/databases/current", json={"name": "default"})

        # Get or create a template
        templates = client.get("/api/v1/templates").json()["data"]
        if not templates:
            client.post(
                "/api/v1/templates",
                json={
                    "name": "IsolationTest",
                    "template_type": "node",
                    "properties": [],
                },
            )
            templates = client.get("/api/v1/templates").json()["data"]
        template_id = templates[0]["id"]

        client.post(
            "/api/v1/nodes",
            json={
                "template_id": template_id,
                "label": "IsolationTestNode",
                "properties": {},
            },
        )

        # Switch to a fresh database
        client.post("/api/v1/databases", json={"name": "e2e-isolation-db"})
        client.patch("/api/v1/databases/current", json={"name": "e2e-isolation-db"})

        nodes_resp = client.get("/api/v1/nodes")
        assert nodes_resp.status_code == 200
        node_labels = [n["label"] for n in nodes_resp.json()["data"]]
        assert "IsolationTestNode" not in node_labels

        # Switch back
        client.patch("/api/v1/databases/current", json={"name": "default"})
