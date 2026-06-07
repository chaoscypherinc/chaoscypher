# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E negative path tests for validation errors, 404s, and edge cases."""

import httpx


class TestNotFoundErrors:
    """Test 404 responses for nonexistent resources."""

    def test_get_nonexistent_node(self, client: httpx.Client) -> None:
        """Getting a nonexistent node returns 404."""
        resp = client.get("/api/v1/nodes/nonexistent-node-id-12345")
        assert resp.status_code == 404

    def test_get_nonexistent_edge(self, client: httpx.Client) -> None:
        """Getting a nonexistent edge returns 404."""
        resp = client.get("/api/v1/edges/nonexistent-edge-id-12345")
        assert resp.status_code == 404

    def test_get_nonexistent_template(self, client: httpx.Client) -> None:
        """Getting a nonexistent template returns 404."""
        resp = client.get("/api/v1/templates/nonexistent-template-id-12345")
        assert resp.status_code == 404

    def test_get_nonexistent_source(self, client: httpx.Client) -> None:
        """Getting a nonexistent source returns 404."""
        resp = client.get("/api/v1/sources/nonexistent-source-id-12345")
        assert resp.status_code == 404

    def test_get_nonexistent_workflow(self, client: httpx.Client) -> None:
        """Getting a nonexistent workflow returns 404."""
        resp = client.get("/api/v1/workflows/nonexistent-workflow-id-12345")
        assert resp.status_code == 404

    def test_get_nonexistent_chat(self, client: httpx.Client) -> None:
        """Getting a nonexistent chat returns 404 or similar."""
        resp = client.get("/api/v1/chats/nonexistent-chat-id-12345")
        # Chat get returns None->404 based on implementation
        assert resp.status_code in (404, 200)
        if resp.status_code == 200:
            assert resp.json() is None


class TestValidationErrors:
    """Test 422/400 for invalid request bodies."""

    def test_create_template_missing_name(self, client: httpx.Client) -> None:
        """Creating a template without a name returns 422."""
        resp = client.post(
            "/api/v1/templates",
            json={"template_type": "node", "properties": []},
        )
        assert resp.status_code == 422

    def test_create_template_system_prefix_forbidden(self, client: httpx.Client) -> None:
        """Creating a template with 'system_' prefix returns 400."""
        resp = client.post(
            "/api/v1/templates",
            json={
                "name": "system_forbidden_name",
                "template_type": "node",
                "properties": [],
            },
        )
        assert resp.status_code == 400

    def test_create_node_invalid_template(self, client: httpx.Client) -> None:
        """Creating a node with an invalid template_id returns error."""
        resp = client.post(
            "/api/v1/nodes",
            json={
                "template_id": "nonexistent-template-999",
                "label": "Bad Node",
                "properties": {},
            },
        )
        assert resp.status_code in (400, 404, 422)

    def test_create_workflow_missing_required(self, client: httpx.Client) -> None:
        """Creating a workflow without required fields returns 422."""
        resp = client.post("/api/v1/workflows", json={})
        assert resp.status_code == 422

    def test_upload_source_url_invalid(self, client: httpx.Client) -> None:
        """Uploading from an invalid URL format returns 422."""
        resp = client.post(
            "/api/v1/sources/url",
            json={"url": "not-a-valid-url", "extract_entities": False},
        )
        assert resp.status_code == 422


class TestAuthErrors:
    """Test authentication error cases."""

    def test_missing_token(self, base_url: str) -> None:
        """Accessing a protected endpoint without token returns 401."""
        resp = httpx.get(f"{base_url}/api/v1/auth/users", timeout=10.0)
        assert resp.status_code == 401

    def test_invalid_token(self, base_url: str) -> None:
        """Using an invalid token returns 401."""
        resp = httpx.get(
            f"{base_url}/api/v1/auth/users",
            headers={"Authorization": "Bearer invalid.token.here"},
            timeout=10.0,
        )
        assert resp.status_code == 401

    def test_malformed_auth_header(self, base_url: str) -> None:
        """A malformed auth header returns 401."""
        resp = httpx.get(
            f"{base_url}/api/v1/auth/users",
            headers={"Authorization": "NotBearer something"},
            timeout=10.0,
        )
        assert resp.status_code == 401


class TestDeletionEdgeCases:
    """Test edge cases in deletion operations."""

    def test_delete_template_in_use_without_force(self, client: httpx.Client) -> None:
        """Deleting a template in use without force returns 409."""
        # Create template
        create_resp = client.post(
            "/api/v1/templates",
            json={
                "name": "E2E_InUseTemplate",
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

        # Create a node using the template
        client.post(
            "/api/v1/nodes",
            json={
                "template_id": template_id,
                "label": "Blocks Delete",
                "properties": {"name": "Blocks Delete"},
            },
        )

        # Attempt to delete without force
        resp = client.delete(f"/api/v1/templates/{template_id}")
        assert resp.status_code == 409

        # Cleanup with force
        client.delete(f"/api/v1/templates/{template_id}?force=true")

    def test_delete_nonexistent_node(self, client: httpx.Client) -> None:
        """Deleting a nonexistent node returns 404."""
        resp = client.delete("/api/v1/nodes/nonexistent-delete-id-99999")
        assert resp.status_code == 404
