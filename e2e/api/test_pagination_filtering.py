# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for pagination and filtering edge cases."""

import httpx


def _setup_template(client: httpx.Client, name: str) -> str:
    """Get or create a node template."""
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
                    "name": "name",
                    "display_name": "name",
                    "property_type": "string",
                    "required": True,
                },
            ],
        },
    )
    return create_resp.json()["id"]


class TestPagination:
    """Test multi-page pagination."""

    def test_node_list_pagination_multipage(self, client: httpx.Client) -> None:
        """Creating 25 nodes and paginating returns correct pages."""
        template_id = _setup_template(client, "E2E_PaginationPerson")

        # Create 25 nodes
        for i in range(25):
            client.post(
                "/api/v1/nodes",
                json={
                    "template_id": template_id,
                    "label": f"Pagination Node {i:03d}",
                    "properties": {"name": f"Pagination Node {i:03d}"},
                },
            )

        # Page 1 with size 10
        page1 = client.get(
            "/api/v1/nodes",
            params={"template_id": template_id, "page": 1, "page_size": 10},
        )
        assert page1.status_code == 200
        p1_data = page1.json()
        assert len(p1_data["data"]) == 10
        assert p1_data["pagination"]["page"] == 1
        assert p1_data["pagination"]["has_next"] is True

        # Page 2
        page2 = client.get(
            "/api/v1/nodes",
            params={"template_id": template_id, "page": 2, "page_size": 10},
        )
        assert page2.status_code == 200
        p2_data = page2.json()
        assert p2_data["pagination"]["page"] == 2
        assert p2_data["pagination"]["has_prev"] is True

        # Verify different items on page 1 vs page 2
        p1_ids = {n["id"] for n in p1_data["data"]}
        p2_ids = {n["id"] for n in p2_data["data"]}
        assert p1_ids.isdisjoint(p2_ids)

    def test_node_list_minimal_mode(self, client: httpx.Client) -> None:
        """Minimal mode returns only essential fields for performance."""
        resp = client.get("/api/v1/nodes", params={"minimal": "true"})
        assert resp.status_code == 200

    def test_node_list_include_stats(self, client: httpx.Client) -> None:
        """include_stats adds edge/citation counts to nodes."""
        resp = client.get("/api/v1/nodes", params={"include_stats": "true"})
        assert resp.status_code == 200


class TestFiltering:
    """Test list filtering by various parameters."""

    def test_template_list_filtered_by_type(self, client: httpx.Client) -> None:
        """Filtering templates by template_type works."""
        resp = client.get("/api/v1/templates", params={"template_type": "node"})
        assert resp.status_code == 200
        for t in resp.json()["data"]:
            assert t["template_type"] == "node"

    def test_template_list_edge_type(self, client: httpx.Client) -> None:
        """Filtering templates by edge type returns only edge templates."""
        resp = client.get("/api/v1/templates", params={"template_type": "edge"})
        assert resp.status_code == 200
        for t in resp.json()["data"]:
            assert t["template_type"] == "edge"

    def test_node_list_by_template(self, client: httpx.Client) -> None:
        """Filtering nodes by template_id returns only matching nodes."""
        template_id = _setup_template(client, "E2E_FilterPerson")
        resp = client.get("/api/v1/nodes", params={"template_id": template_id})
        assert resp.status_code == 200
        for n in resp.json()["data"]:
            assert n["template_id"] == template_id
