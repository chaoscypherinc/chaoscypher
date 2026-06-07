# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for tools endpoints."""

import httpx


class TestTools:
    """Test system and user tools."""

    def test_list_system_tools(self, client: httpx.Client) -> None:
        """Listing system tools returns available tools."""
        resp = client.get("/api/v1/tools/system")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_user_tools(self, client: httpx.Client) -> None:
        """Listing user tools returns paginated response."""
        resp = client.get("/api/v1/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "pagination" in data

    def test_create_user_tool(self, client: httpx.Client) -> None:
        """Creating a user tool requires a valid system tool."""
        # First get a system tool to reference
        sys_tools = client.get("/api/v1/tools/system").json()
        if not sys_tools:
            # No system tools available - skip
            return

        system_tool_id = sys_tools[0]["id"]
        resp = client.post(
            "/api/v1/tools",
            json={
                "name": "E2E Test User Tool",
                "system_tool_id": system_tool_id,
                "configuration": {},
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "E2E Test User Tool"

    def test_get_system_tool(self, client: httpx.Client) -> None:
        """Getting a system tool by ID returns its details."""
        sys_tools = client.get("/api/v1/tools/system").json()
        if not sys_tools:
            return

        tool_id = sys_tools[0]["id"]
        resp = client.get(f"/api/v1/tools/system/{tool_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == tool_id
