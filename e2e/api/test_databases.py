# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for database management endpoints."""

import uuid

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
        """Creating a new database returns 201.

        A uuid-suffixed name keeps the create fresh even in the resume
        phase (where a fixed name persists from the prior run and the
        old (201, 400) set let a broken create pass as "exists").
        """
        name = f"e2e-test-db-{uuid.uuid4().hex[:8]}"
        resp = client.post("/api/v1/databases", json={"name": name})
        assert resp.status_code == 201, f"Create failed: {resp.text}"
        assert resp.json()["name"] == name

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
