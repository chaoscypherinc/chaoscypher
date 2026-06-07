# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for template and regeneration batch operations."""

import httpx


class TestTemplateBatch:
    """Test POST /templates/batch for bulk operations."""

    def test_batch_create_templates(self, client: httpx.Client) -> None:
        """Batch template creation returns a task_id."""
        resp = client.post(
            "/api/v1/templates/batch",
            json={
                "operations": [
                    {
                        "operation": "create",
                        "data": {
                            "name": "E2E_BatchT1",
                            "template_type": "node",
                            "properties": [],
                        },
                    },
                    {
                        "operation": "create",
                        "data": {
                            "name": "E2E_BatchT2",
                            "template_type": "node",
                            "properties": [],
                        },
                    },
                ]
            },
        )
        assert resp.status_code == 202
        assert "task_id" in resp.json()

    def test_regenerate_template_embeddings(self, client: httpx.Client) -> None:
        """Template embedding regeneration returns a task_id."""
        resp = client.post("/api/v1/templates/embeddings")
        assert resp.status_code == 202
        assert "task_id" in resp.json()
